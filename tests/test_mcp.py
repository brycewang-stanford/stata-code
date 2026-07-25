"""Tests for the v0.2 MCP server (rewired to the v1.0 runner pipeline)."""

from __future__ import annotations

import asyncio
import json
import threading
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

    def test_tool_input_schemas_avoid_openai_forbidden_top_level_keywords(self):
        from stata_code.mcp.server import _tool_definitions

        forbidden = {"oneOf", "anyOf", "allOf", "enum", "not"}
        for tool in _tool_definitions():
            schema = tool.inputSchema
            assert schema is not None
            assert schema.get("type") == "object"
            assert forbidden.isdisjoint(schema), tool.name

    def test_notebook_locate_schema_keeps_query_fields_optional(self):
        from stata_code.mcp.server import _tool_definitions

        loc = next(t for t in _tool_definitions() if t.name == "notebook_locate")
        schema = loc.inputSchema
        assert schema["required"] == ["path"]
        assert {"snippet", "regex", "error_text"} <= set(schema["properties"])

    def test_notebook_insert_cell_schema_keeps_anchor_fields_optional(self):
        from stata_code.mcp.server import _tool_definitions

        ins = next(t for t in _tool_definitions() if t.name == "notebook_insert_cell")
        schema = ins.inputSchema
        assert schema["required"] == ["path", "source"]
        assert {"after_cell_id", "before_cell_id", "at_start", "at_end"} <= set(
            schema["properties"]
        )

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
        assert "plan_cross_stack_parity_audit" in prompts
        assert "data_mcp_to_stata_handoff" in prompts
        assert "summarize_estimation_results" in prompts
        assert "did_event_study" in prompts
        assert "iv_2sls" in prompts
        assert "rdd" in prompts
        assert "publication_table" in prompts
        assert "cross_validate_did" in prompts
        assert prompts["run_do_file_and_report"].arguments[0].name == "path"

        parity_args = {
            arg.name: arg for arg in prompts["plan_cross_stack_parity_audit"].arguments
        }
        assert parity_args["stata_entrypoint"].required is True
        assert "target" in parity_args

        handoff_args = {
            arg.name: arg for arg in prompts["data_mcp_to_stata_handoff"].arguments
        }
        assert handoff_args["raw_path"].required is True
        assert "metadata_path" in handoff_args

        did_args = {arg.name: arg for arg in prompts["did_event_study"].arguments}
        assert did_args["data_path"].required is True
        assert did_args["outcome"].required is True
        assert did_args["cohort"].required is True

        iv_args = {arg.name: arg for arg in prompts["iv_2sls"].arguments}
        assert iv_args["endogenous"].required is True
        assert iv_args["instruments"].required is True

        rdd_args = {arg.name: arg for arg in prompts["rdd"].arguments}
        assert rdd_args["running_var"].required is True
        assert "cutoff" in rdd_args

        table_args = {arg.name: arg for arg in prompts["publication_table"].arguments}
        assert table_args["models"].required is True

        cross_args = {arg.name: arg for arg in prompts["cross_validate_did"].arguments}
        assert cross_args["data_path"].required is True
        assert cross_args["cohort"].required is True

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

    def test_sessions_resource_includes_partial_warnings(self, monkeypatch):
        from stata_code.mcp import server

        payload = {
            "sessions": [{"session_id": "main", "frame": "default", "n_obs": 0}],
            "warnings": [{"session_id": "slow", "reason": "timeout"}],
        }

        class DetailedPool:
            @staticmethod
            def list_session_info_detailed():
                return payload

        monkeypatch.setattr(server, "get_default_pool", lambda: DetailedPool())

        content = server._read_resource_payload("stata://sessions")
        assert json.loads(content.content) == payload

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

    def test_get_prompt_renders_parity_and_data_handoff_workflows(self):
        from stata_code.mcp.server import _get_mcp_prompt

        parity = _get_mcp_prompt(
            "plan_cross_stack_parity_audit",
            {
                "stata_entrypoint": "analysis/csdid.do",
                "target": "overall ATT",
                "external_stacks": "R did, Python",
            },
        )
        parity_text = parity.messages[0].content.text
        assert "analysis/csdid.do" in parity_text
        assert "Freeze one common analysis sample" in parity_text
        assert "Do not claim stata-code ran R or Python" in parity_text

        handoff = _get_mcp_prompt(
            "data_mcp_to_stata_handoff",
            {
                "raw_path": "data/raw/oecd.csv",
                "metadata_path": "data/raw/oecd.source.json",
                "analysis_goal": "scatter plot",
            },
        )
        handoff_text = handoff.messages[0].content.text
        assert "data/raw/oecd.csv" in handoff_text
        assert "persist_log_files=true" in handoff_text
        assert "Do not cite an LLM memory value" in handoff_text

    def test_get_prompt_renders_turnkey_recipe_workflows(self):
        from stata_code.mcp.server import _get_mcp_prompt

        did = _get_mcp_prompt(
            "did_event_study",
            {
                "data_path": "data/cfps_panel.dta",
                "outcome": "wage",
                "cohort": "first_treat",
                "controls": "age age2 edu",
            },
        )
        did_text = did.messages[0].content.text
        assert "recipes/did-event-study.md" in did_text
        assert "Callaway-Sant'Anna" in did_text
        assert "install_package" in did_text

        iv = _get_mcp_prompt(
            "iv_2sls",
            {
                "data_path": "data/wage.dta",
                "outcome": "earnings",
                "endogenous": "schooling",
                "instruments": "quarter_birth",
            },
        )
        iv_text = iv.messages[0].content.text
        assert "recipes/iv-2sls.md" in iv_text
        assert "first-stage F" in iv_text
        assert "LATE" in iv_text

        rdd = _get_mcp_prompt(
            "rdd",
            {
                "data_path": "data/rd.dta",
                "outcome": "score",
                "running_var": "margin",
            },
        )
        rdd_text = rdd.messages[0].content.text
        assert "recipes/rdd.md" in rdd_text
        assert "rddensity" in rdd_text
        assert "local" in rdd_text

        table = _get_mcp_prompt(
            "publication_table",
            {"models": "m1 m2", "output_path": "tables/main.tex"},
        )
        table_text = table.messages[0].content.text
        assert "recipes/publication-tables.md" in table_text
        assert "Do not re-estimate models" in table_text

        cross = _get_mcp_prompt(
            "cross_validate_did",
            {
                "data_path": "data/cfps_panel.dta",
                "outcome": "wage",
                "cohort": "first_treat",
            },
        )
        cross_text = cross.messages[0].content.text
        assert "recipes/cross-validation.md" in cross_text
        assert "Callaway-Sant'Anna ATT" in cross_text
        assert "reconcile the spec" in cross_text

    def test_stata_run_missing_code_returns_error_json(self):
        from stata_code.mcp.server import _dispatch

        out = asyncio.run(_dispatch("stata_run", {}))
        assert out.isError is True
        body = _json_body(out)
        assert "error" in body

    def test_stata_run_rejects_unknown_argument_before_execute(self, monkeypatch):
        from stata_code.mcp import server

        def should_not_run(*_args, **_kwargs):
            raise AssertionError("pool_execute should not run")

        monkeypatch.setattr(server, "pool_execute", should_not_run)

        out = asyncio.run(
            server._dispatch(
                "stata_run",
                {"code": "display 1", "originPath": "/tmp/example.do"},
            )
        )
        assert out.isError is True
        body = _json_body(out)
        assert body["kind"] == "invalid_request"
        assert "originPath" in body["error"]

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

    def test_stata_run_maps_pool_value_error_to_invalid_request(self, monkeypatch):
        from stata_code.mcp import server

        def reject(*_args, **_kwargs):
            raise ValueError("bad caller option")

        monkeypatch.setattr(server, "pool_execute", reject)

        out = asyncio.run(server._dispatch("stata_run", {"code": "display 1"}))
        assert out.isError is True
        body = _json_body(out)
        assert body["kind"] == "invalid_request"
        assert "bad caller option" in body["error"]

    def test_list_runs_rejects_bool_offset(self):
        from stata_code.mcp.server import _dispatch

        out = asyncio.run(_dispatch("list_runs", {"log_dir": "/tmp/x", "offset": True}))
        assert out.isError is True
        body = _json_body(out)
        assert body["kind"] == "invalid_request"
        assert "offset" in body["error"]

    def test_list_runs_passes_offset(self, monkeypatch):
        from stata_code.mcp import server

        def fake_list_runs(**kwargs):
            assert kwargs["offset"] == 2
            return {
                "log_dir": "/tmp/x",
                "scanned_count": 0,
                "match_count": 0,
                "skipped_count": 0,
                "limit": kwargs["limit"],
                "offset": kwargs["offset"],
                "truncated": False,
                "runs": [],
            }

        monkeypatch.setattr(server, "_list_runs", fake_list_runs)

        out = asyncio.run(
            server._dispatch("list_runs", {"log_dir": "/tmp/x", "limit": 5, "offset": 2})
        )
        body = _json_body(out)
        assert body["limit"] == 5
        assert body["offset"] == 2

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
        from stata_code.core._runtime import PystataNotAvailable
        from stata_code.mcp import server

        def boom() -> dict:
            raise PystataNotAvailable("pystata is not importable")

        monkeypatch.setattr(server, "pool_stata_info", boom)
        body = json.loads(server._info_payload_from_pool())
        assert body == {
            "available": False,
            "schema_version": "1.0",
            "capabilities": [],
        }

    def test_stata_info_available_shape(self, monkeypatch):
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
        assert body["version"] == "19.0"
        # Top-level alias mirrors stata.edition (the enum value) verbatim so
        # both fields agree within the same payload.
        assert body["edition"] == "MP"
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

    def test_blocking_runner_uses_a_worker_thread(self):
        from stata_code.mcp import server

        async def exercise() -> tuple[int, int]:
            event_loop_thread = threading.get_ident()
            worker_thread = await server._run_blocking(threading.get_ident)
            return event_loop_thread, worker_thread

        event_loop_thread, worker_thread = asyncio.run(exercise())
        assert worker_thread != event_loop_thread

    @pytest.mark.parametrize(
        ("tool_name", "arguments", "patched_name", "replacement", "expected"),
        [
            (
                "stata_run",
                {"code": "display 1"},
                "_run_tool",
                lambda arguments: {"submitted": arguments["code"]},
                {"submitted": "display 1"},
            ),
            (
                "list_sessions",
                {},
                "_list_sessions_payload",
                lambda _pool: {"sessions": []},
                {"sessions": []},
            ),
        ],
    )
    def test_dispatch_routes_blocking_functions_through_runner(
        self,
        monkeypatch,
        tool_name,
        arguments,
        patched_name,
        replacement,
        expected,
    ):
        from stata_code.mcp import server

        calls = []

        async def recording_runner(operation, /, *args, **kwargs):
            calls.append((operation, args, kwargs))
            return operation(*args, **kwargs)

        monkeypatch.setattr(server, patched_name, replacement)
        monkeypatch.setattr(server, "_run_blocking", recording_runner)
        if tool_name == "list_sessions":
            monkeypatch.setattr(server, "get_default_pool", object)

        result = asyncio.run(server._dispatch(tool_name, arguments))

        assert len(calls) == 1
        assert result == expected or result.structuredContent == expected

    @pytest.mark.parametrize(
        ("tool_name", "method_name", "method_result", "expected"),
        [
            (
                "cancel_session",
                "request_cancel",
                (True, True),
                {"was_pending": False, "is_pending": True, "killed_worker": True},
            ),
            (
                "reset_session",
                "reset_session",
                True,
                {"dropped_frame": True},
            ),
        ],
    )
    def test_session_mutations_cross_blocking_runner(
        self, monkeypatch, tool_name, method_name, method_result, expected
    ):
        from stata_code.mcp import server

        class Pool:
            pass

        pool = Pool()
        setattr(pool, method_name, lambda session_id: method_result)
        observed = []

        async def recording_runner(operation, /, *args, **kwargs):
            observed.append((operation, args))
            return operation(*args, **kwargs)

        monkeypatch.setattr(server, "get_default_pool", lambda: pool)
        monkeypatch.setattr(server, "_run_blocking", recording_runner)

        result = asyncio.run(server._dispatch(tool_name, {"session_id": "main"}))

        assert len(observed) == 1
        assert observed[0][1] == ("main",)
        for key, value in expected.items():
            assert result.structuredContent[key] == value

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
        # Backward-compatible flat alias mirrors stata.edition (the enum
        # value) so both fields agree within the same payload.
        assert body["edition"] == "MP"
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
