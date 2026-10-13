"""Semantic recall boundaries with deterministic embeddings, no downloads."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from lib import harness, semantic_memory as semantic


def vector(index=0):
    return [float(i == index) for i in range(semantic.DIMENSIONS)]


class Encoder:
    def __init__(self):
        self.documents = []
        self.queries = []
        self.block = None
        self.entered = threading.Event()

    def passage_embed(self, texts, **kwargs):
        self.entered.set()
        if self.block:
            self.block.wait(2)
        self.documents.extend(texts)
        return iter([vector() for _ in texts])

    def query_embed(self, query):
        self.queries.append(query)
        return iter([vector(1 if query == "unrelated" else 0)])


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"GATHM_HARNESS_DIR": self.temp.name,
            "GATHM_HARNESS_EMBEDDING_DIR": self.temp.name,
            "GATHM_HARNESS_DISABLED": "0", "GATHM_HARNESS_SEMANTIC": "1",
            "GATHM_CHAT_OWNER": "semantic-test", "GATHM_HARNESS_SCOPE": "test"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.manifest = {"version": semantic.MODEL_VERSION, "dimensions": semantic.DIMENSIONS,
                         "path": self.temp.name, "digest": "test-model"}
        (Path(self.temp.name) / "ready.json").write_text(json.dumps(self.manifest))
        self.encoder = Encoder()
        self.loader = patch.object(semantic, "_load_encoder", return_value=self.encoder)
        self.loader.start()
        self.addCleanup(self.loader.stop)
        self.available = patch.object(semantic.importlib.util, "find_spec", return_value=object())
        self.available.start()
        self.addCleanup(self.available.stop)
        self.worker_patch = patch.object(semantic, "_worker", None)
        self.worker_patch.start()
        self.addCleanup(self.worker_patch.stop)
        self.addCleanup(self.stop_worker)
        self.store = harness.Harness()

    def stop_worker(self):
        if self.encoder.block:
            self.encoder.block.set()
        if semantic._worker:
            semantic._worker.jobs.put(None, timeout=2)
            semantic._worker.thread.join(2)

    def approved(self, store=None):
        store = store or self.store
        lesson = store.teach("When choosing the length of answers", "Keep explanations short and give one concrete example.")
        store.review(lesson, "approve")
        return lesson

    def index(self):
        self.assertEqual(semantic.index(self.store)["status"], "indexed")

    def test_paraphrase_recalled_after_approval_and_vectors_persist(self):
        lesson = self.store.teach("When choosing the length of answers", "Keep explanations short and give one concrete example.")
        self.assertEqual(self.store.recall("Be concise."), "")
        self.assertIsNone(semantic._worker)
        self.store.review(lesson, "approve")
        self.index()
        self.assertIn(lesson, self.store.recall("Be concise."))
        self.assertEqual(self.store.recall("unrelated"), "")
        fresh = harness.Harness()
        self.assertIn(lesson, fresh.recall("Be concise."))
        self.assertEqual(len(self.encoder.documents), 1)
        self.assertEqual(len(self.encoder.queries), 2)
        with self.store.read_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM lesson_vectors").fetchone()[0], 1)

    def test_archive_replacement_and_disable_invalidate_recall(self):
        first = self.approved()
        self.index()
        second = self.store.teach("When choosing the length of answers", "Give full explanations when a topic needs careful detail.")
        self.store.review(second, "approve")
        self.assertNotIn(first, self.store.recall("Be concise."))
        self.index()
        self.store.review(second, "archive")
        self.assertEqual(self.store.recall("Be concise."), "")
        with self.store.read_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM lesson_vectors").fetchone()[0], 0)
        self.approved()
        self.store.enable(False)
        self.assertEqual(self.store.recall("Be concise."), "")

    def test_missing_model_or_dependency_preserves_keyword_recall(self):
        lesson = self.approved()
        Path(self.temp.name, "ready.json").unlink()
        self.assertIn(lesson, self.store.recall("length answers"))
        self.assertIsNone(semantic._worker)
        Path(self.temp.name, "ready.json").write_text(json.dumps(self.manifest))
        with patch.object(semantic.importlib.util, "find_spec", return_value=None):
            self.assertIn(lesson, self.store.recall("length answers"))
        self.assertIsNone(semantic._worker)

    def test_cold_or_slow_worker_falls_back_and_does_not_queue_more_jobs(self):
        lesson = self.approved()
        self.encoder.block = threading.Event()
        started = time.monotonic()
        self.assertIn(lesson, self.store.recall("length answers"))
        self.assertLess(time.monotonic() - started, 0.2)
        self.assertTrue(self.encoder.entered.wait(1))
        for _ in range(10):
            self.assertIn(lesson, self.store.recall("length answers"))
        self.assertTrue(semantic._worker.busy)
        self.assertEqual(semantic._worker.jobs.qsize(), 0)

    def test_archive_during_inference_cannot_recreate_vector(self):
        lesson = self.approved()
        self.encoder.block = threading.Event()
        job = semantic._submit(self.store, self.store.lessons("active"), "Be concise.")
        self.assertTrue(self.encoder.entered.wait(1))
        self.store.review(lesson, "archive")
        self.encoder.block.set()
        self.assertTrue(job.done.wait(1))
        with self.store.read_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM lesson_vectors").fetchone()[0], 0)
        self.assertEqual(self.store.recall("Be concise."), "")

    def test_archive_between_ranking_and_prompt_is_rechecked(self):
        lesson = self.approved()
        def score(*args):
            self.store.review(lesson, "archive")
            return {lesson: 1.0}
        with patch.object(semantic, "scores", side_effect=score):
            self.assertEqual(self.store.recall("Be concise."), "")

    def test_ambiguous_semantic_matches_do_not_inject_arbitrary_guidance(self):
        first = self.approved()
        second = self.store.teach("When preparing detailed written reports", "Use short paragraphs and a summary of the findings.")
        self.store.review(second, "approve")
        with patch.object(semantic, "scores", return_value={first: 0.81, second: 0.80}):
            self.assertEqual(self.store.recall("Be concise."), "")
            self.assertIn(first, self.store.recall("length answers"))

    def test_disabled_during_inference_discards_writes(self):
        self.approved()
        self.encoder.block = threading.Event()
        job = semantic._submit(self.store, self.store.lessons("active"), "Be concise.")
        self.assertTrue(self.encoder.entered.wait(1))
        self.store.enable(False)
        self.encoder.block.set()
        self.assertTrue(job.done.wait(1))
        with self.store.read_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM lesson_vectors").fetchone()[0], 0)

    def test_namespaces_never_recall_each_others_vectors(self):
        first = self.approved()
        self.index()
        with patch.dict(os.environ, {"GATHM_CHAT_OWNER": "another-owner"}):
            other = harness.Harness()
            self.assertEqual(other.recall("Be concise."), "")
            second = self.approved(other)
            self.assertEqual(semantic.index(other)["status"], "indexed")
            context = other.recall("Be concise.")
            self.assertIn(second, context)
            self.assertNotIn(first, context)

    def test_corrupt_and_stale_vectors_are_rebuilt(self):
        lesson = self.approved()
        self.index()
        with self.store.db() as db:
            db.execute("UPDATE lesson_vectors SET vector=? WHERE lesson_id=?", (b"bad", lesson))
        self.index()
        with self.store.db() as db:
            db.execute("UPDATE lesson_vectors SET content_hash='old'")
        self.index()
        self.assertEqual(len(self.encoder.documents), 3)
        self.manifest["digest"] = "new-model"
        Path(self.temp.name, "ready.json").write_text(json.dumps(self.manifest))
        self.index()
        self.assertEqual(len(self.encoder.documents), 4)

    def test_invalid_vectors_are_not_persisted_and_failure_backs_off(self):
        lesson = self.approved()
        with patch.object(self.encoder, "passage_embed", return_value=iter([[float("nan")]])):
            result = semantic.index(self.store)
        self.assertIn("unavailable", result["status"])
        self.assertIn(lesson, self.store.recall("length answers"))
        with self.store.read_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM lesson_vectors").fetchone()[0], 0)

    def test_incomplete_embedding_batch_cannot_report_a_successful_index(self):
        self.approved()
        with patch.object(self.encoder, "passage_embed", return_value=iter([])):
            result = semantic.index(self.store)
        self.assertIn("unavailable", result["status"])
        with self.store.read_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM lesson_vectors").fetchone()[0], 0)

    def test_secret_redaction_before_local_query_embedding(self):
        self.approved()
        self.index()
        with patch.dict(os.environ, {"DEMO_API_KEY": "do-not-embed-this-secret"}):
            self.store.recall("Be concise. do-not-embed-this-secret")
        self.assertNotIn("do-not-embed-this-secret", " ".join(self.encoder.queries))

    def test_busy_sqlite_never_waits_the_old_one_second_timeout(self):
        self.approved()
        with sqlite3.connect(self.store.path) as db:
            db.execute("BEGIN EXCLUSIVE")
            start = time.monotonic()
            self.assertEqual(self.store.recall("length answers"), "")
            self.assertLess(time.monotonic() - start, 0.2)

    def test_read_path_does_not_follow_a_database_symlink(self):
        self.approved()
        real = self.store.path.with_suffix(".saved")
        self.store.path.rename(real)
        self.store.path.symlink_to(real)
        self.assertEqual(self.store.recall("length answers"), "")

    def test_keyword_only_setting_never_starts_encoder(self):
        lesson = self.approved()
        with patch.dict(os.environ, {"GATHM_HARNESS_SEMANTIC": "0"}):
            self.assertIn(lesson, self.store.recall("length answers"))
        self.assertIsNone(semantic._worker)


if __name__ == "__main__":
    unittest.main()
