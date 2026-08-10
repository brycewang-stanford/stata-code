"""Regression tests for the agent-ergonomics pass.

Each class here pins one behaviour that an agent driving the MCP server
reported as a concrete cost: an unbounded result payload, an inline graph it
could not see, a long run it could not escape, an error it could not locate, a
session poisoned by a leaked log handle, and generated files it had to hunt for
with shell tools. The Stata-backed cases are marked ``stata_required`` and skip
without a runtime; everything that can be proven without Stata is.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import time
from pathlib import Path

import pytest

from stata_code.core._runtime import is_available

_real_stata = is_available()
_needs_stata = pytest.mark.skipif(not _real_stata, reason="pystata / Stata 17+ not available")


def _tmpdir(name: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=f"sc_{name}_"))


# ─────────────────────────────────────────────────────────────────────────────
# 1. Result payload budget
# ─────────────────────────────────────────────────────────────────────────────


class TestEstimationTrimming:
    """`trim_estimation` is pure — no Stata needed."""

    def _est(self, n: int):
        from stata_code.core.schema import Coefficient, EstimationResult

        coeffs = [Coefficient(term=f"x{i}", b=float(i), se=1.0) for i in range(n)]
        return EstimationResult(
            command="regress",
            coefficients=coeffs,
            n_coefficients=n,
            model_stats={"r2": 0.5},
        )

    def test_full_is_a_passthrough(self):
        from stata_code.core.estimation import trim_estimation
        from stata_code.core.schema import IncludeEstimation

        est = self._est(5)
        out = trim_estimation(est, mode=IncludeEstimation.FULL)
        assert len(out.coefficients) == 5
        assert out.coefficients_truncated is False

    def test_summary_drops_rows_but_keeps_the_true_count(self):
        from stata_code.core.estimation import trim_estimation
        from stata_code.core.schema import IncludeEstimation

        out = trim_estimation(self._est(141), mode=IncludeEstimation.SUMMARY)
        assert out.coefficients == []
        assert out.n_coefficients == 141
        assert out.coefficients_truncated is True
        assert out.model_stats == {"r2": 0.5}

    def test_none_drops_the_block(self):
        from stata_code.core.estimation import trim_estimation
        from stata_code.core.schema import IncludeEstimation

        assert trim_estimation(self._est(3), mode=IncludeEstimation.NONE) is None
        assert trim_estimation(None, mode=IncludeEstimation.FULL) is None

    def test_max_coefficients_caps_and_flags(self):
        from stata_code.core.estimation import trim_estimation

        out = trim_estimation(self._est(141), max_coefficients=10)
        assert len(out.coefficients) == 10
        assert out.n_coefficients == 141
        assert out.coefficients_truncated is True

    def test_cap_at_or_above_the_count_is_not_a_truncation(self):
        from stata_code.core.estimation import trim_estimation

        out = trim_estimation(self._est(4), max_coefficients=4)
        assert len(out.coefficients) == 4
        assert out.coefficients_truncated is False


class TestEstimationResolvesRefdMatrices:
    """A deferred ``e(V)`` must not blank inference. Pure — no Stata needed."""

    def _returns(self, *, v_by_ref: bool):
        from stata_code.core.schema import Matrix, StataReturns

        b = Matrix(rows=["y1"], cols=["x", "_cons"], values=[[2.0, 1.0]], n_rows=1, n_cols=2)
        v_values = [[0.25, 0.0], [0.0, 0.04]]
        if v_by_ref:
            v = Matrix(rows=[], cols=[], values=None, ref="matrix://t/e/V", n_rows=2, n_cols=2)
        else:
            v = Matrix(rows=["x", "_cons"], cols=["x", "_cons"], values=v_values, n_rows=2, n_cols=2)
        e = StataReturns(
            scalars={"N": 100.0},
            macros={"cmd": "regress", "depvar": "y"},
            matrices={"b": b, "V": v},
        )
        return e, StataReturns(), v_values

    def test_inline_v_gives_standard_errors(self):
        from stata_code.core.estimation import build_estimation_from_returns

        e, r, _ = self._returns(v_by_ref=False)
        est = build_estimation_from_returns(e, r)
        assert [c.se for c in est.coefficients] == [0.5, 0.2]
        assert est.source == "e_b_v"

    def test_refd_v_without_a_resolver_still_yields_point_estimates(self):
        from stata_code.core.estimation import build_estimation_from_returns

        e, r, _ = self._returns(v_by_ref=True)
        est = build_estimation_from_returns(e, r)
        assert [c.b for c in est.coefficients] == [2.0, 1.0]
        assert [c.se for c in est.coefficients] == [None, None]

    def test_refd_v_with_a_resolver_recovers_full_inference(self):
        from stata_code.core.estimation import build_estimation_from_returns

        e, r, v_values = self._returns(v_by_ref=True)
        est = build_estimation_from_returns(
            e, r, resolve_matrix=lambda ref: v_values if ref == "matrix://t/e/V" else None
        )
        assert [c.se for c in est.coefficients] == [0.5, 0.2]
        assert all(c.statistic is not None and c.p_value is not None for c in est.coefficients)
        assert est.n_coefficients == 2

    def test_refd_b_is_resolved_too(self):
        """A stubbed e(b) used to make the whole estimation block disappear."""
        from stata_code.core.estimation import build_estimation_from_returns
        from stata_code.core.schema import Matrix, StataReturns

        e = StataReturns(
            macros={"cmd": "regress"},
            matrices={
                "b": Matrix(rows=[], cols=[], values=None, ref="matrix://t/e/b", n_rows=1, n_cols=2)
            },
        )
        est = build_estimation_from_returns(
            e, StataReturns(), resolve_matrix=lambda ref: [[2.0, 1.0]]
        )
        assert est is not None
        assert [c.b for c in est.coefficients] == [2.0, 1.0]
        # Labels were elided from the stub, so positional names stand in.
        assert [c.term for c in est.coefficients] == ["c1", "c2"]


# ─────────────────────────────────────────────────────────────────────────────
# 2. Error localization inside do-files
# ─────────────────────────────────────────────────────────────────────────────


class TestTranscriptParsing:
    """Pure parsing of Stata transcripts — no Stata needed."""

    DO_TRANSCRIPT = (
        ". sysuse auto, clear\n"
        "(1978 automobile data)\n"
        "\n"
        ". summarize price\n"
        "\n"
        ". * a comment\n"
        ". regress price mpg ///\n"
        ">     nosuchvar\n"
        "variable nosuchvar not found\n"
        "r(111);\n"
        "\n"
        "end of do-file\n"
        "\n"
        "r(111);"
    )

    def test_message_skips_end_of_do_file_boilerplate(self):
        from stata_code.core.runner import _last_error_line

        assert _last_error_line(self.DO_TRANSCRIPT) == "variable nosuchvar not found"

    def test_message_skips_break_boilerplate(self):
        from stata_code.core.runner import _last_error_line

        text = ". bootstrap: regress y x\ninsufficient observations\nr(2001);\n--Break--\nr(1);"
        assert _last_error_line(text) == "insufficient observations"

    def test_continuation_fragments_fold_into_one_command(self):
        from stata_code.core.runner import _transcript_command_echoes

        heads, logical = _transcript_command_echoes(self.DO_TRANSCRIPT)
        assert heads[-1] == "regress price mpg ///"
        assert logical[-1] == "regress price mpg nosuchvar"
        # Comment-only echoes are not commands.
        assert "* a comment" not in logical

    def test_line_is_resolved_inside_the_invoked_do_file(self):
        from stata_code.core.runner import _build_error

        tmp = _tmpdir("dofile")
        script = tmp / "analysis.do"
        script.write_text(
            "sysuse auto, clear\n"
            "summarize price\n"
            "* a comment\n"
            "regress price mpg ///\n"
            "    nosuchvar\n"
            "display 1\n",
            encoding="utf-8",
        )
        err = _build_error(111, self.DO_TRANSCRIPT, 'do "analysis.do"', None, working_dir=tmp)
        assert err.line == 4
        assert err.source_file == str(script)
        assert err.context.failing == "regress price mpg nosuchvar"
        assert err.context.before == ["sysuse auto, clear", "summarize price", "* a comment"]
        assert err.message == "variable nosuchvar not found"

    def test_source_file_is_null_when_the_failure_is_in_submitted_code(self):
        from stata_code.core.runner import _build_error

        code = "sysuse auto, clear\nregress price nosuchvar"
        transcript = (
            ". sysuse auto, clear\n"
            ". regress price nosuchvar\n"
            "variable nosuchvar not found\n"
            "r(111);"
        )
        err = _build_error(111, transcript, code, None)
        assert err.line == 2
        assert err.source_file is None

    def test_unresolvable_do_file_degrades_without_raising(self):
        from stata_code.core.runner import _build_error

        err = _build_error(
            111, self.DO_TRANSCRIPT, 'do "no/such/script.do"', None, working_dir=_tmpdir("empty")
        )
        assert err.line is None
        assert err.source_file is None
        # The diagnosis is still recovered even when the file is not readable.
        assert err.message == "variable nosuchvar not found"

    @pytest.mark.parametrize(
        "invocation",
        ['do "analysis.do"', "do analysis.do", "do analysis", 'quietly do "analysis.do"',
         'capture noisily do "analysis.do"', 'run "analysis.do"'],
    )
    def test_do_invocation_forms_are_all_recognized(self, invocation):
        from stata_code.core.runner import _do_file_candidates

        tmp = _tmpdir("forms")
        (tmp / "analysis.do").write_text("display 1\n", encoding="utf-8")
        found = _do_file_candidates(invocation, tmp)
        assert [p.name for p in found] == ["analysis.do"]

    def test_commented_out_do_lines_are_ignored(self):
        from stata_code.core.runner import _do_file_candidates

        tmp = _tmpdir("commented")
        (tmp / "analysis.do").write_text("display 1\n", encoding="utf-8")
        assert _do_file_candidates('* do "analysis.do"', tmp) == []
        assert _do_file_candidates('// do "analysis.do"', tmp) == []


# ─────────────────────────────────────────────────────────────────────────────
# 3. Log-handle hygiene
# ─────────────────────────────────────────────────────────────────────────────


class TestLogStateTaxonomy:
    def test_604_is_typed_with_an_actionable_fix(self):
        from stata_code.core.errors import classify_rc, label_for_rc, suggestions_for
        from stata_code.core.schema import ErrorKind

        assert classify_rc(604) == ErrorKind.LOG_STATE
        assert label_for_rc(604) == "log file already open"
        suggestions = suggestions_for(ErrorKind.LOG_STATE, rc=604)
        assert suggestions
        assert any(s.command == "capture log close _all" for s in suggestions)

    def test_606_gets_a_different_fix_than_604(self):
        from stata_code.core.errors import suggestions_for
        from stata_code.core.schema import ErrorKind

        s604 = suggestions_for(ErrorKind.LOG_STATE, rc=604)
        s606 = suggestions_for(ErrorKind.LOG_STATE, rc=606)
        assert s606 and s604[0].action != s606[0].action

    def test_recovery_says_retriable_without_a_code_change(self):
        from stata_code.core.errors import recovery_for
        from stata_code.core.schema import ErrorKind

        rec = recovery_for(ErrorKind.LOG_STATE)
        assert rec.retriable is True
        assert rec.needs_code_change is False

    def test_log_query_output_is_parsed_into_handle_names(self):
        from stata_code.core.runner import _open_log_names

        class _Rt:
            def run_capture(self, code):  # noqa: ARG002
                return (
                    "      name:  <unnamed>\n"
                    "       log:  /tmp/one.log, on\n"
                    "  log type:  text\n"
                    "\n"
                    "      name:  mylog\n"
                    "       log:  /tmp/two.log, on\n"
                    "  log type:  text\n",
                    0,
                    None,
                )

        assert _open_log_names(_Rt()) == ["<unnamed>", "mylog"]

    def test_no_open_logs_parses_as_empty(self):
        from stata_code.core.runner import _open_log_names

        class _Rt:
            def run_capture(self, code):  # noqa: ARG002
                return (" (closed)\n", 0, None)

        assert _open_log_names(_Rt()) == []

    def test_unnamed_handle_closes_without_a_name_argument(self):
        from stata_code.core.runner import _close_log_handles

        issued: list[str] = []

        class _Rt:
            def run_capture(self, code):
                issued.append(code)
                return ("", 0, None)

        assert _close_log_handles(_Rt(), ["<unnamed>", "mylog"]) == ["<unnamed>", "mylog"]
        assert issued == ["capture log close", "capture log close mylog"]


# ─────────────────────────────────────────────────────────────────────────────
# 4. Background runs
# ─────────────────────────────────────────────────────────────────────────────


class TestJobRegistry:
    """Registry mechanics, exercised against a stubbed executor."""

    def test_submit_returns_immediately_and_completes(self, monkeypatch):
        from stata_code.core import jobs

        gate = threading.Event()

        def _slow(code, **kwargs):  # noqa: ARG001
            gate.wait(5)
            return "RESULT"

        monkeypatch.setattr(jobs, "pool_execute", _slow)
        registry = jobs.JobRegistry()
        job = registry.submit("bootstrap, reps(10000): regress y x", session_id="bg")

        assert job.status == "running"
        assert job.done is False
        assert job.summary()["session_id"] == "bg"

        gate.set()
        assert job.wait(5) is True
        assert job.status == "done"
        assert job.result == "RESULT"
        assert job.finished_at is not None

    def test_elapsed_ms_freezes_when_the_job_finishes(self, monkeypatch):
        # Recomputing elapsed on every poll made a finished job's duration
        # grow with the caller's polling delay, so a 2s run polled a minute
        # later reported a minute of Stata time.
        from stata_code.core import jobs

        monkeypatch.setattr(jobs, "pool_execute", lambda code, **kw: "RESULT")  # noqa: ARG005
        registry = jobs.JobRegistry()
        job = registry.submit("display 1")
        assert job.wait(5) is True

        first = job.elapsed_ms()
        time.sleep(0.15)
        assert job.elapsed_ms() == first
        assert job.summary()["elapsed_ms"] == first

    def test_elapsed_ms_still_advances_while_running(self, monkeypatch):
        from stata_code.core import jobs

        gate = threading.Event()
        monkeypatch.setattr(jobs, "pool_execute", lambda code, **kw: gate.wait(10))  # noqa: ARG005
        registry = jobs.JobRegistry()
        job = registry.submit("display 1")
        first = job.elapsed_ms()
        time.sleep(0.05)
        assert job.elapsed_ms() >= first
        gate.set()
        job.wait(5)

    def test_elapsed_ms_is_frozen_on_the_error_path_too(self, monkeypatch):
        from stata_code.core import jobs

        def _boom(code, **kwargs):  # noqa: ARG001
            raise ValueError("bad option")

        monkeypatch.setattr(jobs, "pool_execute", _boom)
        registry = jobs.JobRegistry()
        job = registry.submit("display 1")
        assert job.wait(5) is True
        first = job.elapsed_ms()
        time.sleep(0.15)
        assert job.elapsed_ms() == first

    def test_failure_is_recorded_not_swallowed(self, monkeypatch):
        from stata_code.core import jobs

        def _boom(code, **kwargs):  # noqa: ARG001
            raise ValueError("bad option")

        monkeypatch.setattr(jobs, "pool_execute", _boom)
        registry = jobs.JobRegistry()
        job = registry.submit("display 1")
        assert job.wait(5) is True
        assert job.status == "error"
        assert "bad option" in job.error
        assert job.result is None

    def test_wait_times_out_without_finishing(self, monkeypatch):
        from stata_code.core import jobs

        gate = threading.Event()
        monkeypatch.setattr(jobs, "pool_execute", lambda code, **kw: gate.wait(10))  # noqa: ARG005
        registry = jobs.JobRegistry()
        job = registry.submit("display 1")
        assert job.wait(0.05) is False
        assert job.status == "running"
        gate.set()
        job.wait(5)

    def test_running_jobs_are_never_evicted(self, monkeypatch):
        from stata_code.core import jobs

        gate = threading.Event()
        monkeypatch.setattr(jobs, "pool_execute", lambda code, **kw: gate.wait(10))  # noqa: ARG005
        registry = jobs.JobRegistry(max_jobs=2)
        held = [registry.submit(f"run {i}") for i in range(4)]
        # Nothing has finished, so the registry holds all four rather than
        # dropping a handle to a job that is still executing.
        assert len(registry.list()) == 4
        gate.set()
        for job in held:
            job.wait(5)

    def test_finished_jobs_evict_oldest_first(self, monkeypatch):
        from stata_code.core import jobs

        monkeypatch.setattr(jobs, "pool_execute", lambda code, **kw: "ok")  # noqa: ARG005
        registry = jobs.JobRegistry(max_jobs=2)
        first = registry.submit("a")
        first.wait(5)
        second = registry.submit("b")
        second.wait(5)
        third = registry.submit("c")
        third.wait(5)
        assert registry.get(first.job_id) is None
        assert registry.get(third.job_id) is not None

    def test_list_is_newest_first(self, monkeypatch):
        from stata_code.core import jobs

        monkeypatch.setattr(jobs, "pool_execute", lambda code, **kw: "ok")  # noqa: ARG005
        registry = jobs.JobRegistry()
        a = registry.submit("a")
        b = registry.submit("b")
        a.wait(5)
        b.wait(5)
        assert [j.job_id for j in registry.list()][:2] == [b.job_id, a.job_id]


class TestSessionBusyResult:
    def test_busy_is_its_own_kind_and_is_retriable(self):
        from stata_code.core._pool import _build_busy_result
        from stata_code.core.schema import ErrorKind

        result = _build_busy_result(session_id="main", elapsed_ms=1200, timeout_ms=1000)
        assert result.ok is False
        assert result.rc == -5
        assert result.error.kind == ErrorKind.SESSION_BUSY
        assert result.error.recovery.retriable is True
        assert result.error.recovery.needs_code_change is False
        # Nothing ran, so the empty log is final rather than a partial capture.
        assert result.error.commands_executed == 0
        assert result.log.complete is True
        assert result.error.suggestions

    def test_worker_execute_bounds_the_queue_wait(self):
        """A call queued behind a long run must honour its own timeout."""
        from stata_code.core._pool import WorkerProcess, _WorkerBusy

        worker = WorkerProcess("busy-test")
        worker._lock.acquire()  # simulate an in-flight run  # noqa: SLF001
        try:
            started = time.monotonic()
            with pytest.raises(_WorkerBusy):
                worker.execute("display 1", {}, timeout_ms=1000)
            waited = time.monotonic() - started
            assert 0.5 < waited < 5.0, f"waited {waited:.2f}s"
        finally:
            worker._lock.release()  # noqa: SLF001


# ─────────────────────────────────────────────────────────────────────────────
# 5. MCP surface
# ─────────────────────────────────────────────────────────────────────────────

pytest.importorskip("mcp", reason="mcp package not installed")


def _call(name, arguments):
    from stata_code.mcp.server import _dispatch

    return asyncio.run(_dispatch(name, arguments))


def _body(result):
    from mcp.types import CallToolResult, TextContent

    content = result.content if isinstance(result, CallToolResult) else result
    return json.loads([c for c in content if isinstance(c, TextContent)][0].text)


def _images(result):
    from mcp.types import CallToolResult, ImageContent

    content = result.content if isinstance(result, CallToolResult) else result
    return [c for c in content if isinstance(c, ImageContent)]


class TestRunToolSchema:
    def test_new_options_are_advertised(self):
        from stata_code.mcp.server import _tool_definitions

        run = next(t for t in _tool_definitions() if t.name == "stata_run")
        props = run.inputSchema["properties"]
        for key in (
            "include_results",
            "include_estimation",
            "max_coefficients",
            "timeout_ms",
            "run_in_background",
            "track_output_files",
            "auto_close_logs",
        ):
            assert key in props, f"{key} must be an advertised stata_run option"
        assert props["include_results"]["default"] == "scalars"
        assert props["timeout_ms"]["default"] == 600000

    def test_background_tools_are_registered(self):
        from stata_code.mcp.server import _tool_definitions

        names = {t.name for t in _tool_definitions()}
        assert {"stata_run_status", "list_background_runs"}.issubset(names)

    def test_timeout_below_the_floor_is_rejected(self):
        result = _call("stata_run", {"code": "display 1", "timeout_ms": 10})
        assert result.isError is True
        assert _body(result)["kind"] == "invalid_request"

    def test_timeout_null_is_accepted_by_validation(self):
        from stata_code.mcp.server import _prepare_run_arguments

        _code, options, err = _prepare_run_arguments({"code": "display 1", "timeout_ms": None})
        assert err is None
        assert options["timeout_ms"] is None

    def test_non_integer_timeout_is_rejected(self):
        from stata_code.mcp.server import _prepare_run_arguments

        for bad in ("600000", True, 1.5):
            _code, _options, err = _prepare_run_arguments({"code": "display 1", "timeout_ms": bad})
            assert err is not None, f"{bad!r} should be rejected"

    def test_negative_max_coefficients_is_rejected(self):
        from stata_code.mcp.server import _prepare_run_arguments

        _code, _options, err = _prepare_run_arguments(
            {"code": "display 1", "max_coefficients": -1}
        )
        assert err is not None

    def test_unknown_job_id_is_typed(self):
        result = _call("stata_run_status", {"job_id": "nope"})
        assert result.isError is True
        assert _body(result)["kind"] == "unknown_job"


class TestInlineGraphDelivery:
    """Inline graphs must arrive as image blocks, not base64 inside JSON."""

    def _result_with_graph(self, fmt="png", count=1):
        from stata_code.core.schema import (
            DatasetInfo,
            GraphFormat,
            GraphInfo,
            LogInfo,
            RunResult,
            StataInfo,
        )

        return RunResult(
            ok=True,
            rc=0,
            request_id="req",
            started_at="2026-07-28T00:00:00.000Z",
            elapsed_ms=1,
            stata=StataInfo(backend="pystata"),
            log=LogInfo(),
            dataset=DatasetInfo(),
            graphs=[
                GraphInfo(
                    ref=f"graph://req/{i}",
                    name=f"g{i}",
                    format=GraphFormat(fmt),
                    inline="aGVsbG8=",
                )
                for i in range(count)
            ],
        )

    def test_png_inline_becomes_an_image_block(self):
        from stata_code.mcp.server import _run_result_payload

        result = _run_result_payload(self._result_with_graph())
        images = _images(result)
        assert len(images) == 1
        assert images[0].mimeType == "image/png"
        assert images[0].data == "aGVsbG8="
        body = _body(result)
        # The bytes cross the wire once — not again as a JSON string.
        assert body["graphs"][0]["inline"] is None
        assert body["graphs"][0]["inline_delivered"] is True

    def test_pdf_is_not_delivered_as_an_image(self):
        from stata_code.mcp.server import _run_result_payload

        result = _run_result_payload(self._result_with_graph(fmt="pdf"))
        assert _images(result) == []
        entry = _body(result)["graphs"][0]
        assert entry["inline"] is None
        assert entry["inline_delivered"] is False
        assert "inline_skipped_reason" in entry

    def test_image_count_is_capped_and_the_overflow_is_reported(self):
        from stata_code.mcp.server import _MAX_INLINE_IMAGES, _run_result_payload

        result = _run_result_payload(self._result_with_graph(count=_MAX_INLINE_IMAGES + 2))
        assert len(_images(result)) == _MAX_INLINE_IMAGES
        body = _body(result)
        kinds = [w["kind"] for w in body["warnings"]]
        assert "inline_graphs_truncated" in kinds
        assert body["graphs"][-1]["inline_delivered"] is False

    def test_ref_mode_result_has_no_image_blocks(self):
        from stata_code.mcp.server import _run_result_payload

        run = self._result_with_graph()
        run.graphs[0].inline = None
        result = _run_result_payload(run)
        assert _images(result) == []
        assert _body(result)["graphs"][0]["inline"] is None


class TestBackgroundDispatch:
    def test_background_run_returns_a_job_id_without_blocking(self, monkeypatch):
        from stata_code.core import jobs

        gate = threading.Event()
        monkeypatch.setattr(jobs, "pool_execute", lambda code, **kw: gate.wait(10))  # noqa: ARG005
        jobs.reset_registry()
        try:
            result = _call(
                "stata_run",
                {"code": "bootstrap, reps(10000): regress y x", "run_in_background": True},
            )
            body = _body(result)
            assert body["status"] == "running"
            job_id = body["job_id"]

            listed = _body(_call("list_background_runs", {}))
            assert job_id in [j["job_id"] for j in listed["jobs"]]

            status = _body(_call("stata_run_status", {"job_id": job_id}))
            assert status["status"] == "running"
            assert status["result"] is None
        finally:
            gate.set()
            for job in jobs.list_jobs():
                job.wait(5)
            jobs.reset_registry()

    def test_finished_job_status_carries_the_run_result(self, monkeypatch):
        from stata_code.core import jobs
        from stata_code.core.schema import DatasetInfo, LogInfo, RunResult, StataInfo

        run = RunResult(
            ok=True,
            rc=0,
            request_id="req",
            started_at="2026-07-28T00:00:00.000Z",
            elapsed_ms=1,
            stata=StataInfo(backend="pystata"),
            log=LogInfo(head="done"),
            dataset=DatasetInfo(),
        )
        monkeypatch.setattr(jobs, "pool_execute", lambda code, **kw: run)  # noqa: ARG005
        jobs.reset_registry()
        try:
            body = _body(_call("stata_run", {"code": "display 1", "run_in_background": True}))
            status = _body(
                _call("stata_run_status", {"job_id": body["job_id"], "wait_ms": 5000})
            )
            assert status["status"] == "done"
            assert status["result"]["ok"] is True
            assert status["result"]["log"]["head"] == "done"
            assert status["error"] is None
        finally:
            jobs.reset_registry()

    def test_wait_ms_is_clamped_to_the_documented_ceiling(self, monkeypatch):
        """An unbounded `wait_ms` must not turn a status poll into a hang."""
        from stata_code.core import jobs
        from stata_code.mcp import server

        gate = threading.Event()
        monkeypatch.setattr(jobs, "pool_execute", lambda code, **kw: gate.wait(30))  # noqa: ARG005
        monkeypatch.setattr(server.jobs, "MAX_WAIT_MS", 250)
        jobs.reset_registry()
        try:
            body = _body(_call("stata_run", {"code": "display 1", "run_in_background": True}))
            started = time.monotonic()
            status = _body(
                _call("stata_run_status", {"job_id": body["job_id"], "wait_ms": 10**9})
            )
            waited = time.monotonic() - started
            assert status["status"] == "running"
            assert waited < 5.0, f"waited {waited:.2f}s despite the 250 ms ceiling"
        finally:
            gate.set()
            for job in jobs.list_jobs():
                job.wait(5)
            jobs.reset_registry()


# ─────────────────────────────────────────────────────────────────────────────
# 6. End-to-end against a real Stata runtime
# ─────────────────────────────────────────────────────────────────────────────


@_needs_stata
class TestAgainstRealStata:
    def test_failed_do_file_reports_the_offending_line(self):
        from stata_code.core.runner import execute

        tmp = _tmpdir("e2e_do")
        script = tmp / "analysis.do"
        script.write_text(
            "sysuse auto, clear\nsummarize price\nregress price nosuchvar\ndisplay 1\n",
            encoding="utf-8",
        )
        r = execute(f'do "{script}"', session_id="e2e_do", working_dir=str(tmp))
        assert r.ok is False
        assert r.error.line == 3
        assert r.error.source_file == str(script)
        assert r.error.message == "variable nosuchvar not found"
        assert r.error.context.before  # not the empty context agents used to get

    def test_failed_run_still_produces_a_searchable_log(self):
        from stata_code.core.runner import execute

        r = execute(
            "sysuse auto, clear\nsummarize price\nregress price nosuchvar",
            session_id="e2e_log",
        )
        assert r.ok is False
        # The transcript pystata raises IS the log; it used to be discarded.
        assert r.log.lines_total > 0
        assert "summarize price" in (r.log.head + r.log.tail)

    def test_a_failed_run_does_not_poison_the_next_one(self):
        from stata_code.core.runner import execute

        tmp = _tmpdir("e2e_leak")
        script = tmp / "leaky.do"
        script.write_text(
            f'log using "{tmp}/run.log", replace\nregress price nosuchvar\nlog close\n',
            encoding="utf-8",
        )
        first = execute(f'do "{script}"', session_id="e2e_leak", working_dir=str(tmp))
        assert first.ok is False
        assert any(w.kind == "log_closed" for w in first.warnings)

        second = execute("display 42", session_id="e2e_leak", working_dir=str(tmp))
        assert second.ok is True, f"session poisoned: {second.error}"

    def test_auto_close_can_be_disabled(self):
        from stata_code.core.runner import execute
        from stata_code.core.schema import ErrorKind

        tmp = _tmpdir("e2e_noclose")
        script = tmp / "leaky.do"
        script.write_text(
            f'log using "{tmp}/run.log", replace\nregress price nosuchvar\nlog close\n',
            encoding="utf-8",
        )
        try:
            execute(
                f'do "{script}"',
                session_id="e2e_noclose",
                working_dir=str(tmp),
                auto_close_logs=False,
            )
            second = execute(
                f'log using "{tmp}/other.log", replace',
                session_id="e2e_noclose",
                auto_close_logs=False,
            )
            assert second.ok is False
            assert second.error.kind == ErrorKind.LOG_STATE
            assert second.error.rc == 604
        finally:
            execute("capture log close _all", session_id="e2e_noclose")

    def test_generated_files_are_reported_without_a_run_bundle(self):
        from stata_code.core.runner import execute

        tmp = _tmpdir("e2e_out")
        r = execute(
            'sysuse auto, clear\nexport delimited using "table1.csv", replace',
            session_id="e2e_out",
            working_dir=str(tmp),
        )
        assert r.ok
        assert [Path(o.path).name for o in r.outputs] == ["table1.csv"]
        assert r.outputs[0].created is True
        assert r.outputs[0].bytes > 0
        assert "output_tracking" in r.capabilities
        # And the run bundle was never requested.
        assert r.log.files is None

    def test_output_tracking_can_be_disabled(self):
        from stata_code.core.runner import execute

        tmp = _tmpdir("e2e_out_off")
        r = execute(
            'sysuse auto, clear\nexport delimited using "t.csv", replace',
            session_id="e2e_out_off",
            working_dir=str(tmp),
            track_output_files=False,
        )
        assert r.ok
        assert r.outputs == []

    def test_r_survives_to_the_next_call(self):
        """`summarize` then `display r(mean)` is the canonical two-call pattern.

        The runner's own probes (`graph dir`, `log query`, `graph export`) are
        all r-class, so without a `_return hold` they wiped `r()` between calls
        and the second call read back a missing value.
        """
        from stata_code.core.runner import execute

        execute("sysuse auto, clear\nsummarize price", session_id="e2e_rhold")
        r = execute("display r(mean)", session_id="e2e_rhold")
        assert r.ok
        assert "6165" in r.log.head, r.log.head

    def test_r_survives_a_graph_producing_call(self):
        from stata_code.core.runner import execute

        execute("sysuse auto, clear\nsummarize price", session_id="e2e_rhold_g")
        r = execute(
            "display r(mean)\nscatter price mpg",
            session_id="e2e_rhold_g",
            include_graphs="ref",
        )
        assert r.ok
        assert "6165" in r.log.head
        assert r.graphs, "the graph should still be captured"

    def test_e_survives_to_the_next_call(self):
        from stata_code.core.runner import execute

        execute("sysuse auto, clear\nregress price mpg", session_id="e2e_ehold")
        r = execute("display e(r2)", session_id="e2e_ehold")
        assert r.ok
        assert ".219" in r.log.head, r.log.head

    def test_default_payload_is_smaller_than_full(self):
        from stata_code.core.runner import execute

        code = "sysuse auto, clear\nregress price mpg weight length turn"
        default = len(execute(code, session_id="e2e_sz").model_dump_json())
        full = len(execute(code, session_id="e2e_sz", include_results="full").model_dump_json())
        assert default < full
