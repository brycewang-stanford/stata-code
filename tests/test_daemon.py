"""Tests for the resident session daemon.

None of these need a Stata install: the transport, lifecycle, and path handling
are exercised directly, and the one ``run`` round-trip monkeypatches
``stata_code.run`` so the daemon's dispatch is tested without a live session.

Sockets go under a deliberately short ``/tmp`` directory because ``sun_path`` is
capped at ~104 bytes and pytest's ``tmp_path`` is routinely longer than that on
macOS — the very constraint :func:`daemon.default_socket_path` works around.
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest

from stata_code.cli import main
from stata_code.core import daemon
from stata_code.core.schema import (
    Backend,
    ResultsInfo,
    RunResult,
    StataEdition,
    StataInfo,
    StataReturns,
)

pytestmark = pytest.mark.skipif(
    not daemon.unix_sockets_supported(), reason="daemon requires AF_UNIX"
)


def _result(ok: bool = True) -> RunResult:
    return RunResult(
        ok=ok,
        rc=0 if ok else 111,
        session_id="main",
        request_id="daemon-1",
        started_at="2026-07-28T10:00:00.000Z",
        elapsed_ms=5,
        stata=StataInfo(version="18.0", edition=StataEdition.MP, backend=Backend.PYSTATA),
        results=ResultsInfo(e=StataReturns(macros={"cmd": "regress"})),
    )


@pytest.fixture
def sock_path() -> Path:
    """A socket path short enough to bind, cleaned up afterwards."""
    directory = Path(tempfile.mkdtemp(prefix="sc-", dir="/tmp"))
    path = directory / "d.sock"
    yield path
    for leftover in (path, path.with_suffix(".pid"), path.with_suffix(".log")):
        try:
            leftover.unlink()
        except OSError:
            pass
    try:
        directory.rmdir()
    except OSError:
        pass


@pytest.fixture
def running_daemon(sock_path, monkeypatch):
    """Serve in a background thread and yield the socket path."""
    # The daemon shuts the shared pool down when it exits; in-process that would
    # evict workers other tests are reusing, so neutralize it here.
    monkeypatch.setattr("stata_code.core._pool.shutdown_default_pool", lambda: None, raising=True)
    thread = threading.Thread(
        target=daemon.serve, kwargs={"socket_path": sock_path, "idle_timeout": 0}, daemon=True
    )
    thread.start()
    _wait_until(lambda: daemon.is_running(sock_path, timeout=0.5), timeout=15.0)
    yield sock_path
    try:
        daemon.request({"op": "shutdown"}, socket_path=sock_path, timeout=5.0)
    except daemon.DaemonError:
        pass
    thread.join(timeout=10.0)


def _wait_until(predicate, *, timeout: float, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# path handling
# ─────────────────────────────────────────────────────────────────────────────


def test_default_socket_path_honors_explicit_override(monkeypatch) -> None:
    monkeypatch.setenv("STATA_CODE_DAEMON_SOCKET", "/tmp/sc-override.sock")
    assert daemon.default_socket_path() == Path("/tmp/sc-override.sock")


def test_default_socket_path_falls_back_when_too_long(monkeypatch) -> None:
    too_long = "/tmp/" + ("x" * 200) + "/daemon.sock"
    monkeypatch.setenv("STATA_CODE_DAEMON_SOCKET", too_long)

    resolved = daemon.default_socket_path()

    assert resolved != Path(too_long)
    assert daemon._path_fits(resolved)  # noqa: SLF001
    assert resolved.suffix == ".sock"


def test_long_path_fallback_is_deterministic(monkeypatch) -> None:
    too_long = "/tmp/" + ("y" * 200) + "/daemon.sock"
    monkeypatch.setenv("STATA_CODE_DAEMON_SOCKET", too_long)

    # Every entry point recomputes the path independently, so the same intended
    # location must always map to the same fallback or clients lose the daemon.
    assert daemon.default_socket_path() == daemon.default_socket_path()


def test_connect_rejects_oversized_explicit_path() -> None:
    with pytest.raises(daemon.DaemonError, match="over this platform"):
        daemon.request({"op": "ping"}, socket_path=Path("/tmp/" + "z" * 200 + ".sock"))


def test_state_dir_prefers_env_override(monkeypatch) -> None:
    monkeypatch.setenv("STATA_CODE_DAEMON_DIR", "/tmp/sc-state")
    monkeypatch.delenv("STATA_CODE_DAEMON_SOCKET", raising=False)
    assert daemon.default_state_dir() == Path("/tmp/sc-state")


# ─────────────────────────────────────────────────────────────────────────────
# transport
# ─────────────────────────────────────────────────────────────────────────────


def test_is_running_false_when_nothing_listens(sock_path) -> None:
    assert daemon.is_running(sock_path, timeout=1.0) is False


def test_request_raises_not_running_when_absent(sock_path) -> None:
    with pytest.raises(daemon.DaemonNotRunning):
        daemon.request({"op": "ping"}, socket_path=sock_path, timeout=1.0)


def test_ping_round_trip(running_daemon) -> None:
    reply = daemon.request({"op": "ping"}, socket_path=running_daemon, timeout=5.0)

    assert reply["ok"] is True
    assert reply["pid"] == os.getpid()
    assert reply["protocol"] == daemon.PROTOCOL_VERSION


def test_status_reports_uptime_and_socket(running_daemon) -> None:
    report = daemon.status(socket_path=running_daemon)

    assert report["running"] is True
    assert report["socket"] == str(running_daemon)
    assert report["uptime_s"] >= 0
    assert report["requests_served"] >= 1


def test_unknown_op_is_rejected_without_killing_the_daemon(running_daemon) -> None:
    reply = daemon.request({"op": "nope"}, socket_path=running_daemon, timeout=5.0)
    assert reply["ok"] is False
    assert "unknown op" in reply["error"]

    # Still serving afterwards.
    assert daemon.is_running(running_daemon, timeout=2.0)


def test_protocol_mismatch_is_reported(running_daemon) -> None:
    reply = daemon.request(
        {"op": "ping", "protocol": daemon.PROTOCOL_VERSION + 99},
        socket_path=running_daemon,
        timeout=5.0,
    )

    assert reply["ok"] is False
    assert "protocol mismatch" in reply["error"]


def test_malformed_request_is_rejected(running_daemon) -> None:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    sock.connect(str(running_daemon))
    try:
        with sock.makefile("rwb") as stream:
            stream.write(b"{not json at all\n")
            stream.flush()
            line = stream.readline()
    finally:
        sock.close()

    reply = json.loads(line.decode("utf-8"))
    assert reply["ok"] is False
    assert "malformed request" in reply["error"]


def test_handler_exception_becomes_an_error_reply(running_daemon, monkeypatch) -> None:
    def _boom(_self, _payload):  # bound as a method on the live server instance
        raise RuntimeError("kaboom")

    # Patch the class so the live server instance picks it up. `status` rather
    # than `ping`, because `is_running` below probes with `ping`.
    monkeypatch.setattr(daemon._Server, "_op_status", _boom, raising=True)  # noqa: SLF001

    reply = daemon.request({"op": "status"}, socket_path=running_daemon, timeout=5.0)

    assert reply["ok"] is False
    assert "kaboom" in reply["error"]
    # A faulting handler must not take the daemon down with it.
    assert daemon.is_running(running_daemon, timeout=2.0)


# ─────────────────────────────────────────────────────────────────────────────
# run dispatch
# ─────────────────────────────────────────────────────────────────────────────


def test_run_op_returns_a_serialized_result(running_daemon, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_run(code: str, **options):
        captured["code"] = code
        captured["options"] = options
        return _result()

    monkeypatch.setattr("stata_code.run", _fake_run, raising=True)

    reply = daemon.request(
        {"op": "run", "code": "sysuse auto", "options": {"session_id": "s1"}},
        socket_path=running_daemon,
        timeout=10.0,
    )

    assert reply["ok"] is True
    assert captured["code"] == "sysuse auto"
    assert captured["options"] == {"session_id": "s1"}
    # The client reconstructs a real RunResult from the wire payload.
    assert RunResult.model_validate(reply["result"]).rc == 0


def test_run_op_requires_string_code(running_daemon) -> None:
    reply = daemon.request({"op": "run", "code": 42}, socket_path=running_daemon, timeout=5.0)
    assert reply["ok"] is False
    assert "string `code`" in reply["error"]


def test_run_op_rejects_non_object_options(running_daemon) -> None:
    reply = daemon.request(
        {"op": "run", "code": "di 1", "options": ["nope"]},
        socket_path=running_daemon,
        timeout=5.0,
    )
    assert reply["ok"] is False
    assert "must be an object" in reply["error"]


def test_get_graph_missing_ref_reports_kind(running_daemon, monkeypatch) -> None:
    from stata_code import RefNotFound

    def _missing(_ref: str):
        raise RefNotFound("graph://nope")

    monkeypatch.setattr("stata_code.get_graph", _missing, raising=True)

    reply = daemon.request(
        {"op": "get_graph", "ref": "graph://nope"}, socket_path=running_daemon, timeout=5.0
    )

    assert reply["ok"] is False
    assert reply["kind"] == "ref_not_found"


# ─────────────────────────────────────────────────────────────────────────────
# lifecycle
# ─────────────────────────────────────────────────────────────────────────────


def test_serve_writes_and_removes_the_pid_file(running_daemon) -> None:
    pid_file = running_daemon.with_suffix(".pid")
    assert pid_file.exists()
    assert daemon.read_pid(running_daemon) == os.getpid()


def test_shutdown_op_stops_the_daemon(sock_path, monkeypatch) -> None:
    monkeypatch.setattr("stata_code.core._pool.shutdown_default_pool", lambda: None, raising=True)
    thread = threading.Thread(
        target=daemon.serve, kwargs={"socket_path": sock_path, "idle_timeout": 0}, daemon=True
    )
    thread.start()
    assert _wait_until(lambda: daemon.is_running(sock_path, timeout=0.5), timeout=15.0)

    ok, message = daemon.stop(socket_path=sock_path)

    assert ok, message
    thread.join(timeout=10.0)
    assert not thread.is_alive()
    # The socket and pid file are cleaned up on the way out.
    assert not sock_path.exists()
    assert not sock_path.with_suffix(".pid").exists()


def test_stop_reports_when_nothing_is_running(sock_path) -> None:
    ok, message = daemon.stop(socket_path=sock_path)
    assert ok is False
    assert "no daemon running" in message


def test_idle_timeout_retires_the_daemon(sock_path, monkeypatch) -> None:
    monkeypatch.setattr("stata_code.core._pool.shutdown_default_pool", lambda: None, raising=True)
    # The watchdog polls on a 5s cadence in production; tighten it so the test
    # observes the retirement without a multi-second sleep.
    monkeypatch.setattr(daemon, "_IDLE_CHECK_INTERVAL_S", 0.05, raising=True)

    thread = threading.Thread(
        target=daemon.serve,
        kwargs={"socket_path": sock_path, "idle_timeout": 0.2},
        daemon=True,
    )
    thread.start()
    assert _wait_until(lambda: daemon.is_running(sock_path, timeout=0.5), timeout=15.0)

    thread.join(timeout=15.0)

    assert not thread.is_alive(), "daemon should retire itself once idle"
    assert not sock_path.exists()


def test_stale_socket_is_cleared_before_binding(sock_path, monkeypatch) -> None:
    # Simulate a daemon that died without unlinking its socket: a socket file
    # exists but nothing answers on it.
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(sock_path))
    stale.close()  # bound-then-closed leaves the inode behind
    assert sock_path.exists()

    monkeypatch.setattr("stata_code.core._pool.shutdown_default_pool", lambda: None, raising=True)
    thread = threading.Thread(
        target=daemon.serve, kwargs={"socket_path": sock_path, "idle_timeout": 0}, daemon=True
    )
    thread.start()
    try:
        assert _wait_until(lambda: daemon.is_running(sock_path, timeout=0.5), timeout=15.0)
    finally:
        daemon.request({"op": "shutdown"}, socket_path=sock_path, timeout=5.0)
        thread.join(timeout=10.0)


def test_clear_stale_socket_refuses_to_unlink_a_live_daemon(running_daemon) -> None:
    with pytest.raises(daemon.DaemonError, match="already listening"):
        daemon._clear_stale_socket(running_daemon)  # noqa: SLF001


def test_status_of_absent_daemon_is_not_running(sock_path) -> None:
    report = daemon.status(socket_path=sock_path)
    assert report == {"running": False, "socket": str(sock_path)}


# ─────────────────────────────────────────────────────────────────────────────
# CLI surface
# ─────────────────────────────────────────────────────────────────────────────


def test_cli_daemon_status_reports_absent(sock_path, capsys) -> None:
    rc = main(["daemon", "status", "--socket", str(sock_path)])

    assert rc == 1
    out = capsys.readouterr().out
    assert "not running" in out
    assert "stata-code daemon start" in out


def test_cli_daemon_status_json(sock_path, capsys) -> None:
    rc = main(["daemon", "status", "--socket", str(sock_path), "--json"])

    assert rc == 1
    assert json.loads(capsys.readouterr().out) == {
        "running": False,
        "socket": str(sock_path),
    }


def test_cli_daemon_status_of_live_daemon(running_daemon, capsys) -> None:
    rc = main(["daemon", "status", "--socket", str(running_daemon)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "running" in out
    assert f"pid={os.getpid()}" in out


def test_cli_daemon_stop_without_daemon(sock_path, capsys) -> None:
    rc = main(["daemon", "stop", "--socket", str(sock_path)])

    assert rc == 1
    assert "no daemon running" in capsys.readouterr().err


def test_cli_daemon_without_subcommand_prints_help(capsys) -> None:
    rc = main(["daemon"])

    assert rc == 0
    # argparse re-wraps the description, so assert on the stable usage line.
    assert "{start,stop,restart,status}" in capsys.readouterr().out


def test_cli_run_daemon_rejects_console_backend(capsys) -> None:
    # The console backend is stateless batch execution, so pinning it to a
    # daemon would silently buy nothing; that must be an error, not a no-op.
    rc = main(["run", "--daemon", "--backend", "console", "-e", "di 1"])

    assert rc == 2
    assert "console backend is stateless" in capsys.readouterr().err
