"""Regressions found by reading back months of real `stata_run` calls.

Each test names the behaviour an agent actually met. The pure-Python cases run
everywhere; the ones marked ``real_stata`` need a local Stata 17+.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

from stata_code.core import _refs, jobs
from stata_code.core._pool import (
    _build_cancelled_result,
    _build_timeout_result,
    new_live_log_path,
    read_live_log,
    remove_live_log,
)
from stata_code.core._runtime import _TeeBuffer, is_available
from stata_code.core.errors import RC_LABEL, classify_error, classify_rc
from stata_code.core.lint import lint_code
from stata_code.core.log_artifacts import (
    snapshot_is_truncated,
    snapshot_working_dir_files,
)
from stata_code.core.runner import (
    _build_error,
    _extract_warnings,
    _merge_transcript,
    _script_search_dirs,
    get_log,
    search_log,
)
from stata_code.core.schema import ErrorKind

real_stata = pytest.mark.skipif(not is_available(), reason="pystata / Stata 17+ not available")


def _call(name, arguments):
    from stata_code.mcp.server import _dispatch

    return asyncio.run(_dispatch(name, arguments))


def _body(result):
    return json.loads(result.content[-1].text)


# ─────────────────────────────────────────────────────────────────────────────
# A failure late in a block: pystata streams the first part of the transcript
# to stdout and raises with the rest. The log used to keep only the first part.
# ─────────────────────────────────────────────────────────────────────────────

_STREAMED = "\n. clear all\n\n. sysuse auto, clear\n(1978 automobile data)\n\n. quietly reg price mpg\n"
_RAISED = "\n. scalar b = _b[mpg]\n\n. estimates store long\nlong invalid name (reserved word)\nr(7);\nr(7);"
_CODE = "clear all\nsysuse auto, clear\nquietly reg price mpg\nscalar b = _b[mpg]\nestimates store long\ngen z = 1\n"


class TestMergedTranscript:
    def test_exception_text_continues_the_streamed_stdout(self):
        merged = _merge_transcript(_STREAMED, _RAISED)
        assert merged == _STREAMED + _RAISED
        assert merged.index("quietly reg") < merged.index("estimates store long")

    def test_stdout_empty_uses_exception_text(self):
        assert _merge_transcript("", _RAISED) == _RAISED
        assert _merge_transcript("\n", _RAISED) == _RAISED

    def test_success_has_no_transcript_to_merge(self):
        assert _merge_transcript(_STREAMED, None) is None

    def test_text_reported_through_both_channels_is_not_doubled(self):
        whole = _STREAMED + _RAISED
        assert _merge_transcript(whole, _RAISED) == whole
        assert _merge_transcript(_STREAMED, whole) == whole

    def test_error_is_located_in_the_whole_block(self):
        error = _build_error(7, _merge_transcript(_STREAMED, _RAISED), _CODE, None)
        assert error.command == "estimates store long"
        assert error.line == 5
        # Four commands ran before it; the exception text alone showed one.
        assert error.commands_executed == 4
        assert error.message == "long invalid name (reserved word)"

    def test_typed_field_comes_from_the_failing_command(self):
        transcript = (
            "\n. capture noisily summarize zzz\nvariable zzz not found\n\n"
            ". summarize qqq\nvariable qqq not found\nr(111);\nr(111);"
        )
        error = _build_error(111, transcript, "capture noisily summarize zzz\nsummarize qqq\n", None)
        assert error.varname == "qqq"


class TestDoFileAfterCd:
    def test_relative_do_resolves_against_a_cd_in_the_block(self, tmp_path):
        project = tmp_path / "project"
        (project / "code").mkdir(parents=True)
        script = project / "code" / "step.do"
        script.write_text("sysuse auto, clear\ndisplay 1\nsummarize nosuchvar\n")
        transcript = (
            f'\n. cd "{project}"\n{project}\n\n. do "code/step.do"\n\n'
            ". sysuse auto, clear\n(1978 automobile data)\n\n. display 1\n1\n\n"
            ". summarize nosuchvar\nvariable nosuchvar not found\nr(111);\n\n"
            "end of do-file\nr(111);\nr(111);"
        )
        code = f'cd "{project}"\ndo "code/step.do"\n'
        # The run started somewhere else entirely.
        error = _build_error(111, transcript, code, None, working_dir=tmp_path / "elsewhere")
        assert error.source_file == str(script)
        assert error.line == 3

    def test_search_dirs_follow_relative_cd(self, tmp_path):
        dirs = _script_search_dirs('. cd "sub"\n\n. cd deeper\n', tmp_path)
        assert dirs == [tmp_path, tmp_path / "sub", tmp_path / "sub" / "deeper"]

    def test_extra_dirs_are_searched_last(self, tmp_path):
        dirs = _script_search_dirs(". display 1\n", tmp_path, [tmp_path / "now"])
        assert dirs == [tmp_path, tmp_path / "now"]


class TestClassification:
    def test_rc7_invalid_name(self):
        assert classify_rc(7) == ErrorKind.UNKNOWN
        assert classify_error(7, "long invalid name (reserved word)") == ErrorKind.INVALID_NAME

    def test_rc7_wrong_type(self):
        message = "'abc' found where numeric variable expected"
        assert classify_error(7, message) == ErrorKind.TYPE_MISMATCH

    def test_message_does_not_override_a_settled_code(self):
        assert classify_error(111, "x invalid name") == ErrorKind.VARNAME_NOT_FOUND
        assert classify_error(7, None) == ErrorKind.UNKNOWN

    @pytest.mark.parametrize(
        ("rc", "label"),
        [
            (9, "assertion is false"),
            (119, "statement out of context"),
            (133, "invalid syntax"),
            (303, "equation not found"),
            (459, "something that should be true of your data is not"),
            (621, "already preserved"),
            (908, "matsize too small"),
        ],
    )
    def test_labels_read_from_stata(self, rc, label):
        assert RC_LABEL[rc] == label

    @pytest.mark.parametrize(
        ("rc", "kind"),
        [
            (106, ErrorKind.TYPE_MISMATCH),
            (133, ErrorKind.SYNTAX),
            (675, ErrorKind.NETWORK),
            (695, ErrorKind.FILE_IO),
            (900, ErrorKind.STATA_LIMIT),
            (913, ErrorKind.OUT_OF_MEMORY),
        ],
    )
    def test_newly_mapped_codes(self, rc, kind):
        assert classify_rc(rc) == kind


_LOOP_INPUT_TRANSCRIPT = (
    '\n. foreach t in "30 70" "50 20" {\n  2.   clear\n  3.   input byte x long w\n'
    "  4.   1 `1'\n  5.   0 `2'\n  6.   end\n--Break--\nr(1);\n--Break--\nr(1);"
)


class TestInputInsideLoop:
    def test_break_is_explained_instead_of_echoing_the_last_body_line(self):
        code = 'foreach t in "30 70" "50 20" {\n  clear\n  input byte x long w\n  1 `1\'\n  0 `2\'\n  end\n}\n'
        error = _build_error(1, _LOOP_INPUT_TRANSCRIPT, code, None)
        assert "input" in error.message and "loop or program" in error.message
        assert error.recovery.needs_code_change is True
        assert error.recovery.retriable is False
        assert error.suggestions and "set obs" in error.suggestions[0].action

    def test_a_plain_break_is_left_alone(self):
        error = _build_error(1, "\n. sleep 100000\n--Break--\nr(1);", "sleep 100000\n", None)
        assert error.recovery.retriable is True
        assert error.suggestions == []

    def test_lint_flags_input_in_a_loop_and_in_a_program(self):
        loop = "forvalues i = 1/2 {\n  clear\n  input x\n  1\n  end\n}\n"
        assert [(f.rule, f.line) for f in lint_code(loop)] == [("input-in-block", 3)]
        program = "program define mk\n  input x\n  1\n  end\nend\n"
        assert [(f.rule, f.line) for f in lint_code(program)] == [("input-in-block", 2)]

    def test_lint_accepts_top_level_input(self):
        # `end` closing inline data used to be reported as `unexpected-end`.
        assert lint_code("clear\ninput x y\n1 2\n3 4\nend\ncount\n") == []


# ─────────────────────────────────────────────────────────────────────────────
# Reading a long log
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def log_ref():
    ref = "log://usage-regression"
    text = "\n".join(f"line {i}" for i in range(1, 101))
    _refs.put(ref, {"text": text, "lines_total": 100, "bytes_total": len(text)})
    return ref


class TestGetLogWindow:
    def test_whole_log_keeps_its_original_shape(self, log_ref):
        assert set(get_log(log_ref)) == {"text", "lines_total", "bytes_total"}

    def test_window(self, log_ref):
        out = get_log(log_ref, start_line=50, max_lines=3)
        assert out["text"] == "line 50\nline 51\nline 52"
        assert (out["start_line"], out["lines_returned"], out["truncated"]) == (50, 3, True)
        assert out["lines_total"] == 100

    def test_negative_start_counts_from_the_end(self, log_ref):
        out = get_log(log_ref, start_line=-2)
        assert out["text"] == "line 99\nline 100"
        assert out["truncated"] is False

    def test_past_the_end_is_empty_not_an_error(self, log_ref):
        out = get_log(log_ref, start_line=500, max_lines=5)
        assert out["text"] == "" and out["lines_returned"] == 0

    def test_bad_window_is_rejected(self, log_ref):
        with pytest.raises(ValueError):
            get_log(log_ref, start_line=0)
        with pytest.raises(ValueError):
            get_log(log_ref, max_lines=0)

    def test_tool_accepts_the_window_arguments(self, log_ref):
        body = _body(_call("get_log", {"ref": log_ref, "start_line": 10, "max_lines": 2}))
        assert body["text"] == "line 10\nline 11"

    def test_tool_rejects_a_non_integer_window(self, log_ref):
        result = _call("get_log", {"ref": log_ref, "start_line": "10"})
        assert result.isError is True


class TestSearchLogFallback:
    @pytest.fixture
    def ref(self):
        ref = "log://usage-search"
        text = "Pre_avg | .01\nTm3 | .02\nTp1 | .03\nprice (USD) | 4"
        _refs.put(ref, {"text": text, "lines_total": 4, "bytes_total": len(text)})
        return ref

    def test_unstated_mode_retries_a_regex_shaped_pattern(self, ref):
        out = search_log(ref, "Pre_avg|Tm[0-9]", is_regex=None)
        assert out["match_count"] == 2
        assert out["is_regex"] is True
        assert "regular expression" in out["note"]

    def test_literal_hit_wins_over_the_regex_reading(self, ref):
        out = search_log(ref, "(USD)", is_regex=None)
        assert out["match_count"] == 1 and out["is_regex"] is False
        assert "note" not in out

    def test_explicit_literal_never_falls_back(self, ref):
        out = search_log(ref, "Pre_avg|Tm[0-9]", is_regex=False)
        assert out["match_count"] == 0 and out["is_regex"] is False

    def test_default_keyword_is_still_literal(self, ref):
        assert search_log(ref, "Pre_avg|Tm[0-9]")["match_count"] == 0

    def test_unparseable_pattern_just_reports_no_match(self, ref):
        out = search_log(ref, "Tm[0-9", is_regex=None)
        assert out["match_count"] == 0 and out["is_regex"] is False

    def test_tool_falls_back_only_when_is_regex_is_omitted(self, ref):
        assert _body(_call("search_log", {"ref": ref, "pattern": "Tm[0-9]|Tp[0-3]"}))[
            "match_count"
        ] == 2
        explicit = {"ref": ref, "pattern": "Tm[0-9]|Tp[0-3]", "is_regex": False}
        assert _body(_call("search_log", explicit))["match_count"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Output tracking in a real project directory
# ─────────────────────────────────────────────────────────────────────────────


class TestSnapshotInALargeCheckout:
    def test_vcs_and_dependency_trees_do_not_exhaust_the_cap(self, tmp_path):
        for heavy in (".git/objects", "node_modules/pkg", ".venv/lib"):
            directory = tmp_path / heavy
            directory.mkdir(parents=True)
            for i in range(40):
                (directory / f"f{i}.json").write_text("{}")
        (tmp_path / "table.tex").write_text("x")
        snapshot = snapshot_working_dir_files(tmp_path, max_entries=10)
        assert not snapshot_is_truncated(snapshot)
        assert list(snapshot) == [str((tmp_path / "table.tex").resolve())]

    def test_source_files_are_not_counted(self, tmp_path):
        for i in range(30):
            (tmp_path / f"mod{i}.py").write_text("")
        (tmp_path / "out.csv").write_text("a\n")
        snapshot = snapshot_working_dir_files(tmp_path, max_entries=5)
        assert not snapshot_is_truncated(snapshot)
        assert len(snapshot) == 1

    def test_too_many_outputs_still_truncate(self, tmp_path):
        for i in range(8):
            (tmp_path / f"t{i}.csv").write_text("a\n")
        assert snapshot_is_truncated(snapshot_working_dir_files(tmp_path, max_entries=5))


class TestCollinearityNotes:
    def test_a_long_run_of_notes_is_folded_into_one(self):
        log = "\n".join(f"note: pnc{i} omitted because of collinearity." for i in range(1, 17))
        warnings = [w for w in _extract_warnings(log) if w.kind == "omitted_collinear"]
        assert len(warnings) == 6
        assert warnings[-1].message.startswith("note: 11 more terms omitted")
        assert "pnc6" in warnings[-1].message and "pnc16" in warnings[-1].message

    def test_a_few_notes_are_kept_as_they_are(self):
        log = "\n".join(f"note: x{i} omitted because of collinearity." for i in range(1, 4))
        assert len(_extract_warnings(log)) == 3


# ─────────────────────────────────────────────────────────────────────────────
# Live logs: progress while running, a transcript after a kill
# ─────────────────────────────────────────────────────────────────────────────

_PARTIAL = "\n. sysuse auto, clear\n(1978 automobile data)\n\n. bootstrap, reps(5000): regress price mpg\n(running regress on estimation sample)\n"
_PARTIAL_CODE = "sysuse auto, clear\nbootstrap, reps(5000): regress price mpg\ndisplay 1\n"


class TestLiveLog:
    def test_tee_buffer_writes_through_as_it_goes(self, tmp_path):
        path = tmp_path / "live.log"
        buf = _TeeBuffer(str(path))
        buf.write(". display 1\n")
        assert path.read_text() == ". display 1\n"
        buf.tee_only("1\n")
        assert path.read_text() == ". display 1\n1\n"
        assert buf.getvalue() == ". display 1\n"
        buf.close()

    def test_tee_buffer_survives_an_unwritable_path(self, tmp_path):
        buf = _TeeBuffer(str(tmp_path / "missing-dir" / "live.log"))
        buf.write("x")
        assert buf.getvalue() == "x"

    def test_read_keeps_the_end_of_an_oversized_log(self, tmp_path):
        path = tmp_path / "live.log"
        path.write_text("".join(f"row {i}\n" for i in range(1000)))
        text = read_live_log(str(path), max_bytes=200)
        assert text.endswith("row 999\n") and len(text) <= 200
        assert text.startswith("row ")
        assert read_live_log(str(tmp_path / "absent.log")) == ""

    def test_timeout_result_keeps_what_ran(self):
        result = _build_timeout_result(
            session_id="s1",
            elapsed_ms=600_000,
            timeout_ms=600_000,
            partial_log=_PARTIAL,
            code=_PARTIAL_CODE,
            options={},
        )
        assert result.log.complete is False
        assert "bootstrap, reps(5000)" in result.log.head
        assert result.error.command == "bootstrap, reps(5000): regress price mpg"
        assert result.error.line == 2
        assert result.error.commands_executed == 1
        assert "It was running: bootstrap" in result.error.message
        assert result.error.suggestions

    def test_timeout_without_a_transcript_is_unchanged(self):
        result = _build_timeout_result(session_id="s1", elapsed_ms=10, timeout_ms=10)
        assert result.log.lines_total == 0 and result.error.command is None

    def test_long_partial_log_is_truncated_behind_a_ref(self):
        text = "".join(f". display {i}\n{i}\n" for i in range(200))
        result = _build_timeout_result(
            session_id="s1",
            elapsed_ms=5,
            timeout_ms=5,
            partial_log=text,
            code=None,
            options={"log_lines_head": 2, "log_lines_tail": 3},
        )
        assert result.log.truncated is True
        assert get_log(result.log.ref)["lines_total"] == 400

    def test_mid_run_cancel_keeps_the_transcript_and_a_pre_run_cancel_has_none(self):
        mid = _build_cancelled_result(
            session_id="s1",
            elapsed_ms=5,
            aborted_mid_run=True,
            partial_log=_PARTIAL,
            code=_PARTIAL_CODE,
            options={},
        )
        assert mid.log.complete is False and "bootstrap" in mid.log.head
        assert mid.error.line == 2
        before = _build_cancelled_result(session_id="s1", elapsed_ms=5, partial_log=_PARTIAL)
        assert before.log.complete is True and before.log.lines_total == 0


class TestBackgroundProgress:
    def test_running_job_reports_its_transcript_and_cleans_up(self, monkeypatch):
        gate = threading.Event()
        started = threading.Event()
        seen: dict[str, str] = {}

        def _fake(code, **kw):  # noqa: ARG001
            seen["path"] = kw["live_log_path"]
            Path(kw["live_log_path"]).write_text(_PARTIAL)
            started.set()
            gate.wait(10)
            return None

        monkeypatch.setattr(jobs, "pool_execute", _fake)
        jobs.reset_registry()
        try:
            job_id = _body(_call("stata_run", {"code": _PARTIAL_CODE, "run_in_background": True}))[
                "job_id"
            ]
            assert started.wait(5)
            status = _body(_call("stata_run_status", {"job_id": job_id, "log_lines_tail": 2}))
            assert status["status"] == "running"
            assert status["current_command"] == "bootstrap, reps(5000): regress price mpg"
            assert status["log_tail"].splitlines()[-1] == "(running regress on estimation sample)"
            assert len(status["log_tail"].splitlines()) == 2
            assert status["log_lines_total"] == 6

            quiet = _body(_call("stata_run_status", {"job_id": job_id, "log_lines_tail": 0}))
            assert "log_tail" not in quiet
        finally:
            gate.set()
            for job in jobs.list_jobs():
                job.wait(5)
            deadline = time.monotonic() + 5
            while Path(seen["path"]).exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not Path(seen["path"]).exists()
            jobs.reset_registry()

    def test_live_log_paths_are_unique_and_removable(self):
        a, b = new_live_log_path(), new_live_log_path()
        assert a != b
        Path(a).write_text("x")
        remove_live_log(a)
        remove_live_log(a)  # already gone: no error
        assert not Path(a).exists()


# ─────────────────────────────────────────────────────────────────────────────
# The same behaviours against a real Stata
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.stata_required
@real_stata
class TestRealStata:
    def test_late_failure_is_in_the_log(self):
        from stata_code import run

        result = run(_CODE, session_id="usage_late")
        assert result.ok is False and result.rc == 7
        assert result.error.kind == ErrorKind.INVALID_NAME
        assert result.error.line == 5 and result.error.commands_executed == 4
        text = result.log.head + "\n" + result.log.tail
        assert "estimates store long" in text
        assert "invalid name" in text
        assert "invalid name" in result.log.error_window

    def test_do_file_reached_through_cd_is_pinpointed(self, tmp_path):
        from stata_code import run

        (tmp_path / "code").mkdir()
        script = tmp_path / "code" / "step.do"
        script.write_text(
            'sysuse auto, clear\ndisplay "a"\ndisplay "b"\nsummarize nosuchvar\n'
        )
        result = run(f'cd "{tmp_path}"\ndo "code/step.do"\n', session_id="usage_cd")
        assert result.error.varname == "nosuchvar"
        assert Path(result.error.source_file).resolve() == script.resolve()
        assert result.error.line == 4

    def test_input_inside_a_loop_is_diagnosed(self):
        from stata_code import run

        code = "forvalues i = 1/2 {\n  clear\n  input byte x\n  1\n  end\n}\n"
        result = run(code, session_id="usage_input")
        assert result.rc == 1
        assert "loop or program" in result.error.message

    def test_timeout_returns_the_partial_transcript(self):
        from stata_code.core._pool import SessionPool

        code = 'display "before"\nforvalues i = 1/30 {\n  display "step `i\'"\n  sleep 1000\n}\n'
        pool = SessionPool()
        try:
            # Warm the worker so the timeout measures the loop, not Stata's start-up.
            assert pool.execute("display 1", session_id="usage_timeout", timeout_ms=120_000).ok
            result = pool.execute(code, session_id="usage_timeout", timeout_ms=4000)
        finally:
            pool.shutdown()
        assert result.rc == -2
        assert "before" in result.log.head and "step 1" in result.log.head
        assert result.log.complete is False
        assert result.error.command == "forvalues i = 1/30 {"
        assert result.error.line == 2
