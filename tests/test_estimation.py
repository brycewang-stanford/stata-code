"""Unit tests for the typed estimation contract — pure Python, no Stata.

Covers both extraction paths:
* ``r(table)`` (referee-grade — values copied exactly as Stata displayed them)
* ``e(b)`` / ``e(V)`` fallback (se / statistic / p / CI computed here, on the
  same distribution Stata used: t when ``e(df_r)`` is set, z otherwise)
"""

from __future__ import annotations

import math

import pytest

from stata_code.core.estimation import (
    _t_crit,
    _two_sided_t_p,
    _z_crit,
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
# Distribution helpers — checked against published t-table values
# ─────────────────────────────────────────────────────────────────────────────


class TestDistributions:
    @pytest.mark.parametrize(
        ("df", "expected"),
        [
            (1, 12.706204736),
            (5, 2.570581836),
            (10, 2.228138852),
            (30, 2.042272456),
            (70, 1.994437112),
            (120, 1.979930405),
            (100000, 1.959987707),
        ],
    )
    def test_t_crit_matches_published_two_sided_95pct_values(self, df, expected):
        assert math.isclose(_t_crit(float(df), 95.0), expected, rel_tol=1e-8)

    def test_t_crit_converges_to_the_normal_as_df_grows(self):
        assert math.isclose(_t_crit(1e9, 95.0), _z_crit(95.0), rel_tol=1e-6)

    @pytest.mark.parametrize(
        ("t", "df", "expected"),
        [
            (2.042272456, 30, 0.05),
            (1.0, 10, 0.340893),
            (0.29443916691636146, 70, 0.769293740812962),
            (5.493002930280353, 70, 5.991178e-7),
        ],
    )
    def test_two_sided_t_p_matches_reference_values(self, t, df, expected):
        assert math.isclose(_two_sided_t_p(t, float(df)), expected, rel_tol=1e-5)

    def test_t_p_is_symmetric_and_bounded(self):
        for t in (0.0, 0.5, 3.0, 40.0):
            p = _two_sided_t_p(t, 12.0)
            assert 0.0 <= p <= 1.0
            assert math.isclose(p, _two_sided_t_p(-t, 12.0), rel_tol=1e-15)
        assert math.isclose(_two_sided_t_p(0.0, 12.0), 1.0, rel_tol=1e-12)

    def test_z_crit_matches_known_levels(self):
        assert math.isclose(_z_crit(95.0), 1.959963984540054, rel_tol=1e-12)
        assert math.isclose(_z_crit(99.0), 2.5758293035489004, rel_tol=1e-10)
        assert math.isclose(_z_crit(90.0), 1.6448536269514722, rel_tol=1e-10)


# ─────────────────────────────────────────────────────────────────────────────
# e(b)/e(V) fallback — computed se / statistic / p / CI
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

    def test_df_r_switches_the_fallback_to_a_t_table(self):
        # Stata prints a t table whenever e(df_r) is set, so the fallback must
        # too — otherwise the rebuilt CI silently disagrees with the log.
        cols = ["x"]
        e = StataReturns(
            scalars={"df_r": 70},
            matrices={"b": _b([10.0], cols), "V": _v([4.0], cols)},
        )
        est = build_estimation_from_returns(e, StataReturns())
        assert est.source == "e_b_v"
        assert est.statistic_kind == "t"
        # t(.975, 70) = 1.9944371... — wider than the 1.95996 normal interval.
        c = est.coefficients[0]
        assert math.isclose(c.ci_low, 10.0 - 1.9944371112999 * 2.0, rel_tol=1e-9)
        assert math.isclose(c.ci_high, 10.0 + 1.9944371112999 * 2.0, rel_tol=1e-9)

    def test_fallback_reproduces_statas_printed_regress_table(self):
        # Values taken from a live `regress price_k mpg weight foreign` on
        # sysuse auto (N=74, df_r=70). Before the t fix this path returned
        # [-0.12362, 0.16732] against a log that printed [-.1261758, .169883].
        cols = ["mpg"]
        b, se = 0.02185360997213235, 0.07422113776846848
        e = StataReturns(
            scalars={"N": 74, "df_m": 3, "df_r": 70},
            macros={"cmd": "regress", "depvar": "price_k"},
            matrices={"b": _b([b], cols), "V": _v([se * se], cols)},
        )
        est = build_estimation_from_returns(e, StataReturns())
        c = est.coefficients[0]
        assert math.isclose(c.ci_low, -0.1261757816711833, rel_tol=1e-9)
        assert math.isclose(c.ci_high, 0.16988300161544798, rel_tol=1e-9)
        assert math.isclose(c.p_value, 0.769293740812962, rel_tol=1e-9)

    def test_no_df_r_stays_on_the_normal_approximation(self):
        # Commands that do not set e(df_r) (ml-family, bootstrap, ...) print a
        # z table; the fallback must not invent residual degrees of freedom.
        cols = ["x"]
        e = StataReturns(
            scalars={"N": 500},
            macros={"cmd": "logit"},
            matrices={"b": _b([10.0], cols), "V": _v([4.0], cols)},
        )
        est = build_estimation_from_returns(e, StataReturns())
        assert est.statistic_kind == "z"
        assert math.isclose(est.coefficients[0].ci_high, 10.0 + 1.959963984540054 * 2.0)

    def test_e_level_drives_both_ci_level_and_the_critical_value(self):
        cols = ["x"]
        e = StataReturns(
            scalars={"df_r": 70, "level": 90},
            matrices={"b": _b([10.0], cols), "V": _v([4.0], cols)},
        )
        est = build_estimation_from_returns(e, StataReturns())
        assert est.ci_level == 90.0
        # t(.95, 70) = 1.6669145... — narrower than the 95% interval.
        assert math.isclose(est.coefficients[0].ci_high, 10.0 + 1.6669145 * 2.0, rel_tol=1e-7)

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


# ─────────────────────────────────────────────────────────────────────────────
# Command-aware family classification + identification/spec diagnostics
# ─────────────────────────────────────────────────────────────────────────────


class TestCommandSpecialization:
    def _est(self, cmd: str, scalars: dict[str, float]):
        cols = ["x", "_cons"]
        e = StataReturns(
            macros={"cmd": cmd},
            scalars={"N": 500, **scalars},
            matrices={"b": _b([1.0, 0.5], cols), "V": _v([0.04, 0.01], cols)},
        )
        return build_estimation_from_returns(e, StataReturns())

    def test_iv_family_and_weak_id_and_overid(self):
        est = self._est("ivreghdfe", {"widstat": 23.5, "j": 1.8, "jdf": 2})
        assert est.command_family == "iv"
        assert est.diagnostics["weak_id_F"] == 23.5
        assert est.diagnostics["overid_j"] == 1.8
        assert est.diagnostics["overid_j_df"] == 2.0

    def test_reghdfe_family_and_within_r2(self):
        est = self._est("reghdfe", {"r2_within": 0.31, "N_hdfe": 2})
        assert est.command_family == "ols"
        assert est.diagnostics["r2_within"] == 0.31
        assert est.diagnostics["n_absorbed_fe_dims"] == 2.0

    def test_gmm_family_and_ar_hansen(self):
        est = self._est("xtabond2", {"ar1p": 0.001, "ar2p": 0.43, "hansenp": 0.27})
        assert est.command_family == "gmm"
        assert est.diagnostics["ar1_p"] == 0.001
        assert est.diagnostics["ar2_p"] == 0.43
        assert est.diagnostics["hansen_p"] == 0.27

    def test_panel_family_and_rho(self):
        est = self._est("xtreg", {"rho": 0.8, "sigma_u": 1.2, "sigma_e": 0.6})
        assert est.command_family == "panel"
        assert est.diagnostics["rho"] == 0.8

    def test_absent_scalars_are_not_fabricated(self):
        # ivreghdfe entry lists widstat/j/jdf/... but only widstat is present.
        est = self._est("ivreghdfe", {"widstat": 10.0})
        assert est.diagnostics == {"weak_id_F": 10.0}

    def test_unknown_command_has_no_family_or_diagnostics(self):
        est = self._est("mycustomcmd", {"widstat": 99.0})
        assert est.command_family is None
        assert est.diagnostics == {}

    def test_did_family(self):
        est = self._est("csdid", {})
        assert est.command_family == "did"
