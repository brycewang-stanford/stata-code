"""Tests for the borrowed-from-stata-ai-fusion convenience tools.

Covers the three tools added on top of the v1.0 surface:

* ``search_log``     — grep within a stored ``log://`` payload (pure, no Stata).
* ``install_package``— build + run ``ssc``/``net install`` and verify.
* ``inspect_data``   — ``describe`` + ``codebook`` projected to a small payload.

The Stata-touching tools are exercised by monkeypatching ``pool_execute`` so
the suite stays green without a Stata install.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp", reason="mcp package not installed")

from stata_code.core import _refs  # noqa: E402
from stata_code.core.runner import search_log  # noqa: E402
from stata_code.mcp import server  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


class _Dumpable:
    """Minimal stand-in for a pydantic sub-model with ``model_dump``."""

    def __init__(self, **kw):
        self._d = kw

    def model_dump(self):
        return dict(self._d)


def _fake_result(*, ok=True, rc=0, head="", truncated=False, ref=None,
                 lines_total=0, stata=None, error=None, dataset=None):
    log = SimpleNamespace(
        head=head, truncated=truncated, lines_total=lines_total, ref=ref
    )
    return SimpleNamespace(
        ok=ok, rc=rc, log=log, stata=stata, error=error, dataset=dataset
    )


def _make_pool(results):
    """Return (fake_pool_execute, recorded_calls)."""
    calls: list[tuple[str, dict]] = []
    seq = list(results)

    def fake(code, **kw):
        calls.append((code, kw))
        return seq[min(len(calls) - 1, len(seq) - 1)]

    return fake, calls


def _body(result):
    """Pull the structured payload out of a CallToolResult."""
    return result.structuredContent


def _put_log(ref: str, lines: list[str]):
    text = "\n".join(lines)
    _refs.put(
        ref,
        {"text": text, "lines_total": len(lines), "bytes_total": len(text)},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tool registry surface
# ─────────────────────────────────────────────────────────────────────────────


class TestRegistry:
    def test_new_tools_registered(self):
        names = {t.name for t in server._tool_definitions()}
        assert {"install_package", "search_log", "inspect_data"}.issubset(names)

    def test_install_package_requires_name(self):
        tool = next(
            t for t in server._tool_definitions() if t.name == "install_package"
        )
        assert tool.inputSchema["required"] == ["name"]
        assert tool.annotations.idempotentHint is True

    def test_search_log_requires_ref_and_pattern(self):
        tool = next(t for t in server._tool_definitions() if t.name == "search_log")
        assert set(tool.inputSchema["required"]) == {"ref", "pattern"}
        assert tool.annotations.readOnlyHint is True

    def test_inspect_data_has_no_required_fields(self):
        tool = next(t for t in server._tool_definitions() if t.name == "inspect_data")
        assert tool.inputSchema.get("required", []) == []
        assert tool.annotations.readOnlyHint is True

    def test_new_tool_schemas_avoid_forbidden_top_level_keywords(self):
        forbidden = {"oneOf", "anyOf", "allOf", "enum", "not"}
        for tool in server._tool_definitions():
            if tool.name not in {"install_package", "search_log", "inspect_data"}:
                continue
            assert forbidden.isdisjoint(tool.inputSchema), tool.name


# ─────────────────────────────────────────────────────────────────────────────
# search_log — pure runner function
# ─────────────────────────────────────────────────────────────────────────────


class TestSearchLogFunction:
    def test_substring_match_default(self):
        _put_log("log://sl1", ["use auto", "regress price mpg", "r2 = 0.5"])
        out = search_log("log://sl1", "regress")
        assert out["match_count"] == 1
        assert out["matches"][0]["line_no"] == 2
        assert out["matches"][0]["text"] == "regress price mpg"
        assert out["truncated"] is False

    def test_case_insensitive_by_default(self):
        _put_log("log://sl2", ["REGRESS price mpg"])
        assert search_log("log://sl2", "regress")["match_count"] == 1
        assert (
            search_log("log://sl2", "regress", ignore_case=False)["match_count"] == 0
        )

    def test_regex_and_context(self):
        _put_log(
            "log://sl3",
            ["line0", "error: variable x not found", "line2", "line3"],
        )
        out = search_log(
            "log://sl3", r"error:.*not found", is_regex=True, context=1
        )
        assert out["match_count"] == 1
        m = out["matches"][0]
        assert m["line_no"] == 2
        assert m["before"] == ["line0"]
        assert m["after"] == ["line2"]

    def test_max_matches_truncates(self):
        _put_log("log://sl4", ["hit"] * 10)
        out = search_log("log://sl4", "hit", max_matches=3)
        assert out["match_count"] == 3
        assert out["truncated"] is True

    def test_unknown_ref_raises(self):
        from stata_code.core.runner import RefNotFound

        with pytest.raises(RefNotFound):
            search_log("log://nope", "x")

    def test_non_log_ref_raises_unknown_log_ref(self):
        from stata_code.core.runner import RefNotFound

        ref = "matrix://not-a-log/e/M"
        _refs.put(ref, {"rows": ["r"], "cols": ["c"], "values": [[1.0]]})
        try:
            with pytest.raises(RefNotFound) as excinfo:
                search_log(ref, "x")
        finally:
            _refs.discard(ref)
        assert excinfo.value.kind == "unknown_log_ref"

    def test_bad_regex_raises_valueerror(self):
        _put_log("log://sl5", ["x"])
        with pytest.raises(ValueError):
            search_log("log://sl5", "(", is_regex=True)


# ─────────────────────────────────────────────────────────────────────────────
# search_log — MCP dispatch
# ─────────────────────────────────────────────────────────────────────────────


class TestSearchLogDispatch:
    def test_dispatch_returns_matches(self):
        _put_log("log://d1", ["alpha", "beta", "gamma"])
        out = asyncio.run(
            server._dispatch("search_log", {"ref": "log://d1", "pattern": "beta"})
        )
        body = _body(out)
        assert body["match_count"] == 1
        assert body["matches"][0]["text"] == "beta"

    def test_dispatch_unknown_ref_is_error(self):
        out = asyncio.run(
            server._dispatch(
                "search_log", {"ref": "log://missing", "pattern": "x"}
            )
        )
        assert out.isError is True
        assert out.structuredContent["kind"] == "unknown_log_ref"

    def test_dispatch_non_log_ref_is_error(self):
        ref = "matrix://not-a-log-dispatch/e/M"
        _refs.put(ref, {"rows": ["r"], "cols": ["c"], "values": [[1.0]]})
        try:
            out = asyncio.run(
                server._dispatch("search_log", {"ref": ref, "pattern": "x"})
            )
        finally:
            _refs.discard(ref)
        assert out.isError is True
        assert out.structuredContent["kind"] == "unknown_log_ref"

    def test_dispatch_bad_regex_is_invalid_request(self):
        _put_log("log://d2", ["x"])
        out = asyncio.run(
            server._dispatch(
                "search_log",
                {"ref": "log://d2", "pattern": "(", "is_regex": True},
            )
        )
        assert out.isError is True
        assert out.structuredContent["kind"] == "invalid_request"

    def test_dispatch_missing_pattern_is_error(self):
        out = asyncio.run(
            server._dispatch("search_log", {"ref": "log://d2"})
        )
        assert out.isError is True

    def test_dispatch_rejects_non_int_context(self):
        _put_log("log://d3", ["x"])
        out = asyncio.run(
            server._dispatch(
                "search_log",
                {"ref": "log://d3", "pattern": "x", "context": "2"},
            )
        )
        assert out.isError is True
        assert out.structuredContent["kind"] == "invalid_request"


# ─────────────────────────────────────────────────────────────────────────────
# install_package — MCP dispatch (Stata mocked)
# ─────────────────────────────────────────────────────────────────────────────


class TestInstallPackage:
    def test_ssc_install_builds_command_and_verifies(self, monkeypatch):
        fake, calls = _make_pool(
            [_fake_result(ok=True), _fake_result(ok=True)]
        )
        monkeypatch.setattr(server, "pool_execute", fake)
        out = asyncio.run(
            server._dispatch("install_package", {"name": "reghdfe"})
        )
        body = _body(out)
        assert body["command"] == "ssc install reghdfe, replace"
        assert body["ok"] is True
        assert body["verified"] is True
        # First call installs, second verifies with `which`.
        assert calls[0][0] == "ssc install reghdfe, replace"
        assert calls[1][0] == "which reghdfe"

    def test_ssc_without_replace(self, monkeypatch):
        fake, _ = _make_pool([_fake_result(ok=True), _fake_result(ok=True)])
        monkeypatch.setattr(server, "pool_execute", fake)
        out = asyncio.run(
            server._dispatch(
                "install_package", {"name": "estout", "replace": False}
            )
        )
        assert _body(out)["command"] == "ssc install estout"

    def test_failed_install_skips_verification(self, monkeypatch):
        err = _Dumpable(kind="network", rc=691, message="cannot connect")
        fake, calls = _make_pool([_fake_result(ok=False, rc=691, error=err)])
        monkeypatch.setattr(server, "pool_execute", fake)
        out = asyncio.run(
            server._dispatch("install_package", {"name": "reghdfe"})
        )
        body = _body(out)
        assert body["ok"] is False
        assert body["verified"] is False
        assert body["error"]["kind"] == "network"
        # No `which` verification call on failure.
        assert len(calls) == 1

    def test_net_install_requires_url(self, monkeypatch):
        monkeypatch.setattr(
            server, "pool_execute",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("should not run")
            ),
        )
        out = asyncio.run(
            server._dispatch(
                "install_package", {"name": "mypkg", "source": "net"}
            )
        )
        assert out.isError is True
        assert out.structuredContent["kind"] == "invalid_request"

    def test_net_install_builds_from_clause(self, monkeypatch):
        fake, calls = _make_pool([_fake_result(ok=True), _fake_result(ok=True)])
        monkeypatch.setattr(server, "pool_execute", fake)
        out = asyncio.run(
            server._dispatch(
                "install_package",
                {
                    "name": "mypkg",
                    "source": "net",
                    "url": "https://example.com/stata",
                },
            )
        )
        assert _body(out)["command"] == (
            "net install mypkg, from(https://example.com/stata) replace"
        )

    def test_rejects_unsafe_name(self, monkeypatch):
        monkeypatch.setattr(
            server, "pool_execute",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("should not run")
            ),
        )
        out = asyncio.run(
            server._dispatch(
                "install_package", {"name": "reghdfe\ndrop _all"}
            )
        )
        assert out.isError is True
        assert out.structuredContent["kind"] == "invalid_request"

    def test_rejects_bad_url(self, monkeypatch):
        monkeypatch.setattr(
            server, "pool_execute",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("should not run")
            ),
        )
        out = asyncio.run(
            server._dispatch(
                "install_package",
                {"name": "p", "source": "net", "url": "ftp://x y"},
            )
        )
        assert out.isError is True

    def test_rejects_url_with_stata_option_delimiter(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "pool_execute",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("should not run")
            ),
        )
        out = asyncio.run(
            server._dispatch(
                "install_package",
                {
                    "name": "p",
                    "source": "net",
                    "url": "https://example.com/repo),force",
                },
            )
        )
        assert out.isError is True
        assert out.structuredContent["kind"] == "invalid_request"


# ─────────────────────────────────────────────────────────────────────────────
# inspect_data — MCP dispatch (Stata mocked)
# ─────────────────────────────────────────────────────────────────────────────


class TestInspectData:
    def test_compact_default_command_and_projection(self, monkeypatch):
        dataset = _Dumpable(frame="default", n_obs=74, n_vars=12)
        fake, calls = _make_pool(
            [_fake_result(ok=True, head="(output)", dataset=dataset)]
        )
        monkeypatch.setattr(server, "pool_execute", fake)
        out = asyncio.run(server._dispatch("inspect_data", {}))
        body = _body(out)
        assert calls[0][0] == "describe\ncodebook, compact"
        assert body["dataset"]["n_obs"] == 74
        assert body["log"]["head"] == "(output)"
        assert body["ok"] is True

    def test_detail_runs_full_codebook(self, monkeypatch):
        fake, calls = _make_pool([_fake_result(ok=True)])
        monkeypatch.setattr(server, "pool_execute", fake)
        asyncio.run(server._dispatch("inspect_data", {"detail": True}))
        assert calls[0][0] == "describe\ncodebook"

    def test_varlist_is_passed_through(self, monkeypatch):
        fake, calls = _make_pool([_fake_result(ok=True)])
        monkeypatch.setattr(server, "pool_execute", fake)
        asyncio.run(
            server._dispatch("inspect_data", {"varlist": "price mpg"})
        )
        assert calls[0][0] == "describe price mpg\ncodebook price mpg, compact"

    def test_rejects_unsafe_varlist(self, monkeypatch):
        monkeypatch.setattr(
            server, "pool_execute",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("should not run")
            ),
        )
        out = asyncio.run(
            server._dispatch(
                "inspect_data", {"varlist": "price\ndrop _all"}
            )
        )
        assert out.isError is True
        assert out.structuredContent["kind"] == "invalid_request"

    def test_error_block_surfaced(self, monkeypatch):
        err = _Dumpable(kind="no_observations", rc=2000, message="no data")
        fake, _ = _make_pool([_fake_result(ok=False, rc=2000, error=err)])
        monkeypatch.setattr(server, "pool_execute", fake)
        out = asyncio.run(server._dispatch("inspect_data", {}))
        body = _body(out)
        assert body["ok"] is False
        assert body["error"]["kind"] == "no_observations"


def test_unknown_argument_rejected_for_new_tools():
    out = asyncio.run(
        server._dispatch("search_log", {"ref": "log://x", "bogus": 1})
    )
    assert out.isError is True
    assert "bogus" in json.dumps(out.structuredContent)


def test_readmes_advertise_current_mcp_tool_count():
    repo = Path(__file__).resolve().parents[1]
    count = len(server._tool_definitions())
    readme = (repo / "README.md").read_text(encoding="utf-8")
    readme_zh = (repo / "README.zh.md").read_text(encoding="utf-8")

    assert f"with its {count} tools" in readme
    assert f"The MCP server registers {count} tools" in readme
    assert f"MCP server ({count} tools)" in readme
    assert f"MCP server: {count} tools" in readme
    assert f"带有 {count} 个工具" in readme_zh
    assert f"MCP server 注册了 {count} 个工具" in readme_zh
    assert f"MCP server ({count} tools)" in readme_zh
    assert f"MCP server：{count} 个工具" in readme_zh


def test_skill_docs_match_current_mcp_argument_names():
    repo = Path(__file__).resolve().parents[1]
    skill_dir = repo / "skills" / "stata-code"
    skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    docs = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(skill_dir.rglob("*.md"))
    )

    assert "notebook_edit_cell(path, cell_id, new_source, expected_source?)" in skill
    assert (
        "notebook_insert_cell(path, source, after_cell_id?, before_cell_id?, "
        "at_start?, at_end?, cell_type?)"
    ) in skill
    assert "notebook_delete_cell(path, cell_id, expected_source?)" in skill
    assert "get_matrix(name=" not in docs
    assert "notebook_edit_cell(path, cell_id, source" not in docs
    assert "notebook_insert_cell(path, after_cell_id" not in docs
