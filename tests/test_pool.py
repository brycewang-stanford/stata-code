"""Tests for `stata_code.core._pool` — subprocess pool + hard timeout.

Pool mechanics are exercised against a mock-worker script (fast, no Stata
required). One end-to-end test exercises the real worker against Stata
when available.
"""

from __future__ import annotations

import json
import sys
import textwrap
import time

import pytest

from stata_code.core import _pool, _refs
from stata_code.core._pool import (
    SessionPool,
    WorkerProcess,
    _WorkerError,
    _WorkerTimeout,
    pool_execute,
    shutdown_default_pool,
)
from stata_code.core._runtime import is_available
from stata_code.core.schema import ErrorKind, RunResult


# ─────────────────────────────────────────────────────────────────────────────
# Mock-worker scripts. Each script reads one JSON request per line and writes
# one JSON response per line. They are launched via `python -c` so they get
# the same stata_code install on sys.path as the test process.
# ─────────────────────────────────────────────────────────────────────────────


_ECHO_WORKER = textwrap.dedent(
    """
    import json, sys
    from stata_code.core._pool import _build_adapter_crash_result
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        sid = req.get("options", {}).get("session_id", "main")
        # Use the adapter-crash builder as a fully-populated RunResult template
        # — we override fields the test cares about. rc=-1 is fine for the
        # ferry-roundtrip tests; happy-path validation tests pin specific fields.
        r = _build_adapter_crash_result(session_id=sid, elapsed_ms=11, message="echo")
        resp = {
            "id": req["id"],
            "ok": True,
            "result": json.loads(r.model_dump_json()),
            "ref_blobs": {},
        }
        sys.stdout.write(json.dumps(resp) + "\\n")
        sys.stdout.flush()
    """
).strip()


_SLOW_WORKER = textwrap.dedent(
    """
    import json, sys, time
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        time.sleep(30)  # well past any test timeout
        sys.stdout.write(json.dumps({"id": req["id"], "ok": True, "result": {}, "ref_blobs": {}}) + "\\n")
        sys.stdout.flush()
    """
).strip()


_CRASH_WORKER = textwrap.dedent(
    """
    import sys
    sys.exit(7)
    """
).strip()


_FERRY_WORKER = textwrap.dedent(
    """
    import base64, json, sys
    from stata_code.core._pool import _build_adapter_crash_result
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        sid = req.get("options", {}).get("session_id", "main")
        r = _build_adapter_crash_result(session_id=sid, elapsed_ms=22, message="ferry")
        ref_blobs = {
            "log://r-1/main": {"kind": "text", "data": "full log here"},
            "graph://r-1/g1": {"kind": "bytes", "data": base64.b64encode(b"PNGBYTES").decode("ascii")},
            "matrix://r-1/r/C": {"kind": "json", "data": {"rows": ["a"], "cols": ["b"], "values": [[1.0]]}},
        }
        resp = {"id": req["id"], "ok": True, "result": json.loads(r.model_dump_json()), "ref_blobs": ref_blobs}
        sys.stdout.write(json.dumps(resp) + "\\n")
        sys.stdout.flush()
    """
).strip()


def _cmd_for(script: str) -> list[str]:
    return [sys.executable, "-u", "-c", script]


# ─────────────────────────────────────────────────────────────────────────────
# WorkerProcess: low-level lifecycle.
# ─────────────────────────────────────────────────────────────────────────────


class TestWorkerProcess:
    def test_lazy_spawn(self):
        w = WorkerProcess("s1", worker_cmd=_cmd_for(_ECHO_WORKER))
        assert not w.is_alive()  # no proc until first execute()
        try:
            result, blobs = w.execute("noop", {"session_id": "s1"}, timeout_ms=5000)
            assert w.is_alive()
            assert result["session_id"] == "s1"
            assert result["error"]["kind"] == "adapter_crash"  # echo template's kind
            assert blobs == {}
        finally:
            w.kill()

    def test_kill_idempotent(self):
        w = WorkerProcess("s1", worker_cmd=_cmd_for(_ECHO_WORKER))
        w.execute("noop", {"session_id": "s1"}, timeout_ms=5000)
        w.kill()
        w.kill()  # second kill is a no-op
        assert not w.is_alive()

    def test_reuse_across_calls(self):
        w = WorkerProcess("s1", worker_cmd=_cmd_for(_ECHO_WORKER))
        try:
            w.execute("a", {"session_id": "s1"}, timeout_ms=5000)
            pid_first = w._proc.pid  # noqa: SLF001
            w.execute("b", {"session_id": "s1"}, timeout_ms=5000)
            pid_second = w._proc.pid  # noqa: SLF001
            assert pid_first == pid_second  # warm reuse
        finally:
            w.kill()

    def test_timeout_raises(self):
        w = WorkerProcess("s1", worker_cmd=_cmd_for(_SLOW_WORKER))
        try:
            with pytest.raises(_WorkerTimeout):
                w.execute("hangs", {"session_id": "s1"}, timeout_ms=200)
        finally:
            w.kill()

    def test_worker_crash_raises(self):
        w = WorkerProcess("s1", worker_cmd=_cmd_for(_CRASH_WORKER))
        try:
            # Worker exits before reading anything; either write fails or read sees EOF.
            with pytest.raises(_WorkerError):
                w.execute("anything", {"session_id": "s1"}, timeout_ms=5000)
        finally:
            w.kill()


# ─────────────────────────────────────────────────────────────────────────────
# SessionPool: per-session workers, capacity, ref ferrying, error mapping.
# ─────────────────────────────────────────────────────────────────────────────


class TestSessionPool:
    def test_per_session_isolation(self):
        pool = SessionPool(worker_cmd=_cmd_for(_ECHO_WORKER))
        try:
            r1 = pool.execute("noop", session_id="alpha", timeout_ms=5000)
            r2 = pool.execute("noop", session_id="beta", timeout_ms=5000)
            assert r1.session_id == "alpha"
            assert r2.session_id == "beta"
            assert set(pool.session_ids()) == {"alpha", "beta"}
        finally:
            pool.shutdown()

    def test_warm_worker_reuse(self):
        pool = SessionPool(worker_cmd=_cmd_for(_ECHO_WORKER))
        try:
            pool.execute("a", session_id="s", timeout_ms=5000)
            w = pool._workers["s"]  # noqa: SLF001
            pid_first = w._proc.pid  # noqa: SLF001
            pool.execute("b", session_id="s", timeout_ms=5000)
            pid_second = w._proc.pid  # noqa: SLF001
            assert pid_first == pid_second
        finally:
            pool.shutdown()

    def test_lru_eviction(self):
        pool = SessionPool(capacity=2, worker_cmd=_cmd_for(_ECHO_WORKER))
        try:
            pool.execute("noop", session_id="a", timeout_ms=5000)
            time.sleep(0.01)
            pool.execute("noop", session_id="b", timeout_ms=5000)
            time.sleep(0.01)
            # `a` is now LRU; spawning `c` evicts it.
            pool.execute("noop", session_id="c", timeout_ms=5000)
            ids = set(pool.session_ids())
            assert "a" not in ids
            assert {"b", "c"} <= ids
        finally:
            pool.shutdown()

    def test_kill_session(self):
        pool = SessionPool(worker_cmd=_cmd_for(_ECHO_WORKER))
        try:
            pool.execute("noop", session_id="s", timeout_ms=5000)
            assert pool.kill_session("s") is True
            assert pool.kill_session("s") is False  # gone now
            assert pool.session_ids() == []
        finally:
            pool.shutdown()

    def test_timeout_returns_synthetic_result(self):
        pool = SessionPool(worker_cmd=_cmd_for(_SLOW_WORKER))
        try:
            r = pool.execute("hangs", session_id="s", timeout_ms=150)
            assert isinstance(r, RunResult)
            assert r.ok is False
            assert r.rc == -2
            assert r.error is not None and r.error.kind is ErrorKind.TIMEOUT
            # Worker was killed; pool entry dropped.
            assert pool.session_ids() == []
        finally:
            pool.shutdown()

    def test_post_timeout_respawns(self):
        # First call hangs and times out. Second call to same session_id
        # should spawn a fresh worker (we swap commands here for clarity).
        pool = SessionPool(worker_cmd=_cmd_for(_SLOW_WORKER))
        try:
            r1 = pool.execute("hangs", session_id="s", timeout_ms=150)
            assert r1.error is not None and r1.error.kind is ErrorKind.TIMEOUT
            # Inject a fast worker for the next round.
            pool._worker_cmd = _cmd_for(_ECHO_WORKER)  # noqa: SLF001
            r2 = pool.execute("noop", session_id="s", timeout_ms=5000)
            assert isinstance(r2, RunResult)
            assert r2.session_id == "s"
        finally:
            pool.shutdown()

    def test_crash_returns_adapter_crash_result(self):
        pool = SessionPool(worker_cmd=_cmd_for(_CRASH_WORKER))
        try:
            r = pool.execute("noop", session_id="s", timeout_ms=5000)
            assert r.ok is False
            assert r.rc == -1
            assert r.error is not None and r.error.kind is ErrorKind.ADAPTER_CRASH
        finally:
            pool.shutdown()

    def test_ref_blobs_ferried_into_parent_refs(self):
        # Wipe the parent's ref store so we can assert exact contents.
        _refs.clear_all()
        pool = SessionPool(worker_cmd=_cmd_for(_FERRY_WORKER))
        try:
            r = pool.execute("noop", session_id="s", timeout_ms=5000)
            assert isinstance(r, RunResult)
            # Refs the fake worker advertised should now live in the parent store.
            assert _refs.has("log://r-1/main")
            assert _refs.get("log://r-1/main") == "full log here"
            assert _refs.has("graph://r-1/g1")
            assert _refs.get("graph://r-1/g1") == b"PNGBYTES"
            assert _refs.has("matrix://r-1/r/C")
            assert _refs.get("matrix://r-1/r/C") == {
                "rows": ["a"],
                "cols": ["b"],
                "values": [[1.0]],
            }
        finally:
            pool.shutdown()
            _refs.clear_all()


# ─────────────────────────────────────────────────────────────────────────────
# Wire-format helpers — payload encode/decode round-trips.
# ─────────────────────────────────────────────────────────────────────────────


class TestWireFormat:
    def test_bytes_roundtrip(self):
        env = _pool._payload_to_wire(b"\x00\x01\x02PNG")
        assert env["kind"] == "bytes"
        assert _pool._payload_from_wire(env) == b"\x00\x01\x02PNG"

    def test_text_roundtrip(self):
        env = _pool._payload_to_wire("a log line\n")
        assert env["kind"] == "text"
        assert _pool._payload_from_wire(env) == "a log line\n"

    def test_json_roundtrip(self):
        m = {"rows": ["x"], "cols": ["y"], "values": [[1.5]]}
        env = _pool._payload_to_wire(m)
        assert env["kind"] == "json"
        assert _pool._payload_from_wire(env) == m

    def test_unknown_kind_returns_none(self):
        assert _pool._payload_from_wire({"kind": "weird", "data": "..."}) is None


# ─────────────────────────────────────────────────────────────────────────────
# pool_execute() default-pool wiring.
# ─────────────────────────────────────────────────────────────────────────────


class TestDefaultPool:
    def teardown_method(self) -> None:
        shutdown_default_pool()

    def test_get_default_pool_is_singleton(self):
        a = _pool.get_default_pool()
        b = _pool.get_default_pool()
        assert a is b

    def test_shutdown_resets_default(self):
        a = _pool.get_default_pool()
        shutdown_default_pool()
        b = _pool.get_default_pool()
        assert a is not b


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end against real Stata. Covers: warm worker, real result schema,
# hard timeout actually firing on a long-running Stata command.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.stata_required
@pytest.mark.skipif(not is_available(), reason="pystata / Stata 17+ not available")
class TestEndToEndWithStata:
    def teardown_method(self) -> None:
        shutdown_default_pool()

    def test_pool_execute_basic_regression(self):
        r = pool_execute("sysuse auto, clear\nregress mpg weight", session_id="e2e_basic")
        assert isinstance(r, RunResult)
        assert r.ok is True
        # The classic auto.dta R^2 for `regress mpg weight`.
        assert r.results.e.scalars.get("r2") == pytest.approx(0.6515, rel=1e-2)

    def test_pool_execute_timeout_kills_long_running_stata(self):
        # `sleep` in Stata is in milliseconds. 30000 = 30 seconds, well past
        # our 1.5s wall-clock budget. The pool must SIGTERM the worker and
        # return a synthetic timeout result in well under the sleep duration.
        started = time.monotonic()
        r = pool_execute("sleep 30000", session_id="e2e_timeout", timeout_ms=1500)
        elapsed = time.monotonic() - started
        assert elapsed < 10.0, f"timeout enforcement took {elapsed:.1f}s — workers not actually killed"
        assert r.ok is False
        assert r.rc == -2
        assert r.error is not None and r.error.kind is ErrorKind.TIMEOUT

    def test_session_recovers_after_timeout(self):
        pool_execute("sleep 30000", session_id="e2e_recover", timeout_ms=1500)
        # Next call to same session_id should spawn a fresh worker and
        # behave normally. This is the killer feature: timeouts don't
        # poison the session.
        r = pool_execute("display 2 + 2", session_id="e2e_recover", timeout_ms=60_000)
        assert r.ok is True
        assert "4" in (r.log.head + r.log.tail)
