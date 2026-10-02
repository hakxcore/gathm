"""Real subprocess transport checks; no LLM, tools, or model downloads."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from api.assistant_worker import AssistantWorker, MAX_MESSAGE_BYTES
from api import server


STUB = r'''
import json, os, sys, time
for line in sys.stdin:
    req = json.loads(line)
    if req.get('exit'):
        sys.exit(3)
    if req.get('delay'):
        time.sleep(req['delay'])
    if req.get('malformed'):
        print('not JSON', flush=True)
        continue
    if req.get('oversized'):
        print('x' * (2 * 1024 * 1024 + 1), flush=True)
        continue
    if req.get('stream'):
        print(json.dumps({'event':'token','text':'Hello '}), flush=True)
    print(json.dumps({'event':'result','data': {
        'reply':req.get('query',''), 'history':req.get('history',[]),
        'pid':os.getpid(), 'owner':os.getenv('GATHM_CHAT_OWNER',''),
        'allowed':os.getenv('GATHM_ALLOWED_TOOLS','')}}), flush=True)
'''


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.worker = AssistantWorker()
        self.addCleanup(self.worker.close)
        self.command = [sys.executable, '-u', '-c', STUB]
        self.env = dict(os.environ)

    def ask(self, payload, timeout=3, **kwargs):
        return self.worker.request(self.command, str(Path.cwd()), self.env, payload, timeout, **kwargs)

    def test_reuses_process_without_inventing_history(self):
        first = self.ask({'query':'first', 'history':[{'role':'user','content':'private'}]})
        second = self.ask({'query':'second'})
        self.assertEqual(first['pid'], second['pid'])
        self.assertEqual(second['history'], [])
        self.assertEqual(second['reply'], 'second')

    def test_owner_and_permission_changes_replace_worker(self):
        self.env.update(GATHM_CHAT_OWNER='alice', GATHM_ALLOWED_TOOLS='dns')
        first = self.ask({'query':'one'})
        self.env.update(GATHM_CHAT_OWNER='bob')
        second = self.ask({'query':'two'})
        self.assertNotEqual(first['pid'], second['pid'])
        self.assertEqual(second['owner'], 'bob')
        self.env.update(GATHM_ALLOWED_TOOLS='weather')
        third = self.ask({'query':'three'})
        self.assertNotEqual(second['pid'], third['pid'])
        self.assertEqual(third['allowed'], 'weather')

    def test_stream_tokens_arrive_separately_from_result(self):
        tokens = []
        result = self.ask({'query':'Hello there', 'stream':True}, on_token=tokens.append)
        self.assertEqual(tokens, ['Hello '])
        self.assertEqual(result['reply'], 'Hello there')

    def test_timeout_retires_child_without_retry(self):
        old = self.ask({'query':'warm'})['pid']
        started = time.monotonic()
        result = self.ask({'delay':5}, timeout=0.1)
        self.assertIn('not retried', result['error'])
        self.assertLess(time.monotonic() - started, 2)
        self.assertIsNone(self.worker._proc)
        self.assertNotEqual(old, self.ask({'query':'new'})['pid'])

    def test_exit_and_malformed_output_fail_current_request(self):
        for payload in ({'exit':True}, {'malformed':True}):
            with self.subTest(payload=payload):
                result = self.ask(payload)
                self.assertIn('not retried', result['error'])
                self.assertIsNone(self.worker._proc)

    def test_oversized_request_never_starts_a_worker(self):
        result = self.ask({'query':'x' * MAX_MESSAGE_BYTES})
        self.assertIn('size limit', result['error'])
        self.assertIsNone(self.worker._proc)

    def test_cancellation_stops_active_child(self):
        cancelled = threading.Event()
        timer = threading.Timer(0.1, cancelled.set)
        timer.start()
        self.addCleanup(timer.cancel)
        result = self.ask({'delay':5}, cancelled=cancelled)
        self.assertIn('cancelled', result['error'])
        self.assertIsNone(self.worker._proc)

    def test_blocked_input_write_obeys_turn_deadline(self):
        self.command = [sys.executable, '-u', '-c', 'import time; time.sleep(30)']
        started = time.monotonic()
        result = self.ask({'query': 'x' * (1024 * 1024)}, timeout=0.2)
        self.assertIn('not retried', result['error'])
        self.assertLess(time.monotonic() - started, 2)
        self.assertIsNone(self.worker._proc)

    def test_oversized_response_retires_child(self):
        result = self.ask({'oversized': True})
        self.assertIn('size limit', result['error'])
        self.assertIsNone(self.worker._proc)

    def test_failed_token_delivery_does_not_replay_turn(self):
        seen = []
        def disconnect(text):
            seen.append(text)
            raise RuntimeError('client disconnected')
        result = self.ask({'stream': True}, on_token=disconnect)
        self.assertEqual(seen, ['Hello '])
        self.assertIn('not retried', result['error'])
        self.assertIsNone(self.worker._proc)

    def test_shutdown_interrupts_active_request_and_prevents_restart(self):
        self.ask({'query': 'warm'})
        result = {}
        def ask():
            result.update(self.ask({'delay': 5}))
        thread = threading.Thread(target=ask)
        thread.start()
        self.worker.shutdown()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertIn('error', result)
        self.assertIsNone(self.worker._proc)
        self.assertIn('shutting down', self.ask({'query': 'never run'})['error'])

    def test_concurrent_requests_do_not_cross_responses(self):
        results = {}
        def ask(name):
            results[name] = self.ask({'query':name,'delay':0.05})
        threads = [threading.Thread(target=ask, args=(str(i),)) for i in range(3)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(5)
        self.assertEqual({name: result['reply'] for name, result in results.items()},
                         {'0':'0','1':'1','2':'2'})
        self.assertEqual(len({r['pid'] for r in results.values()}), 1)

    def test_backlog_is_bounded(self):
        for _ in range(4): self.worker._capacity.acquire()
        try:
            self.assertIn('busy', self.ask({'query':'never run'})['error'])
            self.assertIsNone(self.worker._proc)
        finally:
            for _ in range(4): self.worker._capacity.release()

    def test_real_chat_worker_protocol_keeps_turns_independent(self):
        script = str(Path(__file__).resolve().parents[1] / 'pilot/chat_once.py')
        harness = r'''
import json, os, runpy, sys, types
from langchain_core.messages import AIMessage
pilot = types.ModuleType('main')
pilot.LANGCHAIN_AVAILABLE=True
pilot.AGENT_MAX_STEPS=6
pilot.LLM_BACKEND='stub'; pilot.OLLAMA_MODEL='stub'
pilot._set_token_sink=lambda sink: setattr(pilot, '_TOKEN_SINK', sink)
pilot._TOKEN_SINK=None
browser=types.ModuleType('browser'); browser.closed=0
browser.close_session=lambda: setattr(browser, 'closed', browser.closed+1)
sys.modules['browser']=browser
class Graph:
    turns=0
    def stream(self,state,config):
        assert os.environ['GATHM_NON_INTERACTIVE']=='1'
        assert browser.closed==self.turns
        self.turns+=1
        text=' | '.join(m.content for m in state['messages'])
        if pilot._TOKEN_SINK: pilot._TOKEN_SINK.feed(text)
        yield {'agent':{'next_step':'end','messages':[AIMessage(content=text)]}}
pilot.app=Graph();sys.modules['main']=pilot
sys.argv=[sys.argv[1],'--worker']
runpy.run_path(sys.argv[0],run_name='__main__')
'''
        self.command = [sys.executable, '-u', '-c', harness, script]
        tokens = []
        result = self.ask({'query':'first', 'history':[{'role':'user','content':'Sam'}], 'stream':True},
                          on_token=tokens.append)
        self.assertEqual(result['reply'], 'Sam | first')
        self.assertEqual(tokens, ['Sam | first'])
        result = self.ask({'query':'second'})
        self.assertEqual(result['reply'], 'second')


@unittest.skipUnless(server.HAS_FASTAPI, 'FastAPI required')
class StreamingApiTests(unittest.TestCase):
    def test_slow_consumer_stops_generation_at_deadline(self):
        stopped = threading.Event()
        seen = []
        def chat(query, history, timeout=None, on_token=None, cancelled=None):
            try:
                for _ in range(1000):
                    on_token('token')
                    seen.append(1)
            finally:
                stopped.set()
            return {'reply': 'done'}
        async def run():
            stream = server._stream_chat('hello', [])
            self.assertEqual(await stream.__anext__(), ': connected\n\n')
            # Hold the consumer after headers: generation must not accumulate
            # an unbounded queue or keep a worker blocked past its deadline.
            self.assertTrue(await asyncio.to_thread(stopped.wait, 2))
            await stream.aclose()
        with patch.object(server, 'CHAT_TIMEOUT', 0.15), \
                patch.object(server, 'run_chat_agent', side_effect=chat):
            asyncio.run(run())
        self.assertLessEqual(len(seen), 64)

    def test_disconnect_signals_active_generation(self):
        stopped = threading.Event()
        def chat(query, history, timeout=None, on_token=None, cancelled=None):
            try:
                cancelled.wait(2)
                self.assertTrue(cancelled.is_set())
                return {'reply': 'done'}
            finally:
                stopped.set()
        async def run():
            stream = server._stream_chat('hello', [])
            await stream.__anext__()
            await asyncio.sleep(0)
            await stream.aclose()
            self.assertTrue(await asyncio.to_thread(stopped.wait, 2))
        with patch.object(server, 'run_chat_agent', side_effect=chat):
            asyncio.run(run())

    def test_streaming_and_json_share_authorization_and_input(self):
        import httpx
        seen = []
        def chat(query, history, timeout=None, on_token=None, cancelled=None):
            seen.append((query, history, server._child_env()))
            if on_token: on_token('Hello ')
            return {'reply':'Hello Sam'}
        async def run():
            transport = httpx.ASGITransport(app=server.app, client=('127.0.0.1',12345))
            async with httpx.AsyncClient(transport=transport, base_url='http://localhost') as client:
                return await client.post('/api/v1/agent/chat', json={'query':'hello','history':[]},
                                         headers={'Accept':'text/event-stream'})
        with patch.object(server, 'run_chat_agent', side_effect=chat), \
                patch.multiple(server, AUTH_ENABLED=False, TOKEN_MAP={}):
            server._rate_windows.clear()
            response = asyncio.run(run())
        self.assertEqual(response.status_code, 200)
        events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith('data:')]
        self.assertEqual(events, [{'event':'token','text':'Hello '},
                                  {'event':'result','data':{'reply':'Hello Sam'}}])
        self.assertEqual(seen[0][:2], ('hello', []))
        self.assertEqual(seen[0][2]['GATHM_NON_INTERACTIVE'], '1')
        self.assertIn('GATHM_CHAT_OWNER', seen[0][2])


if __name__ == '__main__':
    unittest.main()
