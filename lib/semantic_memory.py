"""Optional, local embeddings for the small reviewed SQLite lesson library.

Chat never downloads a model or waits for a cold encoder. One daemon worker
per process handles at most one outstanding request, with no growing queue.
Only the explicit prepare command may download public model files.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import queue
import sqlite3
import struct
import threading
import time

MODEL = "BAAI/bge-small-en-v1.5"
MODEL_VERSION = "fastembed-0.9.0:bge-small-en-v1.5:passage-v1"
DIMENSIONS = 384
WAIT_SECONDS = 0.025
MIN_SIMILARITY = 0.70
MIN_MARGIN = 0.04
_VECTOR = struct.Struct("<" + "f" * DIMENSIONS)


def cache_directory():
    state = Path(os.environ.get("GATHM_CONFIG_DIR") or Path.home() / ".gathm")
    return Path(os.environ.get("GATHM_HARNESS_EMBEDDING_DIR") or
                state / "embeddings" / "bge-small-en-v1.5").expanduser().resolve()


def _manifest():
    if os.environ.get("GATHM_HARNESS_SEMANTIC") == "0":
        return None
    try:
        path = cache_directory() / "ready.json"
        if path.stat().st_size > 4096:
            return None
        data = json.loads(path.read_text())
        if (data["version"] != MODEL_VERSION or data["dimensions"] != DIMENSIONS
                or not Path(data["path"]).is_dir() or not data.get("digest")):
            return None
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return None


def status():
    prepared = _manifest() is not None
    return {"model": MODEL, "prepared": prepared,
            "mode": "hybrid when warm" if prepared else "keyword",
            "wait_budget_ms": int(WAIT_SECONDS * 1000)}


def prepare():
    """Explicit setup; model import/download is never part of a chat turn."""
    try:
        import onnxruntime
        onnxruntime.disable_telemetry_events()
        from fastembed import TextEmbedding
    except ImportError as exc:
        raise ValueError("Install the memory extra first: pip install 'gathm[memory]'") from exc
    cache = cache_directory()
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    encoder = TextEmbedding(MODEL, cache_dir=str(cache), threads=1,
                            providers=["CPUExecutionProvider"])
    # This attribute is covered by the pinned FastEmbed version and live eval.
    model_path = Path(encoder.model._model_dir).resolve()
    digest = hashlib.sha256(MODEL_VERSION.encode())
    for path in sorted(p for p in model_path.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(model_path)).encode())
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    data = {"version": MODEL_VERSION, "dimensions": DIMENSIONS,
            "path": str(model_path), "digest": digest.hexdigest()}
    temporary = cache / ("ready-" + str(os.getpid()) + ".tmp")
    temporary.write_text(json.dumps(data))
    temporary.replace(cache / "ready.json")
    return {"status": "prepared", "model": MODEL, "directory": str(cache)}


def _load_encoder(manifest):
    import onnxruntime
    onnxruntime.disable_telemetry_events()
    from fastembed import TextEmbedding
    # A direct model directory bypasses network discovery. The offline flag
    # additionally prevents fallback downloads if the files are unavailable.
    return TextEmbedding(MODEL, specific_model_path=manifest["path"],
                         local_files_only=True, threads=1,
                         providers=["CPUExecutionProvider"])


def document(row):
    return row["trigger"] + "\n" + row["guidance"]


def fingerprint(row):
    return hashlib.sha256(document(row).encode()).hexdigest()


def normalize(vector):
    values = tuple(float(value) for value in vector)
    if len(values) != DIMENSIONS or not all(math.isfinite(value) for value in values):
        raise ValueError("Invalid embedding")
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 0:
        raise ValueError("Empty embedding")
    return tuple(value / norm for value in values)


@dataclass
class _Job:
    store: object
    rows: list
    query: str
    manifest: dict
    done: threading.Event = field(default_factory=threading.Event)
    scores: dict = field(default_factory=dict)
    error: str = ""


class _Worker:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = queue.Queue(maxsize=1)
        self.busy = False
        self.encoder = None
        self.model_key = None
        self.query_cache = OrderedDict()
        self.retry_after = 0.0
        self.thread = threading.Thread(target=self._run, name="gathm-embeddings", daemon=True)
        self.thread.start()

    def submit(self, job):
        with self.lock:
            if self.busy or time.monotonic() < self.retry_after:
                return False
            self.busy = True
            self.jobs.put_nowait(job)
            return True

    def _run(self):
        while True:
            job = self.jobs.get()
            if job is None:  # deterministic shutdown for tests
                return
            try:
                self._evaluate(job)
            except Exception as exc:
                job.error = type(exc).__name__  # no queries or provider details in logs
                self.retry_after = time.monotonic() + 60
            finally:
                with self.lock:
                    self.busy = False
                job.done.set()

    def _evaluate(self, job):
        from lib.harness import redact
        if not job.store.enabled():
            return
        key = job.manifest["version"] + ":" + job.manifest["digest"]
        if (key, job.manifest["path"]) != self.model_key:
            self.encoder = _load_encoder(job.manifest)
            self.model_key = (key, job.manifest["path"])
            self.query_cache.clear()
        rows = [row for row in job.rows if job.store.eligible(row)]
        saved = {}
        try:
            with job.store.read_db() as db:
                saved = {row["lesson_id"]: row for row in db.execute(
                    "SELECT lesson_id,content_hash,vector FROM lesson_vectors WHERE model=?", (key,))}
        except sqlite3.OperationalError:
            pass  # an older library has no vector table yet
        vectors, missing = {}, []
        for row in rows:
            cached = saved.get(row["id"])
            try:
                if cached is None or cached["content_hash"] != fingerprint(row):
                    raise ValueError("Stale vector")
                vectors[row["id"]] = normalize(_VECTOR.unpack(cached["vector"]))
            except (ValueError, TypeError, struct.error):
                missing.append(row)
        if missing:
            encoded = list(self.encoder.passage_embed([document(row) for row in missing], batch_size=4))
            if len(encoded) != len(missing):
                raise ValueError("Incomplete embedding batch")
            for row, vector in zip(missing, encoded):
                vectors[row["id"]] = normalize(vector)
            # Never hold a database lock during inference. An archive, revision,
            # or disable during indexing wins over the worker's old snapshot.
            with job.store.db(timeout=0.01) as db:
                if job.store._get(db, "enabled", "1") != "1":
                    return
                db.execute("DELETE FROM lesson_vectors WHERE model<>? OR lesson_id NOT IN "
                           "(SELECT id FROM lessons WHERE status='active')", (key,))
                for row in missing:
                    fresh = db.execute("SELECT * FROM lessons WHERE id=? AND status='active'", (row["id"],)).fetchone()
                    if (fresh is not None and job.store.eligible(fresh)
                            and fingerprint(fresh) == fingerprint(row) and row["id"] in vectors):
                        db.execute("INSERT OR REPLACE INTO lesson_vectors VALUES (?,?,?,?)",
                                   (row["id"], key, fingerprint(row), _VECTOR.pack(*vectors[row["id"]])))
        if not job.query:
            return
        # Queries are redacted, bounded, and cached only in process memory.
        query = redact(job.query, 600)
        query_key = (str(job.store.path), hashlib.sha256(query.encode()).hexdigest())
        query_vector = self.query_cache.get(query_key)
        if query_vector is None:
            query_vector = normalize(next(iter(self.encoder.query_embed(query))))
            self.query_cache[query_key] = query_vector
            if len(self.query_cache) > 64:
                self.query_cache.popitem(last=False)
        self.query_cache.move_to_end(query_key)
        job.scores = {lesson_id: sum(a * b for a, b in zip(query_vector, vector))
                      for lesson_id, vector in vectors.items()}


_worker = None
_worker_lock = threading.Lock()


def _submit(store, rows, query):
    global _worker
    manifest = _manifest()
    if not rows or manifest is None or importlib.util.find_spec("fastembed") is None:
        return None
    with _worker_lock:
        if _worker is None:
            _worker = _Worker()
        job = _Job(store, rows, query, manifest)
        return job if _worker.submit(job) else None


def scores(store, rows, query):
    start = time.monotonic()
    job = _submit(store, rows, query)
    if job is None:
        return {}
    job.done.wait(max(0, WAIT_SECONDS - (time.monotonic() - start)))
    return job.scores if job.done.is_set() and not job.error else {}


def index(store):
    if not store.enabled():
        return {"status": "disabled"}
    rows = [row for row in store.lessons("active") if store.eligible(row)]
    if not rows:
        return {"status": "no approved lessons", "indexed": 0}
    job = _submit(store, rows, "")
    if job is None:
        return {"status": "not ready; run harness prepare or wait for indexing"}
    if not job.done.wait(75) or job.error:
        return {"status": "index unavailable; keyword recall remains available"}
    return {"status": "indexed", "indexed": len(rows)}
