"""Tests for the ``lint_do`` MCP tool (server dispatch layer)."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("mcp", reason="mcp package not installed")

from stata_code.mcp import server  # noqa: E402


def _payload(result):
    return result.structuredContent


def test_lint_do_is_registered():
    names = {t.name for t in server._tool_definitions()}
    assert "lint_do" in names


def test_lint_do_inline_clean():
    result = server._lint_do_tool({"code": "regress y x"})
    payload = _payload(result)
    assert payload["ok"] is True
    assert payload["counts"] == {"error": 0, "warning": 0}
    assert payload["findings"] == []


def test_lint_do_inline_error():
    result = server._lint_do_tool({"code": "program define f\n regress y x"})
    payload = _payload(result)
    assert payload["ok"] is False
    assert payload["counts"]["error"] == 1
    assert payload["findings"][0]["rule"] == "missing-end"


def test_lint_do_reads_path(tmp_path):
    do = tmp_path / "x.do"
    do.write_text("regress y x }", encoding="utf-8")
    result = server._lint_do_tool({"path": str(do)})
    payload = _payload(result)
    assert payload["path"] == str(do)
    assert payload["ok"] is False
    assert payload["findings"][0]["rule"] == "unbalanced-braces"


def test_lint_do_missing_path():
    result = server._lint_do_tool({"path": "/no/such/file.do"})
    assert result.isError is True
    assert json.loads(result.content[0].text)["kind"] == "file_not_found"


def test_lint_do_requires_code_or_path():
    result = server._lint_do_tool({})
    assert result.isError is True
    assert json.loads(result.content[0].text)["kind"] == "missing_argument"


def test_lint_do_dispatch_end_to_end():
    import asyncio

    result = asyncio.run(server._dispatch("lint_do", {"code": "mata"}))
    payload = _payload(result)
    assert payload["ok"] is False
    assert payload["findings"][0]["rule"] == "missing-end"
