"""Tests for the `stata-code` CLI subcommands and the MCP setup writer.

The ``run`` command is exercised through the command-policy path (a blocked
command returns a synthetic ``policy_blocked`` RunResult), so the suite needs no
Stata install. ``lint`` and ``setup`` are pure.
"""

from __future__ import annotations

import io
import json

import pytest

from stata_code import mcp_setup
from stata_code.cli import main


@pytest.fixture(autouse=True)
def _enforce_policy(monkeypatch):
    # Make the guard deterministic regardless of the ambient environment.
    monkeypatch.setenv("STATA_CODE_COMMAND_POLICY", "enforce")
    monkeypatch.delenv("STATA_CODE_POLICY_ALLOW", raising=False)
    monkeypatch.delenv("STATA_CODE_POLICY_BLOCK", raising=False)


# ─────────────────────────────────────────────────────────────────────────────
# version / help
# ─────────────────────────────────────────────────────────────────────────────


def test_version(capsys):
    assert main(["--version"]) == 0
    from stata_code import __version__

    assert capsys.readouterr().out.strip() == __version__


# ─────────────────────────────────────────────────────────────────────────────
# run
# ─────────────────────────────────────────────────────────────────────────────


def test_run_blocked_text(capsys):
    rc = main(["run", "-e", "shell rm -rf /"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "ok=False" in out
    assert "policy_blocked" in out


def test_run_blocked_json(capsys):
    rc = main(["run", "-e", "erase x.dta", "--json"])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["rc"] == -4
    assert payload["error"]["kind"] == "policy_blocked"


def test_run_from_do_file(tmp_path, capsys):
    do = tmp_path / "job.do"
    do.write_text("shell ls\n", encoding="utf-8")
    rc = main(["run", str(do)])
    assert rc == 1
    assert "policy_blocked" in capsys.readouterr().out


def test_run_missing_file(capsys):
    rc = main(["run", "/no/such/file.do"])
    assert rc == 2
    assert "file not found" in capsys.readouterr().err


def test_run_no_code_empty_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    rc = main(["run"])
    assert rc == 2
    assert "no code provided" in capsys.readouterr().err


# ─────────────────────────────────────────────────────────────────────────────
# lint
# ─────────────────────────────────────────────────────────────────────────────


def test_lint_clean(capsys):
    assert main(["lint", "-e", "regress y x"]) == 0
    assert "clean" in capsys.readouterr().out


def test_lint_error_exit(tmp_path, capsys):
    do = tmp_path / "bad.do"
    do.write_text("program define foo\n regress y x\n", encoding="utf-8")
    rc = main(["lint", str(do)])
    assert rc == 1
    assert "missing-end" in capsys.readouterr().out


def test_lint_json(capsys):
    rc = main(["lint", "-e", "regress y x }", "--json"])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["counts"]["error"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# setup (CLI)
# ─────────────────────────────────────────────────────────────────────────────


def test_setup_requires_a_target(capsys):
    rc = main(["setup"])
    assert rc == 2
    assert "pick at least one target" in capsys.readouterr().err


def test_setup_dry_run_writes_nothing(tmp_path, capsys):
    rc = main(["setup", "--all", "--dry-run", "--workspace", str(tmp_path)])
    assert rc == 0
    assert not (tmp_path / ".mcp.json").exists()
    assert "dry-run" in capsys.readouterr().out


def test_setup_writes_and_is_idempotent(tmp_path, capsys):
    rc = main(["setup", "--claude", "--workspace", str(tmp_path)])
    assert rc == 0
    cfg = tmp_path / ".mcp.json"
    assert cfg.exists()
    data = json.loads(cfg.read_text())
    assert "stata-code" in data["mcpServers"]

    capsys.readouterr()
    rc = main(["setup", "--claude", "--workspace", str(tmp_path)])
    assert rc == 0
    assert "unchanged" in capsys.readouterr().out


def test_setup_json_output(tmp_path, capsys):
    rc = main(["setup", "--vscode", "--workspace", str(tmp_path), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["changes"][0]["action"] == "created"


def test_setup_codex_snippet_is_printed_not_written(tmp_path, capsys):
    rc = main(["setup", "--codex", "--workspace", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "mcp_servers.stata-code" in out
    assert not (tmp_path / ".codex").exists()


# ─────────────────────────────────────────────────────────────────────────────
# mcp_setup module
# ─────────────────────────────────────────────────────────────────────────────


class TestMcpSetup:
    def test_resolve_command_default(self, monkeypatch):
        monkeypatch.setattr(mcp_setup.shutil, "which", lambda _: "/abs/stata-code-mcp")
        assert mcp_setup.resolve_server_command() == ["/abs/stata-code-mcp"]

    def test_resolve_command_fallback(self, monkeypatch):
        monkeypatch.setattr(mcp_setup.shutil, "which", lambda _: None)
        assert mcp_setup.resolve_server_command() == ["stata-code-mcp"]

    def test_resolve_command_python(self):
        assert mcp_setup.resolve_server_command("/venv/bin/python") == [
            "/venv/bin/python",
            "-m",
            "stata_code.mcp",
        ]

    def test_vscode_uses_servers_schema_with_type(self, tmp_path):
        mcp_setup.apply_client(
            "vscode", workspace=tmp_path, command=["stata-code-mcp"]
        )
        data = json.loads((tmp_path / ".vscode" / "mcp.json").read_text())
        entry = data["servers"]["stata-code"]
        assert entry["type"] == "stdio"
        assert entry["command"] == "stata-code-mcp"

    def test_merge_preserves_other_servers_and_backs_up(self, tmp_path):
        cfg = tmp_path / ".mcp.json"
        cfg.write_text(
            json.dumps({"mcpServers": {"other": {"command": "x"}}, "keep": 1}),
            encoding="utf-8",
        )
        report = mcp_setup.apply_client(
            "claude", workspace=tmp_path, command=["stata-code-mcp"]
        )
        assert report.action == "updated"
        assert report.backup is not None
        data = json.loads(cfg.read_text())
        assert data["mcpServers"]["other"] == {"command": "x"}
        assert data["keep"] == 1
        assert data["mcpServers"]["stata-code"]["command"] == "stata-code-mcp"

    def test_unchanged_when_entry_matches(self, tmp_path):
        mcp_setup.apply_client("claude", workspace=tmp_path, command=["stata-code-mcp"])
        second = mcp_setup.apply_client(
            "claude", workspace=tmp_path, command=["stata-code-mcp"]
        )
        assert second.action == "unchanged"

    def test_invalid_existing_json_reports_error(self, tmp_path):
        (tmp_path / ".mcp.json").write_text("{not json", encoding="utf-8")
        report = mcp_setup.apply_client(
            "claude", workspace=tmp_path, command=["stata-code-mcp"]
        )
        assert report.action == "error"
