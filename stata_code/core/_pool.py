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
import inspect
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from queue import Empty, Queue
from typing import Any

from stata_code.core import _refs
from stata_code.core.errors import recovery_for, suggestions_for
from stata_code.core.policy import check as policy_check
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
    # After the protocol streams are open, fd 0/1 themselves are redirected
    # away from the protocol pipe so C-side Stata/pystata writes cannot leak
    # blank/banner lines into the parent's JSON reader.
    saved_stdin_fd = os.dup(0)
    saved_stdout_fd = os.dup(1)
    proto_in = os.fdopen(saved_stdin_fd, "r", buffering=1, encoding="utf-8")
    proto_out = os.fdopen(saved_stdout_fd, "w", buffering=1, encoding="utf-8")
    _redirect_standard_fd(0, os.O_RDONLY)
    _redirect_standard_fd(1, os.O_WRONLY)

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
                # Classify a malformed request BEFORE running it: an unknown
                # option name or bad arity is a caller error. This is done by
                # signature binding rather than by catching TypeError around
                # the call, because `execute()` collects results (returns,
                # dataset, estimation contract, graphs) without a blanket
                # guard — a TypeError from sfi handing back an unexpected type
                # mid-collection is a worker fault, and reporting that as
                # "invalid_request" would tell the agent its arguments were
                # wrong when nothing was wrong with them.
                try:
                    inspect.signature(execute).bind(code, **options)
                except TypeError as exc:
                    raise ValueError(f"bad execute options: {exc}") from exc
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
        except (ValueError, NotImplementedError) as exc:
            response = {
                "id": req_id,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "error_kind": "invalid_request",
            }
        except Exception as exc:  # noqa: BLE001
            response = {
                "id": req_id,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "error_kind": "worker_error",
            }
        proto_out.write(json.dumps(response) + "\n")
        proto_out.flush()
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Parent side: WorkerProcess + SessionPool + pool_execute().
# ─────────────────────────────────────────────────────────────────────────────


def _default_worker_cmd() -> list[str]:
    return [sys.executable, "-u", "-m", "stata_code.core._pool"]


def _redirect_standard_fd(fd: int, flags: int) -> None:
    """Point fd 0/1 at os.devnull after private protocol fds are duplicated."""
    try:
        devnull_fd = os.open(os.devnull, flags)
        try:
            os.dup2(devnull_fd, fd)
        finally:
            os.close(devnull_fd)
    except OSError:
        # Keep the worker usable even on an unusual platform where devnull
        # redirection fails; the parent still guards against blank noise.
        return


class _WorkerError(RuntimeError):
    """Raised on worker-side execution failure (non-Stata, e.g., crash)."""


class _WorkerReportedError(_WorkerError):
    """The worker itself responded ``ok=false`` (an unexpected exception
    inside the worker's request handler).

    Unlike a dead/corrupt worker, the subprocess is alive and its protocol
    stream is in a clean state — the pool reports the failure without
    killing the worker, so the session's loaded data survives.
    """


class _WorkerTimeout(TimeoutError):
    """Raised when a request exceeds its deadline. Parent kills the worker."""


class _WorkerBusy(_WorkerTimeout):
    """A status query could not acquire the worker lock in its budget.

    Distinct from a plain `_WorkerTimeout`: nothing was written to the
    worker, so its protocol stream is clean and the subprocess is healthy —
    it is merely mid-`execute()`. Callers must NOT kill a busy worker; doing
    so destroys the in-flight run and the session's loaded dataset. Subclasses
    `_WorkerTimeout` so existing `except _WorkerTimeout` handlers that only
    want to warn keep working.
    """


class _StderrTail:
    """Bounded, thread-safe tail buffer for a worker's drained stderr."""

    def __init__(self, max_chars: int = 4000) -> None:
        self._chunks: deque[str] = deque()
        self._total = 0
        self._max = max_chars
        self._lock = threading.Lock()

    def append(self, chunk: str) -> None:
        with self._lock:
            self._chunks.append(chunk)
            self._total += len(chunk)
            while self._total > self._max and len(self._chunks) > 1:
                self._total -= len(self._chunks.popleft())

    def text(self) -> str:
        with self._lock:
            return "".join(self._chunks)[-self._max :]


class _StdoutPump:
    """One long-lived reader thread per worker process, feeding a queue.

    The previous design spawned a fresh `readline()` thread per request and
    abandoned it on deadline overrun. An abandoned thread stays blocked on the
    pipe and consumes the NEXT request's response — so a status query that
    timed out while the worker was still initializing would silently eat the
    reply to the following `execute()`, which then burned its full timeout and
    got a healthy worker killed. A single pump per process cannot orphan:
    a timed-out read simply leaves the line in the queue, where the stale-id
    filter in `_read_json_response_with_deadline` discards it.
    """

    __slots__ = ("queue", "_thread")

    def __init__(self, stream: Any) -> None:
        self.queue: Queue[str | BaseException] = Queue()
        self._thread = threading.Thread(target=self._run, args=(stream,), daemon=True)
        self._thread.start()

    def _run(self, stream: Any) -> None:
        try:
            for line in iter(stream.readline, ""):
                self.queue.put(line)
        except BaseException as exc:  # noqa: BLE001
            self.queue.put(exc)
        finally:
            # Sentinel: EOF. `""` can never be a real line (iter stops on it).
            self.queue.put("")


def _ensure_stdout_pump(proc: subprocess.Popen[str]) -> _StdoutPump:
    """Attach a `_StdoutPump` to `proc` on first use, then reuse it.

    Lazy rather than spawn-time so that directly-injected fake processes
    (tests, in-process harnesses) get the same non-orphaning read path.
    """
    pump: _StdoutPump | None = getattr(proc, "_stata_code_stdout_pump", None)
    if pump is None:
        assert proc.stdout is not None
        pump = _StdoutPump(proc.stdout)
        proc._stata_code_stdout_pump = pump  # type: ignore[attr-defined]
    return pump


def _drain_stderr(stream: Any, tail: _StderrTail) -> None:
    """Continuously consume a worker's stderr pipe.

    Runs in a daemon thread for the worker's lifetime. Without this, a worker
    whose cumulative stderr output (pystata banners, Stata C-side messages,
    Python warnings) exceeds the OS pipe buffer (~64 KB on Linux) blocks in
    the stderr write and never sends its stdout response — surfacing as a
    spurious "timeout" against a healthy worker. readline() keeps consuming
    from the pipe even for very long lines, so the pipe can never fill.
    """
    try:
        for line in stream:
            tail.append(line)
    except Exception:  # noqa: BLE001 - reader must never propagate
        pass
    finally:
        try:
            stream.close()
        except Exception:  # noqa: BLE001
            pass


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
        # True while an execute() request is in flight. Read by the pool's
        # LRU eviction to avoid killing a worker mid-run.
        self.busy: bool = False

    def _spawn(self) -> subprocess.Popen[str]:
        env = os.environ.copy()
        # Force unbuffered I/O even if PYTHONUNBUFFERED isn't already set.
        env.setdefault("PYTHONUNBUFFERED", "1")
        proc = subprocess.Popen(
            self._cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered
            env=env,
        )
        # Drain stderr for the worker's lifetime (see _drain_stderr). The
        # tail/thread ride on the Popen object so the module-level helpers
        # that only receive `proc` can find them.
        if proc.stderr is not None:
            tail = _StderrTail()
            thread = threading.Thread(
                target=_drain_stderr, args=(proc.stderr, tail), daemon=True
            )
            proc._stata_code_stderr_tail = tail  # type: ignore[attr-defined]
            proc._stata_code_stderr_thread = thread  # type: ignore[attr-defined]
            thread.start()
        return proc

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
        killing the worker), `_WorkerBusy` when the worker is already running
        another request and the wait would exceed `timeout_ms` (nothing was
        written, so the caller must NOT kill it), `_WorkerError` on protocol or
        worker-side crash.

        ``timeout_ms`` budgets the WHOLE call, queueing included. Stata is
        single-threaded per session, so a second request for the same session
        waits behind the first; taking the deadline only after the lock was
        acquired meant a call queued behind a 10-minute bootstrap blocked
        forever no matter what timeout the caller asked for.
        """
        deadline: float | None = None
        if timeout_ms is None:
            acquired = self._lock.acquire()
        else:
            deadline = time.monotonic() + timeout_ms / 1000.0
            acquired = self._lock.acquire(timeout=max(0.0, deadline - time.monotonic()))
        if not acquired:
            raise _WorkerBusy(
                f"worker for {self.session_id!r} is busy with another request; "
                f"waited {timeout_ms} ms for it to free up"
            )
        try:
            proc = self._ensure_alive()
            assert proc.stdin is not None and proc.stdout is not None  # for mypy
            req_id = uuid.uuid4().hex
            request = {"id": req_id, "op": "execute", "code": code, "options": options}
            # Mark the request window: `busy` keeps LRU eviction away from a
            # mid-run worker, and bumping `last_used` at request START (not
            # just completion) keeps a long-running request from looking like
            # the least-recently-used entry.
            self.busy = True
            self.last_used = time.monotonic()
            try:
                try:
                    proc.stdin.write(json.dumps(request) + "\n")
                    proc.stdin.flush()
                except BrokenPipeError as exc:
                    raise _WorkerError(f"worker pipe broken on write: {exc}") from exc

                response = self._read_json_response_with_deadline(
                    proc, deadline, expect_id=req_id
                )
                self.last_used = time.monotonic()
                if not response.get("ok"):
                    if response.get("error_kind") == "invalid_request":
                        raise ValueError(response.get("error", "<no error>"))
                    raise _WorkerReportedError(
                        f"worker reported failure: {response.get('error', '<no error>')}"
                    )
                return response["result"], response.get("ref_blobs", {})
            finally:
                self.busy = False
        finally:
            self._lock.release()

    @staticmethod
    def _readline_with_deadline(
        proc: subprocess.Popen[str],
        deadline: float | None,
    ) -> str:
        """Read one line from `proc.stdout` honoring an optional wall-clock
        deadline. Raises `_WorkerTimeout` on overrun.

        Reads come off the worker's single long-lived `_StdoutPump` thread
        (see that class): polling a queue is portable (no select on Windows
        pipes) and, crucially, a timed-out read orphans nothing — the pump
        keeps ownership of the pipe, so a late reply lands in the queue for
        the stale-id filter to discard rather than being handed to the next
        request as if it were that request's response.
        """
        pump = _ensure_stdout_pump(proc)

        while True:
            if deadline is None:
                remaining = None
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _WorkerTimeout("deadline exceeded waiting for worker response")
            try:
                item = pump.queue.get(
                    timeout=min(_POLL_INTERVAL_S, remaining)
                    if remaining is not None
                    else _POLL_INTERVAL_S
                )
            except Empty:
                # No line yet. If the process died, surface that; otherwise
                # keep waiting against the deadline. A dead process still
                # gets one more loop to pick up the pump's EOF sentinel and
                # any final buffered line ahead of it.
                if proc.poll() is not None:
                    try:
                        item = pump.queue.get(timeout=0.1)
                    except Empty:
                        raise _WorkerError(
                            _worker_exit_message(proc, proc.returncode)
                        ) from None
                else:
                    continue
            if isinstance(item, BaseException):
                raise _WorkerError(f"reader thread error: {item!r}")
            if not item:
                # EOF sentinel — worker exited or pipe closed unexpectedly.
                raise _WorkerError(_worker_exit_message(proc, proc.poll()))
            return item

    @classmethod
    def _read_json_response_with_deadline(
        cls,
        proc: subprocess.Popen[str],
        deadline: float | None,
        expect_id: str | None = None,
    ) -> dict[str, Any]:
        """Read the next non-blank JSON worker response.

        A few Stata/pystata builds emit a lone newline while initializing. That
        line is protocol noise, not a response, so tolerate blank lines only.
        Non-empty non-JSON still indicates real protocol corruption.

        When `expect_id` is given, responses carrying a different id are the
        late replies of an earlier request that already timed out. They are
        discarded and the read continues against the same deadline — matching
        them against the current request would either mis-attribute a stale
        result or raise a spurious protocol error.
        """
        while True:
            line = cls._readline_with_deadline(proc, deadline)
            if not line.strip():
                continue
            try:
                response = json.loads(line)
            except json.JSONDecodeError as exc:
                raise _WorkerError(f"worker emitted non-JSON: {line!r}") from exc
            if not isinstance(response, dict):
                raise _WorkerError(f"worker emitted non-object JSON: {line!r}")
            if expect_id is not None and response.get("id") != expect_id:
                continue
            return response

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

        The worker lock is held by an in-flight ``execute()`` for the whole
        run, so acquiring it counts against ``timeout_ms`` too — a status
        query must not hang for the duration of a long Stata run. Lock
        acquisition failure raises :class:`_WorkerBusy`, which callers must
        NOT treat as a dead worker: nothing was written, the stream is clean,
        and the subprocess is healthy.

        ``timeout_ms`` is the budget for the whole call. The deadline is fixed
        up front and the lock wait is deducted from it, so the worst case is
        ``timeout_ms`` total rather than ``timeout_ms`` for the lock plus
        another ``timeout_ms`` for the read.
        """
        start = time.monotonic()
        deadline: float | None = (
            None if timeout_ms is None else start + timeout_ms / 1000.0
        )
        if timeout_ms is None:
            acquired = self._lock.acquire()
        else:
            acquired = self._lock.acquire(timeout=timeout_ms / 1000.0)
        if not acquired:
            raise _WorkerBusy(
                f"worker for {self.session_id!r} is busy (request in flight)"
            )
        try:
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

            response = self._read_json_response_with_deadline(
                proc, deadline, expect_id=req_id
            )
            self.last_used = time.monotonic()

            if not response.get("ok"):
                if response.get("error_kind") == "invalid_request":
                    raise ValueError(response.get("error", "<no error>"))
                raise _WorkerError(
                    f"worker reported failure: {response.get('error', '<no error>')}"
                )
            return dict(response)
        finally:
            self._lock.release()

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
            # Same identity guard as the slow path below: a concurrent
            # execute() may have already respawned into self._proc while we
            # were looking at the dead one, and clearing it would strand a
            # live subprocess with a request in flight.
            if self._proc is proc:
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
        # Drain any buffered stderr so we don't leak a fd. When a drain
        # thread is attached it owns the stream (it hits EOF and closes it);
        # reading here too would race it.
        if getattr(proc, "_stata_code_stderr_tail", None) is None:
            try:
                if proc.stderr is not None:
                    proc.stderr.read()
            except Exception:  # noqa: BLE001
                pass
        if self._proc is proc:
            self._proc = None


def _worker_exit_message(proc: subprocess.Popen[str], rc: int | None) -> str:
    if rc is None:
        try:
            rc = proc.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            pass
    message = f"worker exited (returncode={rc}) before responding"
    if rc is None:
        return message
    stderr = _read_process_stderr_tail(proc)
    if not stderr:
        return message
    return f"{message}; stderr: {stderr}"


def _read_process_stderr_tail(
    proc: subprocess.Popen[str],
    *,
    max_chars: int = 4000,
) -> str:
    """Best-effort stderr tail for a worker that has already exited.

    Pool-spawned workers carry a drain thread (see ``_drain_stderr``) whose
    bounded tail is the source of truth; give it a beat to consume the final
    bytes. Bare procs (tests, external callers) fall back to a direct read.
    """
    tail: _StderrTail | None = getattr(proc, "_stata_code_stderr_tail", None)
    if tail is not None:
        thread: threading.Thread | None = getattr(proc, "_stata_code_stderr_thread", None)
        if thread is not None:
            thread.join(timeout=0.2)
        return tail.text().strip()[-max_chars:]
    if proc.stderr is None:
        return ""
    try:
        text: str = proc.stderr.read()
    except Exception:  # noqa: BLE001
        return ""
    return text.strip()[-max_chars:]


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
                victims = self._evict_to_capacity_locked(keep=session_id)
            else:
                victims = []
        # Kill outside the pool lock: kill() can block for the SIGTERM/SIGKILL
        # grace periods, which would stall every other pool operation.
        for victim in victims:
            victim.kill()
        return w

    def _evict_to_capacity_locked(self, *, keep: str) -> list[WorkerProcess]:
        """Pop LRU workers beyond capacity; return them for the caller to kill.

        Never evicts the just-added worker or a worker with a request in
        flight — `last_used` is stale for a busy worker, so without the guard
        the longest-running request would be the preferred victim and an
        unrelated session starting up would abort it mid-run. If every other
        worker is busy the pool temporarily exceeds capacity instead.
        """
        if len(self._workers) <= self._capacity:
            return []
        candidates = sorted(
            ((sid, w) for sid, w in self._workers.items() if sid != keep and not w.busy),
            key=lambda kv: kv[1].last_used,
        )
        victims: list[WorkerProcess] = []
        while len(self._workers) > self._capacity and candidates:
            sid, w = candidates.pop(0)
            self._workers.pop(sid, None)
            victims.append(w)
        return victims

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
        # Command-safety gate: reject OS-escape / file-deletion commands before
        # a worker is spawned or any Stata state is touched. Parent-side so the
        # blocked run costs nothing; the worker enforces the same policy as
        # defense-in-depth for direct `runner.execute` callers.
        policy_block = policy_check(code, session_id=session_id)
        if policy_block is not None:
            return policy_block
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
        # A cancel could land in the gap between the first `_consume_cancel`
        # and now — `request_cancel` adds to `_cancel_pending` but cannot
        # observe a worker that does not yet exist. Re-check here so a
        # cancel issued during spawn fires on THIS call, not the next one.
        # The freshly spawned worker is intentionally LEFT in `_workers`
        # untouched: it never received any code, so it is in the same
        # clean state as a worker that has never run; killing it would
        # only force the next `execute()` to pay the pystata-init cost
        # again. Reuse over churn.
        if self._consume_cancel(session_id):
            return _build_cancelled_result(
                session_id=session_id,
                elapsed_ms=max(1, int((time.monotonic() - started) * 1000)),
            )
        try:
            result_dict, ref_blobs = worker.execute(code, worker_options, timeout_ms=timeout_ms)
        except _WorkerBusy:
            # Healthy worker, mid-run on an earlier request. Nothing was
            # written to it, so it must NOT be killed — that would abort the
            # in-flight run and wipe the session's loaded data. Report the
            # contention instead; the caller can wait, use another session, or
            # send the long job to the background.
            return _build_busy_result(
                session_id=session_id,
                elapsed_ms=max(1, int((time.monotonic() - started) * 1000)),
                timeout_ms=timeout_ms or 0,
            )
        except _WorkerTimeout:
            worker.kill()
            with self._lock:
                # Only remove the handle we actually killed — a newer worker
                # registered for this session in the interim must survive.
                if self._workers.get(session_id) is worker:
                    self._workers.pop(session_id, None)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return _build_timeout_result(
                session_id=session_id,
                elapsed_ms=elapsed_ms,
                timeout_ms=timeout_ms or 0,
            )
        except _WorkerReportedError as exc:
            # The worker is alive and answered with a well-formed failure —
            # report it without killing the worker, so the session's loaded
            # data and r()/e() state survive (e.g. an argument typo must not
            # wipe the user's whole Stata session).
            cancelled = self._consume_cancel(session_id)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if cancelled:
                return _build_cancelled_result(
                    session_id=session_id,
                    elapsed_ms=max(1, elapsed_ms),
                    aborted_mid_run=True,
                )
            return _build_adapter_crash_result(
                session_id=session_id,
                elapsed_ms=elapsed_ms,
                message=str(exc),
            )
        except _WorkerError as exc:
            cancelled = self._consume_cancel(session_id)
            worker.kill()
            with self._lock:
                # Only remove the handle we actually killed — a newer worker
                # registered for this session in the interim must survive.
                if self._workers.get(session_id) is worker:
                    self._workers.pop(session_id, None)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if cancelled:
                return _build_cancelled_result(
                    session_id=session_id,
                    elapsed_ms=max(1, elapsed_ms),
                    aborted_mid_run=True,
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

        Backward-compatible flat-list view. Returns just the sessions list;
        any per-worker failures are silently dropped. Use
        :meth:`list_session_info_detailed` when callers need to know whether
        the result is partial.
        """
        return self.list_session_info_detailed(per_worker_timeout_ms=per_worker_timeout_ms)[
            "sessions"
        ]

    def list_session_info_detailed(
        self,
        *,
        per_worker_timeout_ms: int = 5000,
    ) -> dict[str, list[dict[str, Any]]]:
        """Same as :meth:`list_session_info` but surfaces partial failures.

        Returns ``{"sessions": [...], "warnings": [...]}``. Each warning is
        ``{"session_id": str, "reason": str}`` describing a worker that
        failed to respond to ``list_sessions`` within
        ``per_worker_timeout_ms`` or raised a protocol error. Without this
        view, a caller could not distinguish "no other sessions" from
        "some workers timed out".
        """
        with self._lock:
            workers = list(self._workers.items())
        sessions: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        seen: set[str] = set()
        for sid, worker in workers:
            if not worker.is_alive():
                continue
            try:
                response = worker.send_simple_op("list_sessions", timeout_ms=per_worker_timeout_ms)
            except _WorkerTimeout:
                warnings.append({"session_id": sid, "reason": "timeout"})
                continue
            except _WorkerError as exc:
                warnings.append({"session_id": sid, "reason": f"worker_error: {exc}"})
                continue
            for entry in response.get("sessions") or []:
                inner_sid = entry.get("session_id")
                if inner_sid is None or inner_sid in seen:
                    continue
                seen.add(inner_sid)
                sessions.append(entry)
        return {"sessions": sessions, "warnings": warnings}

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
            response = worker.send_simple_op("stata_info", timeout_ms=timeout_ms, spawn=True)
        except _WorkerBusy:
            # Healthy worker, mid-run. Killing it here would SIGTERM the user's
            # in-flight Stata command and wipe the session's loaded data — for a
            # read-only status query. Surface the busy signal instead.
            raise
        except (_WorkerError, _WorkerTimeout):
            worker.kill()
            with self._lock:
                # Only drop OUR handle: a concurrent execute() may have already
                # noticed the death, spawned a replacement and registered it.
                if self._workers.get(session_id) is worker:
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
        recovery=recovery_for(ErrorKind.TIMEOUT),
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


def _build_busy_result(
    *,
    session_id: str,
    elapsed_ms: int,
    timeout_ms: int,
) -> RunResult:
    """Synthesize the rc=-5 result for a session whose worker is mid-run.

    Distinct from a timeout: nothing was submitted to Stata, the worker is
    healthy, and the earlier run is still going. Retrying the identical code
    later succeeds, so `recovery.retriable` is true and no code change is
    implied.
    """
    err = ErrorInfo(
        kind=ErrorKind.SESSION_BUSY,
        rc=-5,
        rc_label="session_busy",
        message=(
            f"Session {session_id!r} is already executing an earlier request; "
            f"waited {timeout_ms} ms for it to finish. Nothing was submitted to "
            "Stata by this call."
        ),
        command=None,
        line=None,
        context=ErrorContext(before=[], failing="", after=[]),
        commands_executed=0,
        path=None,
        varname=None,
        name=None,
        suggestions=suggestions_for(ErrorKind.SESSION_BUSY),
        recovery=recovery_for(ErrorKind.SESSION_BUSY),
    )
    return RunResult(
        ok=False,
        rc=-5,
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
        capabilities=["pystata", "subprocess_timeout", "background_runs"],
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
        recovery=recovery_for(ErrorKind.ADAPTER_CRASH),
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
    aborted_mid_run: bool = False,
) -> RunResult:
    # Pre-run cancel: worker never received code, so the empty log is genuinely
    # final. Mid-run cancel: worker was killed while Stata may have been
    # emitting output we discarded, so match timeout/crash semantics.
    log_complete = not aborted_mid_run
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
        recovery=recovery_for(ErrorKind.CANCELLED),
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
            complete=log_complete,
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
    return get_default_pool().execute(code, session_id=session_id, timeout_ms=timeout_ms, **options)


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
