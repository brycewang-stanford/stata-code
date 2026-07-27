"""Offline unit tests for the pure/stubable helpers in core/runner.py.

No Stata installation required: helpers taking an ``rt`` runtime object are
exercised against small fakes that mimic pystata's interface exactly as
runner.py calls it (``run_capture``, ``run_suppressed``, ``sfi.*``,
``edition``).
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from stata_code.core import _refs, runner
from stata_code.core.schema import (
    Backend,
    ErrorKind,
    StataEdition,
)

# ─────────────────────────────────────────────────────────────────────────────
# Fake pystata runtime
# ─────────────────────────────────────────────────────────────────────────────


class _RaiseSentinel:
    """Marker value: the fake sfi accessor raises instead of returning."""


RAISE = _RaiseSentinel()


class FakeScalar:
    def __init__(self, values: dict):
        self._values = values

    def getValue(self, key):  # noqa: N802 - mimics sfi API
        if key not in self._values:
            raise KeyError(key)
        v = self._values[key]
        if v is RAISE:
            raise RuntimeError(f"sfi failure for {key}")
        return v


class FakeMacro:
    def __init__(self, values: dict):
        self._values = values

    def getGlobal(self, key):  # noqa: N802
        if key not in self._values:
            raise KeyError(key)
        v = self._values[key]
        if v is RAISE:
            raise RuntimeError(f"sfi failure for {key}")
        return v


class FakeMatrixApi:
    """Matrices as name -> {"values": ..., "rows": ..., "cols": ...}."""

    def __init__(self, matrices: dict):
        self._matrices = matrices

    def _entry(self, key):
        if key not in self._matrices:
            raise KeyError(key)
        entry = self._matrices[key]
        if entry is RAISE:
            raise RuntimeError(f"sfi failure for {key}")
        return entry

    def get(self, key):
        return self._entry(key)["values"]

    def getRowNames(self, key):  # noqa: N802
        return self._entry(key)["rows"]

    def getColNames(self, key):  # noqa: N802
        return self._entry(key)["cols"]


class FakeToolkit:
    def __init__(self, expansions: dict):
        self._expansions = expansions

    def macroExpand(self, expr):  # noqa: N802
        v = self._expansions.get(expr, "")
        if v is RAISE:
            raise RuntimeError(f"macroExpand failure for {expr}")
        return v


class FakeData:
    def __init__(self, variables, n_obs: int):
        self._vars = list(variables)  # (name, type, label) triples
        self._n_obs = n_obs

    def getVarCount(self):  # noqa: N802
        return len(self._vars)

    def getObsTotal(self):  # noqa: N802
        return self._n_obs

    def getVarName(self, i):  # noqa: N802
        return self._vars[i][0]

    def getVarType(self, i):  # noqa: N802
        return self._vars[i][1]

    def getVarLabel(self, i):  # noqa: N802
        return self._vars[i][2]


class FakeRt:
    """Minimal stand-in for stata_code.core._runtime's runtime object."""

    def __init__(
        self,
        *,
        capture: dict | None = None,
        scalars: dict | None = None,
        macros: dict | None = None,
        matrices: dict | None = None,
        expansions: dict | None = None,
        variables=(),
        n_obs: int = 0,
        edition: str | None = "mp",
    ):
        self.commands: list[str] = []
        self._capture = capture or {}
        self.edition = edition
        self.sfi = SimpleNamespace(
            Data=FakeData(variables, n_obs),
            Scalar=FakeScalar(scalars or {}),
            Macro=FakeMacro(macros or {}),
            Matrix=FakeMatrixApi(matrices or {}),
            SFIToolkit=FakeToolkit(expansions or {}),
        )

    def run_capture(self, cmd: str):
        self.commands.append(cmd)
        result = self._capture.get(cmd, ("", 0, None))
        if result is RAISE:
            raise RuntimeError(f"run_capture failure for {cmd}")
        return result

    def run_suppressed(self, cmd: str) -> None:
        self.commands.append(cmd)


# ─────────────────────────────────────────────────────────────────────────────
# _split_log
# ─────────────────────────────────────────────────────────────────────────────


class TestSplitLog:
    def test_short_log_kept_whole(self):
        """A log within head+tail budget is inlined untruncated with no ref."""
        info = runner._split_log("a\nb\nc", 5, 5, False, "req1")
        assert info.head == "a\nb\nc"
        assert info.tail == ""
        assert info.truncated is False
        assert info.ref is None
        assert info.lines_total == 3
        assert info.bytes_total == len(b"a\nb\nc")
        assert info.complete is True

    def test_boundary_exact_budget_not_truncated(self):
        """lines_total == head + tail is NOT truncated (strict > comparison)."""
        log = "\n".join(f"l{i}" for i in range(1, 5))
        info = runner._split_log(log, 2, 2, False, "req-boundary")
        assert info.truncated is False
        assert info.head == log
        assert info.tail == ""

    def test_long_log_truncated_with_ref(self):
        """One line over budget splits into head/tail and stashes the full text."""
        log = "\n".join(f"l{i}" for i in range(1, 6))  # 5 lines
        info = runner._split_log(log, 2, 2, False, "req-trunc")
        assert info.truncated is True
        assert info.head == "l1\nl2"
        assert info.tail == "l4\nl5"
        assert info.lines_total == 5
        assert info.ref == "log://req-trunc"
        full = runner.get_log(info.ref)
        assert full["text"] == log
        assert full["lines_total"] == 5
        assert full["bytes_total"] == len(log.encode())

    def test_include_full_disables_truncation(self):
        """include_full=True inlines everything even when over budget."""
        log = "\n".join(f"l{i}" for i in range(1, 11))
        info = runner._split_log(log, 1, 1, True, "req-full")
        assert info.truncated is False
        assert info.head == log
        assert info.tail == ""
        assert info.ref is None

    def test_trailing_newline_dropped_from_line_count(self):
        """A single trailing newline does not count as an extra (empty) line."""
        info = runner._split_log("a\nb\n", 5, 5, False, "req-nl")
        assert info.lines_total == 2
        assert info.head == "a\nb"
        assert info.bytes_total == 3  # "a\nb"

    def test_crlf_and_cr_normalized(self):
        """CRLF and lone CR are both normalized to LF before splitting."""
        info = runner._split_log("a\r\nb\rc", 5, 5, False, "req-crlf")
        assert info.lines_total == 3
        assert info.head == "a\nb\nc"

    def test_empty_log(self):
        """An empty log yields zero lines/bytes and no truncation."""
        info = runner._split_log("", 5, 5, False, "req-empty")
        assert info.lines_total == 0
        assert info.bytes_total == 0
        assert info.head == ""
        assert info.truncated is False


# ─────────────────────────────────────────────────────────────────────────────
# _parse_return_list
# ─────────────────────────────────────────────────────────────────────────────


_RETURN_LIST_TEXT = """\
scalars:
                  r(N) =  74
               r(mean) =  21.2972972972973

macros:
            r(varlist) : "mpg"

matrices:
              r(table) :  9 x 1
"""


class TestParseReturnList:
    def test_parses_all_three_categories(self):
        """Names land in the section under whose header they appear."""
        out = runner._parse_return_list(_RETURN_LIST_TEXT)
        assert out == {
            "scalars": ["N", "mean"],
            "macros": ["varlist"],
            "matrices": ["table"],
        }

    def test_ereturn_style_names_accepted(self):
        """e(...) names are matched by the same parser as r(...)."""
        text = "scalars:\n               e(N) =  74\nmacros:\n            e(cmd) : \"regress\"\n"
        out = runner._parse_return_list(text)
        assert out["scalars"] == ["N"]
        assert out["macros"] == ["cmd"]

    def test_functions_section_ignored(self):
        """Entries under an unknown header (e.g. functions:) are dropped."""
        text = "functions:\n              r(fn) : whatever\nscalars:\n         r(N) =  1\n"
        out = runner._parse_return_list(text)
        assert out["scalars"] == ["N"]
        assert out["macros"] == []
        assert out["matrices"] == []

    def test_entries_before_any_header_ignored(self):
        """Names appearing before the first section header are not collected."""
        text = "         r(orphan) =  1\nscalars:\n         r(N) =  1\n"
        out = runner._parse_return_list(text)
        assert out["scalars"] == ["N"]

    def test_empty_text(self):
        """Empty input yields empty category lists."""
        assert runner._parse_return_list("") == {
            "scalars": [],
            "macros": [],
            "matrices": [],
        }


# ─────────────────────────────────────────────────────────────────────────────
# _last_error_line
# ─────────────────────────────────────────────────────────────────────────────


class TestLastErrorLine:
    def test_single_error_takes_first_line(self):
        """Without command echoes, the first line is the diagnosis."""
        assert (
            runner._last_error_line("variable mpgg not found\nr(111);")
            == "variable mpgg not found"
        )

    def test_transcript_returns_diagnosis_not_echo(self):
        """With echoes, the sentence between the last echo and r(NN); wins."""
        transcript = (
            '. display "first"\n'
            "first\n"
            "\n"
            ". summarize mpgg\n"
            "variable mpgg not found\n"
            "r(111);\n"
        )
        assert runner._last_error_line(transcript) == "variable mpgg not found"

    def test_transcript_with_only_echo_and_rc_falls_back_to_first_line(self):
        """When nothing but echoes and rc lines exist, fall back to line one."""
        transcript = ". summarize mpgg\nr(111);"
        assert runner._last_error_line(transcript) == ". summarize mpgg"

    def test_empty_text(self):
        """Empty transcript yields an empty message."""
        assert runner._last_error_line("") == ""


# ─────────────────────────────────────────────────────────────────────────────
# _parse_failure_transcript
# ─────────────────────────────────────────────────────────────────────────────


class TestParseFailureTranscript:
    def test_single_line_error_single_line_code(self):
        """A one-liner failure attributes line 1 with zero commands executed."""
        out = runner._parse_failure_transcript(
            "variable mpgg not found\nr(111);", "summarize mpgg"
        )
        assert out["failing"] == "summarize mpgg"
        assert out["command"] == "summarize mpgg"
        assert out["line"] == 1
        assert out["commands_executed"] == 0

    def test_single_line_error_respects_blank_lines_for_line_number(self):
        """Leading blank lines in the user code shift the reported line."""
        out = runner._parse_failure_transcript(
            "variable mpgg not found\nr(111);", "\nsummarize mpgg\n"
        )
        assert out["line"] == 2

    def test_single_line_error_multi_line_code_unattributable(self):
        """A short error with multi-line code cannot be pinpointed."""
        out = runner._parse_failure_transcript(
            "variable mpgg not found\nr(111);", "display 1\nsummarize mpgg"
        )
        assert out["failing"] == ""
        assert out["line"] is None
        assert out["commands_executed"] is None
        assert out["command"] is None

    def test_multi_line_transcript_pinpoints_failure(self):
        """The last `. cmd` echo is the failing command; earlier ones count."""
        code = 'display "first"\nsummarize mpg\nsummarize mpgg\ndisplay "after"'
        transcript = (
            '. display "first"\n'
            "first\n"
            "\n"
            ". summarize mpg\n"
            "    Variable |  Obs ...\n"
            "\n"
            ". summarize mpgg\n"
            "variable mpgg not found\n"
            "r(111);\n"
        )
        out = runner._parse_failure_transcript(transcript, code)
        assert out["failing"] == "summarize mpgg"
        assert out["command"] == "summarize mpgg"
        assert out["line"] == 3
        assert out["commands_executed"] == 2
        assert out["before"] == ['display "first"', "summarize mpg"]
        assert out["after"] == ['display "after"']

    def test_comment_echoes_not_counted_as_executed(self):
        """`. * ...` / `. // ...` echoes are comments, not executed commands."""
        code = '* setup comment\ndisplay "first"\nsummarize mpgg'
        transcript = (
            ". * setup comment\n"
            "\n"
            '. display "first"\n'
            "first\n"
            "\n"
            ". summarize mpgg\n"
            "variable mpgg not found\n"
            "r(111);\n"
        )
        out = runner._parse_failure_transcript(transcript, code)
        assert out["commands_executed"] == 1
        assert out["line"] == 3
        # No line after the failure => empty `after`.
        assert out["after"] == []

    def test_transcript_with_only_empty_prompts_yields_defaults(self):
        """Bare `. ` prompts contain no commands; nothing is attributable."""
        out = runner._parse_failure_transcript("noise\n. \nr(199);", "display 1\ndisplay 2")
        assert out["failing"] == ""
        assert out["commands_executed"] is None


# ─────────────────────────────────────────────────────────────────────────────
# _build_error
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildError:
    def test_varname_not_found_full_envelope(self):
        """rc 111: kind, varname, fuzzy suggestion, and pinpointing all line up."""
        err = runner._build_error(
            111,
            "variable mpgg not found\nr(111);",
            "summarize mpgg",
            ["mpg", "price", "weight"],
        )
        assert err.kind == ErrorKind.VARNAME_NOT_FOUND
        assert err.rc == 111
        assert err.rc_label == "variable not found"
        assert err.message == "variable mpgg not found"
        assert err.varname == "mpgg"
        assert err.command == "summarize mpgg"
        assert err.line == 1
        assert err.commands_executed == 0
        assert err.context.failing == "summarize mpgg"
        assert any("Did you mean `mpg`?" in s.action for s in err.suggestions)
        assert err.recovery is not None
        assert err.recovery.category == "user_code"
        assert err.recovery.needs_code_change is True
        assert err.recovery.retriable is False

    def test_command_not_found_extracts_command_token(self):
        """rc 199: the unrecognized token feeds fuzzy match; error.command is the line."""
        err = runner._build_error(
            199,
            "command regresss is unrecognized\nr(199);",
            "regresss price mpg",
            None,
        )
        assert err.kind == ErrorKind.COMMAND_NOT_FOUND
        # `command` reflects the failing source line (pinpoint), not the token.
        assert err.command == "regresss price mpg"
        assert any("Did you mean `regress`?" in s.action for s in err.suggestions)
        assert "ssc install" in err.suggestions[-1].action

    def test_file_not_found_extracts_path(self):
        """rc 601: the offending path is parsed out of the message."""
        err = runner._build_error(
            601,
            "file /tmp/nope.dta not found\nr(601);",
            'use "/tmp/nope.dta"',
            None,
        )
        assert err.kind == ErrorKind.FILE_NOT_FOUND
        assert err.path == "/tmp/nope.dta"
        assert err.varname is None

    def test_name_conflict_extracts_name(self):
        """rc 110: 'already defined' fills `name` (not `varname`)."""
        err = runner._build_error(
            110,
            "variable price already defined\nr(110);",
            "gen price = 1",
            None,
        )
        assert err.kind == ErrorKind.NAME_CONFLICT
        assert err.name == "price"
        assert err.varname is None
        assert any("drop price" in (s.command or "") for s in err.suggestions)

    def test_unknown_rc_maps_to_unknown_kind_and_empty_label(self):
        """An unverified rc gets kind=unknown and no fabricated label."""
        err = runner._build_error(9999, "weird failure\nr(9999);", "whatever", None)
        assert err.kind == ErrorKind.UNKNOWN
        assert err.rc_label == ""
        assert err.recovery is not None
        assert err.recovery.category == "unknown"

    def test_empty_message_yields_empty_message_field(self):
        """No transcript text at all still builds a valid ErrorInfo."""
        err = runner._build_error(198, "", "error 198", None)
        assert err.kind == ErrorKind.SYNTAX
        assert err.message == ""


# ─────────────────────────────────────────────────────────────────────────────
# _extract_warnings
# ─────────────────────────────────────────────────────────────────────────────


class TestExtractWarnings:
    def test_clean_log_has_no_warnings(self):
        """Plain output produces an empty warning list."""
        assert runner._extract_warnings("nothing to see\nhere") == []

    def test_omitted_collinear_detected(self):
        """The collinearity note maps to kind=omitted_collinear."""
        log = "note: mpg_dup omitted because of collinearity.\n"
        out = runner._extract_warnings(log)
        assert [(w.kind, w.message) for w in out] == [
            ("omitted_collinear", "note: mpg_dup omitted because of collinearity.")
        ]

    def test_convergence_and_singular_detected(self):
        """MLE-family diagnostics map to their specific kinds."""
        log = "convergence not achieved\nmatrix not positive definite\n"
        kinds = {w.kind for w in runner._extract_warnings(log)}
        assert kinds == {"convergence", "singular"}

    def test_generic_note_falls_through(self):
        """A note that matches no specific pattern becomes kind=note."""
        out = runner._extract_warnings("note: variable xyz was byte, now float\n")
        assert len(out) == 1
        assert out[0].kind == "note"
        assert out[0].message == "note: variable xyz was byte, now float"

    def test_duplicate_warnings_deduped(self):
        """Identical (kind, message) pairs are emitted once."""
        log = "convergence not achieved\nconvergence not achieved\n"
        out = runner._extract_warnings(log)
        assert len(out) == 1

    def test_margin_collinear_note_not_double_counted(self):
        """A collinearity note at the margin is NOT also a generic note."""
        out = runner._extract_warnings("note: x omitted because of collinearity.\n")
        assert [w.kind for w in out] == ["omitted_collinear"]

    def test_indented_collinear_note_not_double_counted(self):
        """An *indented* collinearity note yields only the specific warning.

        The generic-note dedup tests span overlap, so the leading whitespace
        that _NOTE_RE includes (and the specific pattern does not) no longer
        defeats it.
        """
        out = runner._extract_warnings("  note: x omitted because of collinearity.\n")
        assert [w.kind for w in out] == ["omitted_collinear"]


# ─────────────────────────────────────────────────────────────────────────────
# _graph_source_hints
# ─────────────────────────────────────────────────────────────────────────────


class TestGraphSourceHints:
    def test_named_graph_mapped_to_source_line(self):
        """name(...) options key the named-hint dict with (line, lineno)."""
        code = 'sysuse auto\nscatter price mpg, name(g1)\ndisplay "x"'
        named, unnamed = runner._graph_source_hints(code)
        assert named == {"g1": ("scatter price mpg, name(g1)", 2)}
        assert unnamed == []

    def test_unnamed_graph_commands_collected_in_order(self):
        """Graph commands without name(...) are retained in source order."""
        code = "scatter price mpg\n\nhistogram weight"
        named, unnamed = runner._graph_source_hints(code)
        assert named == {}
        assert unnamed == [("scatter price mpg", 1), ("histogram weight", 3)]

    def test_comments_and_non_graph_lines_skipped(self):
        """Comments and non-drawing commands produce no hints."""
        code = "* scatter price mpg\n// twoway line y x\nregress price mpg\n"
        named, unnamed = runner._graph_source_hints(code)
        assert named == {}
        assert unnamed == []

    def test_graph_utility_subcommands_not_hints(self):
        """`graph export` / `graph drop` are utilities, not drawing commands."""
        code = 'graph export "out.png", replace\ngraph drop _all\ngraph dir'
        named, unnamed = runner._graph_source_hints(code)
        assert named == {}
        assert unnamed == []

    def test_graph_drawing_subcommands_are_hints(self):
        """`graph bar` / `graph twoway` count as drawing commands."""
        code = "graph bar price, name(bars)\ngraph twoway scatter y x"
        named, unnamed = runner._graph_source_hints(code)
        assert "bars" in named
        assert unnamed == [("graph twoway scatter y x", 2)]

    def test_hist_alias_recognized(self):
        """The `hist` abbreviation is treated as a drawing command."""
        _named, unnamed = runner._graph_source_hints("hist weight")
        assert unnamed == [("hist weight", 1)]


# ─────────────────────────────────────────────────────────────────────────────
# _png_dimensions
# ─────────────────────────────────────────────────────────────────────────────


def _png_bytes(width: int, height: int) -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = (13).to_bytes(4, "big") + b"IHDR"
    return sig + ihdr + width.to_bytes(4, "big") + height.to_bytes(4, "big")


class TestPngDimensions:
    def test_valid_header_parses_width_height(self):
        """Width/height come from bytes 16-24 of a minimal IHDR chunk."""
        assert runner._png_dimensions(_png_bytes(800, 600)) == (800, 600)

    def test_short_data_returns_none(self):
        """Fewer than 24 bytes cannot contain an IHDR — (None, None)."""
        assert runner._png_dimensions(_png_bytes(1, 1)[:23]) == (None, None)

    def test_bad_magic_returns_none(self):
        """A non-PNG signature is rejected even with enough bytes."""
        assert runner._png_dimensions(b"NOTAPNG!" + b"\x00" * 16) == (None, None)

    def test_empty_bytes(self):
        """Empty input is handled without error."""
        assert runner._png_dimensions(b"") == (None, None)


# ─────────────────────────────────────────────────────────────────────────────
# _safe_file_stem
# ─────────────────────────────────────────────────────────────────────────────


class TestSafeFileStem:
    def test_disallowed_runs_collapsed_to_underscore(self):
        """Each run of disallowed chars becomes one underscore."""
        assert runner._safe_file_stem("My Graph #1!") == "My_Graph_1"

    def test_leading_trailing_separators_stripped(self):
        """Dots/underscores/dashes are stripped from both ends."""
        assert runner._safe_file_stem("..name-.") == "name"

    def test_all_symbols_yields_empty(self):
        """A name with no salvageable characters collapses to ''."""
        assert runner._safe_file_stem("!!!") == ""

    def test_truncated_to_64_chars(self):
        """Stems are capped at 64 characters."""
        assert runner._safe_file_stem("a" * 100) == "a" * 64

    def test_interior_dots_and_dashes_preserved(self):
        """Interior . _ - are legal and kept."""
        assert runner._safe_file_stem("fig-1.final_v2") == "fig-1.final_v2"


# ─────────────────────────────────────────────────────────────────────────────
# _frame_for_session / _session_for_frame
# ─────────────────────────────────────────────────────────────────────────────


class TestSessionFrameMapping:
    def test_main_maps_to_default_and_back(self):
        """'main' is the master frame 'default', round-trippable."""
        assert runner._frame_for_session("main") == "default"
        assert runner._session_for_frame("default") == "main"

    def test_legal_stata_name_passes_through(self):
        """A schema id that is a legal Stata name is its own frame."""
        assert runner._frame_for_session("alt_1") == "alt_1"
        assert runner._session_for_frame("alt_1") == "alt_1"

    def test_dash_id_routed_through_private_frame_and_round_trips(self):
        """Ids illegal in Stata get a deterministic _sc_ frame; reverse works."""
        frame = runner._frame_for_session("my-session")
        assert frame.startswith("_sc_")
        assert len(frame) == len("_sc_") + 24
        # Deterministic on repeat call.
        assert runner._frame_for_session("my-session") == frame
        assert runner._session_for_frame(frame) == "my-session"

    def test_digit_leading_id_is_mapped(self):
        """'9abc' is schema-legal but not a Stata name — mapped frame."""
        frame = runner._frame_for_session("9abc")
        assert frame.startswith("_sc_")
        assert runner._session_for_frame(frame) == "9abc"

    def test_reserved_prefix_id_is_remapped(self):
        """A session id that itself starts with _sc_ is never used verbatim."""
        frame = runner._frame_for_session("_sc_sneaky")
        assert frame != "_sc_sneaky"
        assert frame.startswith("_sc_")
        assert runner._session_for_frame(frame) == "_sc_sneaky"

    def test_invalid_session_ids_rejected(self):
        """Spaces and ':' are outside the schema pattern."""
        with pytest.raises(ValueError, match=r"\[A-Za-z0-9_-\]"):
            runner._frame_for_session("my session")
        with pytest.raises(ValueError, match=r"\[A-Za-z0-9_-\]"):
            runner._frame_for_session("host-7:main")

    def test_unknown_frame_maps_to_itself(self):
        """A frame never produced by the mapper echoes back as the session id."""
        assert runner._session_for_frame("some_random_frame") == "some_random_frame"


# ─────────────────────────────────────────────────────────────────────────────
# _resolve_working_dir
# ─────────────────────────────────────────────────────────────────────────────


class TestResolveWorkingDir:
    def test_explicit_working_dir_wins(self, tmp_path):
        """working_dir takes precedence over origin_path."""
        sub = tmp_path / "sub"
        sub.mkdir()
        out = runner._resolve_working_dir(
            origin_path=str(tmp_path / "analysis.do"),
            working_dir=str(sub),
            use_origin_workdir=True,
        )
        assert out == sub

    def test_origin_path_parent_used_when_enabled(self, tmp_path):
        """Without working_dir, the origin file's directory is used."""
        out = runner._resolve_working_dir(
            origin_path=str(tmp_path / "analysis.do"),
            working_dir=None,
            use_origin_workdir=True,
        )
        assert out == tmp_path

    def test_origin_ignored_when_use_origin_workdir_false(self, tmp_path):
        """use_origin_workdir=False with no working_dir resolves to None."""
        out = runner._resolve_working_dir(
            origin_path=str(tmp_path / "analysis.do"),
            working_dir=None,
            use_origin_workdir=False,
        )
        assert out is None

    def test_no_inputs_resolves_to_none(self):
        """Neither working_dir nor origin_path means no cwd change."""
        assert (
            runner._resolve_working_dir(
                origin_path=None, working_dir=None, use_origin_workdir=True
            )
            is None
        )

    def test_missing_directory_raises(self, tmp_path):
        """A nonexistent target directory is a ValueError."""
        with pytest.raises(ValueError, match="working directory does not exist"):
            runner._resolve_working_dir(
                origin_path=None,
                working_dir=str(tmp_path / "nope"),
                use_origin_workdir=True,
            )

    def test_relative_path_resolved_to_absolute(self, tmp_path, monkeypatch):
        """A relative working_dir is resolved against the process cwd."""
        (tmp_path / "rel").mkdir()
        monkeypatch.chdir(tmp_path)
        out = runner._resolve_working_dir(
            origin_path=None, working_dir="rel", use_origin_workdir=True
        )
        assert out is not None
        assert out.is_absolute()
        assert out == (tmp_path / "rel").resolve()


# ─────────────────────────────────────────────────────────────────────────────
# _build_cancelled_result
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildCancelledResult:
    def _rt(self) -> FakeRt:
        return FakeRt(
            variables=[("mpg", "int", "Mileage (mpg)"), ("price", "long", "")],
            n_obs=74,
            scalars={"c(changed)": 1.0},
            expansions={
                "`c(filename)'": "auto.dta",
                "`c(frame)'": "default",
                "`c(stata_version)'": "19",
            },
            edition="mp",
        )

    def test_cancelled_envelope_shape(self):
        """rc=-3, kind=cancelled, empty log, but live dataset snapshot."""
        res = runner._build_cancelled_result(
            rt=self._rt(),
            session_id="main",
            request_id="req-cancel",
            started_at="2026-07-20T00:00:00.000Z",
            started=time.monotonic() - 0.05,
            include_dataset_variables=True,
        )
        assert res.ok is False
        assert res.rc == -3
        assert res.error is not None
        assert res.error.kind == ErrorKind.CANCELLED
        assert res.error.rc == -3
        assert res.error.rc_label == "cancelled"
        assert "main" in res.error.message
        assert res.error.commands_executed == 0
        assert res.error.recovery is not None
        assert res.error.recovery.category == "internal"
        # No code ran: empty log, zero Stata time, but wall time counted.
        assert res.log.lines_total == 0
        assert res.log.head == ""
        assert res.stata_elapsed_ms == 0
        assert res.elapsed_ms >= 1
        assert res.graphs == []
        assert res.warnings == []
        assert res.capabilities == ["cancel", "multi_session"]

    def test_cancelled_result_snapshots_dataset_and_stata(self):
        """The dataset block reflects the fake runtime state post-cancel."""
        res = runner._build_cancelled_result(
            rt=self._rt(),
            session_id="main",
            request_id="req-cancel-2",
            started_at="2026-07-20T00:00:00.000Z",
            started=time.monotonic(),
            include_dataset_variables=True,
        )
        assert res.dataset.n_obs == 74
        assert res.dataset.n_vars == 2
        assert res.dataset.changed is True
        assert res.dataset.filename == "auto.dta"
        assert res.dataset.variables is not None
        assert [v.name for v in res.dataset.variables] == ["mpg", "price"]
        assert res.stata.version == "19"
        assert res.stata.edition == StataEdition.MP
        assert res.stata.backend == Backend.PYSTATA


# ─────────────────────────────────────────────────────────────────────────────
# rt-backed collectors
# ─────────────────────────────────────────────────────────────────────────────


class TestListReturns:
    def test_r_prefix_runs_return_list(self):
        """prefix='r' issues `return list` and parses its stdout."""
        rt = FakeRt(capture={"return list": (_RETURN_LIST_TEXT, 0, None)})
        out = runner._list_returns(rt, "r")
        assert out["scalars"] == ["N", "mean"]
        assert rt.commands == ["return list"]

    def test_e_prefix_runs_ereturn_list(self):
        """prefix='e' issues `ereturn list`."""
        rt = FakeRt(capture={"ereturn list": ("scalars:\n     e(N) =  74\n", 0, None)})
        out = runner._list_returns(rt, "e")
        assert out["scalars"] == ["N"]
        assert rt.commands == ["ereturn list"]

    def test_nonzero_rc_yields_empty_categories(self):
        """A failed `return list` degrades to empty name lists."""
        rt = FakeRt(capture={"return list": ("boom", 199, "err")})
        assert runner._list_returns(rt, "r") == {
            "scalars": [],
            "macros": [],
            "matrices": [],
        }


class TestCollectReturns:
    def _text(self) -> str:
        return (
            "scalars:\n"
            "                  r(N) =  74\n"
            "               r(mean) =  21.5\n"
            "\n"
            "macros:\n"
            '            r(varlist) : "mpg"\n'
            "\n"
            "matrices:\n"
            "              r(small) :  2 x 2\n"
        )

    def test_typed_collection_of_all_categories(self):
        """Scalars come back as floats, macros as strings, matrices inline."""
        rt = FakeRt(
            capture={"return list": (self._text(), 0, None)},
            scalars={"r(N)": 74, "r(mean)": 21.5},
            macros={"r(varlist)": "mpg"},
            matrices={"r(small)": {"values": [[1, 2], [3, 4]], "rows": ["r1", "r2"], "cols": ["c1", "c2"]}},
        )
        out = runner._collect_returns(rt, "r")
        assert out.scalars == {"N": 74.0, "mean": 21.5}
        assert isinstance(out.scalars["N"], float)
        assert out.macros == {"varlist": "mpg"}
        m = out.matrices["small"]
        assert m.values == [[1.0, 2.0], [3.0, 4.0]]
        assert m.rows == ["r1", "r2"]
        assert m.cols == ["c1", "c2"]
        assert m.ref is None

    def test_per_name_failures_coerced(self):
        """A failing scalar becomes None, macro '', matrix silently dropped."""
        text = (
            "scalars:\n"
            "               r(good) =  1\n"
            "                r(bad) =  2\n"
            "macros:\n"
            "               r(mbad) : \"x\"\n"
            "matrices:\n"
            "              r(boom) :  1 x 1\n"
        )
        rt = FakeRt(
            capture={"return list": (text, 0, None)},
            scalars={"r(good)": 1.0, "r(bad)": RAISE},
            macros={"r(mbad)": RAISE},
            matrices={"r(boom)": RAISE},
        )
        out = runner._collect_returns(rt, "r")
        assert out.scalars == {"good": 1.0, "bad": None}
        assert out.macros == {"mbad": ""}
        assert out.matrices == {}

    def test_none_scalar_and_none_macro_coerced(self):
        """sfi returning None yields scalar None / macro '' (not a crash)."""
        text = "scalars:\n               r(miss) =  .\nmacros:\n               r(mm) : \"\"\n"
        rt = FakeRt(
            capture={"return list": (text, 0, None)},
            scalars={"r(miss)": None},
            macros={"r(mm)": None},
        )
        out = runner._collect_returns(rt, "r", "req-none")
        assert out.scalars == {"miss": None}
        assert out.macros == {"mm": ""}

    def test_large_matrix_goes_to_ref(self, monkeypatch):
        """Above the inline cell cap: values=None, ref set, payload in _refs."""
        monkeypatch.setattr(runner, "MATRIX_INLINE_CELL_CAP", 3)
        text = "matrices:\n              r(big) :  2 x 2\n"
        rt = FakeRt(
            capture={"return list": (text, 0, None)},
            matrices={"r(big)": {"values": [[1, None], [3, 4]], "rows": ["a", "b"], "cols": ["x", "y"]}},
        )
        out = runner._collect_returns(rt, "r", "req-big")
        m = out.matrices["big"]
        assert m.values is None
        assert m.ref == "matrix://req-big/r/big"
        payload = runner.get_matrix(m.ref)
        assert payload["values"] == [[1.0, None], [3.0, 4.0]]
        assert payload["rows"] == ["a", "b"]
        assert payload["cols"] == ["x", "y"]

    def test_matrix_with_missing_row_names_gets_positional_names(self):
        """sfi returning None row/col names must not drop the matrix.

        The collector synthesizes positional names (r1.../c1...) so the
        successfully read values survive the Matrix shape validator.
        """
        text = "matrices:\n              r(noname) :  1 x 1\n"
        rt = FakeRt(
            capture={"return list": (text, 0, None)},
            matrices={"r(noname)": {"values": [[5.0]], "rows": None, "cols": None}},
        )
        out = runner._collect_returns(rt, "r", "req-noname")
        assert out.matrices["noname"].rows == ["r1"]
        assert out.matrices["noname"].cols == ["c1"]
        assert out.matrices["noname"].values == [[5.0]]


class TestCollectDataset:
    def test_full_metadata_with_variables(self):
        """Counts, changed flag, filename, frame, and variables all populate."""
        rt = FakeRt(
            variables=[("mpg", "int", "Mileage (mpg)"), ("make", "str18", "")],
            n_obs=74,
            scalars={"c(changed)": 1.0},
            expansions={"`c(filename)'": "auto.dta", "`c(frame)'": "work"},
        )
        ds = runner._collect_dataset(rt, include_variables=True)
        assert ds.n_obs == 74
        assert ds.n_vars == 2
        assert ds.changed is True
        assert ds.filename == "auto.dta"
        assert ds.frame == "work"
        assert ds.variables is not None
        assert [(v.name, v.type, v.label) for v in ds.variables] == [
            ("mpg", "int", "Mileage (mpg)"),
            ("make", "str18", ""),
        ]

    def test_include_variables_false_skips_list(self):
        """include_variables=False leaves `variables` as None but keeps counts."""
        rt = FakeRt(variables=[("mpg", "int", "")], n_obs=10)
        ds = runner._collect_dataset(rt, include_variables=False)
        assert ds.variables is None
        assert ds.n_vars == 1

    def test_empty_dataset_has_no_variable_list(self):
        """Zero variables yields variables=None even when requested."""
        rt = FakeRt(variables=[], n_obs=0)
        ds = runner._collect_dataset(rt, include_variables=True)
        assert ds.variables is None
        assert ds.n_vars == 0

    def test_variable_list_capped_at_200(self):
        """More than _DATASET_VAR_CAP variables are truncated to the cap."""
        rt = FakeRt(variables=[(f"v{i}", "float", "") for i in range(250)], n_obs=1)
        ds = runner._collect_dataset(rt, include_variables=True)
        assert ds.n_vars == 250
        assert ds.variables is not None
        assert len(ds.variables) == 200
        assert ds.variables[0].name == "v0"
        assert ds.variables[-1].name == "v199"

    def test_sfi_failures_fall_back_to_defaults(self):
        """c() lookups that raise degrade to changed=False / filename=None."""
        rt = FakeRt(
            variables=[("a", "byte", "")],
            n_obs=5,
            scalars={"c(changed)": RAISE},
            expansions={"`c(filename)'": RAISE, "`c(frame)'": RAISE},
        )
        ds = runner._collect_dataset(rt, include_variables=False)
        assert ds.changed is False
        assert ds.filename is None
        assert ds.frame == "default"

    def test_empty_frame_macro_falls_back_to_default(self):
        """An empty c(frame) expansion resolves to the 'default' frame."""
        rt = FakeRt(expansions={"`c(frame)'": ""})
        ds = runner._collect_dataset(rt, include_variables=False)
        assert ds.frame == "default"


class TestStataInfo:
    def test_version_and_edition_mapped(self):
        """c(stata_version) and rt.edition populate StataInfo."""
        rt = FakeRt(expansions={"`c(stata_version)'": "19"}, edition="MP")
        info = runner._stata_info(rt)
        assert info.version == "19"
        assert info.edition == StataEdition.MP
        assert info.backend == Backend.PYSTATA

    def test_unknown_edition(self):
        """An unmapped edition string yields StataEdition.UNKNOWN."""
        assert runner._stata_info(FakeRt(edition="weird")).edition == StataEdition.UNKNOWN

    def test_none_edition(self):
        """rt.edition=None is tolerated (empty string lookup)."""
        assert runner._stata_info(FakeRt(edition=None)).edition == StataEdition.UNKNOWN

    def test_version_lookup_failure_yields_none(self):
        """A macroExpand failure degrades version to None."""
        rt = FakeRt(expansions={"`c(stata_version)'": RAISE}, edition="se")
        info = runner._stata_info(rt)
        assert info.version is None
        assert info.edition == StataEdition.SE

    def test_empty_version_string_becomes_none(self):
        """An empty expansion is normalized to None (falsy `or None`)."""
        assert runner._stata_info(FakeRt(expansions={"`c(stata_version)'": ""})).version is None


class TestListGraphNames:
    def test_names_split_from_r_list(self):
        """`graph dir` succeeds: names come from r(list), whitespace-split."""
        rt = FakeRt(
            capture={"graph dir": ("", 0, None)},
            expansions={"`r(list)'": "g_a g_b Graph"},
        )
        assert runner._list_graph_names(rt) == ["g_a", "g_b", "Graph"]

    def test_nonzero_rc_yields_empty(self):
        """A failing `graph dir` yields no names."""
        rt = FakeRt(capture={"graph dir": ("", 111, "err")})
        assert runner._list_graph_names(rt) == []

    def test_empty_r_list_yields_empty(self):
        """No graphs in memory: empty r(list) splits to []."""
        rt = FakeRt(capture={"graph dir": ("", 0, None)}, expansions={"`r(list)'": ""})
        assert runner._list_graph_names(rt) == []

    def test_exception_swallowed(self):
        """Any runtime exception degrades to an empty list."""
        rt = FakeRt(capture={"graph dir": RAISE})
        assert runner._list_graph_names(rt) == []


class TestLastEstimationCmd:
    def test_returns_e_cmd_macro(self):
        """A populated e(cmd) global is returned verbatim."""
        rt = FakeRt(macros={"e(cmd)": "regress"})
        assert runner._last_estimation_cmd(rt) == "regress"

    def test_empty_macro_is_none(self):
        """An empty e(cmd) means no estimation has run."""
        rt = FakeRt(macros={"e(cmd)": ""})
        assert runner._last_estimation_cmd(rt) is None

    def test_lookup_failure_is_none(self):
        """An sfi failure degrades to None."""
        rt = FakeRt(macros={"e(cmd)": RAISE})
        assert runner._last_estimation_cmd(rt) is None


# ─────────────────────────────────────────────────────────────────────────────
# Ref-store integration sanity (used by _split_log / _collect_returns)
# ─────────────────────────────────────────────────────────────────────────────


class TestRefIntegration:
    def test_get_log_unknown_ref_raises_typed_keyerror(self):
        """Unknown log refs raise RefNotFound (a KeyError) with kind info."""
        with pytest.raises(runner.RefNotFound) as exc_info:
            runner.get_log("log://never-existed")
        assert exc_info.value.ref == "log://never-existed"
        assert exc_info.value.kind == "unknown_log_ref"
        assert isinstance(exc_info.value, KeyError)

    def test_split_log_ref_payload_matches_loginfo_totals(self):
        """The stored payload and the LogInfo agree on totals."""
        log = "\n".join(f"line{i}" for i in range(30))
        info = runner._split_log(log, 3, 3, False, "req-agree")
        stored = _refs.get(info.ref)
        assert stored["lines_total"] == info.lines_total == 30
        assert stored["bytes_total"] == info.bytes_total


# ─────────────────────────────────────────────────────────────────────────────
# Keep Path import honest (ruff F401 guard for fixtures typing)
# ─────────────────────────────────────────────────────────────────────────────


def test_resolve_working_dir_returns_path_type(tmp_path):
    """The resolved working dir is a pathlib.Path, ready for cd quoting."""
    out = runner._resolve_working_dir(
        origin_path=None, working_dir=str(tmp_path), use_origin_workdir=True
    )
    assert isinstance(out, Path)
