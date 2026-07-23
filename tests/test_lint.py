"""Tests for the static do-file linter (``stata_code.core.lint``).

Pure and Stata-free. The emphasis is on *not* nagging about correct code: the
"ok" cases (SMCL directives, global-macro braces, mata one-liners, program with
`end`) must stay clean, while genuine structural mistakes are caught.
"""

from __future__ import annotations

from stata_code.core.lint import lint_code


def _rules(code: str) -> list[str]:
    return [f.rule for f in lint_code(code)]


class TestBraces:
    def test_balanced_block_is_clean(self):
        assert lint_code("foreach v of varlist a b {\n summarize `v'\n}") == []

    def test_unclosed_block(self):
        f = lint_code("foreach v of varlist a b {\n summarize x")
        assert [x.rule for x in f] == ["unbalanced-braces"]
        assert f[0].severity == "error"

    def test_stray_close_brace(self):
        assert _rules("regress y x }") == ["unbalanced-braces"]

    def test_smcl_directive_in_string_is_ignored(self):
        assert lint_code('di "{hline 20}"') == []

    def test_global_macro_braces_ignored(self):
        assert lint_code('di "${gdp} and ${cpi}"') == []


class TestBlocks:
    def test_program_without_end(self):
        assert _rules("program define foo\n regress y x") == ["missing-end"]

    def test_program_with_end_is_clean(self):
        assert lint_code("program define foo\n regress y x\nend") == []

    def test_program_drop_is_not_a_block(self):
        assert lint_code("program drop foo") == []

    def test_bare_mata_block_without_end(self):
        assert _rules("mata") == ["missing-end"]

    def test_mata_oneliner_is_clean(self):
        assert lint_code('mata: st_numscalar("r(x)", 3)') == []

    def test_python_block_without_end(self):
        assert _rules("python") == ["missing-end"]

    def test_stray_end(self):
        f = lint_code("regress y x\nend")
        assert f[0].rule == "unexpected-end"
        assert f[0].severity == "warning"

    def test_nested_program_and_loop(self):
        code = "program define foo\n  forvalues i=1/3 {\n    di `i'\n  }\nend"
        assert lint_code(code) == []


class TestContinuation:
    def test_dangling_continuation(self):
        assert _rules("regress y x ///") == ["dangling-continuation"]

    def test_continuation_with_following_line_is_clean(self):
        assert lint_code("regress y x /// comment\n  , robust") == []


class TestEmpty:
    def test_comment_only_is_flagged_empty(self):
        assert _rules("* just a comment\n// another") == ["empty-input"]

    def test_blank_is_flagged_empty(self):
        assert _rules("\n\n   \n") == ["empty-input"]


class TestOrderingAndDicts:
    def test_findings_sorted_by_line(self):
        code = "regress y x }\nprogram define f"
        f = lint_code(code)
        assert [x.line for x in f] == sorted(x.line for x in f)

    def test_to_dict_shape(self):
        d = lint_code("mata")[0].to_dict()
        assert set(d) == {"rule", "severity", "line", "message"}
