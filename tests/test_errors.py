"""Unit tests for `suggestions_for` — pure-Python, no Stata required.

Covers fuzzy "did you mean?" matching for both bad variables (rc 111) and
bad commands (rc 199), plus the canonical hint emitted for every other
suggestion-generating ErrorKind.
"""

from __future__ import annotations

from stata_code.core.errors import (
    COMMON_STATA_COMMANDS,
    suggestions_for,
)
from stata_code.core.schema import ErrorKind

# ─────────────────────────────────────────────────────────────────────────────
# varname_not_found — fuzzy match against the dataset's variable list
# ─────────────────────────────────────────────────────────────────────────────


class TestVarnameFuzzyMatch:
    def test_varname_fuzzy_match_basic(self):
        suggs = suggestions_for(
            ErrorKind.VARNAME_NOT_FOUND,
            varname="mpgg",
            available_varnames=["mpg", "weight", "price"],
        )
        # At least one suggestion mentions `mpg`
        assert any("`mpg`" in s.action for s in suggs)
        # `Did you mean` framing
        assert any("Did you mean" in s.action for s in suggs)

    def test_varname_no_match_returns_describe_hint(self):
        suggs = suggestions_for(
            ErrorKind.VARNAME_NOT_FOUND,
            varname="zzzzzz",
            available_varnames=["mpg"],
        )
        assert len(suggs) == 1
        assert "Did you mean" not in suggs[0].action
        # Falls back to a describe hint
        assert suggs[0].command == "describe"

    def test_varname_without_candidate_list(self):
        # When the runner can't supply variables (older Stata, dataset missing),
        # we still emit a describe hint instead of nothing.
        suggs = suggestions_for(
            ErrorKind.VARNAME_NOT_FOUND, varname="anything"
        )
        assert len(suggs) == 1
        assert suggs[0].command == "describe"

    def test_varname_emits_up_to_three_matches(self):
        # Several plausible neighbors of "prce" in the candidate list.
        suggs = suggestions_for(
            ErrorKind.VARNAME_NOT_FOUND,
            varname="prce",
            available_varnames=["price", "pric", "pre", "weight", "mpg"],
        )
        # At minimum the closest neighbor must appear; cap at 3.
        assert 1 <= len(suggs) <= 3
        actions = " ".join(s.action for s in suggs)
        assert "price" in actions or "pric" in actions

    def test_varname_no_varname_no_candidates(self):
        # No info at all → still emit one generic describe hint, never crash.
        suggs = suggestions_for(ErrorKind.VARNAME_NOT_FOUND)
        assert len(suggs) == 1
        assert suggs[0].command == "describe"


# ─────────────────────────────────────────────────────────────────────────────
# command_not_found — fuzzy match against the curated catalog
# ─────────────────────────────────────────────────────────────────────────────


class TestCommandFuzzyMatch:
    def test_command_fuzzy_match(self):
        suggs = suggestions_for(
            ErrorKind.COMMAND_NOT_FOUND, command="regres"
        )
        assert any("regress" in s.action for s in suggs)

    def test_command_fuzzy_match_summarize(self):
        suggs = suggestions_for(
            ErrorKind.COMMAND_NOT_FOUND, command="sumarize"
        )
        assert any("summarize" in s.action for s in suggs)

    def test_command_no_match_still_returns_ssc_hint(self):
        # Total garbage shouldn't fuzzy-match to anything; ssc hint persists.
        suggs = suggestions_for(
            ErrorKind.COMMAND_NOT_FOUND, command="zzzzzz"
        )
        assert all("Did you mean" not in s.action for s in suggs)
        assert any("ssc install" in s.action for s in suggs)

    def test_command_without_name_returns_ssc_hint(self):
        # Runner couldn't parse the bad command name from the message.
        suggs = suggestions_for(ErrorKind.COMMAND_NOT_FOUND)
        assert any("ssc install" in s.action for s in suggs)

    def test_catalog_is_non_trivial(self):
        # Sanity: the curated catalog actually contains the staples.
        for cmd in ("regress", "summarize", "generate", "merge", "use"):
            assert cmd in COMMON_STATA_COMMANDS


# ─────────────────────────────────────────────────────────────────────────────
# file_not_found
# ─────────────────────────────────────────────────────────────────────────────


class TestFileNotFound:
    def test_file_not_found_with_extension_only_pwd_hint(self):
        # Path already has an extension → no extension hint added.
        suggs = suggestions_for(ErrorKind.FILE_NOT_FOUND, path="auto.dta")
        assert len(suggs) == 1
        assert suggs[0].command == "pwd"
        assert "auto.dta" in suggs[0].action

    def test_file_not_found_extension_hint(self):
        # Bare name → extra hint about adding `.dta` / `.do`.
        suggs = suggestions_for(ErrorKind.FILE_NOT_FOUND, path="auto")
        assert len(suggs) == 2
        assert any("auto.dta" in s.action for s in suggs)
        assert any("auto.do" in s.action for s in suggs)

    def test_file_not_found_without_path(self):
        suggs = suggestions_for(ErrorKind.FILE_NOT_FOUND)
        assert len(suggs) == 1
        assert suggs[0].command == "pwd"


# ─────────────────────────────────────────────────────────────────────────────
# Other suggestion-generating kinds
# ─────────────────────────────────────────────────────────────────────────────


class TestOtherKinds:
    def test_name_conflict_drop_then_gen(self):
        suggs = suggestions_for(ErrorKind.NAME_CONFLICT, name="foo")
        # Drop is offered as a concrete alternative.
        assert any("drop foo" in s.action for s in suggs)
        # And replace is offered as the in-place edit.
        assert any("replace foo" in s.action for s in suggs)

    def test_name_conflict_without_name(self):
        suggs = suggestions_for(ErrorKind.NAME_CONFLICT)
        assert any("replace" in s.action for s in suggs)

    def test_no_observations_suggests_count(self):
        suggs = suggestions_for(ErrorKind.NO_OBSERVATIONS)
        assert any(s.command == "count" for s in suggs)
        assert any("if" in s.action for s in suggs)

    def test_data_in_memory_suggests_clear(self):
        suggs = suggestions_for(ErrorKind.DATA_IN_MEMORY)
        assert any(s.command == "clear" for s in suggs)

    def test_out_of_memory_suggests_compress(self):
        suggs = suggestions_for(ErrorKind.OUT_OF_MEMORY)
        assert any("compress" in s.action.lower() for s in suggs)

    def test_convergence_suggests_iterate_or_technique(self):
        suggs = suggestions_for(ErrorKind.CONVERGENCE)
        text = " ".join(s.action for s in suggs)
        assert "iterate" in text or "technique" in text

    def test_matrix_singular_suggests_collinearity_check(self):
        suggs = suggestions_for(ErrorKind.MATRIX_SINGULAR)
        text = " ".join(s.action for s in suggs).lower()
        assert "collinear" in text or "vif" in text or "corr" in text

    def test_matrix_conformability_mentions_shapes(self):
        suggs = suggestions_for(ErrorKind.MATRIX_CONFORMABILITY)
        text = " ".join(s.action for s in suggs)
        assert "rowsof" in text or "colsof" in text or "conformable" in text

    def test_estimation_sample_empty_suggests_count(self):
        suggs = suggestions_for(ErrorKind.ESTIMATION_SAMPLE_EMPTY)
        assert any(s.command == "count" for s in suggs)

    def test_not_sorted_suggests_sort(self):
        suggs = suggestions_for(ErrorKind.NOT_SORTED)
        assert any(s.command == "sort" for s in suggs)
        assert any("sort" in s.action.lower() for s in suggs)

    def test_no_estimation_results_mentions_regress(self):
        suggs = suggestions_for(ErrorKind.NO_ESTIMATION_RESULTS)
        assert any("regress" in s.action for s in suggs)

    def test_file_exists_mentions_replace_option(self):
        suggs = suggestions_for(ErrorKind.FILE_EXISTS, path="out.dta")
        assert any("replace" in s.action for s in suggs)

    def test_stata_limit_mentions_set_or_upgrade(self):
        suggs = suggestions_for(ErrorKind.STATA_LIMIT)
        text = " ".join(s.action for s in suggs).lower()
        assert "set" in text or "upgrade" in text


# ─────────────────────────────────────────────────────────────────────────────
# Quiet kinds — must return [] without raising
# ─────────────────────────────────────────────────────────────────────────────


class TestQuietKinds:
    def test_unknown_returns_empty(self):
        assert suggestions_for(ErrorKind.UNKNOWN) == []

    def test_syntax_returns_empty(self):
        # SYNTAX is too generic for a canonical hint — agents should look at
        # the error message and `help <command>` themselves.
        assert suggestions_for(ErrorKind.SYNTAX) == []

    def test_adapter_crash_returns_empty(self):
        # Producer-side failure; nothing actionable for the Stata user.
        assert suggestions_for(ErrorKind.ADAPTER_CRASH) == []

    def test_timeout_returns_empty(self):
        assert suggestions_for(ErrorKind.TIMEOUT) == []

    def test_cancelled_returns_empty(self):
        assert suggestions_for(ErrorKind.CANCELLED) == []

    def test_invalid_name_returns_empty(self):
        # We don't currently emit a hint for invalid_name (no obvious fix
        # without parsing the message further). Lock in [] so any future
        # change is intentional.
        assert suggestions_for(ErrorKind.INVALID_NAME) == []
