"""Acceptance checks for "the run finished" vs "the run did what was intended".

Three gaps an agent hits when it trusts ``ok`` alone:

1. ``capture`` turns a failure into rc 0, so ``ok=true`` can hide a missing
   file or variable — reported as ``suppressed_error`` warnings.
2. A failure several ``do`` levels deep reported ``line: null`` — the active
   ``do`` stack in the transcript now pins the script and line.
3. A changed specification (an extra ``if``, a dropped merge) runs cleanly —
   ``expect`` grades sample size, coefficients and output files and turns a
   mismatch into ``error.kind="expectation_failed"``.

The transcripts below are verbatim Stata 18 output. The offline tests need no
Stata; the ``TestReal*`` classes run against a live installation and skip
without one.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from stata_code.core.expect import (
    EXPECTATION_RC,
    evaluate_expectations,
    expectation_error,
    needs_estimation,
    normalize_expect,
)
from stata_code.core.runner import (
    _active_do_stack,
    _build_error,
    _do_file_candidates,
    _extract_suppressed_errors,
)
from stata_code.core.schema import (
    Coefficient,
    ErrorKind,
    EstimationResult,
    RunResult,
    StataWarning,
)


def _tmpdir(tag: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=f"sc-expect-{tag}-"))


def _estimation(n_obs: int = 74, **coefs: float) -> EstimationResult:
    rows = [Coefficient(term=t, b=b) for t, b in coefs.items()]
    return EstimationResult(
        command="regress", n_obs=n_obs, coefficients=rows, n_coefficients=len(rows)
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Errors suppressed by capture
# ─────────────────────────────────────────────────────────────────────────────


class TestSuppressedErrors:
    # Verbatim Stata 18 log of a successful run (rc 0) that swallowed six errors.
    SWALLOWED = (
        "\n. sysuse auto, clear\n(1978 automobile data)\n\n"
        ". capture noisily do inner.do\n\n"
        ". reghdfe_typo price mpg, absorb(rep78)\n"
        "command reghdfe_typo is unrecognized\nr(199);\n\nend of do-file\n\n"
        '. capture noisily confirm file "out/table1.csv"\nfile out/table1.csv not found\n\n'
        '. capture noisily use "nope.dta", clear\nfile nope.dta not found\n\n'
        ". capture {\nvariable nosuchvar not found\n\n"
        ". capture noisily assert price < 0\n"
        "74 contradictions in 74 observations\nassertion is false\n\n"
        ". capture noisily isid rep78\nvariable rep78 should never be missing\n\n"
        '. display "done"\ndone\n\n. '
    )

    def test_every_swallowed_error_is_reported_once(self):
        warnings = _extract_suppressed_errors(self.SWALLOWED)
        assert all(w.kind == "suppressed_error" for w in warnings)
        joined = "\n".join(w.message for w in warnings)
        assert len(warnings) == 6, joined
        assert "r(199)" in joined and "reghdfe_typo price mpg" in joined
        assert "r(601)" in joined and "file nope.dta not found" in joined
        assert "r(111)" in joined and "`capture {`" in joined
        assert "r(9)" in joined and "assertion is false" in joined
        assert "r(459)" in joined and "should never be missing" in joined

    def test_rc_line_and_its_message_are_one_finding(self):
        warnings = _extract_suppressed_errors(
            ". capture noisily do inner.do\n\n. bad_cmd x\n"
            "command bad_cmd is unrecognized\nr(199);\n\nend of do-file\n"
        )
        assert len(warnings) == 1
        assert "`bad_cmd x` failed with r(199)" in warnings[0].message
        assert warnings[0].message.endswith("command bad_cmd is unrecognized")

    def test_error_sentences_outside_capture_are_not_flagged(self):
        # A successful run may well *print* these words — only a captured
        # command's output is read as a failure.
        log = (
            '. display "file x.dta not found"\nfile x.dta not found\n\n'
            ". regress price mpg\n  (table)\n\n"
            ". capture noisily regress price mpg\n      Source |       SS\n\n"
        )
        assert _extract_suppressed_errors(log) == []

    def test_successful_capture_is_silent(self):
        log = ". capture confirm file out.csv\n\n. capture drop tmp\n\n"
        assert _extract_suppressed_errors(log) == []

    @pytest.mark.parametrize(
        "prefix",
        ["cap noi", "capt noisily", "quietly capture noisily", "capture:", "cap{",
         "version 13: capture noisily"],
    )
    def test_capture_abbreviations(self, prefix):
        log = f". {prefix} use nope.dta\nfile nope.dta not found\n\n"
        assert len(_extract_suppressed_errors(log)) == 1

    def test_capture_inside_a_loop_body(self):
        log = (
            ". foreach f in a b {\n"
            "  2.     capture noisily use `f'.dta, clear\n"
            "  3. }\n"
            "file a.dta not found\nfile b.dta not found\n\n. "
        )
        assert len(_extract_suppressed_errors(log)) == 2

    @pytest.mark.parametrize(
        "line",
        [
            "m ambiguous abbreviation",
            "no variables defined",
            "invalid syntax",
            "option badopt not allowed",
            "time variable not set, use tsset varname ...",
            "panel variable not set; use xtset varname ...",
            "must specify panelvar; use xtset",
            "no; dataset in memory has changed since last saved",
            "variable foreign does not uniquely identify the observations",
            "'make' found where numeric variable expected",
            "estimation result nosuch not found",
        ],
    )
    def test_stata18_error_sentences(self, line):
        # Each is verbatim Stata 18 output under `capture noisily`.
        warnings = _extract_suppressed_errors(f". capture noisily cmd\n{line}\n\n. ")
        assert len(warnings) == 1, line

    def test_not_a_capture_prefix(self):
        log = ". captions_cmd x\nfile x.dta not found\n\n"
        assert _extract_suppressed_errors(log) == []


# ─────────────────────────────────────────────────────────────────────────────
# 2. Failures several `do` levels deep
# ─────────────────────────────────────────────────────────────────────────────


class TestNestedDoLocation:
    # Verbatim: `do nested.do` -> `do inner.do` -> failure on inner.do line 2.
    NESTED = (
        ". sysuse auto, clear\n(1978 automobile data)\n\n"
        ". do inner.do\n\n"
        ". display 1\n1\n\n"
        ". reghdfe_typo price mpg, absorb(rep78)\n"
        "command reghdfe_typo is unrecognized\nr(199);\n\n"
        "end of do-file\nr(199);\n\nend of do-file\n\nr(199);"
    )

    def _project(self) -> Path:
        tmp = _tmpdir("nested")
        (tmp / "nested.do").write_text("sysuse auto, clear\ndo inner.do\n", encoding="utf-8")
        (tmp / "inner.do").write_text(
            "display 1\nreghdfe_typo price mpg, absorb(rep78)\n", encoding="utf-8"
        )
        return tmp

    def test_line_is_resolved_two_levels_deep(self):
        tmp = self._project()
        err = _build_error(199, self.NESTED, "do nested.do", None, working_dir=tmp)
        assert err.source_file == str(tmp / "inner.do")
        assert err.line == 2
        assert err.context.before == ["display 1"]

    def test_active_stack_ignores_scripts_that_already_returned(self):
        transcript = (
            ". do a.do\n\n. display 1\n1\n\nend of do-file\n\n"
            ". do b.do\n\n. bad x\ncommand bad is unrecognized\nr(199);\n\nend of do-file"
        )
        assert _active_do_stack(transcript) == ["b.do"]

    def test_active_script_wins_over_identical_line_in_submitted_code(self):
        tmp = _tmpdir("shadow")
        (tmp / "clean.do").write_text("display 1\nuse nope.dta\n", encoding="utf-8")
        code = "use nope.dta\ndo clean.do"
        transcript = (
            ". do clean.do\n\n. display 1\n1\n\n. use nope.dta\n"
            "file nope.dta not found\nr(601);\n\nend of do-file\nr(601);"
        )
        err = _build_error(601, transcript, code, None, working_dir=tmp)
        assert err.source_file == str(tmp / "clean.do")
        assert err.line == 2

    def test_candidates_follow_the_do_chain(self):
        tmp = self._project()
        found = _do_file_candidates("do nested.do", tmp)
        assert [p.name for p in found] == ["nested.do", "inner.do"]

    def test_candidates_survive_a_do_cycle(self):
        tmp = _tmpdir("cycle")
        (tmp / "a.do").write_text("do b.do\n", encoding="utf-8")
        (tmp / "b.do").write_text("do a.do\n", encoding="utf-8")
        assert [p.name for p in _do_file_candidates("do a.do", tmp)] == ["a.do", "b.do"]


# ─────────────────────────────────────────────────────────────────────────────
# 3. Expectations
# ─────────────────────────────────────────────────────────────────────────────


class TestNormalizeExpect:
    def test_none_and_empty_mean_nothing_to_check(self):
        assert normalize_expect(None) is None
        assert normalize_expect({}) is None
        assert normalize_expect({"no_suppressed_errors": False}) is None

    def test_canonical_form(self):
        spec = normalize_expect(
            {
                "n_obs": 74,
                "coefficients": {"mpg": {"b": -49, "tol": 0.5}, "weight": {"sign": "positive"}},
                "outputs": ["t.csv"],
                "no_suppressed_errors": True,
            }
        )
        assert spec == {
            "n_obs": 74,
            "coefficients": {"mpg": {"b": -49.0, "tol": 0.5}, "weight": {"sign": "positive"}},
            "outputs": ["t.csv"],
            "no_suppressed_errors": True,
        }
        assert normalize_expect({"coefficients": {"mpg": {"b": 1}}})["coefficients"]["mpg"][
            "tol"
        ] == pytest.approx(1e-6)

    @pytest.mark.parametrize(
        "bad, fragment",
        [
            ([], "must be an object"),
            ({"nobs": 1}, "unknown key"),
            ({"n_obs": -1}, "n_obs"),
            ({"n_obs": True}, "n_obs"),
            ({"n_obs": 7.5}, "n_obs"),
            ({"coefficients": {}}, "non-empty"),
            ({"coefficients": {"mpg": 1.0}}, "must be an object"),
            ({"coefficients": {"mpg": {}}}, "exactly one"),
            ({"coefficients": {"mpg": {"b": 1, "sign": "positive"}}}, "exactly one"),
            ({"coefficients": {"mpg": {"b": float("nan")}}}, "finite"),
            ({"coefficients": {"mpg": {"b": 1, "tol": -1}}}, "tol"),
            ({"coefficients": {"mpg": {"sign": "up"}}}, "positive"),
            ({"coefficients": {"mpg": {"sign": "positive", "tol": 1}}}, "tol"),
            ({"coefficients": {"mpg": {"se": 1}}}, "unknown key"),
            ({"outputs": "t.csv"}, "list"),
            ({"outputs": []}, "non-empty"),
            ({"outputs": [""]}, "non-empty"),
            ({"no_suppressed_errors": "yes"}, "boolean"),
        ],
    )
    def test_malformed_expectations_are_rejected(self, bad, fragment):
        with pytest.raises(ValueError, match=fragment):
            normalize_expect(bad)

    def test_needs_estimation(self):
        assert needs_estimation({"n_obs": 1})
        assert needs_estimation({"coefficients": {"x": {"sign": "positive"}}})
        assert not needs_estimation({"outputs": ["a"]})
        assert not needs_estimation(None)


class TestEvaluateExpectations:
    def _eval(self, expect, *, estimation=None, estimated_here=True, warnings=(), wd=None):
        return evaluate_expectations(
            normalize_expect(expect),
            estimation=estimation,
            estimated_here=estimated_here,
            warnings=list(warnings),
            started_wall=time.time(),
            working_dir=wd,
        )

    def test_spec_drift_fails_n_obs_and_coefficient(self):
        # `if foreign == 0` turns the full-sample fit (74, -49.5) into (52, 237.7).
        drifted = _estimation(52, mpg=237.691, weight=4.415)
        checks = self._eval(
            {"n_obs": 74, "coefficients": {"mpg": {"b": -49.512, "tol": 0.001}}},
            estimation=drifted,
        )
        assert [c.passed for c in checks] == [False, False]
        assert checks[0].actual == 52 and checks[0].expected == 74
        assert "sample changed" in checks[0].message
        assert checks[1].target == "mpg" and checks[1].actual == pytest.approx(237.691)

        err = expectation_error(checks)
        assert err is not None
        assert err.kind == ErrorKind.EXPECTATION_FAILED
        assert err.rc == EXPECTATION_RC == -6
        assert err.rc_label == "expectation_failed"
        assert "2 of 2 expectation(s) failed" in err.message
        assert err.recovery.needs_code_change and err.recovery.needs_user_input

    def test_matching_spec_passes(self):
        checks = self._eval(
            {
                "n_obs": 74,
                "coefficients": {"mpg": {"b": -49.51, "tol": 0.01}, "weight": {"sign": "positive"}},
            },
            estimation=_estimation(74, mpg=-49.51222, weight=1.746559),
        )
        assert all(c.passed for c in checks)
        assert expectation_error(checks) is None

    def test_sign(self):
        checks = self._eval(
            {"coefficients": {"mpg": {"sign": "negative"}}},
            estimation=_estimation(mpg=0.0),
        )
        assert not checks[0].passed and "zero" in checks[0].message

    def test_inherited_estimation_is_not_graded(self):
        checks = self._eval(
            {"n_obs": 74, "coefficients": {"mpg": {"sign": "negative"}}},
            estimation=_estimation(74, mpg=-1.0),
            estimated_here=False,
        )
        assert [c.passed for c in checks] == [False, False]
        assert all("earlier run" in c.message for c in checks)

    def test_missing_term_lists_available_terms(self):
        checks = self._eval(
            {"coefficients": {"mpgg": {"sign": "negative"}}},
            estimation=_estimation(mpg=-1.0, weight=1.0),
        )
        assert not checks[0].passed
        assert "mpg, weight" in checks[0].message

    def test_outputs_missing_stale_and_fresh(self):
        wd = _tmpdir("outputs")
        stale = wd / "old.csv"
        stale.write_text("x")
        old = time.time() - 3600
        os.utime(stale, (old, old))
        started = time.time()
        (wd / "new.csv").write_text("x")
        checks = evaluate_expectations(
            {"outputs": ["missing.csv", "old.csv", "new.csv"]},
            estimation=None,
            estimated_here=False,
            warnings=[],
            started_wall=started,
            working_dir=wd,
        )
        assert [c.passed for c in checks] == [False, False, True]
        assert "does not exist" in checks[0].message
        assert "stale" in checks[1].message
        assert checks[2].target == str(wd / "new.csv")

    def test_no_suppressed_errors(self):
        hidden = StataWarning(kind="suppressed_error", message="`use x` failed")
        checks = self._eval({"no_suppressed_errors": True}, warnings=[hidden])
        assert not checks[0].passed and checks[0].actual == 1
        assert self._eval({"no_suppressed_errors": True})[0].passed


class TestMcpExpectArgument:
    def test_malformed_expect_is_rejected_before_stata_runs(self, monkeypatch):
        from stata_code.mcp import server

        def never(*_a, **_k):
            raise AssertionError("pool_execute must not be reached")

        monkeypatch.setattr(server, "pool_execute", never)
        out = asyncio.run(
            server._dispatch("stata_run", {"code": "display 1", "expect": {"n_obs": "74"}})
        )
        assert out.isError is True
        body = json.loads(out.content[0].text)
        assert body["kind"] == "invalid_request"
        assert "n_obs" in body["error"]

    def test_expect_is_forwarded_normalized(self, monkeypatch):
        from stata_code.mcp import server

        seen = {}

        def capture(code, **kwargs):
            seen.update(kwargs)
            raise ValueError("stop here")

        monkeypatch.setattr(server, "pool_execute", capture)
        asyncio.run(
            server._dispatch(
                "stata_run",
                {"code": "display 1", "expect": {"coefficients": {"mpg": {"b": -49}}}},
            )
        )
        assert seen["expect"] == {"coefficients": {"mpg": {"b": -49.0, "tol": 1e-6}}}

    def test_checks_round_trip_through_the_schema(self):
        raw = {
            "ok": False,
            "rc": -6,
            "request_id": "r",
            "started_at": "2026-01-01T00:00:00.000Z",
            "elapsed_ms": 1,
            "stata": {"version": "18", "edition": "MP", "backend": "pystata"},
            "checks": [{"check": "n_obs", "passed": False, "expected": 74, "actual": 52}],
            "error": {"kind": "expectation_failed", "rc": -6},
        }
        result = RunResult.model_validate(raw)
        assert result.checks[0].actual == 52
        assert result.error.kind == ErrorKind.EXPECTATION_FAILED


# ─────────────────────────────────────────────────────────────────────────────
# Live Stata
# ─────────────────────────────────────────────────────────────────────────────


def _stata_available() -> bool:
    from stata_code import is_available

    return is_available()


@pytest.mark.stata_required
@pytest.mark.skipif(not _stata_available(), reason="pystata / Stata 17+ not available")
class TestRealExpectations:
    def _run(self, code: str, sid: str, **kw):
        from stata_code import run

        return run(code, session_id=sid, **kw)

    def test_spec_drift_becomes_a_typed_failure(self):
        r = self._run(
            "sysuse auto, clear\nregress price mpg weight if foreign == 0",
            "exp_drift",
            expect={"n_obs": 74, "coefficients": {"mpg": {"b": -49.512, "tol": 0.001}}},
            include_estimation="summary",
        )
        assert not r.ok
        assert r.rc == -6
        assert r.error.kind == ErrorKind.EXPECTATION_FAILED
        assert [(c.check, c.passed, c.actual) for c in r.checks][0] == ("n_obs", False, 52)
        # Graded on the full table even though the wire copy dropped the rows.
        assert r.checks[1].actual == pytest.approx(237.691, abs=1e-3)
        assert r.results.estimation.coefficients == []

    def test_intended_spec_passes(self):
        wd = _tmpdir("real-out")
        r = self._run(
            "sysuse auto, clear\nregress price mpg weight\n"
            "estimates save m1, replace\n"
            'export delimited price mpg using "t1.csv", replace',
            "exp_ok",
            working_dir=str(wd),
            expect={
                "n_obs": 74,
                "coefficients": {
                    "mpg": {"b": -49.512, "tol": 0.001},
                    "weight": {"sign": "positive"},
                },
                "outputs": ["m1.ster", "t1.csv"],
                "no_suppressed_errors": True,
            },
        )
        assert r.ok, r.error
        assert r.error is None and r.rc == 0
        assert len(r.checks) == 6 and all(c.passed for c in r.checks)
        assert "expectations" in r.capabilities

    def test_capture_noisily_failure_is_a_warning_and_can_fail_the_run(self):
        code = 'sysuse auto, clear\ncapture noisily use "nope.dta", clear\nregress price mpg'
        r = self._run(code, "exp_cap")
        assert r.ok
        hidden = [w for w in r.warnings if w.kind == "suppressed_error"]
        assert len(hidden) == 1 and "r(601)" in hidden[0].message

        r = self._run(code, "exp_cap", expect={"no_suppressed_errors": True})
        assert not r.ok and r.error.kind == ErrorKind.EXPECTATION_FAILED

    def test_expectations_are_not_graded_when_stata_fails(self):
        r = self._run(
            "sysuse auto, clear\nregress price nosuchvar",
            "exp_fail",
            expect={"n_obs": 74},
        )
        assert not r.ok
        assert r.error.kind == ErrorKind.VARNAME_NOT_FOUND
        assert r.checks == []

    def test_nested_do_failure_names_the_inner_script(self):
        wd = _tmpdir("real-nested")
        (wd / "master.do").write_text("sysuse auto, clear\ndo step.do\n", encoding="utf-8")
        (wd / "step.do").write_text(
            "summarize price\nregress price nosuchvar\n", encoding="utf-8"
        )
        r = self._run("do master.do", "exp_nested", working_dir=str(wd))
        assert not r.ok
        assert r.error.source_file == str(wd / "step.do")
        assert r.error.line == 2
