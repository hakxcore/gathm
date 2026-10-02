"""Bounded, serialized access to a reusable assistant subprocess.

The child owns the heavy Python imports. Each request supplies its entire
history; a failed or timed-out request is never replayed, since tools may have
already run. Changing the interpreter, environment, or owner replaces it.
"""
from __future__ import annotations

import atexit
import json
import os
import queue
import signal
import subprocess
import threading
import time


MAX_MESSAGE_BYTES = 2 * 1024 * 1024


class AssistantWorker:
    def __init__(self):
        self._lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._shutdown = threading.Event()
        self._capacity = threading.BoundedSemaphore(4)
        self._proc = None
        self._signature = None
        self._events = None
        self._stopped = None
        atexit.register(self.shutdown)

    def shutdown(self):
        """Stop active work and prevent queued requests from launching a child."""
        self._shutdown.set()
        self.close()

    def close(self):
        with self._state_lock:
            proc, self._proc = self._proc, None
            self._signature = None
            if self._stopped is not None:
                self._stopped.set()
        if proc is None:
            return
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        for pipe in (proc.stdin, proc.stdout):
            if pipe:
                pipe.close()

    def _start_locked(self, command, cwd, env):
        self.close()
        if self._shutdown.is_set():
            raise RuntimeError("assistant worker is shutting down")
        proc = subprocess.Popen(
            command, cwd=cwd, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=0, start_new_session=os.name == "posix",
        )
        self._proc = proc
        events = self._events = queue.Queue(maxsize=128)
        stopped = self._stopped = threading.Event()

        def put(event):
            while not stopped.is_set():
                try:
                    events.put(event, timeout=0.1)
                    return
                except queue.Full:
                    pass

        def read():
            try:
                while not stopped.is_set():
                    line = proc.stdout.readline(MAX_MESSAGE_BYTES + 1)
                    if not line:
                        raise RuntimeError("assistant worker exited without a response")
                    if len(line) > MAX_MESSAGE_BYTES:
                        raise RuntimeError("assistant response exceeded the size limit")
                    put(json.loads(line))
            except Exception as exc:
                put(exc)

        threading.Thread(target=read, daemon=True, name="gathm-worker-output").start()

    def request(self, command, cwd, env, payload, timeout, on_token=None, cancelled=None):
        encoded = (json.dumps(payload) + "\n").encode()
        if len(encoded) > MAX_MESSAGE_BYTES:
            return {"error": "conversation exceeds the assistant request size limit"}
        deadline = time.monotonic() + timeout
        if not self._capacity.acquire(blocking=False):
            return {"error": "Gathm is busy. Try again when the current conversation finishes."}
        acquired = False
        try:
            while not acquired and time.monotonic() < deadline:
                if self._shutdown.is_set():
                    return {"error": "assistant worker is shutting down"}
                if cancelled is not None and cancelled.is_set():
                    return {"error": "request cancelled before it started"}
                acquired = self._lock.acquire(timeout=min(0.25, max(0, deadline - time.monotonic())))
            if not acquired:
                return {"error": "Gathm is busy; this request timed out before it started."}
            if cancelled is not None and cancelled.is_set():
                return {"error": "request cancelled before it started"}
            with self._state_lock:
                if self._shutdown.is_set():
                    return {"error": "assistant worker is shutting down"}
                signature = (tuple(command), str(cwd), tuple(sorted(env.items())))
                if self._proc is None or self._proc.poll() is not None or signature != self._signature:
                    self._start_locked(command, cwd, env)
                    self._signature = signature
                proc, events, stopped = self._proc, self._events, self._stopped

            # A large history can fill a pipe while a cold child imports its
            # runtime. Include that write in the same deadline as inference.
            errors = []
            def write():
                try:
                    view = memoryview(encoded)
                    while view:
                        written = proc.stdin.write(view)
                        if not written:
                            raise BrokenPipeError("assistant input closed")
                        view = view[written:]
                    proc.stdin.flush()
                except Exception as exc:
                    errors.append(exc)
            sender = threading.Thread(target=write, daemon=True, name="gathm-worker-input")
            sender.start()
            while sender.is_alive() and time.monotonic() < deadline:
                if stopped.is_set():
                    raise RuntimeError("assistant worker stopped")
                if cancelled is not None and cancelled.is_set():
                    raise RuntimeError("request cancelled")
                sender.join(timeout=min(0.25, max(0, deadline - time.monotonic())))
            if sender.is_alive():
                raise TimeoutError
            if errors:
                raise errors[0]

            received = 0
            while True:
                if stopped.is_set():
                    raise RuntimeError("assistant worker stopped")
                if cancelled is not None and cancelled.is_set():
                    raise RuntimeError("request cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                try:
                    event = events.get(timeout=min(remaining, 0.25))
                except queue.Empty:
                    continue
                if isinstance(event, Exception):
                    raise event
                if not isinstance(event, dict):
                    raise ValueError("invalid assistant response")
                if event.get("event") == "token":
                    text = event.get("text")
                    if not isinstance(text, str):
                        raise ValueError("invalid assistant token")
                    received += len(text.encode())
                    if received > MAX_MESSAGE_BYTES:
                        raise ValueError("assistant response exceeded the size limit")
                    if on_token:
                        on_token(text)
                elif event.get("event") == "result" and isinstance(event.get("data"), dict):
                    return event["data"]
                else:
                    raise ValueError("invalid assistant response")
        except TimeoutError:
            self.close()
            return {"error": f"the agent did not finish within {timeout}s. The request was stopped and was not retried."}
        except Exception as exc:
            self.close()
            return {"error": f"assistant worker failed: {exc}. The request was not retried."}
        finally:
            if acquired:
                self._lock.release()
            self._capacity.release()
