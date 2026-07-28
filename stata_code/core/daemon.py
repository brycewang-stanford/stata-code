"""Resident session daemon — keeps Stata sessions warm across CLI invocations.

The subprocess pool (:mod:`stata_code.core._pool`) already keeps one worker
process per session alive, but a plain ``stata-code run`` *owns* that pool and
tears it down when the process exits. So two consecutive CLI calls cannot share
data in memory: the second one starts from an empty dataset.

This module moves the pool into a resident process behind a Unix domain socket.
``stata-code run --daemon`` connects, ships the same keyword arguments the
in-process path would have used, and gets the same :class:`RunResult` back — but
the Stata session, and everything loaded into it, outlives the client.

Transport is newline-delimited JSON over ``AF_UNIX``. There is no TCP listener:
the daemon executes arbitrary Stata code, so it is deliberately reachable only
through a mode-0600 socket inside a mode-0700 directory owned by the user.
"""

from __future__ import annotations

import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Wire protocol version. Bumped when the request/response shape changes in a way
# an older peer could misread; a mismatch is reported rather than guessed at.
PROTOCOL_VERSION = 1

# Newline-delimited JSON, so a single response must never contain a raw newline.
# ``json.dumps`` escapes newlines inside strings, which keeps that invariant.
_ENCODING = "utf-8"

# A client that connects but never finishes a request must not pin a thread.
_CLIENT_IDLE_S = 300.0

# Default: retire the daemon after 30 minutes of inactivity so a forgotten
# `daemon start` does not hold a Stata license slot indefinitely.
DEFAULT_IDLE_TIMEOUT_S = 1800.0

_IDLE_CHECK_INTERVAL_S = 5.0


class DaemonError(RuntimeError):
    """Base class for daemon transport failures."""


class DaemonNotRunning(DaemonError):
    """No daemon is listening on the socket."""


# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────


def unix_sockets_supported() -> bool:
    """Whether this platform exposes ``AF_UNIX`` to Python."""
    return hasattr(socket, "AF_UNIX")


def default_state_dir() -> Path:
    """Per-user directory holding the socket and pid file.

    Honors ``STATA_CODE_DAEMON_DIR``, then ``XDG_RUNTIME_DIR`` (which is already
    per-user and mode 0700 on Linux), else ``~/.stata-code``.
    """
    override = os.environ.get("STATA_CODE_DAEMON_DIR")
    if override:
        return Path(override).expanduser()
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "stata-code"
    return Path.home() / ".stata-code"


def _max_socket_path_len() -> int:
    """Usable ``sun_path`` bytes for this platform.

    ``sockaddr_un.sun_path`` is 104 bytes on the BSDs (macOS) and 108 on Linux,
    NUL terminator included. Bind fails outright past that, so paths are checked
    before use rather than discovered at bind time.
    """
    return 103 if sys.platform == "darwin" else 107


def _path_fits(path: Path) -> bool:
    return len(str(path).encode(_ENCODING)) <= _max_socket_path_len()


def _short_socket_path(intended: Path) -> Path:
    """A deterministic short path for when the natural location is too long.

    Derived from the intended path, so every entry point (``run``, ``status``,
    ``stop``) independently computes the same fallback and still finds the
    daemon.
    """
    import hashlib
    import tempfile

    digest = hashlib.sha256(str(intended).encode(_ENCODING)).hexdigest()[:10]
    uid = getattr(os, "getuid", lambda: 0)()
    return Path(tempfile.gettempdir()) / f"stata-code-{uid}" / f"{digest}.sock"


def default_socket_path() -> Path:
    """Socket path, overridable wholesale via ``STATA_CODE_DAEMON_SOCKET``.

    Falls back to a short ``/tmp`` path when the natural location would exceed
    the platform's ``sun_path`` limit.
    """
    override = os.environ.get("STATA_CODE_DAEMON_SOCKET")
    intended = Path(override).expanduser() if override else default_state_dir() / "daemon.sock"
    if _path_fits(intended):
        return intended
    return _short_socket_path(intended)


def _pid_path(socket_path: Path) -> Path:
    return socket_path.with_suffix(".pid")


def _log_path(socket_path: Path) -> Path:
    return socket_path.with_suffix(".log")


def _ensure_state_dir(socket_path: Path) -> None:
    """Create the socket's parent directory as owner-only."""
    parent = socket_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    try:
        parent.chmod(0o700)
    except OSError:
        # Best effort: an exotic filesystem may refuse chmod. The socket itself
        # still gets 0600 below, which is the control that matters.
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Client
# ─────────────────────────────────────────────────────────────────────────────


def _connect(socket_path: Path, timeout: float | None) -> socket.socket:
    if not unix_sockets_supported():
        raise DaemonError("the session daemon requires AF_UNIX, which this platform lacks")
    if not _path_fits(socket_path):
        raise DaemonError(
            f"socket path is {len(str(socket_path))} bytes, over this platform's "
            f"{_max_socket_path_len()}-byte limit: {socket_path}"
        )
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(socket_path))
    except (FileNotFoundError, ConnectionRefusedError) as exc:
        sock.close()
        raise DaemonNotRunning(f"no daemon listening at {socket_path}") from exc
    except OSError as exc:
        sock.close()
        raise DaemonError(f"could not connect to {socket_path}: {exc}") from exc
    return sock


def request(
    payload: dict[str, Any],
    *,
    socket_path: Path | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Send one request and return the decoded response envelope.

    Raises :class:`DaemonNotRunning` when nothing is listening, and
    :class:`DaemonError` on transport or protocol failures. A response carrying
    ``ok: false`` is returned as-is for the caller to interpret.
    """
    path = socket_path or default_socket_path()
    body = dict(payload)
    body.setdefault("protocol", PROTOCOL_VERSION)
    sock = _connect(path, timeout)
    try:
        with sock.makefile("rwb") as stream:
            stream.write(json.dumps(body).encode(_ENCODING) + b"\n")
            stream.flush()
            line = stream.readline()
    except TimeoutError as exc:  # a subclass of OSError, so it must come first
        raise DaemonError("timed out waiting for the daemon") from exc
    except OSError as exc:
        raise DaemonError(f"daemon transport failed: {exc}") from exc
    finally:
        sock.close()

    if not line:
        raise DaemonError("daemon closed the connection without responding")
    try:
        decoded = json.loads(line.decode(_ENCODING))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DaemonError("daemon sent a malformed response") from exc
    if not isinstance(decoded, dict):
        raise DaemonError("daemon sent a non-object response")
    return decoded


def is_running(socket_path: Path | None = None, *, timeout: float = 2.0) -> bool:
    """Whether a daemon answers a ``ping`` on the socket."""
    try:
        reply = request({"op": "ping"}, socket_path=socket_path, timeout=timeout)
    except DaemonError:
        return False
    return bool(reply.get("ok"))


def read_pid(socket_path: Path | None = None) -> int | None:
    """The pid recorded by a running daemon, if the pid file is readable."""
    path = _pid_path(socket_path or default_socket_path())
    try:
        return int(path.read_text(encoding=_ENCODING).strip())
    except (OSError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Server
# ─────────────────────────────────────────────────────────────────────────────


class _Handler(socketserver.StreamRequestHandler):
    timeout = _CLIENT_IDLE_S

    def handle(self) -> None:  # noqa: D102 - socketserver hook
        server: _Server = self.server  # type: ignore[assignment]
        try:
            line = self.rfile.readline()
        except OSError:  # includes TimeoutError from the handler's read timeout
            return
        if not line:
            return
        server.note_activity()
        try:
            payload = json.loads(line.decode(_ENCODING))
            if not isinstance(payload, dict):
                raise ValueError("request must be a JSON object")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._reply({"ok": False, "error": f"malformed request: {exc}"})
            return

        try:
            response = server.dispatch(payload)
        except Exception as exc:  # noqa: BLE001 - a handler fault must not kill the daemon
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self._reply(response)
        server.note_activity()

    def _reply(self, response: dict[str, Any]) -> None:
        response.setdefault("protocol", PROTOCOL_VERSION)
        try:
            self.wfile.write(json.dumps(response).encode(_ENCODING) + b"\n")
            self.wfile.flush()
        except OSError:
            # Client hung up mid-response; nothing useful left to do.
            pass


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, socket_path: Path, idle_timeout: float) -> None:
        self._socket_path = socket_path
        self._idle_timeout = idle_timeout
        self._last_activity = time.monotonic()
        self._activity_lock = threading.Lock()
        self._started_at = time.time()
        self._served = 0
        super().__init__(str(socket_path), _Handler)
        try:
            socket_path.chmod(0o600)
        except OSError:
            pass

    # -- activity bookkeeping ------------------------------------------------

    def note_activity(self) -> None:
        with self._activity_lock:
            self._last_activity = time.monotonic()

    def idle_seconds(self) -> float:
        with self._activity_lock:
            return time.monotonic() - self._last_activity

    # -- dispatch ------------------------------------------------------------

    def dispatch(self, payload: dict[str, Any]) -> dict[str, Any]:
        peer = payload.get("protocol")
        if peer is not None and peer != PROTOCOL_VERSION:
            return {
                "ok": False,
                "error": (
                    f"protocol mismatch: client speaks v{peer}, daemon speaks "
                    f"v{PROTOCOL_VERSION}. Restart the daemon "
                    f"(`stata-code daemon restart`)."
                ),
            }
        op = payload.get("op")
        handler = getattr(self, f"_op_{op}", None) if isinstance(op, str) else None
        if handler is None:
            return {"ok": False, "error": f"unknown op: {op!r}"}
        self._served += 1
        reply: dict[str, Any] = handler(payload)
        return reply

    def _op_ping(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "pid": os.getpid()}

    def _op_status(self, _payload: dict[str, Any]) -> dict[str, Any]:
        from stata_code.core._pool import get_default_pool

        note: str | None = None
        try:
            sessions = get_default_pool().list_session_info()
        except Exception as exc:  # noqa: BLE001 - status must never fail hard
            sessions = []
            note = f"{type(exc).__name__}: {exc}"
        return {
            "ok": True,
            "pid": os.getpid(),
            "socket": str(self._socket_path),
            "started_at": self._started_at,
            "uptime_s": round(time.time() - self._started_at, 3),
            "idle_s": round(self.idle_seconds(), 3),
            "idle_timeout_s": self._idle_timeout,
            "requests_served": self._served,
            "sessions": sessions,
            "note": note,
        }

    def _op_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        from stata_code import run

        code = payload.get("code")
        if not isinstance(code, str):
            return {"ok": False, "error": "run requires a string `code`"}
        options = payload.get("options") or {}
        if not isinstance(options, dict):
            return {"ok": False, "error": "`options` must be an object"}
        result = run(code, **options)
        return {"ok": True, "result": result.model_dump(mode="json")}

    def _op_get_graph(self, payload: dict[str, Any]) -> dict[str, Any]:
        from stata_code import RefNotFound, get_graph

        ref = payload.get("ref")
        if not isinstance(ref, str):
            return {"ok": False, "error": "get_graph requires a string `ref`"}
        try:
            return {"ok": True, "graph": get_graph(ref)}
        except RefNotFound as exc:
            return {"ok": False, "error": str(exc), "kind": "ref_not_found"}

    def _op_get_log(self, payload: dict[str, Any]) -> dict[str, Any]:
        from stata_code import RefNotFound
        from stata_code.core.runner import get_log

        ref = payload.get("ref")
        if not isinstance(ref, str):
            return {"ok": False, "error": "get_log requires a string `ref`"}
        try:
            return {"ok": True, "log": get_log(ref)}
        except RefNotFound as exc:
            return {"ok": False, "error": str(exc), "kind": "ref_not_found"}

    def _op_reset(self, payload: dict[str, Any]) -> dict[str, Any]:
        from stata_code import reset_session

        session_id = payload.get("session_id") or "main"
        if not isinstance(session_id, str):
            return {"ok": False, "error": "`session_id` must be a string"}
        return {"ok": True, "reset": bool(reset_session(session_id))}

    def _op_shutdown(self, _payload: dict[str, Any]) -> dict[str, Any]:
        # Must not block the handler thread: shutdown() waits for the serve loop.
        threading.Thread(target=self.shutdown, daemon=True).start()
        return {"ok": True, "stopping": True}


def _watchdog(server: _Server, idle_timeout: float, stop: threading.Event) -> None:
    """Retire the daemon once it has been idle for ``idle_timeout`` seconds."""
    if idle_timeout <= 0:
        return
    while not stop.wait(_IDLE_CHECK_INTERVAL_S):
        if server.idle_seconds() >= idle_timeout:
            server.shutdown()
            return


def _clear_stale_socket(socket_path: Path) -> None:
    """Remove a socket left behind by a daemon that died without cleaning up.

    A socket that still answers means a live daemon owns it, and we must not
    unlink it out from under that process.
    """
    if not socket_path.exists():
        return
    if is_running(socket_path, timeout=1.0):
        raise DaemonError(f"a daemon is already listening at {socket_path}")
    try:
        socket_path.unlink()
    except OSError as exc:
        raise DaemonError(f"could not remove stale socket {socket_path}: {exc}") from exc


def serve(
    *,
    socket_path: Path | None = None,
    idle_timeout: float = DEFAULT_IDLE_TIMEOUT_S,
) -> int:
    """Run the daemon in the foreground until stopped or idle. Returns an exit code."""
    if not unix_sockets_supported():
        print("error: the session daemon requires AF_UNIX", file=sys.stderr)
        return 2
    path = socket_path or default_socket_path()
    if not _path_fits(path):
        print(
            f"error: socket path is {len(str(path))} bytes, over this platform's "
            f"{_max_socket_path_len()}-byte limit: {path}",
            file=sys.stderr,
        )
        return 2
    _ensure_state_dir(path)
    _clear_stale_socket(path)

    previous_umask = os.umask(0o077)
    try:
        server = _Server(path, idle_timeout)
    finally:
        os.umask(previous_umask)

    pid_file = _pid_path(path)
    pid_file.write_text(f"{os.getpid()}\n", encoding=_ENCODING)

    stop = threading.Event()
    watchdog = threading.Thread(target=_watchdog, args=(server, idle_timeout, stop), daemon=True)
    watchdog.start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        for leftover in (pid_file, path):
            try:
                leftover.unlink()
            except OSError:
                pass
        from stata_code.core._pool import shutdown_default_pool

        shutdown_default_pool()
    return 0


def start_background(
    *,
    socket_path: Path | None = None,
    idle_timeout: float = DEFAULT_IDLE_TIMEOUT_S,
    wait_s: float = 20.0,
) -> tuple[bool, str]:
    """Spawn a detached daemon and wait for it to answer. Returns ``(ok, message)``."""
    if not unix_sockets_supported():
        return False, "the session daemon requires AF_UNIX, which this platform lacks"
    path = socket_path or default_socket_path()
    if is_running(path, timeout=1.0):
        return True, f"daemon already running at {path}"
    _ensure_state_dir(path)
    try:
        _clear_stale_socket(path)
    except DaemonError as exc:
        return False, str(exc)

    log_file = _log_path(path)
    cmd = [
        sys.executable,
        "-m",
        "stata_code.cli",
        "daemon",
        "start",
        "--foreground",
        "--socket",
        str(path),
        "--idle-timeout",
        str(idle_timeout),
    ]
    try:
        handle = open(log_file, "ab", buffering=0)  # noqa: SIM115 - owned by the child
    except OSError as exc:
        return False, f"could not open daemon log {log_file}: {exc}"
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=handle,
            start_new_session=True,
            env=os.environ.copy(),
        )
    except OSError as exc:
        handle.close()
        return False, f"could not spawn daemon: {exc}"
    finally:
        handle.close()

    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if is_running(path, timeout=1.0):
            return True, f"daemon listening at {path} (pid {proc.pid})"
        if proc.poll() is not None:
            tail = _tail_log(log_file)
            detail = f": {tail}" if tail else ""
            return False, f"daemon exited immediately (rc={proc.returncode}){detail}"
        time.sleep(0.1)
    return False, f"daemon did not come up within {wait_s:g}s; see {log_file}"


def _tail_log(log_file: Path, limit: int = 600) -> str:
    try:
        text = log_file.read_text(encoding=_ENCODING, errors="replace").strip()
    except OSError:
        return ""
    return text[-limit:]


def stop(*, socket_path: Path | None = None, wait_s: float = 10.0) -> tuple[bool, str]:
    """Ask a running daemon to exit. Returns ``(ok, message)``."""
    path = socket_path or default_socket_path()
    try:
        reply = request({"op": "shutdown"}, socket_path=path, timeout=5.0)
    except DaemonNotRunning:
        return False, f"no daemon running at {path}"
    except DaemonError as exc:
        return False, str(exc)
    if not reply.get("ok"):
        return False, str(reply.get("error", "daemon refused to stop"))

    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if not is_running(path, timeout=1.0):
            return True, f"daemon at {path} stopped"
        time.sleep(0.1)
    return False, f"daemon at {path} did not exit within {wait_s:g}s"


def status(*, socket_path: Path | None = None) -> dict[str, Any]:
    """Structured daemon status; ``running`` is False when nothing answers."""
    path = socket_path or default_socket_path()
    try:
        reply = request({"op": "status"}, socket_path=path, timeout=5.0)
    except DaemonNotRunning:
        return {"running": False, "socket": str(path)}
    except DaemonError as exc:
        return {"running": False, "socket": str(path), "error": str(exc)}
    if not reply.get("ok"):
        return {"running": False, "socket": str(path), "error": reply.get("error")}
    reply.pop("ok", None)
    reply.pop("protocol", None)
    return {"running": True, **reply}
