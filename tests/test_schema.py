"""Tests locking the v1.0 wire schema (see SCHEMA.md)."""

from __future__ import annotations

import json

import pytest

from stata_code.core.errors import classify_rc, suggestions_for
from stata_code.core.schema import (
    Backend,
    DatasetInfo,
    ErrorContext,
    ErrorInfo,
    ErrorKind,
    GraphFormat,
    GraphInfo,
    LogInfo,
    Matrix,
    ResultsInfo,
    RunResult,
    StataEdition,
    StataInfo,
    StataReturns,
    StataWarning,
    Suggestion,
    VariableInfo,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _stata() -> StataInfo:
    return StataInfo(version="18.0", edition=StataEdition.MP, backend=Backend.PYSTATA)


def _ok(**overrides) -> RunResult:
    base = dict(
        ok=True,
        rc=0,
        session_id="main",
        request_id="01HXJ2K4Q9V8P3F7N6M5R2T1B0",
        started_at="2026-04-30T14:22:08.123Z",
        elapsed_ms=10,
        stata=_stata(),
    )
    base.update(overrides)
    return RunResult(**base)


def _err(**overrides) -> RunResult:
    base = dict(
        ok=False,
        rc=111,
        session_id="main",
        request_id="01HXJ2K4Q9V8P3F7N6M5R2T1B1",
        started_at="2026-04-30T14:22:09.456Z",
        elapsed_ms=5,
        stata=_stata(),
        error=ErrorInfo(
            kind=ErrorKind.VARNAME_NOT_FOUND,
            rc=111,
            message="variable mpgg not found",
            varname="mpgg",
        ),
    )
    base.update(overrides)
    return RunResult(**base)


# ─────────────────────────────────────────────────────────────────────────────
# Consistency contract (the most important set of tests)
# ─────────────────────────────────────────────────────────────────────────────


class TestConsistencyContract:
    def test_minimal_ok_result_validates(self):
        r = _ok()
        assert r.ok is True
        assert r.rc == 0
        assert r.error is None

    def test_ok_true_rejects_non_null_error(self):
        with pytest.raises(ValueError, match="error to be None"):
            _ok(error=ErrorInfo(kind=ErrorKind.UNKNOWN, rc=0))

    def test_ok_true_rejects_nonzero_rc(self):
        with pytest.raises(ValueError, match="rc=0"):
            _ok(rc=1)

    def test_ok_false_rejects_null_error(self):
        with pytest.raises(ValueError, match="error to be non-None"):
            _err(error=None)

    def test_top_rc_must_match_error_rc(self):
        with pytest.raises(ValueError, match="must equal error.rc"):
            _err(
                rc=111,
                error=ErrorInfo(kind=ErrorKind.UNKNOWN, rc=999, message="x"),
            )

    def test_synthetic_rc_minus_two_is_timeout(self):
        # A timeout is a non-Stata adapter event with rc=-2.
        r = _err(
            rc=-2,
            error=ErrorInfo(kind=ErrorKind.TIMEOUT, rc=-2, message="timed out"),
        )
        assert r.error.kind == ErrorKind.TIMEOUT


# ─────────────────────────────────────────────────────────────────────────────
# Field-level invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestSessionId:
    def test_default_is_main(self):
        assert _ok().session_id == "main"

    def test_alphanumeric_underscore_dash_accepted(self):
        _ok(session_id="my-session_2")

    def test_colon_rejected(self):
        with pytest.raises(ValueError, match=r"\[A-Za-z0-9_-\]"):
            _ok(session_id="host-7:main")

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            _ok(session_id="")

    def test_space_rejected(self):
        with pytest.raises(ValueError):
            _ok(session_id="my session")


class TestElapsedMs:
    def test_zero_accepted(self):
        _ok(elapsed_ms=0)

    def test_negative_rejected(self):
        with pytest.raises(ValueError, match="≥ 0"):
            _ok(elapsed_ms=-1)

    def test_stata_elapsed_can_be_null(self):
        r = _ok(stata_elapsed_ms=None)
        assert r.stata_elapsed_ms is None

    def test_stata_elapsed_negative_rejected(self):
        with pytest.raises(ValueError, match="≥ 0"):
            _ok(stata_elapsed_ms=-5)


class TestLogInvariants:
    def test_truncated_requires_ref(self):
        with pytest.raises(ValueError, match="requires log.ref"):
            LogInfo(head="head", tail="tail", lines_total=100, truncated=True)

    def test_not_truncated_with_nonempty_tail_rejected(self):
        with pytest.raises(ValueError, match="tail to be empty"):
            LogInfo(head="x", tail="y", lines_total=2, truncated=False)

    def test_not_truncated_with_full_log_in_head_ok(self):
        log = LogInfo(head="entire log", tail="", lines_total=1, truncated=False)
        assert log.tail == ""
        assert log.ref is None

    def test_truncated_with_ref_ok(self):
        log = LogInfo(
            head="head",
            tail="tail",
            lines_total=200,
            truncated=True,
            ref="log://abc",
        )
        assert log.ref == "log://abc"

    def test_negative_lines_rejected(self):
        with pytest.raises(ValueError, match="≥ 0"):
            LogInfo(lines_total=-1)


class TestMatrixInvariants:
    def test_inline_values_basic(self):
        m = Matrix(rows=["y1"], cols=["weight", "_cons"], values=[[-0.006, 39.44]])
        assert m.values == [[-0.006, 39.44]]

    def test_row_count_must_match(self):
        with pytest.raises(ValueError, match="rows"):
            Matrix(rows=["a"], cols=["x", "y"], values=[[1, 2], [3, 4]])

    def test_col_count_must_match(self):
        with pytest.raises(ValueError, match="cols"):
            Matrix(rows=["a"], cols=["x", "y"], values=[[1, 2, 3]])

    def test_values_or_ref_required(self):
        with pytest.raises(ValueError, match="values or ref"):
            Matrix(rows=["a"], cols=["x"])

    def test_ref_only_for_large_matrix(self):
        m = Matrix(rows=["a"], cols=["x"], ref="matrix://abc")
        assert m.values is None
        assert m.ref == "matrix://abc"

    def test_missing_values_as_null(self):
        m = Matrix(rows=["r"], cols=["c"], values=[[None]])
        assert m.values == [[None]]


class TestErrorTruncation:
    def test_message_truncated_at_limit(self):
        e = ErrorInfo(kind=ErrorKind.UNKNOWN, rc=999, message="x" * 5000)
        assert len(e.message) == 4096
        assert e.message.endswith("…")

    def test_message_under_limit_unchanged(self):
        msg = "short"
        e = ErrorInfo(kind=ErrorKind.UNKNOWN, rc=999, message=msg)
        assert e.message == msg

    def test_command_truncated_at_limit(self):
        e = ErrorInfo(kind=ErrorKind.UNKNOWN, rc=999, command="y" * 2000)
        assert len(e.command) == 1024
        assert e.command.endswith("…")

    def test_warning_truncated_at_limit(self):
        w = StataWarning(kind="convergence", message="z" * 3000)
        assert len(w.message) == 1024


# ─────────────────────────────────────────────────────────────────────────────
# Round-trip
# ─────────────────────────────────────────────────────────────────────────────


class TestRoundTrip:
    def test_minimal_ok_dict_roundtrip(self):
        r = _ok()
        d = r.model_dump()
        r2 = RunResult.model_validate(d)
        assert r2.model_dump() == d

    def test_minimal_err_dict_roundtrip(self):
        r = _err()
        d = r.model_dump()
        r2 = RunResult.model_validate(d)
        assert r2.model_dump() == d

    def test_json_string_roundtrip(self):
        r = _ok()
        s = r.model_dump_json()
        json.loads(s)  # confirm it's parseable JSON
        r2 = RunResult.model_validate_json(s)
        assert r2.model_dump() == r.model_dump()

    def test_full_success_fixture_roundtrip(self):
        # Mirrors SCHEMA.md §2 success example (adapted for the consistency contract).
        d = {
            "ok": True,
            "rc": 0,
            "session_id": "main",
            "request_id": "01HXJ2K4Q9V8P3F7N6M5R2T1B0",
            "started_at": "2026-04-30T14:22:08.123Z",
            "elapsed_ms": 234,
            "stata_elapsed_ms": 198,
            "stata": {"version": "18.0", "edition": "MP", "backend": "pystata"},
            "log": {
                "head": "(1 variable, 74 observations)\n",
                "tail": "",
                "lines_total": 1,
                "bytes_total": 30,
                "truncated": False,
                "complete": True,
                "error_window": None,
                "ref": None,
            },
            "results": {
                "r": {
                    "scalars": {"mean": 21.297, "N": 74.0},
                    "macros": {},
                    "matrices": {},
                },
                "e": {
                    "scalars": {"N": 74.0, "df_m": 1.0, "r2": 0.219},
                    "macros": {"cmd": "regress", "depvar": "mpg"},
                    "matrices": {
                        "b": {
                            "rows": ["mpg"],
                            "cols": ["weight", "_cons"],
                            "values": [[-0.006, 39.44]],
                            "ref": None,
                        }
                    },
                },
                "last_estimation_cmd": "regress",
            },
            "dataset": {
                "frame": "default",
                "n_obs": 74,
                "n_vars": 12,
                "changed": False,
                "filename": "auto.dta",
                "variables": [
                    {"name": "make", "type": "str18", "label": "Make and Model"},
                    {"name": "price", "type": "int", "label": "Price"},
                ],
            },
            "graphs": [
                {
                    "ref": "graph://7f3a9b/0",
                    "name": "Graph",
                    "format": "png",
                    "width": 800,
                    "height": 600,
                    "source_command": "scatter price mpg",
                    "source_line": 5,
                    "inline": None,
                }
            ],
            "warnings": [],
            "error": None,
            "schema_version": "1.0",
            "capabilities": ["log_truncation", "graph_ref"],
        }
        r = RunResult.model_validate(d)
        # JSON-string roundtrip path
        d2 = json.loads(r.model_dump_json())
        r2 = RunResult.model_validate(d2)
        assert r.model_dump() == r2.model_dump()

    def test_full_error_fixture_roundtrip(self):
        d = {
            "ok": False,
            "rc": 111,
            "session_id": "main",
            "request_id": "01HXJ2K4Q9V8P3F7N6M5R2T1B1",
            "started_at": "2026-04-30T14:22:09.456Z",
            "elapsed_ms": 12,
            "stata_elapsed_ms": 8,
            "stata": {"version": "18.0", "edition": "MP", "backend": "pystata"},
            "log": {
                "head": "...",
                "tail": "",
                "lines_total": 1,
                "bytes_total": 3,
                "truncated": False,
                "complete": True,
                "error_window": "summarize mpgg\nvariable mpgg not found\nr(111);",
                "ref": None,
            },
            "results": {
                "r": {"scalars": {}, "macros": {}, "matrices": {}},
                "e": {"scalars": {}, "macros": {}, "matrices": {}},
                "last_estimation_cmd": None,
            },
            "dataset": {
                "frame": "default",
                "n_obs": 74,
                "n_vars": 12,
                "changed": False,
                "filename": "auto.dta",
                "variables": None,
            },
            "graphs": [],
            "warnings": [],
            "error": {
                "kind": "varname_not_found",
                "rc": 111,
                "rc_label": "variable not found",
                "message": "variable mpgg not found",
                "command": "summarize mpgg",
                "line": 2,
                "context": {
                    "before": ["use auto, clear"],
                    "failing": "summarize mpgg",
                    "after": [],
                },
                "commands_executed": 1,
                "varname": "mpgg",
                "path": None,
                "name": None,
                "suggestions": [
                    {
                        "action": "Did you mean `mpg`? "
                        "`mpgg` is not in the current dataset.",
                        "command": "describe",
                    }
                ],
            },
            "schema_version": "1.0",
            "capabilities": ["log_truncation"],
        }
        r = RunResult.model_validate(d)
        d2 = json.loads(r.model_dump_json())
        r2 = RunResult.model_validate(d2)
        assert r.model_dump() == r2.model_dump()


# ─────────────────────────────────────────────────────────────────────────────
# Forward-compat / version
# ─────────────────────────────────────────────────────────────────────────────


class TestForwardCompat:
    def test_unknown_top_level_field_tolerated(self):
        d = {
            "ok": True,
            "rc": 0,
            "session_id": "main",
            "request_id": "x",
            "started_at": "2026-01-01T00:00:00.000Z",
            "elapsed_ms": 1,
            "stata": {"version": "18.0", "edition": "MP", "backend": "pystata"},
            "future_field": {"not": "in v1.0"},
        }
        r = RunResult.model_validate(d)
        assert r.ok is True

    def test_unknown_inner_field_tolerated(self):
        d = {
            "frame": "default",
            "n_obs": 10,
            "n_vars": 2,
            "changed": False,
            "filename": None,
            "variables": None,
            "future_inner": "ok",
        }
        ds = DatasetInfo.model_validate(d)
        assert ds.n_obs == 10


class TestSchemaVersion:
    def test_default_is_1_0(self):
        assert _ok().schema_version == "1.0"

    def test_other_version_rejected(self):
        with pytest.raises(ValueError):
            _ok(schema_version="2.0")


# ─────────────────────────────────────────────────────────────────────────────
# Enum serialization
# ─────────────────────────────────────────────────────────────────────────────


class TestEnumSerialization:
    def test_error_kind_str_value(self):
        assert ErrorKind.VARNAME_NOT_FOUND.value == "varname_not_found"

    def test_error_kind_serialized_as_string(self):
        e = ErrorInfo(kind=ErrorKind.SYNTAX, rc=198)
        d = e.model_dump()
        assert d["kind"] == "syntax"

    def test_error_kind_round_trip_via_string(self):
        e = ErrorInfo.model_validate({"kind": "varname_not_found", "rc": 111})
        assert e.kind == ErrorKind.VARNAME_NOT_FOUND

    def test_graph_format_default_png(self):
        g = GraphInfo(ref="graph://x")
        assert g.format == GraphFormat.PNG


# ─────────────────────────────────────────────────────────────────────────────
# rc → kind classification
# ─────────────────────────────────────────────────────────────────────────────


class TestClassifyRc:
    def test_varname_not_found_rc_111(self):
        assert classify_rc(111) == ErrorKind.VARNAME_NOT_FOUND

    def test_syntax_rc_198(self):
        # Critical: 198 must NOT route to varname_not_found via message-parse.
        assert classify_rc(198) == ErrorKind.SYNTAX

    def test_command_not_found_rc_199(self):
        assert classify_rc(199) == ErrorKind.COMMAND_NOT_FOUND

    def test_no_observations_rc_2000(self):
        assert classify_rc(2000) == ErrorKind.NO_OBSERVATIONS

    def test_data_in_memory_rc_4(self):
        assert classify_rc(4) == ErrorKind.DATA_IN_MEMORY

    def test_stata_limit_rc_901(self):
        # 901 (Stata-imposed limit) is distinct from 909 (OS OOM).
        assert classify_rc(901) == ErrorKind.STATA_LIMIT
        assert classify_rc(909) == ErrorKind.OUT_OF_MEMORY

    def test_matrix_split_into_three_kinds(self):
        assert classify_rc(503) == ErrorKind.MATRIX_CONFORMABILITY
        assert classify_rc(504) == ErrorKind.MATRIX_MISSING
        assert classify_rc(506) == ErrorKind.MATRIX_SINGULAR

    def test_synthetic_minus_one_adapter_crash(self):
        assert classify_rc(-1) == ErrorKind.ADAPTER_CRASH

    def test_synthetic_minus_two_timeout(self):
        assert classify_rc(-2) == ErrorKind.TIMEOUT

    def test_synthetic_minus_three_cancelled(self):
        assert classify_rc(-3) == ErrorKind.CANCELLED

    def test_unmapped_rc_falls_back_to_unknown(self):
        assert classify_rc(99999) == ErrorKind.UNKNOWN


# ─────────────────────────────────────────────────────────────────────────────
# Suggestion seeds
# ─────────────────────────────────────────────────────────────────────────────


class TestSuggestions:
    def test_varname_with_close_match_proposes_it(self):
        suggs = suggestions_for(
            ErrorKind.VARNAME_NOT_FOUND,
            varname="mpgg",
            available_varnames=["mpg", "price", "weight"],
        )
        assert len(suggs) == 1
        assert "mpg" in suggs[0].action
        assert suggs[0].command == "describe"

    def test_varname_without_candidates(self):
        suggs = suggestions_for(ErrorKind.VARNAME_NOT_FOUND, varname="xyz")
        assert len(suggs) == 1
        assert "xyz" in suggs[0].action

    def test_varname_unknown_no_match(self):
        # close_match cutoff at 0.6 — completely different name shouldn't match.
        suggs = suggestions_for(
            ErrorKind.VARNAME_NOT_FOUND,
            varname="qqqqqqq",
            available_varnames=["mpg", "price"],
        )
        assert len(suggs) == 1
        # No "Did you mean" — just the "not in dataset" form.
        assert "Did you mean" not in suggs[0].action

    def test_command_not_found_suggests_ssc(self):
        suggs = suggestions_for(ErrorKind.COMMAND_NOT_FOUND)
        assert any("ssc install" in s.action for s in suggs)

    def test_name_conflict_mentions_replace(self):
        suggs = suggestions_for(ErrorKind.NAME_CONFLICT, name="mpg")
        assert any("replace" in s.action for s in suggs)

    def test_data_in_memory_suggests_clear(self):
        suggs = suggestions_for(ErrorKind.DATA_IN_MEMORY)
        assert any(s.command == "clear" for s in suggs)

    def test_file_not_found_with_path_suggests_pwd(self):
        suggs = suggestions_for(ErrorKind.FILE_NOT_FOUND, path="auto.dta")
        assert len(suggs) == 1
        assert "auto.dta" in suggs[0].action
        assert suggs[0].command == "pwd"

    def test_syntax_has_no_canonical_suggestion(self):
        # SYNTAX is generic; no automatic hint.
        assert suggestions_for(ErrorKind.SYNTAX) == []

    def test_unknown_has_no_canonical_suggestion(self):
        assert suggestions_for(ErrorKind.UNKNOWN) == []
