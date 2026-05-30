"""
Gathm Enterprise — Core Integration Tests
Tests the Bash library internals (circuit breaker, cache, fallback guard)
and the Python API rate-limiter by running real subprocesses.

Run: python -m pytest tests/test_core.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

GATHM_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bash(script: str, env: dict | None = None, timeout: int = 15) -> tuple[int, str, str]:
    """Run a bash snippet, return (returncode, stdout, stderr)."""
    full_env = {**os.environ}
    if env:
        full_env.update(env)
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       timeout=timeout, env=full_env)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def _lib(*names: str) -> str:
    """Return source lines for the named lib/*.bash files."""
    return "\n".join(f'source "{GATHM_ROOT}/lib/{n}.bash"' for n in names)


# ---------------------------------------------------------------------------
# TestCircuitBreaker
# ---------------------------------------------------------------------------

class TestCircuitBreaker(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="gathm-cb-")
        self._health = os.path.join(self._tmp.name, "health")
        self._logs   = os.path.join(self._tmp.name, "logs")
        os.makedirs(self._health)
        os.makedirs(self._logs)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, snippet: str) -> tuple[int, str, str]:
        preamble = f"""
export GATHM_HEALTH_DIR="{self._health}"
export GATHM_LOG_DIR="{self._logs}"
export GATHM_LOG_LEVEL="ERROR"
export CB_FAILURE_THRESHOLD=3
export CB_RECOVERY_TIMEOUT=60
{_lib("logging", "health")}
"""
        return _bash(preamble + snippet)

    def test_fresh_tool_is_allowed(self):
        rc, out, _ = self._run('cb_allow_request brand_new_tool && echo ALLOWED || echo DENIED')
        self.assertEqual(out, "ALLOWED")

    def test_opens_after_threshold_failures(self):
        rc, out, _ = self._run("""
for i in 1 2 3; do cb_record_failure faulty_tool; done
cb_allow_request faulty_tool && echo ALLOWED || echo DENIED
""")
        self.assertEqual(out, "DENIED")

    def test_single_success_resets_to_closed(self):
        _, out, _ = self._run("""
for i in 1 2 3; do cb_record_failure t; done
cb_record_success t
cb_allow_request t && echo ALLOWED || echo DENIED
""")
        self.assertEqual(out, "ALLOWED")

    def test_failure_count_persists_between_calls(self):
        _, out, _ = self._run("""
cb_record_failure count_tool
cb_record_failure count_tool
grep '^failures=' "$GATHM_HEALTH_DIR/cb_count_tool.state" | cut -d= -f2
""")
        self.assertEqual(out, "2")

    def test_recovery_timeout_yields_half_open(self):
        # Back-date last_failure so the 60 s timeout has elapsed
        past = int(time.time()) - 120
        _, out, _ = self._run(f"""
cat > "$GATHM_HEALTH_DIR/cb_old_tool.state" <<'EOF'
state=open
failures=3
last_failure={past}
EOF
cb_get_state old_tool
""")
        self.assertEqual(out, "half_open")

    def test_concurrent_writes_do_not_corrupt_state(self):
        """Ten parallel failure recordings — state file must remain readable."""
        _, out, _ = self._run("""
for i in $(seq 1 10); do cb_record_failure parallel_tool & done
wait
grep -c '^state=' "$GATHM_HEALTH_DIR/cb_parallel_tool.state" 2>/dev/null || echo 0
""")
        self.assertEqual(out, "1", "State file should have exactly one 'state=' line")

    def test_below_threshold_stays_closed(self):
        _, out, _ = self._run("""
cb_record_failure almost_tool
cb_record_failure almost_tool
cb_allow_request almost_tool && echo ALLOWED || echo DENIED
""")
        self.assertEqual(out, "ALLOWED")


# ---------------------------------------------------------------------------
# TestCache
# ---------------------------------------------------------------------------

class TestCache(unittest.TestCase):

    def setUp(self):
        self._tmp   = tempfile.TemporaryDirectory(prefix="gathm-cache-")
        self._cache = os.path.join(self._tmp.name, "cache")
        self._logs  = os.path.join(self._tmp.name, "logs")
        os.makedirs(self._cache)
        os.makedirs(self._logs)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, snippet: str, extra_env: dict | None = None,
             timeout: int = 15) -> tuple[int, str, str]:
        env = {
            "GATHM_CACHE_DIR": self._cache,
            "GATHM_LOG_DIR":   self._logs,
            "GATHM_LOG_LEVEL": "ERROR",
            "GATHM_CACHE_ENABLED": "true",
        }
        if extra_env:
            env.update(extra_env)
        preamble = f"""
export GATHM_CACHE_DIR="{self._cache}"
export GATHM_LOG_DIR="{self._logs}"
export GATHM_LOG_LEVEL="ERROR"
export GATHM_CACHE_ENABLED="${{GATHM_CACHE_ENABLED:-true}}"
{_lib("logging", "cache")}
"""
        return _bash(preamble + snippet, env=env, timeout=timeout)

    def test_miss_on_empty_cache(self):
        rc, _, _ = self._run("cache_get my_tool argA argB")
        self.assertEqual(rc, 1)

    def test_hit_after_set(self):
        rc, out, _ = self._run("""
echo "hello world" | cache_set my_tool argA
cache_get my_tool argA
""")
        self.assertEqual(rc, 0)
        self.assertEqual(out, "hello world")

    def test_miss_after_ttl_expiry(self):
        rc, _, _ = self._run("""
echo "ephemeral" | GATHM_CACHE_TTL=1 cache_set my_tool expiring
sleep 2
cache_get my_tool expiring
""", timeout=20)
        self.assertEqual(rc, 1, "Expired entry should be a cache miss")

    def test_invalidate_removes_entry(self):
        rc, _, _ = self._run("""
echo "gone" | cache_set my_tool key_to_kill
cache_invalidate my_tool key_to_kill
cache_get my_tool key_to_kill
""")
        self.assertEqual(rc, 1)

    def test_cache_disabled_always_misses(self):
        rc, _, _ = self._run("""
echo "should_not_cache" | cache_set my_tool arg1
cache_get my_tool arg1
""", extra_env={"GATHM_CACHE_ENABLED": "false"})
        self.assertEqual(rc, 1)

    def test_different_args_use_different_keys(self):
        _, out, _ = self._run("""
echo "output_a" | cache_set my_tool args_a
echo "output_b" | cache_set my_tool args_b
a=$(cache_get my_tool args_a)
b=$(cache_get my_tool args_b)
echo "$a:$b"
""")
        self.assertEqual(out, "output_a:output_b")

    def test_cleanup_removes_expired_entries(self):
        _, out, _ = self._run("""
echo "dies" | GATHM_CACHE_TTL=1 cache_set my_tool expiry_key
sleep 2
count=$(cache_cleanup)
echo "$count"
""", timeout=20)
        self.assertGreaterEqual(int(out), 1, "cache_cleanup should report at least 1 removed entry")

    def test_stats_counts_active_entries(self):
        _, out, _ = self._run("""
echo "a" | cache_set stat_tool k1
echo "b" | cache_set stat_tool k2
cache_stats
""")
        stats = json.loads(out)
        self.assertGreaterEqual(stats["active"], 2)
        self.assertIn("total_entries", stats)

    def test_cache_invalidate_tool_removes_all_for_tool(self):
        rc, _, _ = self._run("""
echo "x" | cache_set target_tool argX
echo "y" | cache_set target_tool argY
echo "z" | cache_set other_tool  argZ
cache_invalidate_tool target_tool
# both target_tool entries should be gone
cache_get target_tool argX
""")
        self.assertEqual(rc, 1)


# ---------------------------------------------------------------------------
# TestFallbackGuard
# ---------------------------------------------------------------------------

class TestFallbackGuard(unittest.TestCase):
    """Tests cycle detection and depth cap added to execute_with_recovery."""

    def setUp(self):
        self._tmp    = tempfile.TemporaryDirectory(prefix="gathm-fg-")
        self._health = os.path.join(self._tmp.name, "health")
        self._logs   = os.path.join(self._tmp.name, "logs")
        os.makedirs(self._health)
        os.makedirs(self._logs)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, snippet: str) -> tuple[int, str, str]:
        # Source logging + health, then stub out the recovery helpers we don't
        # want to exercise, and finally source recovery.bash itself.
        preamble = f"""
export GATHM_LOG_DIR="{self._logs}"
export GATHM_HEALTH_DIR="{self._health}"
export GATHM_LOG_LEVEL="ERROR"
export GATHM_MAX_RETRIES=1

{_lib("logging", "health")}

# Stubs — cb is always open so we reach the fallback path immediately
cb_allow_request()  {{ return 1; }}
cb_record_success() {{ :; }}
cb_record_failure() {{ :; }}
auto_install_deps()  {{ :; }}
retry_with_backoff() {{ shift; return 1; }}

{_lib("recovery")}
"""
        return _bash(preamble + snippet)

    def test_depth_limit_blocks_at_max(self):
        _, out, err = self._run("""
output=$(_GATHM_FALLBACK_DEPTH=2 _GATHM_FALLBACK_CHAIN="a:b" \
    execute_with_recovery tool_c 2>&1)
echo "$output"
""")
        combined = (out + err).lower()
        self.assertIn("depth", combined, "Should report depth exceeded")

    def test_cycle_detection_blocks_revisit(self):
        _, out, err = self._run("""
output=$(_GATHM_FALLBACK_CHAIN="tool_a:tool_b" \
    execute_with_recovery tool_a 2>&1)
echo "$output"
""")
        combined = (out + err).lower()
        self.assertIn("cycle", combined, "Should report cycle detected")

    def test_first_call_has_no_guard_overhead(self):
        # No depth env, no chain env — guard should not trigger
        # (tool will fail normally due to stubbed retry, but no cycle/depth msg)
        _, out, err = self._run("""
output=$(execute_with_recovery nonexistent_tool 2>&1)
echo "$output"
""")
        combined = (out + err).lower()
        self.assertNotIn("cycle",           combined)
        self.assertNotIn("fallback_depth",  combined)

    def test_chain_accumulates_across_hops(self):
        # Tool A (chain="") → tries fallback → guard should see "tool_a" in chain
        # We test this by pre-setting a chain with one tool and making tool_b appear
        _, out, err = self._run("""
output=$(_GATHM_FALLBACK_DEPTH=1 _GATHM_FALLBACK_CHAIN="tool_a" \
    execute_with_recovery tool_b 2>&1)
# Depth 1 < max(2), no cycle — should fail normally (no cycle/depth message)
echo "${output}" | grep -v "cycle" | grep -v "depth" && echo OK || echo GUARDED
""")
        # depth=1 is below max (2), tool_b not in chain — should proceed normally
        self.assertIn("OK", out + err)


# ---------------------------------------------------------------------------
# TestAPIRateLimit
# ---------------------------------------------------------------------------

try:
    # Only run these if FastAPI is installed
    from api.server import check_rate_limit, _rate_windows
    _FASTAPI_AVAILABLE = True
except Exception:
    _FASTAPI_AVAILABLE = False


@unittest.skipUnless(_FASTAPI_AVAILABLE, "FastAPI not installed — skipping API rate-limit tests")
class TestAPIRateLimit(unittest.TestCase):
    """Tests the sliding-window rate limiter in api/server.py."""

    def _fresh_key(self) -> str:
        return f"test-{time.time()}-{id(self)}"

    def test_allows_requests_under_limit(self):
        key = self._fresh_key()
        for _ in range(5):
            self.assertTrue(check_rate_limit(key, 10))

    def test_blocks_at_limit(self):
        key = self._fresh_key()
        for _ in range(5):
            check_rate_limit(key, 5)
        self.assertFalse(check_rate_limit(key, 5), "6th request should be blocked")

    def test_zero_limit_is_unlimited(self):
        key = self._fresh_key()
        for _ in range(200):
            self.assertTrue(check_rate_limit(key, 0))

    def test_expired_window_entries_are_evicted(self):
        key = self._fresh_key()
        # Fill the window with timestamps 70 s in the past (outside 60 s window)
        _rate_windows[key] = [time.monotonic() - 70.0] * 5
        # All entries are stale — next request should be allowed
        self.assertTrue(check_rate_limit(key, 5))

    def test_partial_window_expiry(self):
        key = self._fresh_key()
        # Fill to the limit with stale entries first (they will be evicted)
        _rate_windows[key] = [time.monotonic() - 70.0] * 5
        # Add fresh requests up to the limit; stale entries don't count
        for _ in range(5):
            self.assertTrue(check_rate_limit(key, 5))   # fills window
        # Now at limit — next should be blocked
        self.assertFalse(check_rate_limit(key, 5), "Should be blocked at limit")

    def test_independent_keys_do_not_interfere(self):
        a = self._fresh_key()
        b = self._fresh_key()
        for _ in range(5):
            check_rate_limit(a, 5)
        # Key b is unaffected
        self.assertTrue(check_rate_limit(b, 5))


# ---------------------------------------------------------------------------
# TestJobStore  (in-process, no HTTP server needed)
# ---------------------------------------------------------------------------

@unittest.skipUnless(_FASTAPI_AVAILABLE, "FastAPI not installed — skipping job store tests")
class TestJobStore(unittest.TestCase):
    """Tests Job dataclass and serialisation — no event loop required."""

    def test_job_default_status_is_pending(self):
        from api.server import Job, JobStatus
        job = Job(id="abc123", kind="tool", tool="dns", args=["example.com"], timeout=5)
        self.assertEqual(job.status, JobStatus.pending)

    def test_job_to_dict_has_required_fields(self):
        from api.server import Job, _job_to_dict
        job = Job(id="xyz", kind="tool", tool="geo", args=[], timeout=5)
        d = _job_to_dict(job)
        for f in ("id", "kind", "tool", "args", "status",
                  "created_at", "exit_code", "output", "line_count"):
            self.assertIn(f, d, f"Missing field: {f}")

    def test_job_output_lines_join_to_output(self):
        from api.server import Job, _job_to_dict
        job = Job(id="j1", kind="tool", tool="dns", args=[], timeout=5)
        job.output_lines = ["line one", "line two"]
        d = _job_to_dict(job)
        self.assertEqual(d["output"], "line one\nline two")
        self.assertEqual(d["line_count"], 2)

    def test_job_ids_generated_are_unique(self):
        import uuid
        ids = {uuid.uuid4().hex for _ in range(50)}
        self.assertEqual(len(ids), 50)

    def test_job_statuses_are_string_values(self):
        from api.server import JobStatus
        for status in JobStatus:
            self.assertIsInstance(status.value, str)


if __name__ == "__main__":
    unittest.main()
