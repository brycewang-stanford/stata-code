"""Extra offline coverage for `_refs`, `_pool`, and `log_artifacts`.

Complements `tests/test_pool.py` and `tests/test_log_artifacts.py` with the
branches those files leave untouched:

- `_refs` LRU mechanics (capacity, eviction order, prefix clearing).
- `_pool` wire-format edge cases, worker protocol error paths (id mismatch,
  non-JSON output, generic worker failures), kill escalation, stderr-tail
  handling, cancel/reset bookkeeping, `list_session_info` warnings, and the
  worker main loop itself (run in-process against pipes with a monkeypatched
  runner so no Stata is needed).
- `log_artifacts` failure/fallback paths: atomic-write cleanup, snapshot
  pruning, oversized/unchanged output filtering, out-of-root copies, corrupt
  manifests, and unique-name exhaustion.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import stata_code.core.log_artifacts as log_artifacts
from stata_code.core import _pool, _refs
from stata_code.core._pool import (
    SessionPool,
    WorkerProcess,
    _WorkerError,
    pool_execute,
    shutdown_default_pool,
)
from stata_code.core.log_artifacts import (
    changed_output_files,
    copy_output_artifacts,
    persist_run_log_files,
    snapshot_working_dir_files,
    update_run_artifact_manifest,
)
from stata_code.core.schema import (
    Backend,
    ErrorKind,
    LogFileInfo,
    RunResult,
    StataEdition,
    StataInfo,
)

# ─────────────────────────────────────────────────────────────────────────────
# _refs: LRU store mechanics.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def _restore_refs_capacity():
    saved = _refs.get_capacity()
    try:
        yield
    finally:
        _refs.set_capacity(saved)


@pytest.mark.usefixtures("_restore_refs_capacity")
class TestRefsLRU:
    def test_set_capacity_rejects_below_one(self):
        with pytest.raises(ValueError, match="capacity"):
            _refs.set_capacity(0)

    def test_put_beyond_capacity_evicts_oldest(self):
        _refs.set_capacity(3)
        for k in ("a", "b", "c", "d"):
            _refs.put(k, k.upper())
        assert _refs.size() == 3
        assert not _refs.has("a")
        assert _refs.keys() == ["b", "c", "d"]

    def test_get_touches_lru_order_so_hot_refs_survive(self):
        _refs.set_capacity(2)
        _refs.put("a", 1)
        _refs.put("b", 2)
        assert _refs.get("a") == 1  # refresh "a"; "b" becomes LRU
        _refs.put("c", 3)
        assert _refs.has("a")
        assert not _refs.has("b")
        assert _refs.keys() == ["a", "c"]

    def test_put_existing_key_refreshes_order_and_payload(self):
        _refs.set_capacity(2)
        _refs.put("a", "old")
        _refs.put("b", 2)
        _refs.put("a", "new")  # refresh, not insert: "b" is now LRU
        _refs.put("c", 3)
        assert not _refs.has("b")
        assert _refs.get("a") == "new"

    def test_set_capacity_shrink_evicts_immediately(self):
        _refs.set_capacity(8)
        for k in ("a", "b", "c"):
            _refs.put(k, k)
        _refs.set_capacity(1)
        assert _refs.get_capacity() == 1
        assert _refs.size() == 1
        assert _refs.keys() == ["c"]

    def test_clear_prefix_drops_only_matching_and_returns_count(self):
        _refs.put("log://r1/main", "l1")
        _refs.put("log://r2/main", "l2")
        _refs.put("graph://r1/g1", b"png")
        assert _refs.clear_prefix("log://") == 2
        assert _refs.keys() == ["graph://r1/g1"]
        assert _refs.clear_prefix("log://") == 0

    def test_snapshot_does_not_touch_lru_order(self):
        _refs.put("a", 1)
        _refs.put("b", 2)
        snap = _refs.snapshot()
        assert snap == [("a", 1), ("b", 2)]
        assert _refs.keys() == ["a", "b"]  # order unchanged by snapshot
        _refs.get("a")
        assert _refs.keys() == ["b", "a"]  # ...but changed by get


# ─────────────────────────────────────────────────────────────────────────────
# _pool: wire-format edge cases.
# ─────────────────────────────────────────────────────────────────────────────


class TestWireFormatEdges:
    def test_to_jsonable_handles_tuples_and_nested_bytes(self):
        out = _pool._to_jsonable((b"ab", {"x": bytearray(b"c")}, [1, "s"]))
        assert out == [
            {_pool._BYTES_MARKER: "YWI="},
            {"x": {_pool._BYTES_MARKER: "Yw=="}},
            [1, "s"],
        ]

    def test_from_jsonable_marker_with_non_string_data_yields_empty_bytes(self):
        assert _pool._from_jsonable({_pool._BYTES_MARKER: 123}) == b""

    def test_from_jsonable_marker_with_extra_keys_is_a_plain_dict(self):
        obj = {_pool._BYTES_MARKER: "YWI=", "other": 1}
        # Not the single-key marker form, so it round-trips as a normal dict.
        assert _pool._from_jsonable(obj) == obj

    def test_payload_to_wire_unknown_payload_falls_back_to_repr(self):
        env = _pool._payload_to_wire(object())
        assert env["kind"] == "unknown"
        assert "object" in env["data"]
        assert _pool._payload_from_wire(env) is None

    def test_payload_from_wire_defensive_on_bad_data_types(self):
        assert _pool._payload_from_wire({"kind": "bytes", "data": 123}) == b""
        assert _pool._payload_from_wire({"kind": "text", "data": 5}) == ""


class TestRedirectStandardFd:
    def test_redirects_fd_to_devnull(self):
        r, w = os.pipe()
        try:
            _pool._redirect_standard_fd(w, os.O_WRONLY)
            os.write(w, b"swallowed")  # goes to devnull now, not the pipe
            os.close(w)
            assert os.read(r, 100) == b""  # pipe write end gone: immediate EOF
        finally:
            os.close(r)

    def test_devnull_open_failure_is_swallowed(self, monkeypatch):
        def fail_open(*_args: Any, **_kwargs: Any) -> int:
            raise OSError("no devnull here")

        monkeypatch.setattr(_pool.os, "open", fail_open)
        assert _pool._redirect_standard_fd(1, os.O_WRONLY) is None  # no raise


# ─────────────────────────────────────────────────────────────────────────────
# _pool: worker main loop, run in-process against pipes.
# ─────────────────────────────────────────────────────────────────────────────


def _run_worker_main(requests: list[str]) -> tuple[list[dict[str, Any]], int]:
    """Drive `_worker_main()` in-process with fds 0/1 pointed at pipes.

    All requests are written up-front and the stdin write end closed, so the
    loop drains them and exits on EOF. fds are restored afterwards.
    """
    stdin_r, stdin_w = os.pipe()
    stdout_r, stdout_w = os.pipe()
    os.write(stdin_w, "".join(f"{line}\n" for line in requests).encode("utf-8"))
    os.close(stdin_w)
    saved_stdin = os.dup(0)
    saved_stdout = os.dup(1)
    try:
        os.dup2(stdin_r, 0)
        os.dup2(stdout_w, 1)
        rc = _pool._worker_main()
    finally:
        os.dup2(saved_stdin, 0)
        os.dup2(saved_stdout, 1)
        for fd in (saved_stdin, saved_stdout, stdin_r, stdout_w):
            os.close(fd)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(stdout_r, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    os.close(stdout_r)
    text = b"".join(chunks).decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()], rc


class TestWorkerMainInProcess:
    def test_full_protocol_roundtrip(self, monkeypatch):
        from stata_code.core import _runtime, runner

        template = _pool._build_adapter_crash_result(session_id="w", elapsed_ms=1, message="t")

        def fake_execute(code: str, **_options: Any) -> RunResult:
            if code == "ok":
                _refs.put("log://inproc/main", "ferried log text")
                return template
            if code == "badopt":
                raise ValueError("badopt rejected")
            raise RuntimeError("kaboom")

        monkeypatch.setattr(runner, "execute", fake_execute)
        monkeypatch.setattr(runner, "list_sessions", lambda: [{"session_id": "w1"}])
        monkeypatch.setattr(
            runner,
            "_stata_info",
            lambda _rt: StataInfo(
                version="18.5", edition=StataEdition.MP, backend=Backend.PYSTATA
            ),
        )
        monkeypatch.setattr(_runtime, "get_runtime", lambda: None)

        requests = [
            "",  # blank protocol noise: skipped, no response
            '{"id": "p1", "op": "ping"}',
            '{"id": "l1", "op": "list_sessions"}',
            '{"id": "i1", "op": "stata_info"}',
            '{"id": "e1", "op": "execute", "code": "ok", "options": {"session_id": "w"}}',
            '{"id": "e2", "op": "execute", "code": "badopt", "options": {}}',
            '{"id": "e3", "op": "execute", "code": "boom", "options": {}}',
            '{"id": "u1", "op": "bogus"}',
            "this is not json",
        ]
        responses, rc = _run_worker_main(requests)
        assert rc == 0
        assert len(responses) == 8

        ping, listed, info, ok_exec, badopt, boom, unknown, garbage = responses
        assert ping == {"id": "p1", "ok": True, "pong": True}

        assert listed["ok"] is True
        assert listed["sessions"] == [{"session_id": "w1"}]

        assert info["ok"] is True
        assert info["stata"]["version"] == "18.5"
        assert info["stata"]["edition"] == "MP"

        assert ok_exec["ok"] is True
        assert ok_exec["result"]["session_id"] == "w"
        assert ok_exec["ref_blobs"] == {
            "log://inproc/main": {"kind": "text", "data": "ferried log text"}
        }

        assert badopt["ok"] is False
        assert badopt["error_kind"] == "invalid_request"
        assert "ValueError" in badopt["error"]
        assert "badopt rejected" in badopt["error"]

        assert boom["ok"] is False
        assert boom["error_kind"] == "worker_error"
        assert "kaboom" in boom["error"]

        assert unknown == {"id": "u1", "ok": False, "error": "unknown op: bogus"}

        assert garbage["id"] is None
        assert garbage["ok"] is False
        assert garbage["error_kind"] == "invalid_request"


# ─────────────────────────────────────────────────────────────────────────────
# _pool: mock-worker scripts (same pattern as tests/test_pool.py).
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
        r = _build_adapter_crash_result(session_id=sid, elapsed_ms=1, message="echo")
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


_WRONG_ID_WORKER = textwrap.dedent(
    """
    import json, sys
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        json.loads(line)
        sys.stdout.write(json.dumps({"id": "not-the-request-id", "ok": True, "pong": True}) + "\\n")
        sys.stdout.flush()
    """
).strip()


_FAILING_OP_WORKER = textwrap.dedent(
    """
    import json, sys
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        resp = {"id": req["id"], "ok": False, "error": "exploded sideways",
                "error_kind": "worker_error"}
        sys.stdout.write(json.dumps(resp) + "\\n")
        sys.stdout.flush()
    """
).strip()


_GARBAGE_WORKER = textwrap.dedent(
    """
    import sys
    for line in sys.stdin:
        sys.stdout.write("definitely not json\\n")
        sys.stdout.flush()
    """
).strip()


_ARRAY_WORKER = textwrap.dedent(
    """
    import sys
    for line in sys.stdin:
        sys.stdout.write("[1, 2, 3]\\n")
        sys.stdout.flush()
    """
).strip()


_SIGTERM_IGNORING_WORKER = textwrap.dedent(
    """
    import json, signal, sys
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        sys.stdout.write(json.dumps({"id": req["id"], "ok": True, "pong": True}) + "\\n")
        sys.stdout.flush()
    """
).strip()


_LIST_SESSIONS_WORKER = textwrap.dedent(
    """
    import json, sys
    from stata_code.core._pool import _build_adapter_crash_result
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        if req.get("op") == "list_sessions":
            sessions = [
                {"session_id": "alpha", "frame": "default"},
                {"session_id": None},
                {"session_id": "alpha"},
            ]
            resp = {"id": req["id"], "ok": True, "sessions": sessions}
        else:
            sid = req.get("options", {}).get("session_id", "main")
            r = _build_adapter_crash_result(session_id=sid, elapsed_ms=1, message="echo")
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


_LIST_SESSIONS_FAIL_WORKER = _LIST_SESSIONS_WORKER.replace(
    'resp = {"id": req["id"], "ok": True, "sessions": sessions}',
    'resp = {"id": req["id"], "ok": False, "error": "no sessions for you",\n'
    '                    "error_kind": "worker_error"}',
)


_LIST_SESSIONS_SLOW_WORKER = textwrap.dedent(
    """
    import json, sys, time
    from stata_code.core._pool import _build_adapter_crash_result
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        if req.get("op") == "list_sessions":
            time.sleep(10)
            resp = {"id": req["id"], "ok": True, "sessions": []}
        else:
            sid = req.get("options", {}).get("session_id", "main")
            r = _build_adapter_crash_result(session_id=sid, elapsed_ms=1, message="echo")
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


_STATA_INFO_WORKER = textwrap.dedent(
    """
    import json, sys
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        stata = {"version": "18.5", "edition": "MP", "backend": "pystata"}
        resp = {"id": req["id"], "ok": True, "stata": stata}
        sys.stdout.write(json.dumps(resp) + "\\n")
        sys.stdout.flush()
    """
).strip()


_STATA_INFO_WEIRD_WORKER = _STATA_INFO_WORKER.replace(
    'stata = {"version": "18.5", "edition": "MP", "backend": "pystata"}',
    'stata = "not-a-dict"',
)


def _cmd_for(script: str) -> list[str]:
    return [sys.executable, "-u", "-c", script]


class TestWorkerProcessProtocolErrors:
    def test_execute_response_id_mismatch_raises(self):
        w = WorkerProcess("s1", worker_cmd=_cmd_for(_WRONG_ID_WORKER))
        try:
            with pytest.raises(_WorkerError, match="response id mismatch"):
                w.execute("noop", {"session_id": "s1"}, timeout_ms=5000)
        finally:
            w.kill()

    def test_execute_generic_worker_failure_raises_worker_error(self):
        w = WorkerProcess("s1", worker_cmd=_cmd_for(_FAILING_OP_WORKER))
        try:
            with pytest.raises(_WorkerError, match="exploded sideways"):
                w.execute("noop", {"session_id": "s1"}, timeout_ms=5000)
        finally:
            w.kill()

    def test_non_json_worker_output_raises(self):
        w = WorkerProcess("s1", worker_cmd=_cmd_for(_GARBAGE_WORKER))
        try:
            with pytest.raises(_WorkerError, match="non-JSON"):
                w.execute("noop", {"session_id": "s1"}, timeout_ms=5000)
        finally:
            w.kill()

    def test_non_object_json_worker_output_raises(self):
        w = WorkerProcess("s1", worker_cmd=_cmd_for(_ARRAY_WORKER))
        try:
            with pytest.raises(_WorkerError, match="non-object JSON"):
                w.execute("noop", {"session_id": "s1"}, timeout_ms=5000)
        finally:
            w.kill()

    def test_send_simple_op_without_spawn_on_cold_worker_raises(self):
        w = WorkerProcess("cold", worker_cmd=_cmd_for(_ECHO_WORKER))
        with pytest.raises(_WorkerError, match="not running"):
            w.send_simple_op("ping", timeout_ms=1000)
        assert not w.is_alive()  # spawn=False must not start a process

    def test_send_simple_op_response_id_mismatch_raises(self):
        w = WorkerProcess("s1", worker_cmd=_cmd_for(_WRONG_ID_WORKER))
        try:
            with pytest.raises(_WorkerError, match="response id mismatch"):
                w.send_simple_op("ping", timeout_ms=5000, spawn=True)
        finally:
            w.kill()

    def test_send_simple_op_generic_failure_raises_worker_error(self):
        w = WorkerProcess("s1", worker_cmd=_cmd_for(_FAILING_OP_WORKER))
        try:
            with pytest.raises(_WorkerError, match="exploded sideways"):
                w.send_simple_op("ping", timeout_ms=5000, spawn=True)
        finally:
            w.kill()

    def test_kill_escalates_to_sigkill_when_sigterm_ignored(self, monkeypatch):
        monkeypatch.setattr(_pool, "_KILL_GRACE_S", 0.3)
        w = WorkerProcess("s1", worker_cmd=_cmd_for(_SIGTERM_IGNORING_WORKER))
        # Ping first so the SIGTERM handler is definitely installed.
        assert w.send_simple_op("ping", timeout_ms=5000, spawn=True)["pong"] is True
        started = time.monotonic()
        w.kill()
        assert time.monotonic() - started < 5.0
        assert not w.is_alive()
        assert w._proc is None  # noqa: SLF001


class TestReadlineDeadlineEdges:
    def test_reader_thread_exception_surfaces_as_worker_error(self):
        class _BoomStdout:
            def readline(self) -> str:
                raise RuntimeError("stdout exploded")

        proc = SimpleNamespace(stdout=_BoomStdout(), poll=lambda: None)
        with pytest.raises(_WorkerError, match="reader thread error"):
            WorkerProcess._readline_with_deadline(proc, None)  # type: ignore[arg-type]

    def test_line_that_lands_just_after_process_death_is_returned(self):
        class _LateStdout:
            def readline(self) -> str:
                time.sleep(0.1)
                return '{"late": true}\n'

        proc = SimpleNamespace(stdout=_LateStdout(), poll=lambda: 1, returncode=1, stderr=None)
        line = WorkerProcess._readline_with_deadline(proc, None)  # type: ignore[arg-type]
        assert line == '{"late": true}\n'

    def test_dead_process_with_no_line_raises_with_returncode(self):
        class _NeverStdout:
            def readline(self) -> str:
                time.sleep(1.5)
                return ""

        proc = SimpleNamespace(stdout=_NeverStdout(), poll=lambda: 9, returncode=9, stderr=None)
        with pytest.raises(_WorkerError, match=r"returncode=9"):
            WorkerProcess._readline_with_deadline(proc, None)  # type: ignore[arg-type]


class TestWorkerExitDiagnostics:
    def test_exit_message_for_still_running_process_reports_none(self):
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            msg = _pool._worker_exit_message(proc, None)
            assert "returncode=None" in msg
            assert "stderr" not in msg
        finally:
            proc.kill()
            proc.wait()

    def test_exit_message_recovers_late_returncode_and_stderr(self):
        proc = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stderr.write('tail-marker'); sys.exit(3)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        proc.wait(timeout=30)
        # rc passed as None: the helper re-polls and picks up the real code.
        msg = _pool._worker_exit_message(proc, None)
        assert "returncode=3" in msg
        assert "tail-marker" in msg

    def test_stderr_tail_none_stream_is_empty(self):
        proc = SimpleNamespace(stderr=None)
        assert _pool._read_process_stderr_tail(proc) == ""  # type: ignore[arg-type]

    def test_stderr_tail_read_failure_is_empty(self):
        class _BoomStderr:
            def read(self) -> str:
                raise RuntimeError("stream gone")

        proc = SimpleNamespace(stderr=_BoomStderr())
        assert _pool._read_process_stderr_tail(proc) == ""  # type: ignore[arg-type]

    def test_stderr_tail_is_truncated_to_last_max_chars(self):
        class _LongStderr:
            def read(self) -> str:
                return "  " + "x" * 90 + "TAIL  "

        proc = SimpleNamespace(stderr=_LongStderr())
        tail = _pool._read_process_stderr_tail(proc, max_chars=4)  # type: ignore[arg-type]
        assert tail == "TAIL"


class TestSessionPoolExtra:
    def test_capacity_below_one_rejected_and_property_exposed(self):
        with pytest.raises(ValueError, match="capacity"):
            SessionPool(capacity=0)
        pool = SessionPool(capacity=3, worker_cmd=_cmd_for(_ECHO_WORKER))
        try:
            assert pool.capacity == 3
        finally:
            pool.shutdown()

    def test_dead_worker_is_cleaned_up_and_respawned(self):
        pool = SessionPool(worker_cmd=_cmd_for(_ECHO_WORKER))
        try:
            pool.execute("noop", session_id="s", timeout_ms=5000)
            old = pool._workers["s"]  # noqa: SLF001
            old.kill()  # dead, but still present in the pool map
            assert not old.is_alive()
            r = pool.execute("noop", session_id="s", timeout_ms=5000)
            assert isinstance(r, RunResult)
            assert pool._workers["s"] is not old  # noqa: SLF001
        finally:
            pool.shutdown()

    def test_cancel_bookkeeping_clear_and_duplicate_registration(self):
        pool = SessionPool(worker_cmd=_cmd_for(_ECHO_WORKER))
        try:
            assert pool.is_cancel_pending("s") is False
            assert pool.clear_cancel("s") is False  # nothing pending yet

            registered, killed = pool.request_cancel("s")
            assert (registered, killed) == (True, False)
            registered, killed = pool.request_cancel("s")
            assert (registered, killed) == (False, False)  # already pending

            assert pool.clear_cancel("s") is True
            assert pool.is_cancel_pending("s") is False
        finally:
            pool.shutdown()

    def test_reset_session_clears_cancel_and_reports_worker_presence(self):
        pool = SessionPool(worker_cmd=_cmd_for(_ECHO_WORKER))
        try:
            assert pool.reset_session("ghost") is False  # no worker, no-op

            pool.execute("noop", session_id="s", timeout_ms=5000)
            pool.request_cancel("other")  # unrelated session stays pending
            pool._cancel_pending.add("s")  # noqa: SLF001
            assert pool.reset_session("s") is True
            assert pool.is_cancel_pending("s") is False
            assert pool.is_cancel_pending("other") is True
            assert "s" not in pool.session_ids()
        finally:
            pool.shutdown()

    def test_cancel_registered_during_spawn_cancels_this_call(self):
        pool = SessionPool(worker_cmd=_cmd_for(_ECHO_WORKER))
        orig = pool._get_or_spawn  # noqa: SLF001

        def spawn_then_cancel(session_id: str) -> WorkerProcess:
            worker = orig(session_id)
            pool.request_cancel(session_id)  # lands in the spawn/execute gap
            return worker

        pool._get_or_spawn = spawn_then_cancel  # type: ignore[method-assign]
        try:
            r = pool.execute("noop", session_id="s", timeout_ms=5000)
            assert r.ok is False
            assert r.rc == -3
            assert r.error is not None and r.error.kind is ErrorKind.CANCELLED
            assert r.log.complete is True  # pre-run cancel: empty log is final
            assert pool.is_cancel_pending("s") is False
        finally:
            pool.shutdown()

    def test_list_session_info_dedupes_and_skips_null_ids(self):
        pool = SessionPool(worker_cmd=_cmd_for(_LIST_SESSIONS_WORKER))
        try:
            pool.execute("noop", session_id="s1", timeout_ms=5000)
            pool.execute("noop", session_id="s2", timeout_ms=5000)
            detailed = pool.list_session_info_detailed(per_worker_timeout_ms=5000)
            # Both workers report "alpha" twice plus a null id: one survivor.
            assert detailed["sessions"] == [{"session_id": "alpha", "frame": "default"}]
            assert detailed["warnings"] == []
            # Flat view matches.
            assert pool.list_session_info(per_worker_timeout_ms=5000) == detailed["sessions"]
        finally:
            pool.shutdown()

    def test_list_session_info_reports_worker_error_warning(self):
        pool = SessionPool(worker_cmd=_cmd_for(_LIST_SESSIONS_FAIL_WORKER))
        try:
            pool.execute("noop", session_id="s1", timeout_ms=5000)
            detailed = pool.list_session_info_detailed(per_worker_timeout_ms=5000)
            assert detailed["sessions"] == []
            assert len(detailed["warnings"]) == 1
            warning = detailed["warnings"][0]
            assert warning["session_id"] == "s1"
            assert "no sessions for you" in warning["reason"]
        finally:
            pool.shutdown()

    def test_list_session_info_reports_timeout_warning(self):
        pool = SessionPool(worker_cmd=_cmd_for(_LIST_SESSIONS_SLOW_WORKER))
        try:
            pool.execute("noop", session_id="s1", timeout_ms=5000)
            detailed = pool.list_session_info_detailed(per_worker_timeout_ms=300)
            assert detailed["sessions"] == []
            assert detailed["warnings"] == [{"session_id": "s1", "reason": "timeout"}]
        finally:
            pool.shutdown()

    def test_list_session_info_skips_dead_workers_silently(self):
        pool = SessionPool(worker_cmd=_cmd_for(_LIST_SESSIONS_WORKER))
        try:
            pool.execute("noop", session_id="s1", timeout_ms=5000)
            pool._workers["s1"].kill()  # noqa: SLF001
            detailed = pool.list_session_info_detailed(per_worker_timeout_ms=1000)
            assert detailed == {"sessions": [], "warnings": []}
        finally:
            pool.shutdown()

    def test_stata_info_returns_worker_dict(self):
        pool = SessionPool(worker_cmd=_cmd_for(_STATA_INFO_WORKER))
        try:
            info = pool.stata_info(session_id="s", timeout_ms=5000)
            assert info == {"version": "18.5", "edition": "MP", "backend": "pystata"}
        finally:
            pool.shutdown()

    def test_stata_info_non_dict_payload_becomes_empty(self):
        pool = SessionPool(worker_cmd=_cmd_for(_STATA_INFO_WEIRD_WORKER))
        try:
            assert pool.stata_info(session_id="s", timeout_ms=5000) == {}
        finally:
            pool.shutdown()

    def test_stata_info_failure_kills_and_drops_worker(self):
        pool = SessionPool(worker_cmd=_cmd_for(_FAILING_OP_WORKER))
        try:
            with pytest.raises(_WorkerError, match="exploded sideways"):
                pool.stata_info(session_id="s", timeout_ms=5000)
            assert pool.session_ids() == []
        finally:
            pool.shutdown()


class TestDefaultPoolWiring:
    def teardown_method(self) -> None:
        shutdown_default_pool()

    def test_pool_execute_routes_through_default_pool(self):
        pool = _pool.get_default_pool()
        pool._worker_cmd = _cmd_for(_ECHO_WORKER)  # noqa: SLF001
        r = pool_execute("noop", session_id="wired", timeout_ms=5000)
        assert isinstance(r, RunResult)
        assert r.session_id == "wired"
        assert pool.session_ids() == ["wired"]


# ─────────────────────────────────────────────────────────────────────────────
# log_artifacts: failure and fallback paths.
# ─────────────────────────────────────────────────────────────────────────────


def _stata() -> StataInfo:
    return StataInfo(version="18.0", edition=StataEdition.MP, backend=Backend.PYSTATA)


def _persist(do_file: Path, working_dir: Path | None = None, **overrides: Any) -> LogFileInfo:
    kwargs: dict[str, Any] = dict(
        log_text="ok\n",
        code="di 1\n",
        origin_path=str(do_file),
        origin_kind="file",
        origin_label=f"{do_file.name}:1",
        request_id="0123456789abcdef",
        session_id="main",
        started_at="2026-05-08T01:22:33.456Z",
        elapsed_ms=7,
        rc=0,
        ok=True,
        stata=_stata(),
        working_dir=None if working_dir is None else str(working_dir),
    )
    kwargs.update(overrides)
    return persist_run_log_files(**kwargs)


class TestAtomicWrite:
    def test_failure_cleans_up_temp_file_and_reraises(self, tmp_path, monkeypatch):
        def fail_replace(_src: str, _dst: Any) -> None:
            raise OSError("no rename for you")

        monkeypatch.setattr(log_artifacts.os, "replace", fail_replace)
        with pytest.raises(OSError, match="no rename for you"):
            log_artifacts._atomic_write_text(tmp_path / "m.json", "{}")
        assert list(tmp_path.iterdir()) == []  # temp removed, target absent

    def test_failure_with_broken_unlink_still_reraises_original(self, tmp_path, monkeypatch):
        def fail_replace(_src: str, _dst: Any) -> None:
            raise OSError("no rename for you")

        def fail_unlink(_p: str) -> None:
            raise OSError("no unlink either")

        monkeypatch.setattr(log_artifacts.os, "replace", fail_replace)
        monkeypatch.setattr(log_artifacts.os, "unlink", fail_unlink)
        with pytest.raises(OSError, match="no rename for you"):
            log_artifacts._atomic_write_text(tmp_path / "m.json", "{}")

    def test_fsync_directory_on_missing_dir_is_a_noop(self, tmp_path):
        assert log_artifacts._fsync_directory(tmp_path / "does-not-exist") is None


class TestPersistEdges:
    def test_relative_origin_path_is_resolved_against_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "rel.do").write_text("di 1\n", encoding="utf-8")
        info = _persist(Path("rel.do"))
        assert Path(info.directory).parent == tmp_path.resolve() / "log-files"
        manifest = json.loads(Path(info.manifest_path).read_text(encoding="utf-8"))
        assert manifest["source_path"] == str(tmp_path.resolve() / "rel.do")

    def test_unparseable_started_at_falls_back_to_safe_name(self, tmp_path):
        do_file = tmp_path / "t.do"
        do_file.write_text("di 1\n", encoding="utf-8")
        info = _persist(do_file, started_at="2026/05/08 01:22 weird")
        # ":" and "-" stripped, remaining unsafe chars replaced by "_".
        assert "t__2026_05_08_0122_weird__main__" in Path(info.directory).name

    def test_timestamp_for_name_fallback_direct(self):
        assert log_artifacts._timestamp_for_name("not a timestamp") == "not_a_timestamp"


class TestSnapshotAndOutputs:
    def test_snapshot_missing_root_returns_empty(self, tmp_path):
        assert snapshot_working_dir_files(tmp_path / "nope") == {}

    def test_snapshot_skips_dangling_symlinks_and_respects_max_depth(self, tmp_path):
        (tmp_path / "top.csv").write_text("x", encoding="utf-8")
        os.symlink(tmp_path / "missing-target", tmp_path / "dangling.csv")
        deep = tmp_path / "d1" / "d2"
        deep.mkdir(parents=True)
        (tmp_path / "d1" / "one-down.csv").write_text("y", encoding="utf-8")
        (deep / "two-down.csv").write_text("z", encoding="utf-8")

        snap = snapshot_working_dir_files(tmp_path, max_depth=1)
        assert {Path(p).name for p in snap} == {"top.csv"}

        # Default depth (3) picks up the nested files but never the symlink.
        snap_deep = snapshot_working_dir_files(tmp_path)
        assert {Path(p).name for p in snap_deep} == {"top.csv", "one-down.csv", "two-down.csv"}

    def test_changed_outputs_skip_unchanged_and_oversized_files(self, tmp_path):
        stable = tmp_path / "stable.csv"
        stable.write_text("a", encoding="utf-8")
        before = snapshot_working_dir_files(tmp_path)

        big = tmp_path / "big.csv"
        with open(big, "wb") as f:
            f.truncate(log_artifacts._MAX_OUTPUT_ARTIFACT_BYTES + 1)  # sparse
        fresh = tmp_path / "fresh.csv"
        fresh.write_text("b", encoding="utf-8")

        assert changed_output_files(before, tmp_path) == [fresh]

    def test_copy_output_artifacts_empty_list_returns_same_info(self, tmp_path):
        do_file = tmp_path / "t.do"
        do_file.write_text("di 1\n", encoding="utf-8")
        info = _persist(do_file, working_dir=tmp_path)
        assert copy_output_artifacts(info, [], working_dir=tmp_path) is info

    def test_copy_output_artifacts_out_of_root_and_missing_sources(self, tmp_path):
        wd = tmp_path / "wd"
        wd.mkdir()
        do_file = wd / "t.do"
        do_file.write_text("di 1\n", encoding="utf-8")
        info = _persist(do_file, working_dir=wd)

        outside = tmp_path / "elsewhere" / "out.csv"
        outside.parent.mkdir()
        outside.write_text("x,y\n", encoding="utf-8")
        missing = tmp_path / "gone.csv"  # never created

        updated = copy_output_artifacts(info, [outside, missing], working_dir=wd)
        assert updated is not info
        # Out-of-root file is flattened to its basename inside outputs/.
        assert [Path(p).name for p in updated.output_paths] == ["out.csv"]
        copied = Path(updated.output_paths[0])
        assert copied.parent == Path(updated.outputs_dir or "")
        assert copied.read_text(encoding="utf-8") == "x,y\n"

    def test_copy_output_artifacts_all_sources_failing_returns_same_info(self, tmp_path):
        do_file = tmp_path / "t.do"
        do_file.write_text("di 1\n", encoding="utf-8")
        info = _persist(do_file, working_dir=tmp_path)
        result = copy_output_artifacts(info, [tmp_path / "gone.csv"], working_dir=tmp_path)
        assert result is info
        assert result.output_paths == []


class TestManifestUpdateResilience:
    def test_missing_manifest_is_a_noop(self, tmp_path):
        do_file = tmp_path / "t.do"
        do_file.write_text("di 1\n", encoding="utf-8")
        info = _persist(do_file, working_dir=tmp_path)
        os.unlink(info.manifest_path)
        update_run_artifact_manifest(info)  # must not raise
        assert not Path(info.manifest_path).exists()

    def test_corrupt_manifest_is_left_untouched(self, tmp_path):
        do_file = tmp_path / "t.do"
        do_file.write_text("di 1\n", encoding="utf-8")
        info = _persist(do_file, working_dir=tmp_path)
        Path(info.manifest_path).write_text("{not json", encoding="utf-8")
        update_run_artifact_manifest(info)  # must not raise
        assert Path(info.manifest_path).read_text(encoding="utf-8") == "{not json"


class TestUniqueNameExhaustion:
    def test_unique_dir_falls_back_to_uuid_suffix(self, tmp_path):
        base = tmp_path / "run"
        base.mkdir()
        for i in range(2, 1000):
            (tmp_path / f"run__{i}").mkdir()
        candidate = log_artifacts._unique_dir(base)
        assert not candidate.exists()
        suffix = candidate.name.rsplit("__", 1)[1]
        assert len(suffix) == 12
        assert all(c in "0123456789abcdef" for c in suffix)

    def test_unique_dir_raises_when_even_uuid_candidates_collide(self, tmp_path, monkeypatch):
        base = tmp_path / "run"
        base.mkdir()
        for i in range(2, 1000):
            (tmp_path / f"run__{i}").mkdir()
        (tmp_path / ("run__" + "a" * 12)).mkdir()
        monkeypatch.setattr(
            log_artifacts.uuid, "uuid4", lambda: SimpleNamespace(hex="a" * 32)
        )
        with pytest.raises(FileExistsError, match="log artifact directory"):
            log_artifacts._unique_dir(base)

    def test_unique_file_numbered_then_uuid_then_error(self, tmp_path, monkeypatch):
        base = tmp_path / "t.csv"
        base.write_text("x", encoding="utf-8")
        assert log_artifacts._unique_file(base).name == "t__2.csv"

        for i in range(2, 1000):
            (tmp_path / f"t__{i}.csv").write_text("x", encoding="utf-8")
        fallback = log_artifacts._unique_file(base)
        assert not fallback.exists()
        assert fallback.suffix == ".csv"
        assert len(fallback.stem.rsplit("__", 1)[1]) == 12

        (tmp_path / ("t__" + "b" * 12 + ".csv")).write_text("x", encoding="utf-8")
        monkeypatch.setattr(
            log_artifacts.uuid, "uuid4", lambda: SimpleNamespace(hex="b" * 32)
        )
        with pytest.raises(FileExistsError, match="unique artifact path"):
            log_artifacts._unique_file(base)
