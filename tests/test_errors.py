"""Unit tests for `suggestions_for` — pure-Python, no Stata required.

Covers fuzzy "did you mean?" matching for both bad variables (rc 111) and
bad commands (rc 199), plus the canonical hint emitted for every other
suggestion-generating ErrorKind.
"""

from __future__ import annotations

from stata_code.core.errors import (
    COMMON_STATA_COMMANDS,
    RC_LABEL,
    RC_TO_KIND,
    classify_rc,
    label_for_rc,
    recovery_for,
    suggestions_for,
)
from stata_code.core.schema import ErrorKind, Recovery

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
        suggs = suggestions_for(ErrorKind.VARNAME_NOT_FOUND, varname="anything")
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
        suggs = suggestions_for(ErrorKind.COMMAND_NOT_FOUND, command="regres")
        assert any("regress" in s.action for s in suggs)

    def test_command_fuzzy_match_summarize(self):
        suggs = suggestions_for(ErrorKind.COMMAND_NOT_FOUND, command="sumarize")
        assert any("summarize" in s.action for s in suggs)

    def test_command_no_match_still_returns_ssc_hint(self):
        # Total garbage shouldn't fuzzy-match to anything; ssc hint persists.
        suggs = suggestions_for(ErrorKind.COMMAND_NOT_FOUND, command="zzzzzz")
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


# ─────────────────────────────────────────────────────────────────────────────
# rc → kind classification, verified against StataCorp [P] error (Stata 19).
#
# Each assertion below cites the manual's canonical message for the rc so the
# mapping is auditable. The previous table mis-classified several codes; these
# tests lock in the corrected behavior and guard against regressions.
# ─────────────────────────────────────────────────────────────────────────────


class TestClassifyRcAgainstManual:
    def test_not_sorted_is_rc_5_not_119_or_459(self):
        # r(5) "not sorted" is the canonical code. r(119) is "statement out of
        # context" and r(459) is "something that should be true of your data is
        # not" — neither is a sort error, so they must NOT classify as such.
        assert classify_rc(5) == ErrorKind.NOT_SORTED
        assert classify_rc(119) == ErrorKind.UNKNOWN
        assert classify_rc(459) == ErrorKind.UNKNOWN

    def test_numlist_errors_are_syntax_not_invalid_name(self):
        # r(122)/r(123) are "invalid numlist has too few/many elements" —
        # numlist parse failures, not name errors.
        for rc in (121, 122, 123, 124, 125, 126, 127):
            assert classify_rc(rc) == ErrorKind.SYNTAX, rc

    def test_rc_322_is_estimation_failure_not_file_not_found(self):
        # r(322) "something that should be true of your estimation results is
        # not" — a postestimation/prefix consistency failure, not a file error.
        assert classify_rc(322) == ErrorKind.ESTIMATION_FAILURE

    def test_rc_480_is_infeasible_not_out_of_memory(self):
        # r(480) "starting values invalid or some RHS variables have missing
        # values" (nl) — an infeasibility, not memory exhaustion.
        assert classify_rc(480) == ErrorKind.INFEASIBLE
        assert classify_rc(491) == ErrorKind.INFEASIBLE

    def test_rc_1400_is_estimation_failure(self):
        # r(1400) "numerical overflow" — not an empty-sample error.
        assert classify_rc(1400) == ErrorKind.ESTIMATION_FAILURE

    def test_local_io_codes_are_file_io_not_network(self):
        # r(691)/r(692)/r(693) are local filesystem I/O errors, not network.
        for rc in (691, 692, 693):
            assert classify_rc(rc) == ErrorKind.FILE_IO, rc

    def test_real_network_codes_classify_as_network(self):
        # r(2) connection timed out, r(631) host not found, r(672) server
        # refused to send file, r(677) remote connection failed.
        for rc in (2, 631, 672, 677):
            assert classify_rc(rc) == ErrorKind.NETWORK, rc

    def test_corrupt_and_memory_additions(self):
        assert classify_rc(688) == ErrorKind.FILE_CORRUPT  # file is corrupt
        assert classify_rc(610) == ErrorKind.FILE_CORRUPT  # not Stata format
        assert classify_rc(907) == ErrorKind.STATA_LIMIT  # maxvar too small
        assert classify_rc(950) == ErrorKind.OUT_OF_MEMORY  # insufficient memory

    def test_removed_mismappings_fall_through_to_unknown(self):
        # Previously these were (incorrectly) mapped; they have no good kind.
        assert classify_rc(9) == ErrorKind.UNKNOWN  # assertion is false
        assert classify_rc(604) == ErrorKind.UNKNOWN  # log file already open
        assert classify_rc(615) == ErrorKind.UNKNOWN  # (not in manual table)
        assert classify_rc(616) == ErrorKind.UNKNOWN  # checksum file error

    def test_stable_known_mappings_unchanged(self):
        assert classify_rc(111) == ErrorKind.VARNAME_NOT_FOUND
        assert classify_rc(199) == ErrorKind.COMMAND_NOT_FOUND
        assert classify_rc(198) == ErrorKind.SYNTAX
        assert classify_rc(4) == ErrorKind.DATA_IN_MEMORY
        assert classify_rc(909) == ErrorKind.OUT_OF_MEMORY

    def test_every_classified_code_is_a_known_error_kind(self):
        # Guard: the table never maps to a value outside the enum.
        for rc, kind in RC_TO_KIND.items():
            assert isinstance(kind, ErrorKind), rc


# ─────────────────────────────────────────────────────────────────────────────
# rc_label — canonical Stata short messages (label_for_rc / RC_LABEL)
# ─────────────────────────────────────────────────────────────────────────────


class TestRcLabel:
    def test_known_labels_match_manual(self):
        assert label_for_rc(111) == "variable not found"
        assert label_for_rc(199) == "unrecognized command"
        assert label_for_rc(5) == "not sorted"
        assert label_for_rc(430) == "convergence not achieved"
        assert label_for_rc(2000) == "no observations"

    def test_synthetic_labels(self):
        assert label_for_rc(-1) == "adapter_crash"
        assert label_for_rc(-2) == "timeout"
        assert label_for_rc(-3) == "cancelled"

    def test_unknown_rc_returns_empty_string(self):
        # Never guess: an unverified code yields "" so consumers can tell.
        assert label_for_rc(99999) == ""
        assert label_for_rc(408) == ""  # not in the public manual table

    def test_every_mapped_rc_has_a_label(self):
        # Coverage guard: each classified Stata rc should carry a label so
        # error.rc_label is never silently empty for a code we do classify.
        # (408/1401/1402 are kept from prior art but absent from the manual,
        # so they are deliberately label-less.)
        no_manual_label = {408, 1401, 1402}
        for rc in RC_TO_KIND:
            if rc in no_manual_label:
                continue
            assert RC_LABEL.get(rc), f"rc {rc} classified but has no label"


# ─────────────────────────────────────────────────────────────────────────────
# Agent recovery contract (recovery_for)
# ─────────────────────────────────────────────────────────────────────────────


class TestRecoveryContract:
    def test_user_code_error_requires_code_change(self):
        recovery = recovery_for(ErrorKind.VARNAME_NOT_FOUND)
        assert recovery.category == "user_code"
        assert recovery.retriable is False
        assert recovery.needs_code_change is True
        assert recovery.needs_user_input is False

    def test_network_error_is_retriable_environment_failure(self):
        recovery = recovery_for(ErrorKind.NETWORK)
        assert recovery.category == "environment"
        assert recovery.retriable is True
        assert recovery.needs_code_change is False

    def test_timeout_is_retriable_internal_failure(self):
        recovery = recovery_for(ErrorKind.TIMEOUT)
        assert recovery.category == "internal"
        assert recovery.retriable is True
        assert recovery.needs_code_change is False

    def test_all_error_kinds_have_recovery_contracts(self):
        for kind in ErrorKind:
            recovery = recovery_for(kind)
            assert isinstance(recovery, Recovery), kind
            assert recovery.category != "unknown" or kind is ErrorKind.UNKNOWN

    def test_public_exports_include_recovery_contract(self):
        import stata_code

        assert stata_code.Recovery is Recovery
        assert stata_code.recovery_for is recovery_for


# ─────────────────────────────────────────────────────────────────────────────
# Newly-covered remediation suggestions (previously emitted nothing)
# ─────────────────────────────────────────────────────────────────────────────


class TestExpandedSuggestions:
    def test_network_mentions_connectivity_or_retry(self):
        text = " ".join(s.action for s in suggestions_for(ErrorKind.NETWORK))
        assert "retry" in text.lower() or "connect" in text.lower()

    def test_infeasible_mentions_starting_values(self):
        text = " ".join(s.action for s in suggestions_for(ErrorKind.INFEASIBLE))
        assert "start" in text.lower() or "from(" in text.lower()

    def test_estimation_failure_suggests_ereturn(self):
        suggs = suggestions_for(ErrorKind.ESTIMATION_FAILURE)
        assert any(s.command == "ereturn list" for s in suggs)

    def test_type_mismatch_suggests_destring(self):
        text = " ".join(s.action for s in suggestions_for(ErrorKind.TYPE_MISMATCH))
        assert "destring" in text or "tostring" in text

    def test_matrix_missing_suggests_matrix_list(self):
        suggs = suggestions_for(ErrorKind.MATRIX_MISSING)
        assert any(s.command == "matrix list" for s in suggs)

    def test_file_io_mentions_writable_or_disk(self):
        suggs = suggestions_for(ErrorKind.FILE_IO, path="out.dta")
        text = " ".join(s.action for s in suggs).lower()
        assert "writable" in text or "disk" in text
        assert any("out.dta" in s.action for s in suggs)

    def test_file_corrupt_mentions_stata_file(self):
        text = " ".join(
            s.action for s in suggestions_for(ErrorKind.FILE_CORRUPT, path="x.dta")
        ).lower()
        assert "stata" in text or "corrupt" in text

    def test_permission_mentions_read_only_or_writable(self):
        text = " ".join(
            s.action for s in suggestions_for(ErrorKind.PERMISSION, path="x.dta")
        ).lower()
        assert "read-only" in text or "writable" in text
