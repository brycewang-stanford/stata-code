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
    _WorkerBusy,
    _WorkerReportedError,
    _WorkerTimeout,
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


# ─────────────────────────────────────────────────────────────────────────────
# 2026-07 follow-up review: bugs the first round of fixes left behind.
#
# The common failure of the first round was testing a fix in isolation while
# the real call path stayed uncovered. These tests deliberately go through
# SessionPool (not WorkerProcess) wherever the pool is what carries the bug.
# ─────────────────────────────────────────────────────────────────────────────


class TestStatusQuerySparesBusyWorker:
    """A read-only status query must never kill a healthy mid-run worker."""

    def test_stata_info_on_busy_worker_does_not_kill_the_run(self):
        """`SessionPool.stata_info` used to treat "busy" and "dead" alike.

        Once send_simple_op learned to bail out rather than block on the
        worker lock, that bail-out arrived as a _WorkerTimeout — and
        stata_info's handler killed the worker and dropped it from the pool.
        A status query during a long regression therefore returned the run as
        `adapter_crash` and wiped the session's loaded data.
        """
        pool = SessionPool(capacity=4, worker_cmd=_cmd_for(_SLEEPY_OK_WORKER))
        results: dict[str, object] = {}
        try:
            def _run() -> None:
                results["run"] = pool.execute("slow", session_id="main", timeout_ms=15000)

            t = threading.Thread(target=_run)
            t.start()
            time.sleep(0.3)  # let the execute take the worker lock
            worker_before = pool._workers.get("main")

            with pytest.raises(_WorkerBusy):
                pool.stata_info(session_id="main", timeout_ms=200)

            t.join(timeout=20)
            assert not t.is_alive()

            # The run completed normally...
            payload = json.dumps(results["run"].model_dump(mode="json"))  # type: ignore[attr-defined]
            assert "returncode=-15" not in payload
            # ...and the worker (and therefore the session's data) survived,
            # still the same handle the pool had before the status query.
            assert pool._workers.get("main") is worker_before
            assert worker_before is not None and worker_before.is_alive()
        finally:
            pool.shutdown()

    def test_busy_is_distinguishable_from_dead(self):
        """_WorkerBusy must stay a _WorkerTimeout subclass.

        Callers that only want to warn (list_session_info_detailed) keep
        catching _WorkerTimeout; callers that would kill must special-case
        _WorkerBusy first.
        """
        from stata_code.core._pool import _WorkerTimeout

        assert issubclass(_WorkerBusy, _WorkerTimeout)


# Answers `execute` after a short hold (so the worker lock is released partway
# through a concurrent status query's budget) but never answers a simple op.
# This is what separates the total-budget bug from the plain busy bail-out:
# the lock IS acquired, just late, and the read then got a fresh full budget.
_SLOW_LOCK_THEN_SILENT_WORKER = textwrap.dedent(
    """
    import json, sys, time
    from stata_code.core._pool import _build_adapter_crash_result
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        if req.get("op") != "execute":
            continue          # never answer status queries
        sid = req.get("options", {}).get("session_id", "main")
        time.sleep(0.4)
        r = _build_adapter_crash_result(session_id=sid, elapsed_ms=1, message="ok")
        resp = {"id": req["id"], "ok": True,
                "result": json.loads(r.model_dump_json()), "ref_blobs": {}}
        sys.stdout.write(json.dumps(resp) + "\\n")
        sys.stdout.flush()
    """
).strip()


class TestSimpleOpHonorsTotalBudget:
    """timeout_ms is the budget for the whole call, lock wait included."""

    def test_lock_wait_counts_against_the_timeout(self):
        """Pre-fix the read deadline was computed AFTER acquiring the lock.

        A status query that spent most of its budget waiting for the lock then
        got a second full budget for the read — worst case 2x `timeout_ms`.
        `list_session_info_detailed` iterates workers serially, so N busy
        workers stalled the status tool for 2*timeout*N.
        """
        w = WorkerProcess("busy", worker_cmd=_cmd_for(_SLOW_LOCK_THEN_SILENT_WORKER))
        try:
            t = threading.Thread(
                target=lambda: w.execute("slow", {"session_id": "busy"}, timeout_ms=15000)
            )
            t.start()
            time.sleep(0.1)  # execute holds the lock for ~0.4s total
            start = time.monotonic()
            with pytest.raises(_WorkerTimeout):
                # Lock frees at ~0.3s into this 0.5s budget; the read then has
                # ~0.2s left, not another 0.5s.
                w.send_simple_op("list_sessions", timeout_ms=500)
            elapsed = time.monotonic() - start
            assert elapsed < 0.75, (
                f"took {elapsed:.3f}s — budget must be ~0.5s total, not 2x"
            )
            t.join(timeout=20)
        finally:
            w.kill()


# Drops request #1 entirely, then answers everything after. Models a worker
# still inside pystata init when a status query's deadline expires: the
# abandoned reader for #1 is still blocked on the pipe when #2's reply lands,
# so pre-fix it swallowed a reply that was never addressed to it.
_DROP_FIRST_REQUEST_WORKER = textwrap.dedent(
    """
    import json, sys
    from stata_code.core._pool import _build_adapter_crash_result
    seen = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        seen += 1
        if seen == 1:
            continue          # never answer #1
        sid = req.get("options", {}).get("session_id", "main")
        r = _build_adapter_crash_result(session_id=sid, elapsed_ms=1, message="ok")
        resp = {"id": req["id"], "ok": True,
                "result": json.loads(r.model_dump_json()), "ref_blobs": {}}
        sys.stdout.write(json.dumps(resp) + "\\n")
        sys.stdout.flush()
    """
).strip()


class TestNoOrphanedReaderTheft:
    """A timed-out read must not steal the NEXT request's response."""

    def test_abandoned_reader_does_not_eat_the_following_response(self):
        """The per-request reader thread used to be abandoned on timeout.

        It stayed blocked on the pipe and consumed the *next* response, so the
        following execute() burned its entire timeout waiting for a reply that
        had already been eaten — after which the pool SIGKILLed a perfectly
        healthy worker and the session's loaded data went with it. Verified
        against the pre-fix source: request #2 below timed out at the full
        3.00s. One long-lived pump per worker cannot orphan.
        """
        w = WorkerProcess("s", worker_cmd=_cmd_for(_DROP_FIRST_REQUEST_WORKER))
        try:
            # Request #1 is never answered and gives up.
            with pytest.raises(_WorkerTimeout):
                w.send_simple_op("list_sessions", timeout_ms=300, spawn=True)

            # Request #2 must get its OWN answer, promptly.
            start = time.monotonic()
            result, _refs_out = w.execute("noop", {"session_id": "s"}, timeout_ms=3000)
            elapsed = time.monotonic() - start
            assert result["session_id"] == "s"
            assert elapsed < 2.0, f"execute took {elapsed:.2f}s — response was stolen"
        finally:
            w.kill()


# A real worker whose runner.execute() raises a TypeError from *inside* the
# call — i.e. the kind sfi throws mid result-collection, not an argument
# binding error. Patched before _worker_main imports it.
_DEEP_TYPE_ERROR_WORKER = textwrap.dedent(
    """
    import sys
    import stata_code.core.runner as R

    def _boom(code, **kw):
        raise TypeError("sfi handed back a str where an int was expected")

    R.execute = _boom
    from stata_code.core._pool import _worker_main
    sys.exit(_worker_main())
    """
).strip()


# A real worker with the genuine runner.execute, so signature binding decides.
_REAL_EXECUTE_WORKER = textwrap.dedent(
    """
    import sys
    from stata_code.core._pool import _worker_main
    sys.exit(_worker_main())
    """
).strip()


class TestWorkerErrorClassification:
    """Only genuine caller errors may be reported as invalid_request."""

    def test_unknown_option_is_invalid_request(self):
        """An unknown option name is a caller error and must say so.

        Signature binding runs before execute(), so this never reaches Stata.
        """
        w = WorkerProcess("s", worker_cmd=_cmd_for(_REAL_EXECUTE_WORKER))
        try:
            with pytest.raises(ValueError, match="bad execute options"):
                w.execute(
                    "di 1",
                    {"session_id": "s", "no_such_option": True},
                    timeout_ms=15000,
                )
        finally:
            w.kill()

    def test_deep_type_error_is_not_blamed_on_the_caller(self):
        """A TypeError raised inside result collection is a worker fault.

        Classifying it as `invalid_request` tells the agent its arguments were
        wrong when they were fine — and routes it out of the adapter_crash
        path that would otherwise recycle the wedged worker.
        """
        w = WorkerProcess("s", worker_cmd=_cmd_for(_DEEP_TYPE_ERROR_WORKER))
        try:
            # worker_error (not invalid_request) => _WorkerReportedError,
            # NOT the ValueError that invalid_request would raise.
            with pytest.raises(_WorkerReportedError, match="sfi handed back"):
                w.execute("di 1", {"session_id": "s"}, timeout_ms=15000)
        finally:
            w.kill()


class TestFrameNameLength:
    """Session ids longer than Stata's 32-char name cap must be mapped."""

    def test_overlong_session_id_routes_through_mapped_frame(self):
        long_id = "a" * 40
        frame = runner._frame_for_session(long_id)
        assert frame != long_id
        assert len(frame) <= 32
        assert runner._session_for_frame(frame) == long_id

    def test_normal_session_id_still_passes_through(self):
        assert runner._frame_for_session("panel2") == "panel2"


class TestDuplicateCollinearNotes:
    """Repeated identical notes must not fall through to the generic pass."""

    def test_same_note_twice_is_not_double_counted(self):
        log = (
            "note: dup omitted because of collinearity.\n"
            "some other output\n"
            "note: dup omitted because of collinearity.\n"
        )
        kinds = [w.kind for w in runner._extract_warnings(log)]
        assert kinds == ["omitted_collinear"], kinds

    def test_indented_duplicate_also_deduped(self):
        log = (
            "  note: dup omitted because of collinearity.\n"
            "  note: dup omitted because of collinearity.\n"
        )
        kinds = [w.kind for w in runner._extract_warnings(log)]
        assert kinds == ["omitted_collinear"], kinds

    def test_genuine_generic_note_still_reported(self):
        log = (
            "note: x omitted because of collinearity.\n"
            "note: something else entirely\n"
        )
        kinds = sorted(w.kind for w in runner._extract_warnings(log))
        assert kinds == ["note", "omitted_collinear"], kinds
