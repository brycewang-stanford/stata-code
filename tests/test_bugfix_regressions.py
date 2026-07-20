"""Regression tests for bugs found in the 2026-07 correctness review.

Each test pins the FIXED behavior; see the commit message for the failure
scenario each bug produced.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from stata_code.core import runner
from stata_code.core._runtime import _extract_rc
from stata_code.core.log_artifacts import snapshot_working_dir_files


class TestExtractRc:
    """rc must come from the LAST r(NNN); in the transcript, not the first."""

    def test_takes_trailing_rc_not_echoed_literal(self):
        text = 'display "see r(198); for details"\nvariable mpgg not found\nr(111);'
        assert _extract_rc(text) == 111

    def test_single_rc(self):
        assert _extract_rc("variable x not found\nr(111);") == 111

    def test_no_rc_returns_minus_one(self):
        assert _extract_rc("something exploded with no return code") == -1

    def test_negative_synthetic_rc(self):
        assert _extract_rc("worker gave up\nr(-2);") == -2


class TestGetGraphFormat:
    """get_graph(format=...) must not silently return mismatched bytes."""

    def _put_graph(self, ref: str) -> None:
        from stata_code.core import _refs

        _refs.put(
            ref,
            {"format": "png", "bytes": b"\x89PNG fake", "width": 10, "height": 10},
        )

    def test_matching_format_returns_payload(self):
        ref = "graph://test-format-match/g1"
        self._put_graph(ref)
        payload = runner.get_graph(ref, "png")
        assert payload["format"] == "png"

    def test_none_format_returns_stored(self):
        ref = "graph://test-format-none/g1"
        self._put_graph(ref)
        assert runner.get_graph(ref)["format"] == "png"

    def test_mismatched_format_raises_value_error(self):
        ref = "graph://test-format-mismatch/g1"
        self._put_graph(ref)
        with pytest.raises(ValueError, match="stored as 'png'"):
            runner.get_graph(ref, "svg")


class TestSessionDefaultAliasing:
    """A session literally named "default" must not alias "main"'s frame."""

    def test_default_maps_to_private_frame(self):
        frame = runner._frame_for_session("default")
        assert frame != "default"
        assert frame.startswith("_sc_")

    def test_default_round_trips(self):
        frame = runner._frame_for_session("default")
        assert runner._session_for_frame(frame) == "default"

    def test_main_still_owns_the_default_frame(self):
        assert runner._frame_for_session("main") == "default"
        assert runner._session_for_frame("default") == "main"


class TestSnapshotEscapingSymlink:
    """A symlink pointing outside the working dir must be skipped, not crash."""

    def test_symlink_out_of_root_is_skipped(self, tmp_path: Path):
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "target.csv"
        target.write_text("a,b\n1,2\n")

        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "normal.csv").write_text("x\n")
        os.symlink(target, workdir / "escaping-link.csv")

        snapshot = snapshot_working_dir_files(str(workdir))
        assert any(p.endswith("normal.csv") for p in snapshot)
        assert not any("target.csv" in p for p in snapshot)


# ─────────────────────────────────────────────────────────────────────────────
# Subprocess-pool fixes
# ─────────────────────────────────────────────────────────────────────────────


import json  # noqa: E402
import sys  # noqa: E402
import textwrap  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

from stata_code.core._pool import (  # noqa: E402
    SessionPool,
    WorkerProcess,
    _WorkerReportedError,
)


def _cmd_for(script: str) -> list[str]:
    return [sys.executable, "-u", "-c", script]


# Writes a large stderr payload (past the ~64 KB OS pipe buffer) BEFORE
# responding. Without a continuous stderr drain this deadlocks: the worker
# blocks in the stderr write, never responds, and the parent misreports a
# healthy worker as a timeout.
_NOISY_STDERR_WORKER = textwrap.dedent(
    """
    import json, sys
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        sys.stderr.write("x" * 262144)
        sys.stderr.flush()
        sys.stdout.write(json.dumps({"id": req["id"], "ok": True, "pong": True}) + "\\n")
        sys.stdout.flush()
    """
).strip()


# Responds ok=false with a generic (non-invalid_request) error kind, then
# keeps serving. A well-formed failure response must NOT get the worker
# killed.
_REPORTED_FAILURE_WORKER = textwrap.dedent(
    """
    import json, sys
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        resp = {"id": req["id"], "ok": False,
                "error": "RuntimeError: transient boom",
                "error_kind": "worker_error"}
        sys.stdout.write(json.dumps(resp) + "\\n")
        sys.stdout.flush()
    """
).strip()


# Sleeps while "executing" so a concurrent spawn can trigger eviction while
# the request is in flight.
_SLEEPY_OK_WORKER = textwrap.dedent(
    """
    import json, sys, time
    from stata_code.core._pool import _build_adapter_crash_result
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        sid = req.get("options", {}).get("session_id", "main")
        time.sleep(1.0)
        r = _build_adapter_crash_result(session_id=sid, elapsed_ms=1, message="ok")
        resp = {"id": req["id"], "ok": True,
                "result": json.loads(r.model_dump_json()), "ref_blobs": {}}
        sys.stdout.write(json.dumps(resp) + "\\n")
        sys.stdout.flush()
    """
).strip()


class TestStderrDrain:
    """A worker flooding stderr must not deadlock into a phantom timeout."""

    def test_large_stderr_before_response_does_not_block(self):
        w = WorkerProcess("noisy", worker_cmd=_cmd_for(_NOISY_STDERR_WORKER))
        try:
            start = time.monotonic()
            resp = w.send_simple_op("ping", timeout_ms=8000, spawn=True)
            elapsed = time.monotonic() - start
            assert resp["pong"] is True
            assert elapsed < 5.0  # far below the timeout budget
        finally:
            w.kill()


class TestWorkerReportedFailureKeepsWorker:
    """ok=false from a live worker must not destroy the session's worker."""

    def test_worker_process_raises_reported_error(self):
        w = WorkerProcess("rep", worker_cmd=_cmd_for(_REPORTED_FAILURE_WORKER))
        try:
            with pytest.raises(_WorkerReportedError):
                w.execute("boom", {"session_id": "rep"}, timeout_ms=5000)
            assert w.is_alive()  # the worker survived its own failure report
        finally:
            w.kill()

    def test_pool_keeps_worker_after_reported_failure(self):
        pool = SessionPool(capacity=4, worker_cmd=_cmd_for(_REPORTED_FAILURE_WORKER))
        try:
            result = pool.execute("boom", session_id="rep", timeout_ms=5000)
            assert result.ok is False
            assert result.error is not None
            assert result.error.kind.value == "adapter_crash"
            # The worker must still be registered and alive — its session
            # state (loaded data, r()/e()) survives the failure report.
            worker = pool._workers.get("rep")
            assert worker is not None
            assert worker.is_alive()
        finally:
            pool.shutdown()


class TestEvictionSkipsBusyWorkers:
    """LRU eviction must not kill a worker with a request in flight."""

    def test_in_flight_worker_survives_capacity_pressure(self):
        pool = SessionPool(capacity=1, worker_cmd=_cmd_for(_SLEEPY_OK_WORKER))
        results: dict[str, object] = {}
        try:
            def _run_a() -> None:
                results["a"] = pool.execute("slow", session_id="a", timeout_ms=15000)

            t = threading.Thread(target=_run_a)
            t.start()
            time.sleep(0.3)  # let session a get in flight
            results["b"] = pool.execute("other", session_id="b", timeout_ms=15000)
            t.join(timeout=20)
            assert not t.is_alive()
            a = results["a"]
            # Before the fix, spawning session b evicted (SIGTERMed) session
            # a's worker mid-run and a came back as a killed-worker crash
            # with "returncode=-15" in the message.
            assert "returncode=-15" not in json.dumps(a.model_dump(mode="json"))
        finally:
            pool.shutdown()


class TestSimpleOpBoundedLockWait:
    """A status query must not hang for the duration of a long run."""

    def test_busy_worker_times_out_instead_of_hanging(self):
        from stata_code.core._pool import _WorkerTimeout

        w = WorkerProcess("busy", worker_cmd=_cmd_for(_SLEEPY_OK_WORKER))
        try:
            t = threading.Thread(
                target=lambda: w.execute("slow", {"session_id": "busy"}, timeout_ms=15000)
            )
            t.start()
            time.sleep(0.3)  # let the execute acquire the worker lock
            start = time.monotonic()
            with pytest.raises(_WorkerTimeout, match="busy"):
                w.send_simple_op("list_sessions", timeout_ms=200)
            assert time.monotonic() - start < 2.0
            t.join(timeout=20)
            assert not t.is_alive()
        finally:
            w.kill()
