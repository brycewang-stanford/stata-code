"""Unit tests for the typed estimation contract — pure Python, no Stata.

Covers both extraction paths:
* ``r(table)`` (referee-grade — values copied exactly as Stata displayed them)
* ``e(b)`` / ``e(V)`` fallback (se/z/p/CI computed under a normal approximation)
"""

from __future__ import annotations

import math

from stata_code.core.estimation import (
    build_estimation_from_returns,
    build_estimation_result,
)
from stata_code.core.schema import Matrix, ResultsInfo, StataReturns


def _b(values: list[float], cols: list[str]) -> Matrix:
    return Matrix(rows=["y1"], cols=cols, values=[values])


def _v(diag: list[float], cols: list[str]) -> Matrix:
    n = len(diag)
    vals = [[diag[i] if i == j else 0.0 for j in range(n)] for i in range(n)]
    return Matrix(rows=cols, cols=cols, values=vals)


def _r_table(cols: list[str], rows: dict[str, list[float]]) -> Matrix:
    return Matrix(rows=list(rows.keys()), cols=cols, values=list(rows.values()))


# ─────────────────────────────────────────────────────────────────────────────
# r(table) path — exact copy of Stata's displayed coefficients
# ─────────────────────────────────────────────────────────────────────────────


class TestRTablePath:
    def _make(self) -> StataReturns:
        cols = ["mpg", "_cons"]
        table = _r_table(
            cols,
            {
                "b": [-238.0, 11253.0],
                "se": [53.0, 1171.0],
                "t": [-4.5, 9.6],
                "pvalue": [0.0001, 0.0],
                "ll": [-342.0, 8910.0],
                "ul": [-134.0, 13596.0],
            },
        )
        e = StataReturns(
            scalars={"N": 74, "df_m": 1, "df_r": 72, "r2": 0.2196},
            macros={"cmd": "regress", "depvar": "price"},
            matrices={"b": _b([-238.0, 11253.0], cols), "V": _v([2809.0, 1371241.0], cols)},
        )
        r = StataReturns(matrices={"table": table})
        return build_estimation_from_returns(e, r)

    def test_source_and_metadata(self):
        est = self._make()
        assert est is not None
        assert est.source == "r_table"
        assert est.statistic_kind == "t"
        assert est.command == "regress"
        assert est.depvar == "price"
        assert est.n_obs == 74
        assert est.df_resid == 72

    def test_coefficients_copied_verbatim(self):
        est = self._make()
        mpg = est.coefficients[0]
        assert mpg.term == "mpg"
        assert mpg.b == -238.0
        assert mpg.se == 53.0
        assert mpg.statistic == -4.5  # exactly Stata's value, not recomputed
        assert mpg.ci_low == -342.0
        assert mpg.ci_high == -134.0

    def test_model_stats_subset(self):
        est = self._make()
        assert est.model_stats.get("r2") == 0.2196
        assert est.model_stats.get("N") == 74

    def test_z_command_sets_statistic_kind_z(self):
        cols = ["x"]
        table = _r_table(
            cols,
            {"b": [1.0], "se": [0.5], "z": [2.0], "pvalue": [0.0455]},
        )
        e = StataReturns(
            macros={"cmd": "logit"},
            matrices={"b": _b([1.0], cols), "V": _v([0.25], cols)},
        )
        est = build_estimation_from_returns(e, StataReturns(matrices={"table": table}))
        assert est.statistic_kind == "z"
        assert est.coefficients[0].statistic == 2.0


# ─────────────────────────────────────────────────────────────────────────────
# e(b)/e(V) fallback — computed se/z/p/CI
# ─────────────────────────────────────────────────────────────────────────────


class TestBVFallback:
    def test_computes_se_and_z(self):
        cols = ["mpg", "_cons"]
        e = StataReturns(
            scalars={"N": 74},
            macros={"cmd": "regress"},
            matrices={"b": _b([-238.0, 11253.0], cols), "V": _v([2809.0, 1371241.0], cols)},
        )
        est = build_estimation_from_returns(e, StataReturns())
        assert est.source == "e_b_v"
        assert est.statistic_kind == "z"
        mpg = est.coefficients[0]
        assert math.isclose(mpg.se, 53.0, rel_tol=1e-12)
        assert math.isclose(mpg.statistic, -238.0 / 53.0, rel_tol=1e-12)
        # two-sided normal p for |z|≈4.49 is tiny but positive
        assert 0.0 < mpg.p_value < 1e-4

    def test_ci_uses_95pct_normal_critical(self):
        cols = ["x"]
        e = StataReturns(matrices={"b": _b([10.0], cols), "V": _v([4.0], cols)})
        est = build_estimation_from_returns(e, StataReturns())
        c = est.coefficients[0]
        # se = 2.0; CI = 10 ± 1.95996*2
        assert math.isclose(c.ci_low, 10.0 - 1.959963984540054 * 2.0, rel_tol=1e-12)
        assert math.isclose(c.ci_high, 10.0 + 1.959963984540054 * 2.0, rel_tol=1e-12)

    def test_no_vcov_yields_point_estimates_only(self):
        cols = ["x"]
        e = StataReturns(matrices={"b": _b([3.0], cols)})  # no V
        est = build_estimation_from_returns(e, StataReturns())
        c = est.coefficients[0]
        assert c.b == 3.0
        assert c.se is None
        assert c.statistic is None
        assert c.p_value is None

    def test_negative_variance_yields_no_se(self):
        # Defensive: a malformed/negative diagonal must not raise.
        cols = ["x"]
        e = StataReturns(matrices={"b": _b([1.0], cols), "V": _v([-1.0], cols)})
        est = build_estimation_from_returns(e, StataReturns())
        assert est.coefficients[0].se is None


# ─────────────────────────────────────────────────────────────────────────────
# Guards / edge cases
# ─────────────────────────────────────────────────────────────────────────────


class TestGuards:
    def test_no_estimation_returns_none(self):
        assert build_estimation_from_returns(StataReturns(), StataReturns()) is None

    def test_r_table_with_mismatched_columns_falls_back_to_bv(self):
        # A stale r(table) from a different command must not be trusted.
        bcols = ["mpg", "_cons"]
        table = _r_table(["other"], {"b": [1.0], "se": [0.5], "t": [2.0]})
        e = StataReturns(
            matrices={"b": _b([-238.0, 11253.0], bcols), "V": _v([2809.0, 1371241.0], bcols)},
        )
        est = build_estimation_from_returns(e, StataReturns(matrices={"table": table}))
        assert est.source == "e_b_v"
        assert [c.term for c in est.coefficients] == bcols

    def test_r_table_with_same_columns_but_stale_values_falls_back_to_bv(self):
        # Column names alone are not enough: a later r-class command can leave
        # an r(table) shape that looks plausible but does not describe e(b).
        cols = ["mpg", "_cons"]
        table = _r_table(
            cols,
            {
                "b": [999.0, -999.0],
                "se": [1.0, 1.0],
                "t": [999.0, -999.0],
            },
        )
        e = StataReturns(
            matrices={
                "b": _b([-238.0, 11253.0], cols),
                "V": _v([2809.0, 1371241.0], cols),
            },
        )
        est = build_estimation_from_returns(e, StataReturns(matrices={"table": table}))
        assert est.source == "e_b_v"
        assert est.coefficients[0].b == -238.0

    def test_by_reference_eb_declines(self):
        # e(b) too large to inline (values=None) → we decline rather than guess.
        b = Matrix(rows=["y1"], cols=["x"], values=None, ref="matrix://r/e/b")
        e = StataReturns(matrices={"b": b})
        assert build_estimation_from_returns(e, StataReturns()) is None

    def test_results_info_wrapper(self):
        cols = ["x"]
        results = ResultsInfo(
            e=StataReturns(matrices={"b": _b([5.0], cols), "V": _v([1.0], cols)}),
        )
        est = build_estimation_result(results)
        assert est is not None
        assert est.coefficients[0].b == 5.0

    def test_last_estimation_cmd_used_when_no_cmd_macro(self):
        cols = ["x"]
        e = StataReturns(matrices={"b": _b([5.0], cols), "V": _v([1.0], cols)})
        est = build_estimation_from_returns(e, StataReturns(), last_estimation_cmd="reghdfe")
        assert est.command == "reghdfe"

    def test_public_exports_include_estimation_contract(self):
        import stata_code

        assert stata_code.EstimationResult is not None
        assert stata_code.Coefficient is not None
        assert stata_code.build_estimation_result is build_estimation_result
        assert stata_code.build_estimation_from_returns is build_estimation_from_returns
