"""Extra offline coverage for the MCP server and the Jupyter kernel.

Targets Stata-free branches left uncovered by the existing suites:
resource projection helpers, prompt rendering, MCP app handler wrappers,
tool-argument validation error paths, dispatch exception mapping, kernel
streaming/publishing with stub sockets, and both ``run_main`` entrypoints.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

pytest.importorskip("mcp", reason="mcp package not installed")

from mcp.types import CallToolResult, ImageContent, TextContent, Tool  # noqa: E402

from stata_code.core import _refs  # noqa: E402
from stata_code.core._runtime import PystataNotAvailable  # noqa: E402
from stata_code.core.schema import (  # noqa: E402
    Backend,
    ErrorContext,
    ErrorInfo,
    ErrorKind,
    GraphFormat,
    GraphInfo,
    RunResult,
    StataEdition,
    StataInfo,
    StataWarning,
    Suggestion,
)
from stata_code.mcp import server  # noqa: E402


def _text_items(result):
    if isinstance(result, CallToolResult):
        return [item for item in result.content if isinstance(item, TextContent)]
    return [item for item in result if isinstance(item, TextContent)]


def _image_items(result):
    if isinstance(result, CallToolResult):
        return [item for item in result.content if isinstance(item, ImageContent)]
    return [item for item in result if isinstance(item, ImageContent)]


def _json_body(result):
    return json.loads(_text_items(result)[0].text)


def _make_run_result(*, ok: bool = True, **overrides) -> RunResult:
    base: dict = {
        "ok": ok,
        "rc": 0 if ok else 111,
        "session_id": "main",
        "request_id": "extra-req",
        "started_at": "2026-07-20T00:00:00.000Z",
        "elapsed_ms": 1,
        "stata": StataInfo(
            version="18.0", edition=StataEdition.MP, backend=Backend.PYSTATA
        ),
    }
    base.update(overrides)
    if not ok and base.get("error") is None:
        base["error"] = ErrorInfo(
            kind=ErrorKind.VARNAME_NOT_FOUND,
            rc=base["rc"],
            message="variable mpgg not found",
            varname="mpgg",
            line=1,
            context=ErrorContext(before=[], failing="summarize mpgg", after=[]),
            suggestions=[Suggestion(action="Did you mean `mpg`?", command="describe")],
        )
    return RunResult(**base)


def _write_notebook(tmp_path, cells=None):
    if cells is None:
        cells = [
            {
                "cell_type": "code",
                "id": "cell-a",
                "source": "display 1",
                "metadata": {},
                "outputs": [],
            },
            {
                "cell_type": "code",
                "id": "cell-b",
                "source": "summarize price",
                "metadata": {},
                "outputs": [],
            },
        ]
    nb_path = tmp_path / "nb.ipynb"
    nb_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": cells,
            }
        ),
        encoding="utf-8",
    )
    return nb_path


# ─────────────────────────────────────────────────────────────────────────────
# Resource projection helpers
# ─────────────────────────────────────────────────────────────────────────────


class TestResourceForRef:
    def test_log_ref_projects_text_plain_resource(self):
        res = server._resource_for_ref("log://req-1", {"text": "x"})
        assert res is not None
        assert str(res.uri) == "log://req-1"
        assert res.mimeType == "text/plain"
        assert "req-1" in res.title

    def test_graph_ref_uses_payload_format_for_mime(self):
        res = server._resource_for_ref("graph://req-1/0", {"format": "svg"})
        assert res is not None
        assert res.mimeType == "image/svg+xml"

    def test_graph_ref_defaults_to_png_for_non_dict_payload(self):
        res = server._resource_for_ref("graph://req-1/0", "not-a-dict")
        assert res is not None
        assert res.mimeType == "image/png"

    def test_unknown_scheme_returns_none(self):
        assert server._resource_for_ref("bogus://x", {}) is None


class TestListResourcesCap:
    def test_ref_listing_is_capped_to_most_recent(self, monkeypatch):
        total = server._LIST_RESOURCES_REFS_CAP + 40
        snapshot = [(f"log://r{i}", {"text": ""}) for i in range(total)]
        monkeypatch.setattr(
            server, "_refs", SimpleNamespace(snapshot=lambda: list(snapshot))
        )

        resources = server._list_mcp_resources()
        static_count = len(server._static_resources())
        dynamic = resources[static_count:]
        assert len(dynamic) == server._LIST_RESOURCES_REFS_CAP
        # LRU tail survives: the oldest 40 refs are dropped.
        assert str(dynamic[0].uri) == "log://r40"
        assert str(dynamic[-1].uri) == f"log://r{total - 1}"


class TestReadResourcePayload:
    def test_capabilities_resource_embeds_tools_and_prompts(self):
        content = server._read_resource_payload("stata://server/capabilities")
        assert content.mime_type == "application/json"
        body = json.loads(content.content)
        assert body["name"] == "stata-code"
        assert body["version"] == server.__version__
        assert body["schema_version"] == "1.0"
        tool_names = {t["name"] for t in body["tools"]}
        assert "stata_run" in tool_names
        prompt_names = {p["name"] for p in body["prompts"]}
        assert "debug_stata_error" in prompt_names
        assert len(body["resource_templates"]) == len(server._resource_templates())

    def test_log_resource_returns_text_and_meta(self):
        ref = "log://extra-read"
        _refs.put(ref, {"text": "line one\nline two", "lines_total": 2, "bytes_total": 17})
        try:
            content = server._read_resource_payload(ref)
        finally:
            _refs.discard(ref)
        assert content.mime_type == "text/plain"
        assert content.content == "line one\nline two"
        assert content.meta == {"lines_total": 2, "bytes_total": 17}

    def test_graph_resource_returns_decoded_bytes(self):
        ref = "graph://extra-read/0"
        raw = b"\x89PNG-fake-bytes"
        _refs.put(ref, {"format": "png", "bytes": raw, "width": 10, "height": 20})
        try:
            content = server._read_resource_payload(ref)
        finally:
            _refs.discard(ref)
        assert content.mime_type == "image/png"
        assert content.content == raw
        assert content.meta == {"format": "png", "width": 10, "height": 20}

    def test_unknown_uri_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown resource URI"):
            server._read_resource_payload("stata://nope")


# ─────────────────────────────────────────────────────────────────────────────
# Prompt rendering branches
# ─────────────────────────────────────────────────────────────────────────────


class TestPromptRendering:
    def test_debug_stata_error_includes_error_json_when_given(self):
        prompt = server._get_mcp_prompt(
            "debug_stata_error",
            {"code_or_path": "analysis/fail.do", "error_json": '{"kind": "syntax"}'},
        )
        text = prompt.messages[0].content.text
        assert "analysis/fail.do" in text
        assert '{"kind": "syntax"}' in text
        assert "error.suggestions" in text

    def test_debug_stata_error_omits_error_json_clause_when_absent(self):
        prompt = server._get_mcp_prompt("debug_stata_error", {"code_or_path": "x.do"})
        text = prompt.messages[0].content.text
        assert "The RunResult.error JSON is" not in text

    def test_fix_and_rerun_until_passes_renders_path_and_session(self):
        prompt = server._get_mcp_prompt(
            "fix_and_rerun_until_passes",
            {"path": "fix/me.do", "session_id": "repair"},
        )
        assert prompt.description == "Fix and rerun the Stata code"
        text = prompt.messages[0].content.text
        assert "fix/me.do" in text
        assert "repair" in text
        assert "smallest defensible edit" in text

    def test_replication_audit_renders_entrypoint(self):
        prompt = server._get_mcp_prompt("replication_audit", {"path": "repl/main.do"})
        text = prompt.messages[0].content.text
        assert "repl/main.do" in text
        assert "persist_log_files=true" in text
        assert "reproducibility risks" in text

    def test_summarize_estimation_results_embeds_run_result(self):
        prompt = server._get_mcp_prompt(
            "summarize_estimation_results", {"run_result_json": '{"ok": true}'}
        )
        text = prompt.messages[0].content.text
        assert '{"ok": true}' in text
        assert "results.e" in text

    def test_run_notebook_cell_and_report_wires_origin_fields(self):
        prompt = server._get_mcp_prompt(
            "run_notebook_cell_and_report",
            {"path": "/nb.ipynb", "cell_id": "abc123", "session_id": "nb"},
        )
        text = prompt.messages[0].content.text
        assert "abc123" in text
        assert "/nb.ipynb" in text
        assert "origin_kind='cell'" in text
        assert "Do not edit the cell" in text


# ─────────────────────────────────────────────────────────────────────────────
# Registered MCP app handlers (the decorated async wrappers)
# ─────────────────────────────────────────────────────────────────────────────


class TestAppHandlers:
    def test_list_tools_handler_matches_definitions(self):
        tools = asyncio.run(server.list_tools())
        assert [t.name for t in tools] == [t.name for t in server._tool_definitions()]

    def test_call_tool_handler_dispatches(self):
        ref = "matrix://handler-test/e/M"
        _refs.put(ref, {"rows": ["r"], "cols": ["c"], "values": [[2.0]]})
        try:
            out = asyncio.run(server.call_tool("get_matrix", {"ref": ref}))
        finally:
            _refs.discard(ref)
        assert out.isError is False
        assert _json_body(out)["values"] == [[2.0]]

    def test_list_resources_handler_includes_static(self):
        uris = {str(r.uri) for r in asyncio.run(server.list_resources())}
        assert "stata://schema/run-result" in uris
        assert "stata://server/capabilities" in uris

    def test_list_resource_templates_handler(self):
        names = {t.name for t in asyncio.run(server.list_resource_templates())}
        assert {"stata_log_ref", "stata_graph_ref", "stata_matrix_ref"} <= names

    def test_read_resource_handler_wraps_payload_in_list(self):
        contents = asyncio.run(server.read_resource("stata://schema/run-result"))
        assert len(contents) == 1
        assert contents[0].mime_type == "application/schema+json"

    def test_list_prompts_handler(self):
        names = {p.name for p in asyncio.run(server.list_prompts())}
        assert "run_do_file_and_report" in names

    def test_get_prompt_handler_renders(self):
        out = asyncio.run(server.get_prompt("replication_audit", {"path": "a.do"}))
        assert out.description == "Audit Stata replication"
        assert "a.do" in out.messages[0].content.text


# ─────────────────────────────────────────────────────────────────────────────
# Argument validation and dispatch exception mapping
# ─────────────────────────────────────────────────────────────────────────────


class TestValidateToolArguments:
    def test_schema_without_additional_properties_false_is_permissive(self, monkeypatch):
        permissive = Tool(
            name="permissive_tool",
            inputSchema={"type": "object", "properties": {"a": {"type": "string"}}},
        )
        monkeypatch.setattr(server, "_tool_definitions", lambda: [permissive])
        assert (
            server._validate_tool_arguments("permissive_tool", {"anything": 1}) is None
        )


class TestDispatchErrorMapping:
    def test_notebook_error_maps_to_typed_kind(self, tmp_path):
        out = asyncio.run(
            server._dispatch("notebook_outline", {"path": str(tmp_path / "missing.ipynb")})
        )
        assert out.isError is True
        body = _json_body(out)
        assert body["kind"] == "notebook_not_found"

    def test_run_index_error_maps_to_typed_kind(self, tmp_path):
        out = asyncio.run(
            server._dispatch("list_runs", {"log_dir": str(tmp_path), "limit": 0})
        )
        assert out.isError is True
        body = _json_body(out)
        assert body["kind"] == "limit_invalid"

    def test_plain_key_error_maps_to_unknown_ref(self, monkeypatch):
        def boom(_ref):
            raise KeyError("log://gone")

        monkeypatch.setattr(server, "get_log", boom)
        out = asyncio.run(server._dispatch("get_log", {"ref": "log://gone"}))
        assert out.isError is True
        body = _json_body(out)
        assert body["kind"] == "unknown_ref"
        assert "log://gone" in body["error"]

    def test_pystata_not_available_maps_to_stata_unavailable(self, monkeypatch):
        def boom(_ref):
            raise PystataNotAvailable("pystata is not importable")

        monkeypatch.setattr(server, "get_log", boom)
        out = asyncio.run(server._dispatch("get_log", {"ref": "log://x"}))
        body = _json_body(out)
        assert out.isError is True
        assert body["kind"] == "stata_unavailable"
        assert "pystata is not importable" in body["error"]

    def test_unexpected_exception_maps_to_internal_error(self, monkeypatch):
        def boom(_ref):
            raise RuntimeError("socket melted")

        monkeypatch.setattr(server, "get_log", boom)
        out = asyncio.run(server._dispatch("get_log", {"ref": "log://x"}))
        body = _json_body(out)
        assert out.isError is True
        assert body["kind"] == "internal_error"
        assert "RuntimeError" in body["error"]
        assert "socket melted" in body["error"]


class TestGetLogGraphDispatch:
    def test_get_log_known_ref_returns_payload(self):
        ref = "log://dispatch-extra"
        _refs.put(ref, {"text": "hello log", "lines_total": 1, "bytes_total": 9})
        try:
            out = asyncio.run(server._dispatch("get_log", {"ref": ref}))
        finally:
            _refs.discard(ref)
        assert out.isError is False
        body = _json_body(out)
        assert body == {"text": "hello log", "lines_total": 1, "bytes_total": 9}
        assert out.structuredContent == body

    def test_get_graph_known_ref_returns_image_and_metadata(self):
        ref = "graph://dispatch-extra/0"
        raw = b"<svg>fake</svg>"
        _refs.put(ref, {"format": "svg", "bytes": raw, "width": 400, "height": 300})
        try:
            out = asyncio.run(server._dispatch("get_graph", {"ref": ref}))
        finally:
            _refs.discard(ref)
        assert out.isError is False
        images = _image_items(out)
        assert len(images) == 1
        assert images[0].mimeType == "image/svg+xml"
        assert base64.b64decode(images[0].data) == raw
        assert out.structuredContent == {
            "ref": ref,
            "format": "svg",
            "mimeType": "image/svg+xml",
            "width": 400,
            "height": 300,
        }


class TestRunToolBooleanNormalization:
    def test_valid_bool_argument_is_forwarded_to_pool(self, monkeypatch):
        seen: dict = {}

        def fake_execute(code, **kwargs):
            seen["code"] = code
            seen.update(kwargs)
            return _make_run_result()

        monkeypatch.setattr(server, "pool_execute", fake_execute)
        out = asyncio.run(
            server._dispatch(
                "stata_run", {"code": "display 1", "include_full_log": True}
            )
        )
        assert out.isError is False
        assert seen["code"] == "display 1"
        assert seen["include_full_log"] is True
        assert _json_body(out)["ok"] is True


# ─────────────────────────────────────────────────────────────────────────────
# install_package / search_log / inspect_data validation
# ─────────────────────────────────────────────────────────────────────────────


class TestInstallPackageValidation:
    def test_missing_name_is_rejected(self):
        out = asyncio.run(server._dispatch("install_package", {}))
        body = _json_body(out)
        assert out.isError is True
        assert body["kind"] == "missing_argument"
        assert "name" in body["error"]

    def test_bad_source_is_rejected(self):
        out = asyncio.run(
            server._dispatch("install_package", {"name": "estout", "source": "github"})
        )
        body = _json_body(out)
        assert out.isError is True
        assert body["kind"] == "invalid_request"
        assert "'ssc' or 'net'" in body["error"]

    def test_non_bool_replace_is_rejected(self):
        out = asyncio.run(
            server._dispatch("install_package", {"name": "estout", "replace": "yes"})
        )
        body = _json_body(out)
        assert out.isError is True
        assert "replace" in body["error"]
        assert "boolean" in body["error"]

    def test_empty_session_id_is_rejected(self):
        out = asyncio.run(
            server._dispatch("install_package", {"name": "estout", "session_id": ""})
        )
        body = _json_body(out)
        assert out.isError is True
        assert "session_id" in body["error"]

    def test_non_string_url_is_rejected(self):
        out = asyncio.run(
            server._dispatch("install_package", {"name": "estout", "url": 42})
        )
        body = _json_body(out)
        assert out.isError is True
        assert "url must be a string" in body["error"]

    def test_pool_value_error_maps_to_invalid_request(self, monkeypatch):
        def reject(cmd, **_kwargs):
            raise ValueError("bad session option")

        monkeypatch.setattr(server, "pool_execute", reject)
        out = asyncio.run(server._dispatch("install_package", {"name": "estout"}))
        body = _json_body(out)
        assert out.isError is True
        assert body["kind"] == "invalid_request"
        assert "bad session option" in body["error"]

    def test_verification_failure_reports_verified_false(self, monkeypatch):
        calls: list[str] = []

        def fake_execute(cmd, **_kwargs):
            calls.append(cmd)
            if cmd.startswith("which "):
                raise RuntimeError("worker died mid-verify")
            return _make_run_result()

        monkeypatch.setattr(server, "pool_execute", fake_execute)
        out = asyncio.run(server._dispatch("install_package", {"name": "estout"}))
        assert out.isError is False
        body = _json_body(out)
        assert body["ok"] is True
        assert body["verified"] is False
        assert body["command"] == "ssc install estout, replace"
        assert calls == ["ssc install estout, replace", "which estout"]


class TestSearchLogValidation:
    def test_missing_ref_is_rejected(self):
        out = asyncio.run(server._dispatch("search_log", {"pattern": "x"}))
        body = _json_body(out)
        assert out.isError is True
        assert body["kind"] == "missing_argument"
        assert "ref" in body["error"]

    def test_non_bool_is_regex_is_rejected(self):
        out = asyncio.run(
            server._dispatch(
                "search_log", {"ref": "log://x", "pattern": "p", "is_regex": "true"}
            )
        )
        body = _json_body(out)
        assert out.isError is True
        assert "is_regex" in body["error"]

    def test_non_bool_ignore_case_is_rejected(self):
        out = asyncio.run(
            server._dispatch(
                "search_log", {"ref": "log://x", "pattern": "p", "ignore_case": 1}
            )
        )
        body = _json_body(out)
        assert out.isError is True
        assert "ignore_case" in body["error"]

    def test_non_positive_max_matches_is_rejected(self):
        out = asyncio.run(
            server._dispatch(
                "search_log", {"ref": "log://x", "pattern": "p", "max_matches": 0}
            )
        )
        body = _json_body(out)
        assert out.isError is True
        assert "max_matches" in body["error"]


class TestInspectDataValidation:
    def test_non_string_varlist_is_rejected(self):
        out = asyncio.run(server._dispatch("inspect_data", {"varlist": 5}))
        body = _json_body(out)
        assert out.isError is True
        assert "varlist must be a string" in body["error"]

    def test_non_bool_detail_is_rejected(self):
        out = asyncio.run(server._dispatch("inspect_data", {"detail": "yes"}))
        body = _json_body(out)
        assert out.isError is True
        assert "detail" in body["error"]

    def test_empty_session_id_is_rejected(self):
        out = asyncio.run(server._dispatch("inspect_data", {"session_id": ""}))
        body = _json_body(out)
        assert out.isError is True
        assert "session_id" in body["error"]

    def test_pool_value_error_maps_to_invalid_request(self, monkeypatch):
        def reject(code, **_kwargs):
            raise ValueError("no dataset in memory")

        monkeypatch.setattr(server, "pool_execute", reject)
        out = asyncio.run(server._dispatch("inspect_data", {}))
        body = _json_body(out)
        assert out.isError is True
        assert body["kind"] == "invalid_request"
        assert "no dataset in memory" in body["error"]

    def test_detail_varlist_builds_codebook_without_compact(self, monkeypatch):
        seen: dict = {}

        def fake_execute(code, **kwargs):
            seen["code"] = code
            seen.update(kwargs)
            return _make_run_result()

        monkeypatch.setattr(server, "pool_execute", fake_execute)
        out = asyncio.run(
            server._dispatch(
                "inspect_data", {"varlist": "price mpg", "detail": True}
            )
        )
        assert out.isError is False
        assert seen["code"] == "describe price mpg\ncodebook price mpg"
        assert seen["session_id"] == "main"


# ─────────────────────────────────────────────────────────────────────────────
# Notebook tool wrappers and list_runs validation
# ─────────────────────────────────────────────────────────────────────────────


class TestNotebookToolWrappers:
    def test_outline_requires_path(self):
        out = asyncio.run(server._dispatch("notebook_outline", {}))
        body = _json_body(out)
        assert out.isError is True
        assert body["kind"] == "missing_argument"

    def test_outline_rejects_negative_preview_lines(self, tmp_path):
        nb = _write_notebook(tmp_path)
        out = asyncio.run(
            server._dispatch(
                "notebook_outline", {"path": str(nb), "preview_lines": -1}
            )
        )
        body = _json_body(out)
        assert out.isError is True
        assert "preview_lines" in body["error"]

    def test_outline_happy_path_lists_cells(self, tmp_path):
        nb = _write_notebook(tmp_path)
        out = asyncio.run(server._dispatch("notebook_outline", {"path": str(nb)}))
        assert out.isError is False
        body = _json_body(out)
        ids = [c["cell_id"] for c in body["cells"]]
        assert ids == ["cell-a", "cell-b"]

    def test_get_cell_requires_path_and_locator(self, tmp_path):
        out = asyncio.run(server._dispatch("notebook_get_cell", {}))
        assert _json_body(out)["kind"] == "missing_argument"

        nb = _write_notebook(tmp_path)
        out = asyncio.run(server._dispatch("notebook_get_cell", {"path": str(nb)}))
        body = _json_body(out)
        assert out.isError is True
        assert "cell_id or cell_index" in body["error"]

    def test_get_cell_rejects_wrong_locator_types(self, tmp_path):
        nb = _write_notebook(tmp_path)
        out = asyncio.run(
            server._dispatch("notebook_get_cell", {"path": str(nb), "cell_id": 5})
        )
        assert "cell_id must be a string" in _json_body(out)["error"]

        out = asyncio.run(
            server._dispatch(
                "notebook_get_cell", {"path": str(nb), "cell_index": "0"}
            )
        )
        assert "cell_index must be an integer" in _json_body(out)["error"]

    def test_get_cell_by_index_returns_source(self, tmp_path):
        nb = _write_notebook(tmp_path)
        out = asyncio.run(
            server._dispatch("notebook_get_cell", {"path": str(nb), "cell_index": 1})
        )
        assert out.isError is False
        body = _json_body(out)
        assert body["cell_id"] == "cell-b"
        assert body["source"] == "summarize price"

    def test_locate_requires_path(self):
        out = asyncio.run(server._dispatch("notebook_locate", {}))
        body = _json_body(out)
        assert out.isError is True
        assert body["kind"] == "missing_argument"

    def test_locate_rejects_wrong_types(self, tmp_path):
        nb = _write_notebook(tmp_path)
        out = asyncio.run(
            server._dispatch("notebook_locate", {"path": str(nb), "snippet": 5})
        )
        assert "snippet must be a string" in _json_body(out)["error"]

        out = asyncio.run(
            server._dispatch("notebook_locate", {"path": str(nb), "cell_type": 5})
        )
        assert "cell_type must be a string" in _json_body(out)["error"]

        out = asyncio.run(
            server._dispatch(
                "notebook_locate",
                {"path": str(nb), "snippet": "x", "limit": "ten"},
            )
        )
        assert "limit must be an integer" in _json_body(out)["error"]

    def test_locate_finds_cell_by_snippet(self, tmp_path):
        nb = _write_notebook(tmp_path)
        out = asyncio.run(
            server._dispatch(
                "notebook_locate", {"path": str(nb), "snippet": "summarize"}
            )
        )
        assert out.isError is False
        body = _json_body(out)
        assert [m["cell_id"] for m in body["matches"]] == ["cell-b"]

    def test_edit_cell_validation_and_happy_path(self, tmp_path):
        nb = _write_notebook(tmp_path)

        out = asyncio.run(server._dispatch("notebook_edit_cell", {}))
        assert _json_body(out)["kind"] == "missing_argument"

        out = asyncio.run(server._dispatch("notebook_edit_cell", {"path": str(nb)}))
        body = _json_body(out)
        assert body["kind"] == "missing_argument"
        assert "cell_id" in body["error"]

        out = asyncio.run(
            server._dispatch(
                "notebook_edit_cell", {"path": str(nb), "cell_id": "cell-a"}
            )
        )
        assert "new_source" in _json_body(out)["error"]

        out = asyncio.run(
            server._dispatch(
                "notebook_edit_cell",
                {
                    "path": str(nb),
                    "cell_id": "cell-a",
                    "new_source": "display 2",
                    "expected_source": 7,
                },
            )
        )
        assert "expected_source must be a string" in _json_body(out)["error"]

        out = asyncio.run(
            server._dispatch(
                "notebook_edit_cell",
                {
                    "path": str(nb),
                    "cell_id": "cell-a",
                    "new_source": "display 2",
                    "expected_source": "display 1",
                },
            )
        )
        assert out.isError is False
        cells = json.loads(nb.read_text(encoding="utf-8"))["cells"]
        assert cells[0]["source"] == "display 2"

    def test_insert_cell_validation(self, tmp_path):
        nb = _write_notebook(tmp_path)

        out = asyncio.run(server._dispatch("notebook_insert_cell", {}))
        assert _json_body(out)["kind"] == "missing_argument"

        out = asyncio.run(
            server._dispatch("notebook_insert_cell", {"path": str(nb), "source": 5})
        )
        assert "source is required" in _json_body(out)["error"]

        out = asyncio.run(
            server._dispatch(
                "notebook_insert_cell",
                {"path": str(nb), "source": "x", "cell_type": 5},
            )
        )
        assert "cell_type must be a string" in _json_body(out)["error"]

        out = asyncio.run(
            server._dispatch(
                "notebook_insert_cell",
                {"path": str(nb), "source": "x", "at_end": "true"},
            )
        )
        assert "at_end" in _json_body(out)["error"]

        out = asyncio.run(
            server._dispatch(
                "notebook_insert_cell",
                {"path": str(nb), "source": "x", "after_cell_id": 9},
            )
        )
        assert "after_cell_id must be a string" in _json_body(out)["error"]

        # None of the rejected calls may have touched the file.
        cells = json.loads(nb.read_text(encoding="utf-8"))["cells"]
        assert len(cells) == 2

    def test_delete_cell_validation_and_happy_path(self, tmp_path):
        nb = _write_notebook(tmp_path)

        out = asyncio.run(server._dispatch("notebook_delete_cell", {}))
        assert _json_body(out)["kind"] == "missing_argument"

        out = asyncio.run(
            server._dispatch("notebook_delete_cell", {"path": str(nb)})
        )
        assert "cell_id" in _json_body(out)["error"]

        out = asyncio.run(
            server._dispatch(
                "notebook_delete_cell",
                {"path": str(nb), "cell_id": "cell-b", "expected_source": 3},
            )
        )
        assert "expected_source must be a string" in _json_body(out)["error"]

        out = asyncio.run(
            server._dispatch(
                "notebook_delete_cell", {"path": str(nb), "cell_id": "cell-b"}
            )
        )
        assert out.isError is False
        cells = json.loads(nb.read_text(encoding="utf-8"))["cells"]
        assert [c["id"] for c in cells] == ["cell-a"]


class TestListRunsValidation:
    def test_requires_log_dir_or_origin_path(self):
        out = asyncio.run(server._dispatch("list_runs", {}))
        body = _json_body(out)
        assert out.isError is True
        assert body["kind"] == "missing_argument"
        assert "log_dir or origin_path" in body["error"]

    def test_rejects_non_string_log_dir(self):
        out = asyncio.run(server._dispatch("list_runs", {"log_dir": 5}))
        assert "log_dir must be a string" in _json_body(out)["error"]

    def test_rejects_non_string_cell_id(self, tmp_path):
        out = asyncio.run(
            server._dispatch("list_runs", {"log_dir": str(tmp_path), "cell_id": 5})
        )
        assert "cell_id must be a string" in _json_body(out)["error"]

    def test_rejects_non_bool_ok(self, tmp_path):
        out = asyncio.run(
            server._dispatch("list_runs", {"log_dir": str(tmp_path), "ok": "yes"})
        )
        assert "ok must be a boolean" in _json_body(out)["error"]

    def test_rejects_non_int_limit(self, tmp_path):
        out = asyncio.run(
            server._dispatch("list_runs", {"log_dir": str(tmp_path), "limit": "9"})
        )
        assert "limit must be an integer" in _json_body(out)["error"]


class TestServerEntrypoint:
    def test_run_main_exits_when_mcp_missing(self, monkeypatch, capsys):
        monkeypatch.setattr(server, "_MCP_AVAILABLE", False)
        with pytest.raises(SystemExit) as excinfo:
            server.run_main()
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "MCP support is not installed" in err
        assert "stata-code[mcp]" in err


# ─────────────────────────────────────────────────────────────────────────────
# Kernel: word helper, execute error paths, streaming, publishing
# ─────────────────────────────────────────────────────────────────────────────


class TestWordAtCursor:
    def test_extends_forward_from_mid_word_cursor(self):
        from stata_code.kernel.kernel import _word_at_cursor

        assert _word_at_cursor("summarize mpg", 3) == ("summarize", 0, 9)

    def test_cursor_past_end_is_clamped(self):
        from stata_code.kernel.kernel import _word_at_cursor

        assert _word_at_cursor("mpg", 99) == ("mpg", 0, 3)

    def test_cursor_on_whitespace_yields_empty_word(self):
        from stata_code.kernel.kernel import _word_at_cursor

        assert _word_at_cursor("a  b", 2) == ("", 2, 2)


class TestDoExecuteErrorPaths:
    def test_pystata_not_available_becomes_error_reply(self):
        from stata_code.kernel import StataKernel

        kb = StataKernel()
        with patch(
            "stata_code.kernel.kernel.execute",
            side_effect=PystataNotAvailable("pystata missing"),
        ):
            reply = kb.do_execute("display 1", silent=True)
        assert reply["status"] == "error"
        assert reply["ename"] == "RuntimeError"
        assert "Stata not available" in reply["evalue"]
        assert "pystata missing" in reply["evalue"]

    def test_unexpected_exception_becomes_error_reply(self, capsys):
        from stata_code.kernel import StataKernel

        kb = StataKernel()
        with patch(
            "stata_code.kernel.kernel.execute",
            side_effect=RuntimeError("worker exploded"),
        ):
            reply = kb.do_execute("display 1", silent=True)
        assert reply["status"] == "error"
        assert reply["evalue"] == "worker exploded"
        # do_execute prints the traceback before replying.
        assert "worker exploded" in capsys.readouterr().err


class TestDoExecuteStreaming:
    def test_warnings_are_streamed_to_stderr(self):
        from stata_code.kernel import StataKernel

        result = _make_run_result(
            warnings=[StataWarning(kind="deprecated", message="old syntax")]
        )
        kb = StataKernel()
        streamed: list[tuple[str, str]] = []
        with patch.object(kb, "_stream", side_effect=lambda n, t: streamed.append((n, t))):
            with patch("stata_code.kernel.kernel.execute", return_value=result):
                kb.do_execute("display 1", silent=False)
        assert ("stderr", "[deprecated] old syntax\n") in streamed

    def test_error_summary_is_streamed_to_stderr(self):
        from stata_code.kernel import StataKernel

        result = _make_run_result(ok=False)
        kb = StataKernel()
        streamed: list[tuple[str, str]] = []
        with patch.object(kb, "_stream", side_effect=lambda n, t: streamed.append((n, t))):
            with patch("stata_code.kernel.kernel.execute", return_value=result):
                reply = kb.do_execute("summarize mpgg", silent=False)
        assert reply["status"] == "error"
        stderr_text = "".join(t for n, t in streamed if n == "stderr")
        assert "!!! Stata error: varname_not_found (rc=111)" in stderr_text
        assert "at line 1" in stderr_text
        assert "→ Did you mean `mpg`?" in stderr_text

    def test_inline_graphs_are_published(self):
        from stata_code.kernel import StataKernel

        inline_b64 = base64.b64encode(b"fake-png").decode()
        result = _make_run_result(
            graphs=[
                GraphInfo(ref="graph://r/0", format=GraphFormat.PNG, inline=inline_b64),
                GraphInfo(ref="graph://r/1", format=GraphFormat.PNG, inline=None),
            ]
        )
        kb = StataKernel()
        published: list[tuple[str, str]] = []
        with patch.object(
            kb, "_publish_image", side_effect=lambda b, f: published.append((b, f))
        ):
            with patch("stata_code.kernel.kernel.execute", return_value=result):
                kb.do_execute("scatter y x", silent=False)
        # Only the graph with inline bytes is published.
        assert published == [(inline_b64, "png")]


class TestStreamAndPublishImage:
    def _kernel_with_recorder(self):
        from stata_code.kernel import StataKernel

        kb = StataKernel()
        sent: list[tuple[Any, str, dict]] = []
        kb.send_response = lambda sock, msg_type, content: sent.append(
            (sock, msg_type, content)
        )
        return kb, sent

    def test_stream_sends_stream_message(self):
        kb, sent = self._kernel_with_recorder()
        kb._stream("stdout", "hello world\n")
        assert len(sent) == 1
        _sock, msg_type, content = sent[0]
        assert msg_type == "stream"
        assert content == {"name": "stdout", "text": "hello world\n"}

    def test_stream_skips_empty_text(self):
        kb, sent = self._kernel_with_recorder()
        kb._stream("stdout", "")
        assert sent == []

    def test_stream_swallows_send_failures(self):
        from stata_code.kernel import StataKernel

        kb = StataKernel()

        def boom(*_a, **_k):
            raise RuntimeError("no socket")

        kb.send_response = boom
        kb._stream("stdout", "text")  # must not raise

    def test_publish_image_maps_svg_mime(self):
        kb, sent = self._kernel_with_recorder()
        kb._publish_image("c3ZnZGF0YQ==", "svg")
        assert len(sent) == 1
        _sock, msg_type, content = sent[0]
        assert msg_type == "display_data"
        assert content["data"]["image/svg+xml"] == "c3ZnZGF0YQ=="
        assert content["data"]["text/plain"] == "[graph: svg]"

    def test_publish_image_unknown_format_falls_back_to_png(self):
        kb, sent = self._kernel_with_recorder()
        kb._publish_image("YWJj", "gif")
        assert sent[0][2]["data"]["image/png"] == "YWJj"

    def test_publish_image_swallows_send_failures(self):
        from stata_code.kernel import StataKernel

        kb = StataKernel()

        def boom(*_a, **_k):
            raise RuntimeError("no socket")

        kb.send_response = boom
        kb._publish_image("YWJj", "png")  # must not raise


class TestDoInspectEdgeCases:
    def test_non_numeric_cursor_pos_falls_back_to_zero(self):
        from stata_code.kernel import StataKernel

        kb = StataKernel()
        result = kb.do_inspect("summarize", cursor_pos="not-a-number")
        # cursor 0 still resolves the word under it.
        assert result["found"] is True
        assert result["name"] == "summarize"
        assert result["cursor_start"] == 0
        assert result["cursor_end"] == 9

    def test_whitespace_only_code_reports_not_found(self):
        from stata_code.kernel import StataKernel

        kb = StataKernel()
        assert kb.do_inspect("   ", cursor_pos=1) == {"status": "ok", "found": False}


class TestKernelRunMain:
    def test_no_args_prints_usage(self, monkeypatch, capsys):
        from stata_code.kernel import kernel as kernel_module

        monkeypatch.setattr(sys, "argv", ["stata-code-kernel"])
        kernel_module.run_main()
        out = capsys.readouterr().out
        assert "usage: stata-code-kernel install" in out
        assert "-f <connection_file>" in out

    def test_help_flag_prints_usage(self, monkeypatch, capsys):
        from stata_code.kernel import kernel as kernel_module

        monkeypatch.setattr(sys, "argv", ["stata-code-kernel", "--help"])
        kernel_module.run_main()
        assert "usage: stata-code-kernel install" in capsys.readouterr().out

    def test_install_subcommand_defaults_to_user(self, monkeypatch):
        from stata_code.kernel import kernel as kernel_module

        calls: list[dict] = []
        monkeypatch.setattr(
            kernel_module,
            "install_kernel",
            lambda **kwargs: calls.append(kwargs),
        )
        monkeypatch.setattr(sys, "argv", ["stata-code-kernel", "install"])
        kernel_module.run_main()
        assert calls == [{"user": True, "system": False}]

    def test_install_subcommand_system_flag(self, monkeypatch):
        from stata_code.kernel import kernel as kernel_module

        calls: list[dict] = []
        monkeypatch.setattr(
            kernel_module,
            "install_kernel",
            lambda **kwargs: calls.append(kwargs),
        )
        monkeypatch.setattr(sys, "argv", ["stata-code-kernel", "install", "--system"])
        kernel_module.run_main()
        assert calls == [{"user": False, "system": True}]

    def test_launch_path_uses_stata_kernel_class(self, monkeypatch):
        from stata_code.kernel import kernel as kernel_module
        from stata_code.kernel.kernel import StataKernel

        monkeypatch.setattr(sys, "argv", ["prog", "-f", "/tmp/conn.json"])
        with patch("ipykernel.kernelapp.IPKernelApp.launch_instance") as launch:
            kernel_module.run_main()
        launch.assert_called_once_with(kernel_class=StataKernel)
