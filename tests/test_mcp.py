"""Tests for the v0.2 MCP server (rewired to the v1.0 runner pipeline)."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

pytest.importorskip("mcp", reason="mcp package not installed")

from mcp.types import CallToolResult, ImageContent, TextContent  # noqa: E402

from stata_code.core._runtime import is_available  # noqa: E402

_real_stata = is_available()


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


# ─────────────────────────────────────────────────────────────────────────────
# Tool-registry surface (no Stata required)
# ─────────────────────────────────────────────────────────────────────────────


class TestToolRegistry:
    def test_lists_expected_tools(self):
        from stata_code.mcp.server import _tool_definitions

        names = {t.name for t in _tool_definitions()}
        assert {
            "stata_run",
            "stata_info",
            "get_log",
            "get_graph",
            "get_matrix",
            "list_sessions",
            "reset_session",
        }.issubset(names)

    def test_stata_run_schema_requires_code(self):
        from stata_code.mcp.server import _tool_definitions

        run = next(t for t in _tool_definitions() if t.name == "stata_run")
        schema = run.inputSchema
        assert schema["type"] == "object"
        assert "code" in schema["properties"]
        assert "code" in schema["required"]
        assert "ok=false" in run.description
        assert "iterative debug/fix loops" in run.description
        assert "run/validate-only requests" in run.description
        assert "without rewriting source code" in run.description
        # Token-economy options are exposed
        assert "include_graphs" in schema["properties"]
        assert "include_full_log" in schema["properties"]
        assert "persist_log_files" in schema["properties"]
        assert "persist_generated_files" in schema["properties"]
        assert "origin_path" in schema["properties"]
        assert "use_origin_workdir" in schema["properties"]
        assert "working_dir" in schema["properties"]
        assert run.outputSchema is not None
        assert run.annotations is not None
        assert run.annotations.readOnlyHint is False
        assert run.annotations.destructiveHint is True

    def test_get_graph_schema_requires_ref(self):
        from stata_code.mcp.server import _tool_definitions

        gg = next(t for t in _tool_definitions() if t.name == "get_graph")
        assert "ref" in gg.inputSchema["required"]
        assert gg.outputSchema is not None
        assert gg.annotations is not None
        assert gg.annotations.readOnlyHint is True

    def test_notebook_locate_schema_oneof(self):
        from stata_code.mcp.server import _tool_definitions

        loc = next(t for t in _tool_definitions() if t.name == "notebook_locate")
        schema = loc.inputSchema
        assert "oneOf" in schema
        required_sets = [tuple(sorted(o.get("required", []))) for o in schema["oneOf"]]
        assert ("snippet",) in required_sets
        assert ("regex",) in required_sets
        assert ("error_text",) in required_sets

    def test_notebook_insert_cell_schema_oneof(self):
        from stata_code.mcp.server import _tool_definitions

        ins = next(t for t in _tool_definitions() if t.name == "notebook_insert_cell")
        schema = ins.inputSchema
        assert "oneOf" in schema
        anchors = {tuple(sorted(o.get("required", []))) for o in schema["oneOf"]}
        assert anchors == {
            ("after_cell_id",),
            ("before_cell_id",),
            ("at_start",),
            ("at_end",),
        }

    def test_resource_templates_include_ref_shapes(self):
        from stata_code.mcp.server import _resource_templates

        templates = {tmpl.name: tmpl for tmpl in _resource_templates()}
        assert templates["stata_log_ref"].uriTemplate == "log://{request_id}"
        assert templates["stata_graph_ref"].uriTemplate == "graph://{request_id}/{index}"
        assert (
            templates["stata_matrix_ref"].uriTemplate
            == "matrix://{request_id}/{scope}/{name}"
        )

    def test_prompts_include_agent_workflows(self):
        from stata_code.mcp.server import _prompt_definitions

        prompts = {prompt.name: prompt for prompt in _prompt_definitions()}
        assert "run_do_file_and_report" in prompts
        assert "debug_stata_error" in prompts
        assert "fix_and_rerun_until_passes" in prompts
        assert "replication_audit" in prompts
        assert "summarize_estimation_results" in prompts
        assert prompts["run_do_file_and_report"].arguments[0].name == "path"

    def test_prompts_include_notebook_cell_workflows(self):
        from stata_code.mcp.server import _get_mcp_prompt, _prompt_definitions

        prompts = {p.name: p for p in _prompt_definitions()}
        assert "run_notebook_cell_and_report" in prompts
        assert "fix_and_rerun_notebook_cell" in prompts

        # Both prompts require path + cell_id; fix prompt also takes
        # max_attempts.
        run_args = {a.name: a for a in prompts["run_notebook_cell_and_report"].arguments}
        assert run_args["path"].required is True
        assert run_args["cell_id"].required is True

        fix_args = {a.name: a for a in prompts["fix_and_rerun_notebook_cell"].arguments}
        assert fix_args["path"].required is True
        assert fix_args["cell_id"].required is True
        assert "max_attempts" in fix_args

        # Rendered prompt body wires origin_cell_id and the expected_source
        # concurrency guard — these are the load-bearing details for agents.
        rendered = _get_mcp_prompt(
            "fix_and_rerun_notebook_cell",
            {"path": "/x.ipynb", "cell_id": "abc"},
        )
        body = rendered.messages[0].content.text
        assert "origin_cell_id" in body
        assert "expected_source" in body
        assert "restart kernel" in body


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch surface (no Stata required for non-execution branches)
# ─────────────────────────────────────────────────────────────────────────────


class TestDispatch:
    def test_unknown_tool_returns_text_error(self):
        from stata_code.mcp.server import _dispatch

        out = asyncio.run(_dispatch("nonexistent_tool", {}))
        assert out.isError is True
        assert "Unknown tool" in _text_items(out)[0].text

    def test_get_log_unknown_ref_returns_error_text(self):
        from stata_code.mcp.server import _dispatch

        out = asyncio.run(_dispatch("get_log", {"ref": "log://does-not-exist"}))
        assert out.isError is True
        assert "Unknown ref" in _text_items(out)[0].text

    def test_get_graph_unknown_ref_returns_error_text(self):
        from stata_code.mcp.server import _dispatch

        out = asyncio.run(_dispatch("get_graph", {"ref": "graph://no/0"}))
        assert out.isError is True
        assert "Unknown ref" in _text_items(out)[0].text

    def test_get_matrix_unknown_ref_returns_error_text(self):
        from stata_code.mcp.server import _dispatch

        out = asyncio.run(_dispatch("get_matrix", {"ref": "matrix://no/r/M"}))
        assert out.isError is True
        assert "Unknown ref" in _text_items(out)[0].text

    def test_get_matrix_known_ref_returns_payload(self):
        """Roundtrip: stash a payload via _refs and let dispatch deliver it."""
        from stata_code.core import _refs
        from stata_code.mcp.server import _dispatch

        ref = "matrix://test-fake/e/M"
        _refs.put(
            ref,
            {"rows": ["y1"], "cols": ["x1", "_cons"], "values": [[0.5, 1.0]]},
        )
        try:
            out = asyncio.run(_dispatch("get_matrix", {"ref": ref}))
        finally:
            _refs.discard(ref)
        assert out.isError is False
        body = _json_body(out)
        assert out.structuredContent == body
        assert body == {
            "rows": ["y1"],
            "cols": ["x1", "_cons"],
            "values": [[0.5, 1.0]],
        }

    def test_resources_list_static_and_dynamic_refs(self):
        from stata_code.core import _refs
        from stata_code.mcp.server import _list_mcp_resources

        ref = "matrix://resource-test/e/M"
        _refs.put(ref, {"rows": ["r"], "cols": ["c"], "values": [[1.0]]})
        try:
            by_uri = {str(resource.uri): resource for resource in _list_mcp_resources()}
        finally:
            _refs.discard(ref)

        assert "stata://schema/run-result" in by_uri
        assert "stata://server/capabilities" in by_uri
        assert "stata://sessions" in by_uri
        assert by_uri[ref].mimeType == "application/json"

    def test_read_static_schema_resource(self):
        from stata_code.mcp.server import _read_resource_payload

        content = _read_resource_payload("stata://schema/run-result")
        assert content.mime_type == "application/schema+json"
        body = json.loads(content.content)
        assert body["title"] == "RunResult"
        assert body["properties"]["ok"]["type"] == "boolean"

    def test_read_matrix_resource(self):
        from stata_code.core import _refs
        from stata_code.mcp.server import _read_resource_payload

        ref = "matrix://resource-test/e/M"
        _refs.put(ref, {"rows": ["r"], "cols": ["c"], "values": [[1.0]]})
        try:
            content = _read_resource_payload(ref)
        finally:
            _refs.discard(ref)

        assert content.mime_type == "application/json"
        assert json.loads(content.content) == {
            "rows": ["r"],
            "cols": ["c"],
            "values": [[1.0]],
        }

    def test_get_prompt_renders_workflow_arguments(self):
        from stata_code.mcp.server import _get_mcp_prompt

        prompt = _get_mcp_prompt(
            "run_do_file_and_report",
            {"path": "analysis/main.do", "session_id": "audit"},
        )
        assert prompt.description == "Run the Stata do-file and report"
        text = prompt.messages[0].content.text
        assert "analysis/main.do" in text
        assert "audit" in text
        assert "stata_run" in text
        assert "Do not edit" not in text

    def test_get_prompt_rejects_unknown_prompt(self):
        from stata_code.mcp.server import _get_mcp_prompt

        with pytest.raises(ValueError, match="Unknown prompt"):
            _get_mcp_prompt("nope", {})

    def test_stata_run_missing_code_returns_error_json(self):
        from stata_code.mcp.server import _dispatch

        out = asyncio.run(_dispatch("stata_run", {}))
        assert out.isError is True
        body = _json_body(out)
        assert "error" in body

    def test_stata_run_rejects_non_boolean_argument(self):
        """The schema declares ``include_full_log`` as boolean, but many MCP
        clients do not validate. Server must reject coerced strings rather
        than silently truthy-coerce them (``bool("false") is True``).
        """
        from stata_code.mcp.server import _dispatch

        out = asyncio.run(
            _dispatch(
                "stata_run", {"code": "display 1", "include_full_log": "false"}
            )
        )
        assert out.isError is True
        body = _json_body(out)
        assert "include_full_log" in body["error"]
        assert "boolean" in body["error"].lower()

    def test_notebook_insert_cell_rejects_string_at_start(self, tmp_path):
        from stata_code.mcp.server import _dispatch

        nb_path = tmp_path / "nb.ipynb"
        nb_path.write_text(
            json.dumps(
                {
                    "nbformat": 4,
                    "nbformat_minor": 5,
                    "metadata": {},
                    "cells": [
                        {
                            "cell_type": "code",
                            "id": "a",
                            "source": "x=1",
                            "metadata": {},
                            "outputs": [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        out = asyncio.run(
            _dispatch(
                "notebook_insert_cell",
                {"path": str(nb_path), "source": "x=2", "at_start": "true"},
            )
        )
        assert out.isError is True
        body = _json_body(out)
        assert "at_start" in body["error"]
        # No cell was appended despite the truthy-looking string.
        cells = json.loads(nb_path.read_text(encoding="utf-8"))["cells"]
        assert len(cells) == 1

    def test_notebook_insert_cell_accepts_bool_at_start(self, tmp_path):
        from stata_code.mcp.server import _dispatch

        nb_path = tmp_path / "nb.ipynb"
        nb_path.write_text(
            json.dumps(
                {
                    "nbformat": 4,
                    "nbformat_minor": 5,
                    "metadata": {},
                    "cells": [
                        {
                            "cell_type": "code",
                            "id": "a",
                            "source": "x=1",
                            "metadata": {},
                            "outputs": [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        out = asyncio.run(
            _dispatch(
                "notebook_insert_cell",
                {"path": str(nb_path), "source": "x=2", "at_start": True},
            )
        )
        assert out.isError is not True
        cells = json.loads(nb_path.read_text(encoding="utf-8"))["cells"]
        assert len(cells) == 2
        assert cells[0]["source"] == "x=2"

    def test_stata_info_unavailable_shape(self, monkeypatch):
        from stata_code.mcp import server

        monkeypatch.setattr(server, "is_available", lambda: False)
        body = json.loads(server._info_payload())
        assert body == {
            "available": False,
            "schema_version": "1.0",
            "capabilities": [],
        }

    def test_stata_info_available_shape(self, monkeypatch):
        from stata_code.core import _runtime
        from stata_code.mcp import server

        class DummyToolkit:
            @staticmethod
            def macroExpand(value: str) -> str:
                assert value == "`c(stata_version)'"
                return "19.0"

        class DummySfi:
            SFIToolkit = DummyToolkit

        class DummyRuntime:
            edition = "mp"
            sfi = DummySfi()

        monkeypatch.setattr(server, "is_available", lambda: True)
        monkeypatch.setattr(_runtime, "get_runtime", lambda: DummyRuntime())

        body = json.loads(server._info_payload())
        assert body["available"] is True
        assert body["stata"] == {
            "version": "19.0",
            "edition": "MP",
            "backend": "pystata",
        }
        assert body["version"] == "19.0"
        assert "matrix_ref" in body["capabilities"]
        # Phase 1-3 surface advertised so clients can feature-detect.
        for cap in (
            "notebook_navigation",
            "notebook_search",
            "notebook_edit",
            "run_index",
            "origin_echo",
        ):
            assert cap in body["capabilities"]

    def test_stata_info_dispatch_does_not_block_event_loop(self, monkeypatch):
        from stata_code.mcp import server

        payload = json.dumps(
            {
                "available": False,
                "schema_version": "1.0",
                "capabilities": [],
            }
        )

        def slow_info() -> str:
            time.sleep(0.05)
            return payload

        monkeypatch.setattr(server, "_info_payload_from_pool", slow_info)

        async def probe() -> CallToolResult:
            task = asyncio.create_task(_dispatch_stata_info())
            await asyncio.sleep(0.01)
            assert not task.done()
            return await task

        async def _dispatch_stata_info() -> CallToolResult:
            return await server._dispatch("stata_info", {})

        out = asyncio.run(probe())
        assert _json_body(out) == json.loads(payload)
        assert out.structuredContent == json.loads(payload)

    def test_stata_run_dispatch_does_not_block_event_loop(self, monkeypatch):
        from stata_code.core.schema import RunResult
        from stata_code.mcp import server

        payload = {
            "ok": True,
            "rc": 0,
            "session_id": "main",
            "request_id": "req-test",
            "started_at": "2026-05-10T00:00:00.000Z",
            "elapsed_ms": 50,
            "stata_elapsed_ms": 50,
            "stata": {
                "version": "19.0",
                "edition": "MP",
                "backend": "pystata",
            },
            "log": {
                "head": "slow run",
                "tail": "",
                "lines_total": 1,
                "bytes_total": 8,
                "truncated": False,
                "complete": True,
            },
            "results": {
                "r": {"scalars": {}, "macros": {}, "matrices": {}},
                "e": {"scalars": {}, "macros": {}, "matrices": {}},
                "last_estimation_cmd": None,
            },
            "dataset": {
                "frame": "default",
                "n_obs": 0,
                "n_vars": 0,
                "changed": False,
                "filename": None,
                "variables": None,
            },
            "graphs": [],
            "warnings": [],
            "error": None,
            "schema_version": "1.0",
            "capabilities": [],
        }

        def slow_run(code: str, **_kwargs):
            assert code == "display 1"
            time.sleep(0.05)
            return RunResult.model_validate(payload)

        monkeypatch.setattr(server, "pool_execute", slow_run)

        async def probe() -> CallToolResult:
            task = asyncio.create_task(_dispatch_stata_run())
            await asyncio.sleep(0.01)
            assert not task.done()
            return await task

        async def _dispatch_stata_run() -> CallToolResult:
            return await server._dispatch("stata_run", {"code": "display 1"})

        out = asyncio.run(probe())
        expected = json.loads(RunResult.model_validate(payload).model_dump_json())
        assert _json_body(out) == expected
        assert out.structuredContent == expected

    def test_list_sessions_dispatch_does_not_block_event_loop(self, monkeypatch):
        from stata_code.mcp import server

        sessions = [{"session_id": "main", "frame": "default", "n_obs": 0}]

        class SlowPool:
            @staticmethod
            def list_session_info():
                time.sleep(0.05)
                return sessions

        monkeypatch.setattr(server, "get_default_pool", lambda: SlowPool())

        async def probe() -> CallToolResult:
            task = asyncio.create_task(_dispatch_list_sessions())
            await asyncio.sleep(0.01)
            assert not task.done()
            return await task

        async def _dispatch_list_sessions() -> CallToolResult:
            return await server._dispatch("list_sessions", {})

        out = asyncio.run(probe())
        assert _json_body(out) == sessions
        assert out.structuredContent == {"sessions": sessions}

    def test_cancel_session_dispatch_does_not_block_event_loop(self, monkeypatch):
        from stata_code.mcp import server

        class SlowPool:
            @staticmethod
            def kill_session(session_id: str):
                assert session_id == "main"
                time.sleep(0.05)
                return True

        monkeypatch.setattr(server, "cancel", lambda _sid: True)
        monkeypatch.setattr(server, "is_cancel_pending", lambda _sid: False)
        monkeypatch.setattr(server, "get_default_pool", lambda: SlowPool())

        async def probe() -> CallToolResult:
            task = asyncio.create_task(_dispatch_cancel_session())
            await asyncio.sleep(0.01)
            assert not task.done()
            return await task

        async def _dispatch_cancel_session() -> CallToolResult:
            return await server._dispatch("cancel_session", {"session_id": "main"})

        out = asyncio.run(probe())
        assert _json_body(out) == {
            "session_id": "main",
            "was_pending": False,
            "is_pending": False,
            "killed_worker": True,
        }

    def test_reset_session_dispatch_does_not_block_event_loop(self, monkeypatch):
        from stata_code.mcp import server

        class SlowPool:
            @staticmethod
            def kill_session(session_id: str):
                assert session_id == "main"
                time.sleep(0.05)
                return True

        monkeypatch.setattr(server, "get_default_pool", lambda: SlowPool())

        async def probe() -> CallToolResult:
            task = asyncio.create_task(_dispatch_reset_session())
            await asyncio.sleep(0.01)
            assert not task.done()
            return await task

        async def _dispatch_reset_session() -> CallToolResult:
            return await server._dispatch("reset_session", {"session_id": "main"})

        out = asyncio.run(probe())
        assert _json_body(out) == {
            "session_id": "main",
            "dropped_frame": True,
        }

    def test_info_payload_from_pool_happy_path(self, monkeypatch):
        """The production path returns the merged shape from a worker dict."""
        from stata_code.mcp import server

        monkeypatch.setattr(
            server,
            "pool_stata_info",
            lambda: {"version": "19.0", "edition": "MP", "backend": "pystata"},
        )
        body = json.loads(server._info_payload_from_pool())

        assert body["available"] is True
        assert body["stata"] == {
            "version": "19.0",
            "edition": "MP",
            "backend": "pystata",
        }
        # Backward-compatible flat aliases use the lower-cased edition (matches
        # the legacy raw rt.edition value).
        assert body["edition"] == "mp"
        assert body["version"] == "19.0"
        assert "matrix_ref" in body["capabilities"]
        assert "subprocess_timeout" in body["capabilities"]
        assert "error" not in body

    def test_info_payload_from_pool_pystata_not_available_clean(self, monkeypatch):
        """A worker-side PystataNotAvailable error reports unavailable cleanly."""
        from stata_code.mcp import server

        def boom() -> dict:
            raise RuntimeError(
                "worker reported failure: PystataNotAvailable: pystata is not importable"
            )

        monkeypatch.setattr(server, "pool_stata_info", boom)
        body = json.loads(server._info_payload_from_pool())

        assert body == {
            "available": False,
            "schema_version": "1.0",
            "capabilities": [],
        }

    def test_info_payload_from_pool_other_errors_include_diagnostic(self, monkeypatch):
        """Operational errors (timeout, crash) surface as available=false + error."""
        from stata_code.mcp import server

        def boom() -> dict:
            raise TimeoutError("worker hung past deadline")

        monkeypatch.setattr(server, "pool_stata_info", boom)
        body = json.loads(server._info_payload_from_pool())

        assert body["available"] is False
        assert body["capabilities"] == []
        assert "error" in body
        assert "TimeoutError" in body["error"]
        assert "worker hung past deadline" in body["error"]


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end with real Stata
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.stata_required
@pytest.mark.skipif(not _real_stata, reason="Stata not available")
class TestEndToEnd:
    def test_stata_run_returns_run_result_json(self):
        from stata_code.mcp.server import _dispatch

        out = asyncio.run(_dispatch("stata_run", {"code": 'display "hello mcp"'}))
        assert out.isError is False
        body = _json_body(out)
        assert out.structuredContent == body
        assert body["ok"] is True
        assert body["schema_version"] == "1.0"
        assert "hello mcp" in body["log"]["head"]

    def test_stata_run_typed_error(self):
        from stata_code.mcp.server import _dispatch

        # Ensure data is loaded so the error is "variable not found" rather
        # than "no variables defined" (state-dependent across tests).
        asyncio.run(_dispatch("stata_run", {"code": "sysuse auto, clear"}))
        out = asyncio.run(_dispatch("stata_run", {"code": "summarize mpgg"}))
        body = _json_body(out)
        assert body["ok"] is False
        assert body["error"]["kind"] == "varname_not_found"
        assert body["error"]["varname"] == "mpgg"

    def test_stata_info_reports_available(self):
        from stata_code.mcp.server import _dispatch

        out = asyncio.run(_dispatch("stata_info", {}))
        body = _json_body(out)
        assert body["available"] is True
        assert body["schema_version"] == "1.0"
        assert body["backend"] == "pystata"

    def test_get_log_after_truncation(self):
        from stata_code.mcp.server import _dispatch

        run = asyncio.run(
            _dispatch(
                "stata_run",
                {
                    "code": (
                        "forvalues i = 1/50 {\n"
                        '  display "row=`i\'"\n'
                        "}"
                    ),
                    "log_lines_head": 5,
                    "log_lines_tail": 5,
                },
            )
        )
        body = _json_body(run)
        assert body["log"]["truncated"] is True
        ref = body["log"]["ref"]

        out = asyncio.run(_dispatch("get_log", {"ref": ref}))
        full = _json_body(out)
        assert out.structuredContent == full
        assert full["lines_total"] >= 50
        assert "row=1" in full["text"]
        assert "row=50" in full["text"]

    def test_get_graph_returns_image_content(self):
        from stata_code.mcp.server import _dispatch

        # Clean slate
        asyncio.run(_dispatch("stata_run", {"code": "graph drop _all"}))
        asyncio.run(_dispatch("stata_run", {"code": "sysuse auto, clear"}))
        run = asyncio.run(
            _dispatch(
                "stata_run",
                {"code": "scatter price mpg, name(g_mcp)"},
            )
        )
        body = _json_body(run)
        assert len(body["graphs"]) == 1
        ref = body["graphs"][0]["ref"]

        out = asyncio.run(_dispatch("get_graph", {"ref": ref}))
        images = _image_items(out)
        assert len(images) == 1
        assert images[0].mimeType == "image/png"
        assert out.structuredContent["ref"] == ref
        # b64 decodes to PNG header
        import base64

        raw = base64.b64decode(images[0].data)
        assert raw[:4] == b"\x89PNG"

    def test_list_and_reset_sessions(self):
        from stata_code.mcp.server import _dispatch

        # Create a session
        asyncio.run(
            _dispatch(
                "stata_run",
                {"code": "sysuse auto, clear", "session_id": "mcp_test"},
            )
        )
        out = asyncio.run(_dispatch("list_sessions", {}))
        sessions = _json_body(out)
        assert out.structuredContent["sessions"] == sessions
        by_id = {s["session_id"]: s for s in sessions}
        assert "mcp_test" in by_id
        # Pool aggregator round-trips to the worker, so n_obs reflects the
        # real Stata state (auto.dta has 74 obs) rather than the legacy 0
        # placeholder.
        mcp_entry = by_id["mcp_test"]
        assert mcp_entry["n_obs"] == 74
        # Non-main session_id maps to a same-named frame.
        assert mcp_entry["frame"] == "mcp_test"

        # Reset
        out = asyncio.run(_dispatch("reset_session", {"session_id": "mcp_test"}))
        result = _json_body(out)
        assert result["dropped_frame"] is True

        out = asyncio.run(_dispatch("list_sessions", {}))
        sessions = _json_body(out)
        ids = {s["session_id"] for s in sessions}
        assert "mcp_test" not in ids
