"""Subprocess pool for hard timeout enforcement.

`runner.execute()` runs pystata in-process, which is fast but offers no
clean way to cancel a long-running Stata command (`bayes`, `bootstrap,
reps(10000)`, infinite loop, ...). pystata holds the GIL inside C land
and ignores Python signals until it returns. v0.2 documented this:
`timeout_ms` was accepted but not enforced.

This module fills that gap. `pool_execute()` is a drop-in for
`runner.execute()` that routes the call through a per-session
subprocess. The parent enforces `timeout_ms` by reading from the
worker's stdout with a deadline; on overrun, the worker is SIGTERMed
(grace period) then SIGKILLed, and the parent returns a synthetic
`RunResult(rc=-2, error.kind="timeout")`. The dead worker is dropped
from the pool and respawned on the next call to that `session_id`.

Design choices:

- **One worker per session_id.** Stata is single-threaded; serialize per
  session at the worker level. Different sessions get different
  workers and run truly in parallel.
- **Workers are warm.** First call to a new session pays the pystata
  init cost (~3-10s); subsequent calls are pipe-roundtrip + JSON only
  (typically <50ms overhead).
- **Refs are ferried.** The worker's `_refs` store is local to that
  process. After each `execute()`, the parent harvests any newly-
  created refs and re-puts them in its OWN `_refs` so that
  `get_log/get_graph/get_matrix` calls served by the parent (the MCP
  server, typically) hit the parent's store directly without IPC.
- **Wire protocol.** Newline-delimited JSON. Request: one line in
  `{id, code, options}`. Response: one line in
  `{id, ok, result, ref_blobs}` (or `{id, ok=false, error}`).

Not exposed in the public API. Frontends import `pool_execute` from
this module if they want the timeout guarantee.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from stata_code.core import _refs
from stata_code.core.schema import (
    Backend,
    DatasetInfo,
    ErrorContext,
    ErrorInfo,
    ErrorKind,
    LogInfo,
    ResultsInfo,
    RunResult,
    StataEdition,
    StataInfo,
    StataReturns,
)

# Grace period after SIGTERM before SIGKILL.
_KILL_GRACE_S = 2.0

# Read-poll interval while waiting for worker stdout.
_POLL_INTERVAL_S = 0.05

# Default pool capacity: how many distinct sessions to keep warm.
_DEFAULT_POOL_CAPACITY = 8


# ─────────────────────────────────────────────────────────────────────────────
# Worker side: runs in the SUBPROCESS, not the parent.
# ─────────────────────────────────────────────────────────────────────────────


_BYTES_MARKER = "__bytes_b64__"


def _to_jsonable(obj: Any) -> Any:
    """Recursively rewrite bytes/bytearray as `{__bytes_b64__: <base64>}`.

    Other primitives (str, int, float, bool, None) pass through. Nested
    dicts and lists are walked. This lets us ferry the runner's graph
    payload — which is a dict that *contains* bytes (`{"format": "png",
    "bytes": <data>, "width": ..., "height": ...}`) — over a JSON pipe
    without losing structure.
    """
    if isinstance(obj, (bytes, bytearray)):
        return {_BYTES_MARKER: base64.b64encode(bytes(obj)).decode("ascii")}
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, tuple):
        return [_to_jsonable(v) for v in obj]
    return obj


def _from_jsonable(obj: Any) -> Any:
    """Inverse of `_to_jsonable`. Restores bytes from the marker form."""
    if isinstance(obj, dict):
        if _BYTES_MARKER in obj and len(obj) == 1:
            data = obj[_BYTES_MARKER]
            return base64.b64decode(data) if isinstance(data, str) else b""
        return {k: _from_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_from_jsonable(v) for v in obj]
    return obj


def _payload_to_wire(payload: Any) -> dict[str, Any]:
    """Serialize a `_refs` payload to a JSON-safe envelope.

    `_refs` stores three payload shapes today:
      - bytes:   graph data (legacy / direct path)
      - str:     full log text (legacy / direct path)
      - dict:    log info `{text, lines_total, bytes_total}`,
                 matrix info `{rows, cols, values}`,
                 graph info `{format, bytes, width, height}` — note the
                 nested bytes inside this last shape, which is why dict
                 payloads go through `_to_jsonable`.
    """
    if isinstance(payload, (bytes, bytearray)):
        return {"kind": "bytes", "data": base64.b64encode(bytes(payload)).decode("ascii")}
    if isinstance(payload, str):
        return {"kind": "text", "data": payload}
    if isinstance(payload, dict):
        return {"kind": "json", "data": _to_jsonable(payload)}
    # Conservative fallback — wrap repr; the parent will ignore unknown kinds.
    return {"kind": "unknown", "data": repr(payload)}


def _payload_from_wire(envelope: dict[str, Any]) -> Any:
    kind = envelope.get("kind")
    data = envelope.get("data")
    if kind == "bytes":
        return base64.b64decode(data) if isinstance(data, str) else b""
    if kind == "text":
        return data if isinstance(data, str) else ""
    if kind == "json":
        return _from_jsonable(data)
    return None


def _worker_main() -> int:
    """Worker entry. Reads one JSON request per line from stdin, writes one
    JSON response per line to stdout. Exits 0 on EOF.
    """
    # CRITICAL: dup FDs 0 and 1 BEFORE pystata gets imported / initialized.
    # `pystata.config.init()` reaches into the runtime's FDs (closes /
    # redirects stdin, redirects stdout to its own buffer). If we read /
    # write through `sys.stdin` / `sys.stdout` the protocol breaks the
    # moment Stata initializes — subsequent reads see EOF and the worker
    # exits returncode=0 without responding to the second request.
    #
    # Duping the file descriptors gives us a reader/writer rooted at a
    # *separate* FD that pystata cannot reach via the sys.* indirection.
    saved_stdin_fd = os.dup(0)
    saved_stdout_fd = os.dup(1)
    proto_in = os.fdopen(saved_stdin_fd, "r", buffering=1, encoding="utf-8")
    proto_out = os.fdopen(saved_stdout_fd, "w", buffering=1, encoding="utf-8")

    # Imported here so `python -m stata_code.core._pool` can fail loudly
    # only if a request actually arrives — listing the worker as a tool
    # candidate shouldn't cost a Stata init.
    from stata_code.core.runner import execute

    # Explicit readline() loop instead of `for line in proto_in` — the latter
    # uses the io module's buffered iterator, which read-aheads more bytes
    # than are available on a pipe and breaks the request/response cadence
    # after pystata init.
    while True:
        line = proto_in.readline()
        if not line:
            break  # EOF — parent closed the pipe
        line = line.strip()
        if not line:
            continue
        req_id: str | None = None
        try:
            req = json.loads(line)
            req_id = req.get("id")
            op = req.get("op", "execute")
            if op == "ping":
                response = {"id": req_id, "ok": True, "pong": True}
            elif op == "list_sessions":
                # Imported lazily — calling list_sessions() on a worker that
                # hasn't yet had any execute() request still triggers pystata
                # init, which is the price of an honest answer.
                from stata_code.core.runner import list_sessions as _ls

                response = {"id": req_id, "ok": True, "sessions": _ls()}
            elif op == "stata_info":
                from stata_code.core._runtime import get_runtime
                from stata_code.core.runner import _stata_info

                info = _stata_info(get_runtime())
                response = {
                    "id": req_id,
                    "ok": True,
                    "stata": info.model_dump(mode="json"),
                }
            elif op == "execute":
                code = req["code"]
                options = req.get("options", {})
                # Snapshot ref keys before so we can ferry only the *new* ones.
                # _refs._store is private but we own this codebase.
                with _refs._lock:  # noqa: SLF001
                    keys_before = set(_refs._store.keys())  # noqa: SLF001
                result = execute(code, **options)
                with _refs._lock:  # noqa: SLF001
                    keys_after = set(_refs._store.keys())  # noqa: SLF001
                new_keys = keys_after - keys_before
                ref_blobs: dict[str, dict[str, Any]] = {}
                for k in new_keys:
                    payload = _refs.get(k)
                    if payload is None:
                        continue
                    ref_blobs[k] = _payload_to_wire(payload)
                response = {
                    "id": req_id,
                    "ok": True,
                    "result": json.loads(result.model_dump_json()),
                    "ref_blobs": ref_blobs,
                }
            else:
                response = {"id": req_id, "ok": False, "error": f"unknown op: {op}"}
        except Exception as exc:  # noqa: BLE001
            response = {
                "id": req_id,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        proto_out.write(json.dumps(response) + "\n")
        proto_out.flush()
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Parent side: WorkerProcess + SessionPool + pool_execute().
# ─────────────────────────────────────────────────────────────────────────────


def _default_worker_cmd() -> list[str]:
    return [sys.executable, "-u", "-m", "stata_code.core._pool"]


class _WorkerError(RuntimeError):
    """Raised on worker-side execution failure (non-Stata, e.g., crash)."""


class _WorkerTimeout(TimeoutError):
    """Raised when a request exceeds its deadline. Parent kills the worker."""


class WorkerProcess:
    """Parent-side handle for one subprocess worker.

    Construct via `WorkerProcess(session_id, ...)` — the subprocess is
    spawned lazily on first `execute()`. Use `kill()` to terminate.
    Workers are not thread-safe internally; the pool serializes calls.
    """

    def __init__(
        self,
        session_id: str,
        *,
        worker_cmd: list[str] | None = None,
    ) -> None:
        self.session_id = session_id
        self._cmd = list(worker_cmd) if worker_cmd is not None else _default_worker_cmd()
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self.last_used: float = time.monotonic()

    def _spawn(self) -> subprocess.Popen[str]:
        env = os.environ.copy()
        # Force unbuffered I/O even if PYTHONUNBUFFERED isn't already set.
        env.setdefault("PYTHONUNBUFFERED", "1")
        return subprocess.Popen(
            self._cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered
            env=env,
        )

    def _ensure_alive(self) -> subprocess.Popen[str]:
        if self._proc is None or self._proc.poll() is not None:
            self._proc = self._spawn()
        return self._proc

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def execute(
        self,
        code: str,
        options: dict[str, Any],
        *,
        timeout_ms: int | None,
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        """Send one execute request and return (result_dict, ref_blobs).

        Raises `_WorkerTimeout` on timeout (caller is responsible for
        killing the worker), `_WorkerError` on protocol or worker-side
        crash.
        """
        with self._lock:
            proc = self._ensure_alive()
            assert proc.stdin is not None and proc.stdout is not None  # for mypy
            req_id = uuid.uuid4().hex
            request = {"id": req_id, "op": "execute", "code": code, "options": options}
            try:
                proc.stdin.write(json.dumps(request) + "\n")
                proc.stdin.flush()
            except BrokenPipeError as exc:
                raise _WorkerError(f"worker pipe broken on write: {exc}") from exc

            deadline: float | None
            deadline = None if timeout_ms is None else time.monotonic() + timeout_ms / 1000.0

            line = self._readline_with_deadline(proc, deadline)
            self.last_used = time.monotonic()

            try:
                response = json.loads(line)
            except json.JSONDecodeError as exc:
                raise _WorkerError(f"worker emitted non-JSON: {line!r}") from exc

            if response.get("id") != req_id:
                raise _WorkerError(
                    f"worker response id mismatch: expected {req_id}, got {response.get('id')}"
                )
            if not response.get("ok"):
                raise _WorkerError(
                    f"worker reported failure: {response.get('error', '<no error>')}"
                )
            return response["result"], response.get("ref_blobs", {})

    @staticmethod
    def _readline_with_deadline(
        proc: subprocess.Popen[str],
        deadline: float | None,
    ) -> str:
        """Read one line from `proc.stdout` honoring an optional wall-clock
        deadline. Raises `_WorkerTimeout` on overrun.

        Implementation note: we poll `proc.poll()` plus a short readline
        in a thread, joining with the remaining budget. This is portable
        (no select on Windows pipes) and robust for the line-oriented
        protocol.
        """
        assert proc.stdout is not None
        result: dict[str, str | BaseException] = {}

        def _reader() -> None:
            try:
                line = proc.stdout.readline()  # type: ignore[union-attr]
                result["line"] = line
            except BaseException as exc:  # noqa: BLE001
                result["err"] = exc

        thread = threading.Thread(target=_reader, daemon=True)
        thread.start()

        while True:
            if deadline is None:
                remaining = None
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _WorkerTimeout("deadline exceeded waiting for worker response")
            thread.join(timeout=min(_POLL_INTERVAL_S, remaining) if remaining is not None else _POLL_INTERVAL_S)
            if not thread.is_alive():
                if "err" in result:
                    raise _WorkerError(f"reader thread error: {result['err']!r}")
                line = result.get("line", "")
                assert isinstance(line, str)
                if not line:
                    # EOF — worker exited or pipe closed unexpectedly.
                    rc = proc.poll()
                    raise _WorkerError(f"worker exited (returncode={rc}) before responding")
                return line
            # Worker still running but no line yet. If the process died, surface that.
            if proc.poll() is not None:
                # Wait briefly for any final bytes the reader thread might catch.
                thread.join(timeout=0.1)
                if "line" in result and isinstance(result["line"], str) and result["line"]:
                    return result["line"]
                rc = proc.returncode
                raise _WorkerError(f"worker exited (returncode={rc}) before responding")

    def send_simple_op(
        self,
        op: str,
        *,
        timeout_ms: int | None,
        spawn: bool = False,
    ) -> dict[str, Any]:
        """Send a no-payload op (e.g., ``ping``, ``list_sessions``) and return
        the full response dict.

        By default, unlike :meth:`execute`, this does **not** respawn a dead
        worker; if the subprocess isn't running, raises :class:`_WorkerError`.
        Caller should treat that as "this worker has nothing to report"
        rather than block on a fresh pystata init for a status query.
        """
        with self._lock:
            if spawn:
                proc = self._ensure_alive()
            elif self._proc is None or self._proc.poll() is not None:
                raise _WorkerError(f"worker for {self.session_id!r} not running")
            else:
                proc = self._proc
            assert proc.stdin is not None and proc.stdout is not None  # for mypy
            req_id = uuid.uuid4().hex
            request = {"id": req_id, "op": op}
            try:
                proc.stdin.write(json.dumps(request) + "\n")
                proc.stdin.flush()
            except BrokenPipeError as exc:
                raise _WorkerError(f"worker pipe broken on write: {exc}") from exc

            deadline: float | None
            deadline = None if timeout_ms is None else time.monotonic() + timeout_ms / 1000.0

            line = self._readline_with_deadline(proc, deadline)
            self.last_used = time.monotonic()

            try:
                response = json.loads(line)
            except json.JSONDecodeError as exc:
                raise _WorkerError(f"worker emitted non-JSON: {line!r}") from exc

            if response.get("id") != req_id:
                raise _WorkerError(
                    f"worker response id mismatch: expected {req_id}, got {response.get('id')}"
                )
            if not response.get("ok"):
                raise _WorkerError(
                    f"worker reported failure: {response.get('error', '<no error>')}"
                )
            return dict(response)

    def kill(self) -> None:
        """Terminate the worker. SIGTERM with grace, then SIGKILL.

        Cancellation must work while ``execute()`` is blocked waiting for a
        worker response. ``execute()`` holds ``self._lock`` during that read,
        so kill deliberately does not wait for the same lock.
        """
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is not None:
            self._proc = None
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=_KILL_GRACE_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=_KILL_GRACE_S)
                except subprocess.TimeoutExpired:
                    pass
        except ProcessLookupError:
            pass
        # Drain any buffered stderr so we don't leak a fd.
        try:
            if proc.stderr is not None:
                proc.stderr.read()
        except Exception:  # noqa: BLE001
            pass
        if self._proc is proc:
            self._proc = None


class SessionPool:
    """LRU pool of subprocess workers, keyed by `session_id`."""

    def __init__(
        self,
        *,
        capacity: int = _DEFAULT_POOL_CAPACITY,
        worker_cmd: list[str] | None = None,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be ≥ 1")
        self._capacity = capacity
        self._worker_cmd = worker_cmd
        self._workers: dict[str, WorkerProcess] = {}
        self._cancel_pending: set[str] = set()
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._capacity

    def _get_or_spawn(self, session_id: str) -> WorkerProcess:
        with self._lock:
            w = self._workers.get(session_id)
            if w is None or not w.is_alive():
                if w is not None:
                    # Existing-but-dead — clean up before respawn.
                    w.kill()
                w = WorkerProcess(session_id, worker_cmd=self._worker_cmd)
                self._workers[session_id] = w
                self._evict_to_capacity_locked(keep=session_id)
            return w

    def _evict_to_capacity_locked(self, *, keep: str) -> None:
        if len(self._workers) <= self._capacity:
            return
        # LRU by `last_used`. Never evict the just-added worker.
        candidates = sorted(
            ((sid, w) for sid, w in self._workers.items() if sid != keep),
            key=lambda kv: kv[1].last_used,
        )
        while len(self._workers) > self._capacity and candidates:
            sid, w = candidates.pop(0)
            w.kill()
            self._workers.pop(sid, None)

    def execute(
        self,
        code: str,
        *,
        session_id: str = "main",
        timeout_ms: int | None = 600_000,
        **options: Any,
    ) -> RunResult:
        """Execute `code` in the session's worker, enforcing `timeout_ms`.

        On timeout: SIGTERM/SIGKILL the worker and return a synthetic
        `RunResult(rc=-2, error.kind="timeout")`. Subsequent calls to the
        same `session_id` will respawn a fresh worker.
        """
        # Normalize: pass session_id through to the worker so it routes
        # to the right Stata frame. timeout_ms is enforced HERE — the
        # worker doesn't see it.
        started = time.monotonic()
        if self._consume_cancel(session_id):
            return _build_cancelled_result(
                session_id=session_id,
                elapsed_ms=max(1, int((time.monotonic() - started) * 1000)),
            )

        worker_options = {**options, "session_id": session_id}
        # Forward timeout_ms verbatim so the worker stores it on the result
        # for observability, even though the real enforcement is parent-side.
        worker_options.setdefault("timeout_ms", timeout_ms)
        worker = self._get_or_spawn(session_id)
        try:
            result_dict, ref_blobs = worker.execute(
                code, worker_options, timeout_ms=timeout_ms
            )
        except _WorkerTimeout:
            worker.kill()
            with self._lock:
                self._workers.pop(session_id, None)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return _build_timeout_result(
                session_id=session_id,
                elapsed_ms=elapsed_ms,
                timeout_ms=timeout_ms or 0,
            )
        except _WorkerError as exc:
            cancelled = self._consume_cancel(session_id)
            worker.kill()
            with self._lock:
                self._workers.pop(session_id, None)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if cancelled:
                return _build_cancelled_result(
                    session_id=session_id,
                    elapsed_ms=max(1, elapsed_ms),
                )
            return _build_adapter_crash_result(
                session_id=session_id,
                elapsed_ms=elapsed_ms,
                message=str(exc),
            )

        # Ferry refs into the parent's _refs store.
        for ref_id, envelope in ref_blobs.items():
            payload = _payload_from_wire(envelope)
            if payload is not None:
                _refs.put(ref_id, payload)

        return RunResult.model_validate(result_dict)

    def kill_session(self, session_id: str) -> bool:
        """Terminate a session's worker. Returns True if a worker existed."""
        with self._lock:
            w = self._workers.pop(session_id, None)
        if w is None:
            return False
        w.kill()
        return True

    def request_cancel(self, session_id: str) -> tuple[bool, bool]:
        """Request cancellation and terminate a live worker if one exists.

        Returns ``(registered, killed_worker)``. ``registered`` is ``False``
        when a cancellation for this session was already pending.
        """
        with self._lock:
            registered = session_id not in self._cancel_pending
            self._cancel_pending.add(session_id)
            worker = self._workers.pop(session_id, None)
        if worker is not None:
            worker.kill()
        return registered, worker is not None

    def is_cancel_pending(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._cancel_pending

    def clear_cancel(self, session_id: str) -> bool:
        with self._lock:
            if session_id not in self._cancel_pending:
                return False
            self._cancel_pending.remove(session_id)
            return True

    def _consume_cancel(self, session_id: str) -> bool:
        with self._lock:
            if session_id not in self._cancel_pending:
                return False
            self._cancel_pending.remove(session_id)
            return True

    def reset_session(self, session_id: str) -> bool:
        """Clear pending cancellation and terminate a session worker."""
        with self._lock:
            self._cancel_pending.discard(session_id)
            worker = self._workers.pop(session_id, None)
        if worker is None:
            return False
        worker.kill()
        return True

    def shutdown(self) -> None:
        """Kill all workers."""
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
            self._cancel_pending.clear()
        for w in workers:
            w.kill()

    def session_ids(self) -> list[str]:
        with self._lock:
            return list(self._workers)

    def list_session_info(
        self,
        *,
        per_worker_timeout_ms: int = 5000,
    ) -> list[dict[str, Any]]:
        """Aggregate live-session info across all workers.

        For each worker that's alive, sends ``op=list_sessions`` and pulls
        back ``[{session_id, frame, n_obs}, ...]`` from that worker's
        pystata. The pool dedupes by ``session_id`` (first writer wins) and
        returns the union.

        Workers that are dead, that fail to respond within
        ``per_worker_timeout_ms``, or that raise a protocol error are
        silently skipped — partial information is better than failing the
        whole list call. Workers that haven't yet served an ``execute``
        will pay the pystata-init cost on the next ``stata_run``, not here:
        :meth:`WorkerProcess.send_simple_op` deliberately does **not**
        respawn dead workers.
        """
        with self._lock:
            workers = list(self._workers.items())
        sessions: list[dict[str, Any]] = []
        seen: set[str] = set()
        for _sid, worker in workers:
            if not worker.is_alive():
                continue
            try:
                response = worker.send_simple_op(
                    "list_sessions", timeout_ms=per_worker_timeout_ms
                )
            except (_WorkerError, _WorkerTimeout):
                continue
            for entry in response.get("sessions") or []:
                sid = entry.get("session_id")
                if sid is None or sid in seen:
                    continue
                seen.add(sid)
                sessions.append(entry)
        return sessions

    def stata_info(
        self,
        *,
        session_id: str = "main",
        timeout_ms: int | None = 60_000,
    ) -> dict[str, Any]:
        """Return Stata runtime info from a worker process.

        This keeps pystata initialization out of the parent MCP process, where
        it can otherwise block the asyncio event loop and delay stdout flushes.
        """
        worker = self._get_or_spawn(session_id)
        try:
            response = worker.send_simple_op(
                "stata_info", timeout_ms=timeout_ms, spawn=True
            )
        except (_WorkerError, _WorkerTimeout):
            worker.kill()
            with self._lock:
                self._workers.pop(session_id, None)
            raise
        stata = response["stata"]
        return stata if isinstance(stata, dict) else {}


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic-result builders for timeout / adapter crash.
# ─────────────────────────────────────────────────────────────────────────────


def _utc_iso_ms() -> str:
    # Capture `now` once: a previous version called `datetime.now()` twice and
    # could straddle a second boundary, producing e.g. "...T23:59:59.000Z" —
    # silently breaking lexicographic compare downstream (e.g. list_runs'
    # `since` filter).
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _empty_returns() -> StataReturns:
    return StataReturns(scalars={}, macros={}, matrices={})


def _empty_dataset() -> DatasetInfo:
    return DatasetInfo(
        frame="default",
        n_obs=0,
        n_vars=0,
        changed=False,
        filename=None,
        variables=None,
    )


def _build_timeout_result(
    *,
    session_id: str,
    elapsed_ms: int,
    timeout_ms: int,
) -> RunResult:
    err = ErrorInfo(
        kind=ErrorKind.TIMEOUT,
        rc=-2,
        rc_label="timeout",
        message=(
            f"Execution exceeded the configured timeout of {timeout_ms} ms. "
            f"The worker process for session_id={session_id!r} was terminated."
        ),
        command=None,
        line=None,
        context=ErrorContext(before=[], failing="", after=[]),
        commands_executed=None,
        path=None,
        varname=None,
        name=None,
        suggestions=[],
    )
    return RunResult(
        ok=False,
        rc=-2,
        session_id=session_id,
        request_id=uuid.uuid4().hex,
        started_at=_utc_iso_ms(),
        elapsed_ms=elapsed_ms,
        stata_elapsed_ms=elapsed_ms,
        stata=StataInfo(
            version="unknown",
            edition=StataEdition.UNKNOWN,
            backend=Backend.PYSTATA,
        ),
        log=LogInfo(
            head="",
            tail="",
            lines_total=0,
            bytes_total=0,
            truncated=False,
            complete=False,
            error_window=None,
            ref=None,
        ),
        results=ResultsInfo(r=_empty_returns(), e=_empty_returns(), last_estimation_cmd=None),
        dataset=_empty_dataset(),
        graphs=[],
        warnings=[],
        error=err,
        schema_version="1.0",
        capabilities=["pystata", "subprocess_timeout"],
    )


def _build_adapter_crash_result(
    *,
    session_id: str,
    elapsed_ms: int,
    message: str,
) -> RunResult:
    err = ErrorInfo(
        kind=ErrorKind.ADAPTER_CRASH,
        rc=-1,
        rc_label="adapter_crash",
        message=f"Subprocess worker crashed: {message}",
        command=None,
        line=None,
        context=ErrorContext(before=[], failing="", after=[]),
        commands_executed=None,
        path=None,
        varname=None,
        name=None,
        suggestions=[],
    )
    return RunResult(
        ok=False,
        rc=-1,
        session_id=session_id,
        request_id=uuid.uuid4().hex,
        started_at=_utc_iso_ms(),
        elapsed_ms=elapsed_ms,
        stata_elapsed_ms=elapsed_ms,
        stata=StataInfo(
            version="unknown",
            edition=StataEdition.UNKNOWN,
            backend=Backend.PYSTATA,
        ),
        log=LogInfo(
            head="",
            tail="",
            lines_total=0,
            bytes_total=0,
            truncated=False,
            complete=False,
            error_window=None,
            ref=None,
        ),
        results=ResultsInfo(r=_empty_returns(), e=_empty_returns(), last_estimation_cmd=None),
        dataset=_empty_dataset(),
        graphs=[],
        warnings=[],
        error=err,
        schema_version="1.0",
        capabilities=["pystata", "subprocess_timeout"],
    )


def _build_cancelled_result(
    *,
    session_id: str,
    elapsed_ms: int,
) -> RunResult:
    err = ErrorInfo(
        kind=ErrorKind.CANCELLED,
        rc=-3,
        rc_label="cancelled",
        message=(
            "Execution was cancelled before a completed Stata response was "
            f"received for session_id={session_id!r}."
        ),
        command=None,
        line=None,
        context=ErrorContext(before=[], failing="", after=[]),
        commands_executed=None,
        path=None,
        varname=None,
        name=None,
        suggestions=[],
    )
    return RunResult(
        ok=False,
        rc=-3,
        session_id=session_id,
        request_id=uuid.uuid4().hex,
        started_at=_utc_iso_ms(),
        elapsed_ms=elapsed_ms,
        stata_elapsed_ms=0,
        stata=StataInfo(
            version="unknown",
            edition=StataEdition.UNKNOWN,
            backend=Backend.PYSTATA,
        ),
        log=LogInfo(
            head="",
            tail="",
            lines_total=0,
            bytes_total=0,
            truncated=False,
            complete=True,
            error_window=None,
            ref=None,
        ),
        results=ResultsInfo(r=_empty_returns(), e=_empty_returns(), last_estimation_cmd=None),
        dataset=_empty_dataset(),
        graphs=[],
        warnings=[],
        error=err,
        schema_version="1.0",
        capabilities=["pystata", "subprocess_timeout", "cancel"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Module-level convenience: lazy default pool + pool_execute().
# ─────────────────────────────────────────────────────────────────────────────


_default_pool: SessionPool | None = None
_default_pool_lock = threading.Lock()


def get_default_pool() -> SessionPool:
    global _default_pool
    if _default_pool is not None:
        return _default_pool
    with _default_pool_lock:
        if _default_pool is None:
            _default_pool = SessionPool()
        return _default_pool


def pool_execute(
    code: str,
    *,
    session_id: str = "main",
    timeout_ms: int | None = 600_000,
    **options: Any,
) -> RunResult:
    """Drop-in replacement for `runner.execute()` that enforces `timeout_ms`.

    Routes through the module's default `SessionPool`. See `SessionPool.execute`
    for behavior.
    """
    return get_default_pool().execute(
        code, session_id=session_id, timeout_ms=timeout_ms, **options
    )


def pool_stata_info(
    *,
    session_id: str = "main",
    timeout_ms: int | None = 60_000,
) -> dict[str, Any]:
    """Query Stata info through the default subprocess pool."""
    return get_default_pool().stata_info(session_id=session_id, timeout_ms=timeout_ms)


def shutdown_default_pool() -> None:
    """Kill all default-pool workers. Useful for clean shutdown / tests."""
    global _default_pool
    with _default_pool_lock:
        if _default_pool is not None:
            _default_pool.shutdown()
            _default_pool = None


# Worker entry-point. `python -m stata_code.core._pool` lands here.
if __name__ == "__main__":
    sys.exit(_worker_main())
