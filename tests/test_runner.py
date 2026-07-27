"""Integration tests for the v0.1 runner against a real Stata installation.

Skipped when pystata cannot be initialized (CI without Stata).
"""

from __future__ import annotations

import pytest

from stata_code.core._runtime import is_available
from stata_code.core.schema import Backend, ErrorKind, RunResult, StataEdition

pytestmark = [
    pytest.mark.stata_required,
    pytest.mark.skipif(
        not is_available(), reason="pystata / Stata 17+ not available"
    ),
]


@pytest.fixture(scope="session")
def loaded_auto():
    """Session fixture: load auto.dta once, reused across tests."""
    from stata_code.core.runner import execute

    r = execute("sysuse auto, clear")
    assert r.ok, f"setup failed: {r.error}"
    return r


# ─────────────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────────────


class TestSuccessfulRun:
    def test_simple_display(self):
        from stata_code.core.runner import execute

        r = execute('display "hello stata_code"')
        assert r.ok is True
        assert r.rc == 0
        assert r.error is None
        assert "hello stata_code" in r.log.head
        assert r.stata.backend == Backend.PYSTATA
        assert r.stata.edition in (
            StataEdition.MP,
            StataEdition.SE,
            StataEdition.BE,
        )
        assert r.elapsed_ms >= 1
        assert r.session_id == "main"
        assert r.request_id  # non-empty
        assert r.schema_version == "1.0"

    def test_summarize_returns_r_scalars(self, loaded_auto):
        from stata_code.core.runner import execute

        r = execute("summarize mpg, detail")
        assert r.ok is True
        assert "mean" in r.results.r.scalars
        # auto.dta mpg mean is ~21.30
        assert 20.0 < r.results.r.scalars["mean"] < 22.0
        # Native float, not stringified
        assert isinstance(r.results.r.scalars["mean"], float)

    def test_regress_returns_e_with_cmd_macro(self, loaded_auto):
        from stata_code.core.runner import execute

        r = execute("regress mpg weight", include_results="full")
        assert r.ok is True
        assert r.results.e.macros.get("cmd") == "regress"
        assert r.results.last_estimation_cmd == "regress"
        # e(b) coefficient matrix
        assert "b" in r.results.e.matrices
        b = r.results.e.matrices["b"]
        assert b.cols == ["weight", "_cons"]
        assert b.values is not None
        assert len(b.values) == 1
        assert len(b.values[0]) == 2
        # weight coefficient is small negative (~-0.006)
        assert -0.01 < b.values[0][0] < 0
        # r2 scalar
        assert "r2" in r.results.e.scalars
        assert 0 < r.results.e.scalars["r2"] < 1


class TestDataset:
    def test_dataset_metadata(self, loaded_auto):
        from stata_code.core.runner import execute

        r = execute("describe")
        assert r.ok is True
        assert r.dataset.n_obs == 74  # auto.dta
        assert r.dataset.n_vars == 12
        # auto.dta has 'mpg', 'price', 'weight' among others
        assert r.dataset.variables is not None
        names = {v.name for v in r.dataset.variables}
        assert {"mpg", "price", "weight"}.issubset(names)
        # variable types & labels are populated
        mpg_var = next(v for v in r.dataset.variables if v.name == "mpg")
        assert mpg_var.type  # any storage type string
        assert mpg_var.label  # auto.dta variables have labels

    def test_include_variables_false_skips_list(self, loaded_auto):
        from stata_code.core.runner import execute

        r = execute("describe", include_dataset_variables=False)
        assert r.ok is True
        assert r.dataset.variables is None
        # still get counts
        assert r.dataset.n_obs == 74

    def test_dataset_frame_default(self, loaded_auto):
        from stata_code.core.runner import execute

        r = execute('display "noop"')
        assert r.dataset.frame == "default"


# ─────────────────────────────────────────────────────────────────────────────
# Error path
# ─────────────────────────────────────────────────────────────────────────────


class TestErrorClassification:
    def test_varname_not_found(self, loaded_auto):
        from stata_code.core.runner import execute

        r = execute("summarize mpgg")
        assert r.ok is False
        assert r.rc == 111
        assert r.error is not None
        assert r.error.kind == ErrorKind.VARNAME_NOT_FOUND
        assert r.error.varname == "mpgg"
        # Suggestion includes the closest match (mpg in auto.dta)
        assert any("mpg" in s.action for s in r.error.suggestions)

    def test_varname_not_found_emits_did_you_mean(self, loaded_auto):
        """End-to-end: real fuzzy match against the dataset's variable list."""
        from stata_code.core.runner import execute

        r = execute("summarize mpgg")
        assert r.ok is False
        # The headline win for agents: a "Did you mean `mpg`?" suggestion.
        assert any(
            "Did you mean" in s.action and "mpg" in s.action
            for s in r.error.suggestions
        )

    def test_unrecognized_command(self):
        from stata_code.core.runner import execute

        r = execute("thisIsNotARealCommand")
        assert r.ok is False
        assert r.rc == 199
        assert r.error.kind == ErrorKind.COMMAND_NOT_FOUND
        # Suggestion mentions ssc install
        assert any("ssc install" in s.action for s in r.error.suggestions)

    def test_unrecognized_command_fuzzy_match(self):
        # A typo for a real command must surface a "did you mean" suggestion.
        # Stata's actual rc 199 message is "command <X> is unrecognized"; this
        # exercises the command-name extraction against the live runtime (a
        # regex that only matched synthetic phrasing left this dead).
        from stata_code.core.runner import execute

        r = execute("regresss price mpg")
        assert r.ok is False
        assert r.rc == 199
        assert r.error.kind == ErrorKind.COMMAND_NOT_FOUND
        assert any(
            "Did you mean" in s.action and "regress" in s.action
            for s in r.error.suggestions
        ), [s.action for s in r.error.suggestions]

    def test_syntax_error(self):
        from stata_code.core.runner import execute

        # `error 198` is Stata's explicit "raise rc 198" — deterministic,
        # not affected by prior session state.
        r = execute("error 198")
        assert r.ok is False
        assert r.rc == 198
        assert r.error.kind == ErrorKind.SYNTAX

    def test_error_window_populated_on_failure(self, loaded_auto):
        from stata_code.core.runner import execute

        r = execute("summarize mpgg")
        assert r.ok is False
        # error_window contains text related to the error
        assert r.log.error_window is not None

    def test_consistency_contract_holds(self, loaded_auto):
        """ok / rc / error must agree — Pydantic validators enforce this."""
        from stata_code.core.runner import execute

        r_ok = execute('display "fine"')
        assert r_ok.ok is True and r_ok.rc == 0 and r_ok.error is None

        r_err = execute("summarize mpgg")
        assert r_err.ok is False and r_err.rc != 0 and r_err.error is not None
        assert r_err.rc == r_err.error.rc


# ─────────────────────────────────────────────────────────────────────────────
# Schema compliance
# ─────────────────────────────────────────────────────────────────────────────


class TestSchemaCompliance:
    def test_result_round_trips_through_json(self, loaded_auto):
        """Real Stata output must round-trip through model_dump_json."""
        from stata_code.core.runner import execute

        r = execute("summarize mpg")
        s = r.model_dump_json()
        r2 = RunResult.model_validate_json(s)
        assert r2.model_dump() == r.model_dump()

    # (Multi-session is supported as of Module 4; no-longer-NotImplemented.)


# ─────────────────────────────────────────────────────────────────────────────
# Multi-call session reuse
# ─────────────────────────────────────────────────────────────────────────────


class TestGraphCapture:
    def _clean_graphs(self, execute):
        # Helper: drop all graphs to start each test from clean slate.
        execute("graph drop _all")

    def test_no_graph_no_capture(self, loaded_auto):
        from stata_code.core.runner import execute

        self._clean_graphs(execute)
        r = execute('display "no graph here"')
        assert r.ok is True
        assert r.graphs == []

    def test_single_graph_captured(self, loaded_auto):
        from stata_code.core.runner import execute

        self._clean_graphs(execute)
        r = execute("scatter price mpg, name(g_test_single)")
        assert r.ok is True
        assert len(r.graphs) == 1
        g = r.graphs[0]
        assert g.name == "g_test_single"
        assert g.format.value == "png"
        assert g.ref.startswith("graph://")
        assert g.ref.endswith("/0")
        assert g.inline is None  # ref mode is the default
        assert g.source_command == "scatter price mpg, name(g_test_single)"
        assert g.source_line == 1
        # PNG dimensions parsed
        assert g.width and g.width > 0
        assert g.height and g.height > 0

    def test_multiple_graphs_captured(self, loaded_auto):
        from stata_code.core.runner import execute

        self._clean_graphs(execute)
        r = execute(
            """
            scatter price mpg, name(g_a)
            histogram weight, name(g_b)
            """
        )
        assert r.ok is True
        assert len(r.graphs) == 2
        names = {g.name for g in r.graphs}
        assert names == {"g_a", "g_b"}
        by_name = {g.name: g for g in r.graphs}
        assert by_name["g_a"].source_command == "scatter price mpg, name(g_a)"
        assert by_name["g_b"].source_command == "histogram weight, name(g_b)"
        assert by_name["g_a"].source_line == 2
        assert by_name["g_b"].source_line == 3
        # refs are unique per index
        assert len({g.ref for g in r.graphs}) == 2

    def test_redrawn_default_graph_recaptured_each_cell(self, loaded_auto):
        """Regression: in a persistent session every unnamed graph command
        overwrites the default "Graph" in place, so a pure name set-diff sees
        no new name on the 2nd+ run and captured nothing — only the first
        Jupyter cell's graph displayed. Each redraw must now surface its own
        graph. NOTE: deliberately no _clean_graphs between runs."""
        from stata_code.core.runner import execute

        self._clean_graphs(execute)
        r1 = execute("scatter price mpg")
        assert len(r1.graphs) == 1
        r2 = execute("scatter weight mpg")
        assert len(r2.graphs) == 1  # was 0 before the fix
        r3 = execute("histogram price")
        assert len(r3.graphs) == 1

    def test_redrawn_named_graph_recaptured(self, loaded_auto):
        """A named graph redrawn under the same name across runs is captured
        every time, not just the first."""
        from stata_code.core.runner import execute

        self._clean_graphs(execute)
        r1 = execute("scatter price mpg, name(g_redraw, replace)")
        assert len(r1.graphs) == 1
        r2 = execute("scatter weight mpg, name(g_redraw, replace)")
        assert len(r2.graphs) == 1  # was 0 before the fix
        assert r2.graphs[0].name == "g_redraw"

    def test_no_graph_cell_after_graph_no_recapture(self, loaded_auto):
        """A cell that draws nothing must not re-surface an existing graph
        left over from an earlier cell."""
        from stata_code.core.runner import execute

        self._clean_graphs(execute)
        first = execute("scatter price mpg")
        assert len(first.graphs) == 1
        r = execute('display "just text, no graph"')
        assert r.graphs == []

    def test_graph_export_cell_does_not_recapture(self, loaded_auto, tmp_path):
        """A utility-only cell (graph export of a prior graph) must not be
        treated as a redraw — `graph export` is not a drawing command."""
        from stata_code.core.runner import execute

        self._clean_graphs(execute)
        execute("scatter price mpg")  # default Graph now exists
        out = (tmp_path / "export.png").as_posix()
        r = execute(f'graph export "{out}", replace')
        assert r.graphs == []

    def test_new_graph_with_untouched_graph_persisting(self, loaded_auto):
        """When a cell draws one new named graph while an older, untouched
        named graph still lives in memory, only the new one is captured."""
        from stata_code.core.runner import execute

        self._clean_graphs(execute)
        execute("scatter price mpg, name(g_keep)")
        r = execute("histogram weight, name(g_fresh)")
        assert len(r.graphs) == 1
        assert r.graphs[0].name == "g_fresh"

    def test_get_graph_returns_bytes(self, loaded_auto):
        from stata_code.core.runner import execute, get_graph

        self._clean_graphs(execute)
        r = execute("scatter price mpg, name(g_for_get)")
        assert len(r.graphs) == 1
        ref = r.graphs[0].ref
        payload = get_graph(ref)
        assert payload["format"] == "png"
        assert payload["bytes_b64"]
        # b64 decoding round-trip
        import base64

        raw = base64.b64decode(payload["bytes_b64"])
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"

    def test_get_graph_unknown_ref_raises(self):
        from stata_code.core.runner import get_graph

        with pytest.raises(KeyError):
            get_graph("graph://does-not-exist/0")

    def test_include_graphs_none_skips(self, loaded_auto):
        from stata_code.core.runner import execute

        self._clean_graphs(execute)
        r = execute(
            "scatter price mpg, name(g_skipped)", include_graphs="none"
        )
        assert r.ok is True
        assert r.graphs == []

    def test_include_graphs_inline_embeds_bytes(self, loaded_auto):
        from stata_code.core.runner import execute

        self._clean_graphs(execute)
        r = execute(
            "scatter price mpg, name(g_inline)", include_graphs="inline"
        )
        assert r.ok is True
        assert len(r.graphs) == 1
        assert r.graphs[0].inline  # base64-encoded
        # PNG header through b64
        import base64

        raw = base64.b64decode(r.graphs[0].inline)
        assert raw[:4] == b"\x89PNG"

    def test_capabilities_include_graph_ref(self, loaded_auto):
        from stata_code.core.runner import execute

        self._clean_graphs(execute)
        r = execute('display "anything"')
        assert "graph_ref" in r.capabilities

    def test_invalid_graph_format_rejected(self):
        from stata_code.core.runner import execute

        with pytest.raises(ValueError):
            execute('display "x"', graph_format="bmp")


class TestLogTruncation:
    def test_short_log_not_truncated(self, loaded_auto):
        from stata_code.core.runner import execute

        r = execute('display "one line"')
        assert r.log.truncated is False
        assert r.log.tail == ""
        assert r.log.ref is None
        assert "one line" in r.log.head

    def test_long_log_is_truncated(self, loaded_auto):
        from stata_code.core.runner import execute

        # Stata loop printing many lines forces truncation.
        # 60 lines > default head=20 + tail=20 = 40
        code = """
            forvalues i = 1/60 {
                display "row=`i'"
            }
        """
        r = execute(code, log_lines_head=20, log_lines_tail=20)
        assert r.ok is True
        assert r.log.truncated is True
        assert r.log.lines_total >= 60
        assert r.log.ref is not None
        assert r.log.ref.startswith("log://")
        # head has first 20 lines, tail has last 20
        assert r.log.head.count("\n") == 19  # 20 lines = 19 newlines
        assert r.log.tail.count("\n") == 19
        # Earliest content in head, latest in tail
        assert "row=1" in r.log.head
        assert "row=60" in r.log.tail

    def test_get_log_returns_full_text(self, loaded_auto):
        from stata_code.core.runner import execute, get_log

        code = """
            forvalues i = 1/50 {
                display "line=`i'"
            }
        """
        r = execute(code, log_lines_head=5, log_lines_tail=5)
        assert r.log.truncated is True
        full = get_log(r.log.ref)
        assert full["lines_total"] == r.log.lines_total
        # All 50 displays show up in the full text
        for i in (1, 25, 50):
            assert f"line={i}" in full["text"]

    def test_get_log_unknown_ref_raises(self):
        from stata_code.core.runner import get_log

        with pytest.raises(KeyError):
            get_log("log://does-not-exist")

    def test_include_full_log_inlines_everything(self, loaded_auto):
        from stata_code.core.runner import execute

        code = """
            forvalues i = 1/30 {
                display "x=`i'"
            }
        """
        r = execute(code, log_lines_head=5, log_lines_tail=5, include_full_log=True)
        assert r.log.truncated is False
        assert r.log.tail == ""
        assert "x=1" in r.log.head
        assert "x=30" in r.log.head

    def test_capabilities_include_log_truncation(self, loaded_auto):
        from stata_code.core.runner import execute

        r = execute('display "x"')
        assert "log_truncation" in r.capabilities
        assert "matrix_ref" in r.capabilities


class TestWarnings:
    def test_no_warnings_on_clean_run(self, loaded_auto):
        from stata_code.core.runner import execute

        r = execute("summarize mpg")
        assert r.warnings == []

    def test_omitted_collinear_warning(self, loaded_auto):
        from stata_code.core.runner import execute

        # Force a collinearity drop: duplicate variable in the regression.
        execute("capture drop mpg_dup")
        execute("gen mpg_dup = mpg")
        r = execute("regress price mpg mpg_dup weight")
        assert r.ok is True
        # At least one warning of kind omitted_collinear or note
        kinds = {w.kind for w in r.warnings}
        assert "omitted_collinear" in kinds, (
            f"expected omitted_collinear warning; got {[(w.kind, w.message) for w in r.warnings]}"
        )
        omitted = [w for w in r.warnings if w.kind == "omitted_collinear"]
        # Message contains "mpg_dup" (the omitted varname)
        assert any("mpg_dup" in w.message for w in omitted)

    def test_warnings_dedup(self, loaded_auto):
        from stata_code.core.runner import execute

        # Same warning text duplicated in log → dedup by (kind, message).
        # Force this via a fake echo of a note line.
        r = execute('display "note: foo omitted because of collinearity."')
        # `display` outputs the literal string. `_extract_warnings` should
        # find the pattern and emit one warning, not multiple.
        omitted = [w for w in r.warnings if w.kind == "omitted_collinear"]
        assert len(omitted) <= 1


class TestMatrices:
    """Matrix collection: small ones inline; large ones go through `_refs`.

    These pin the ``include_results="full"`` contract. The default
    (``"scalars"``) stubs every matrix — see :class:`TestResultBudget`.
    """

    def test_small_matrix_inlined_with_no_ref(self, loaded_auto):
        from stata_code.core.runner import execute

        r = execute("regress mpg weight", include_results="full")
        assert r.ok
        # e(b) is 1×2 for `regress mpg weight` (intercept + 1 slope).
        b = r.results.e.matrices.get("b")
        assert b is not None
        assert b.values is not None  # inline
        assert b.ref is None
        assert len(b.values) == 1
        assert len(b.values[0]) == 2

    def test_large_matrix_emits_ref_and_drops_values(
        self, loaded_auto, monkeypatch
    ):
        """When a matrix exceeds the cell cap, values is None and a ref is set."""
        from stata_code.core import runner

        # Lower the cap so a normal-size auto.dta matrix triggers ref mode.
        # `regress mpg weight length` produces e(V) = 3×3 = 9 cells; setting
        # the cap to 4 forces ref mode for that matrix while leaving e(b)
        # (1×3 = 3 cells) inline.
        monkeypatch.setattr(runner, "MATRIX_INLINE_CELL_CAP", 4)

        r = runner.execute("regress mpg weight length", include_results="full")
        assert r.ok

        v = r.results.e.matrices.get("V")
        assert v is not None, f"expected e(V); got {list(r.results.e.matrices)}"
        assert v.values is None
        assert v.ref is not None
        assert v.ref.startswith("matrix://")
        # Row/col labels remain visible inline (cheap, useful for the agent).
        assert v.rows
        assert v.cols
        assert v.n_rows == 3 and v.n_cols == 3

        # Small matrix in the same result is still inlined (smoke test).
        b = r.results.e.matrices.get("b")
        assert b is not None
        assert b.values is not None
        assert b.ref is None

    def test_get_matrix_returns_full_payload(self, loaded_auto, monkeypatch):
        from stata_code.core import runner

        monkeypatch.setattr(runner, "MATRIX_INLINE_CELL_CAP", 4)
        r = runner.execute("regress mpg weight length", include_results="full")
        assert r.ok
        v = r.results.e.matrices["V"]
        assert v.ref is not None

        payload = runner.get_matrix(v.ref)
        assert payload["rows"] == v.rows
        assert payload["cols"] == v.cols
        # 3×3, every cell is a finite float.
        assert len(payload["values"]) == len(v.rows)
        for row in payload["values"]:
            assert len(row) == len(v.cols)
            for cell in row:
                assert cell is None or isinstance(cell, float)

    def test_get_matrix_unknown_ref_raises(self):
        from stata_code.core.runner import get_matrix

        with pytest.raises(KeyError):
            get_matrix("matrix://does-not-exist/r/M")


class TestResultBudget:
    """`include_results` / `include_estimation` / `max_coefficients`.

    The default exists because a single estimation otherwise encodes the same
    numbers four times over — e(b), e(V), e(beta) and r(table) — while
    `results.estimation` already carries the typed, deduplicated view.
    """

    def test_default_stubs_every_matrix_but_keeps_scalars(self, loaded_auto):
        from stata_code.core.runner import execute

        r = execute("regress mpg weight length")
        assert r.ok
        assert r.results.e.scalars["r2"] > 0  # scalars survive
        assert r.results.e.macros["cmd"] == "regress"
        for name, m in r.results.e.matrices.items():
            assert m.values is None, f"e({name}) should be stubbed by default"
            assert m.ref is not None, f"e({name}) needs a ref to stay retrievable"
            assert m.rows == [] and m.cols == []
            assert m.n_rows and m.n_cols

    def test_stubbed_matrix_is_still_retrievable(self, loaded_auto):
        from stata_code.core.runner import execute, get_matrix

        r = execute("regress mpg weight length")
        v = r.results.e.matrices["V"]
        payload = get_matrix(v.ref)
        # Labels elided from the wire come back with the values.
        assert payload["rows"] and payload["cols"]
        assert len(payload["values"]) == v.n_rows
        assert len(payload["values"][0]) == v.n_cols

    def test_estimation_survives_stubbing_with_real_standard_errors(self, loaded_auto):
        """The whole point: trimming the wire must not blank inference."""
        from stata_code.core.runner import execute

        r = execute("regress mpg weight length")
        est = r.results.estimation
        assert est is not None
        assert est.n_coefficients == len(est.coefficients) == 3
        assert all(c.se is not None and c.se > 0 for c in est.coefficients)
        assert all(c.p_value is not None for c in est.coefficients)

    def test_standard_errors_survive_a_deferred_e_v(self, loaded_auto, monkeypatch):
        """A ref'd e(V) used to blank se/statistic/p_value for every term."""
        from stata_code.core import runner

        monkeypatch.setattr(runner, "MATRIX_INLINE_CELL_CAP", 4)
        r = runner.execute(
            "regress mpg weight length",
            include_results="full",
            # Force the e(b)/e(V) path rather than r(table).
        )
        assert r.results.e.matrices["V"].values is None  # deferred
        est = r.results.estimation
        assert est is not None
        assert all(c.se is not None and c.se > 0 for c in est.coefficients)

    def test_include_results_none_drops_returns_but_keeps_estimation(self, loaded_auto):
        from stata_code.core.runner import execute

        r = execute("regress mpg weight", include_results="none")
        assert r.results.e.scalars == {} and r.results.e.matrices == {}
        assert r.results.r.scalars == {} and r.results.r.matrices == {}
        assert r.results.estimation is not None
        assert len(r.results.estimation.coefficients) == 2

    def test_include_estimation_none_and_summary(self, loaded_auto):
        from stata_code.core.runner import execute

        assert execute("regress mpg weight", include_estimation="none").results.estimation is None

        est = execute("regress mpg weight", include_estimation="summary").results.estimation
        assert est is not None
        assert est.coefficients == []
        assert est.n_coefficients == 2  # true count still reported
        assert est.coefficients_truncated is True
        assert est.model_stats["r2"] > 0

    def test_max_coefficients_truncates_and_flags(self, loaded_auto):
        from stata_code.core.runner import execute

        est = execute("regress mpg weight length", max_coefficients=1).results.estimation
        assert len(est.coefficients) == 1
        assert est.n_coefficients == 3
        assert est.coefficients_truncated is True

    def test_rejects_unknown_modes(self, loaded_auto):
        from stata_code.core.runner import execute

        with pytest.raises(ValueError, match="include_results"):
            execute("display 1", include_results="everything")
        with pytest.raises(ValueError, match="include_estimation"):
            execute("display 1", include_estimation="all")
        with pytest.raises(ValueError, match="max_coefficients"):
            execute("display 1", max_coefficients=-1)


class TestStataMissingValues:
    """Stata's system missing is maxdouble (2**1023), not a number."""

    def test_missing_markers_normalize_to_null(self):
        """Pin the primitive: sfi hands missings back as ordinary floats."""
        from stata_code.core.runner import _norm_stata_number

        assert _norm_stata_number(2.0**1023) is None  # system missing `.`
        assert _norm_stata_number(8.990660123939097e307) is None  # extended `.a`
        assert _norm_stata_number(8.992854573566614e307) is None  # extended `.b`
        assert _norm_stata_number(float("nan")) is None
        assert _norm_stata_number(float("inf")) is None
        # Largest legal Stata double stays a number.
        assert _norm_stata_number(8.9e307) == 8.9e307
        assert _norm_stata_number(3.5) == 3.5
        assert _norm_stata_number(-0.0) == 0.0
        assert _norm_stata_number(None) is None
        assert _norm_stata_number("not a number") is None

    def test_omitted_base_level_has_null_not_maxdouble(self, loaded_auto):
        """`i.rep78`'s base level has missing se/t/p in r(table)."""
        from stata_code.core.runner import execute

        r = execute("regress price mpg i.rep78")
        assert r.ok
        est = r.results.estimation
        base = [c for c in est.coefficients if c.b == 0.0 and c.se is None]
        assert base, "expected at least one omitted base level with null se"
        for c in est.coefficients:
            for value in (c.b, c.se, c.statistic, c.p_value, c.ci_low, c.ci_high):
                assert value is None or abs(value) < 1e300


class TestCooperativeCancel:
    """End-to-end: cancel(session_id) short-circuits the next execute()."""

    def setup_method(self):
        from stata_code.core import runner as rm

        with rm._cancel_lock:
            rm._cancel_pending.clear()

    def test_cancel_then_execute_returns_cancelled_result(self, loaded_auto):
        from stata_code.core.runner import cancel, execute, is_cancel_pending
        from stata_code.core.schema import ErrorKind

        assert cancel("main") is True
        assert is_cancel_pending("main") is True

        r = execute('display "should not run"')
        assert r.ok is False
        assert r.rc == -3
        assert r.error is not None
        assert r.error.kind == ErrorKind.CANCELLED
        assert r.error.rc == -3
        assert r.error.commands_executed == 0
        # Cancel was consumed; next call runs normally.
        assert is_cancel_pending("main") is False

    def test_cancelled_result_does_not_run_user_code(self, loaded_auto):
        from stata_code.core.runner import cancel, execute

        cancel("main")
        r = execute('display "marker_X1Y2Z3"')
        assert r.ok is False
        # The marker string must not appear anywhere in the cancelled
        # envelope — it never reached Stata.
        assert "marker_X1Y2Z3" not in r.log.head
        assert "marker_X1Y2Z3" not in r.log.tail
        assert r.log.lines_total == 0

    def test_cancel_only_fires_once(self, loaded_auto):
        from stata_code.core.runner import cancel, execute

        cancel("main")
        r1 = execute('display "first"')
        r2 = execute('display "second"')
        assert r1.ok is False  # cancelled
        assert r2.ok is True
        assert "second" in r2.log.head

    def test_cancel_isolates_sessions_under_real_runner(self, loaded_auto):
        from stata_code.core.runner import cancel, execute

        cancel("alpha")
        # Main session unaffected.
        r_main = execute('display "main runs"')
        assert r_main.ok is True
        # Alpha session run is short-circuited.
        r_alpha = execute('display "alpha"', session_id="alpha")
        assert r_alpha.ok is False
        assert r_alpha.session_id == "alpha"


class TestMultiSession:
    def setup_method(self):
        # Ensure clean state — drop any non-default frames left over.
        from stata_code.core.runner import list_sessions, reset_session

        for sess in list_sessions():
            if sess["session_id"] != "main":
                reset_session(sess["session_id"])

    def test_main_session_routes_to_default_frame(self):
        from stata_code.core.runner import execute

        r = execute('display "in main"', session_id="main")
        assert r.ok
        assert r.session_id == "main"
        assert r.dataset.frame == "default"

    def test_create_named_session_on_demand(self):
        from stata_code.core.runner import execute

        r = execute("sysuse auto, clear", session_id="alt")
        assert r.ok
        assert r.session_id == "alt"
        assert r.dataset.frame == "alt"
        assert r.dataset.n_obs == 74

    def test_data_isolation_between_sessions(self):
        from stata_code.core.runner import execute

        # Load auto into 'alt'
        r1 = execute("sysuse auto, clear", session_id="alt")
        assert r1.dataset.n_obs == 74

        # 'main' should NOT see auto.dta
        r2 = execute('display "checking"', session_id="main")
        # main may have data from prior tests; just verify it's a different
        # frame (data identity is what matters, not n_obs).
        assert r2.dataset.frame == "default"

        # Back to alt: data still there
        r3 = execute('display "still here"', session_id="alt")
        assert r3.dataset.n_obs == 74

    def test_schema_compatible_session_ids_are_supported(self):
        from stata_code.core.runner import execute, list_sessions

        r_dash = execute('display "x"', session_id="my-session")
        assert r_dash.ok
        assert r_dash.session_id == "my-session"
        assert r_dash.dataset.frame.startswith("_sc_")

        r_digit = execute('display "x"', session_id="9abc")
        assert r_digit.ok
        assert r_digit.session_id == "9abc"
        assert r_digit.dataset.frame.startswith("_sc_")

        ids = {s["session_id"] for s in list_sessions()}
        assert "my-session" in ids
        assert "9abc" in ids

    def test_invalid_session_id_rejected(self):
        from stata_code.core.runner import execute

        with pytest.raises(ValueError, match=r"\[A-Za-z0-9_-\]"):
            execute('display "x"', session_id="my session")

        with pytest.raises(ValueError, match=r"\[A-Za-z0-9_-\]"):
            execute('display "x"', session_id="host-7:main")

    def test_list_sessions_includes_main_plus_named(self):
        from stata_code.core.runner import execute, list_sessions

        execute('display "x"', session_id="alt2")
        sessions = list_sessions()
        ids = {s["session_id"] for s in sessions}
        assert "main" in ids
        assert "alt2" in ids

    def test_reset_session_drops_named_frame(self):
        from stata_code.core.runner import execute, list_sessions, reset_session

        execute("sysuse auto, clear", session_id="to_drop")
        ids_before = {s["session_id"] for s in list_sessions()}
        assert "to_drop" in ids_before

        result = reset_session("to_drop")
        assert result["dropped_frame"] is True
        ids_after = {s["session_id"] for s in list_sessions()}
        assert "to_drop" not in ids_after

    def test_reset_main_clears_data_but_keeps_frame(self):
        from stata_code.core.runner import execute, list_sessions, reset_session

        # Make sure main has data
        execute("sysuse auto, clear", session_id="main")
        result = reset_session("main")
        assert result["dropped_frame"] is False
        # main still listed (default frame can't be dropped)
        ids_after = {s["session_id"] for s in list_sessions()}
        assert "main" in ids_after

    def test_capabilities_include_multi_session(self, loaded_auto):
        from stata_code.core.runner import execute

        r = execute('display "x"')
        assert "multi_session" in r.capabilities


class TestRefsLruEviction:
    def test_capacity_drops_oldest(self):
        from stata_code.core import _refs

        try:
            original = _refs.get_capacity()
            _refs.set_capacity(3)
            _refs.put("a", "first")
            _refs.put("b", "second")
            _refs.put("c", "third")
            _refs.put("d", "fourth")  # should evict 'a'
            assert _refs.get("a") is None
            assert _refs.get("b") == "second"
            assert _refs.get("d") == "fourth"
        finally:
            _refs.set_capacity(original)
            _refs.clear_all()

    def test_get_promotes_to_recent(self):
        from stata_code.core import _refs

        try:
            original = _refs.get_capacity()
            _refs.set_capacity(3)
            _refs.put("a", "first")
            _refs.put("b", "second")
            _refs.put("c", "third")
            # touch 'a' to mark recent — 'b' is now oldest
            _refs.get("a")
            _refs.put("d", "fourth")  # should evict 'b'
            assert _refs.get("a") == "first"
            assert _refs.get("b") is None
            assert _refs.get("c") == "third"
        finally:
            _refs.set_capacity(original)
            _refs.clear_all()

    def test_capacity_must_be_positive(self):
        from stata_code.core import _refs

        with pytest.raises(ValueError):
            _refs.set_capacity(0)


class TestErrorPinpointing:
    def setup_method(self):
        # Some prior test classes (TestMultiSession.test_reset_main_*) call
        # `reset_session("main")` which wipes the auto dataset. Re-load to
        # make pinpointing tests deterministic regardless of run order.
        from stata_code.core.runner import execute

        execute("sysuse auto, clear")

    def test_single_line_error_attributed_to_line_one(self, loaded_auto):
        from stata_code.core.runner import execute

        r = execute("summarize mpgg")
        assert r.ok is False
        assert r.error.command == "summarize mpgg"
        assert r.error.line == 1
        assert r.error.commands_executed == 0
        assert r.error.context.failing == "summarize mpgg"

    def test_multiline_error_pinpoints_failing_line(self, loaded_auto):
        from stata_code.core.runner import execute

        code = (
            'display "first"\n'
            "summarize mpg\n"
            "summarize mpgg\n"
            'display "after"\n'
        )
        r = execute(code)
        assert r.ok is False
        # Line 3 (1-indexed) is the failing command.
        assert r.error.line == 3
        assert r.error.command == "summarize mpgg"
        assert r.error.context.failing == "summarize mpgg"
        # Two commands ran successfully before the failure.
        assert r.error.commands_executed == 2
        # `before` includes the two preceding commands.
        assert "summarize mpg" in r.error.context.before
        assert any("first" in b for b in r.error.context.before)

    def test_error_message_is_sentence_not_echo(self, loaded_auto):
        from stata_code.core.runner import execute

        code = "display \"x\"\nsummarize mpgg"
        r = execute(code)
        # message should be the diagnosis, not the echoed command line.
        assert "not found" in r.error.message
        assert not r.error.message.startswith(". ")

    def test_comments_dont_count_as_executed(self, loaded_auto):
        from stata_code.core.runner import execute

        code = (
            "* this is a comment\n"
            "// another comment\n"
            'display "first"\n'
            "summarize mpgg\n"
        )
        r = execute(code)
        # Only one *real* command (display) ran before the failure.
        assert r.error.commands_executed == 1


class TestSessionReuse:
    def test_state_persists_across_calls(self):
        """Variables created in one call survive into the next (single session)."""
        from stata_code.core.runner import execute

        r1 = execute("global sc_test_var = 42")
        assert r1.ok, f"setup failed: {r1.error}"
        r2 = execute('display "test=$sc_test_var"')
        assert r2.ok
        assert "test=42" in r2.log.head
