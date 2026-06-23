"""End-to-end validation of the typed contracts against a real Stata runtime.

These exercise the estimation contract, error taxonomy, provenance, and handoff
verifier through the live pystata backend — the path agents actually use. They
are marked ``stata_required`` and skip automatically when Stata is unavailable
(CI), so they harden the new features without breaking the no-Stata gate.

Assertions favor structure and tolerances over brittle exact numbers; where a
number is checked it is cross-checked against Stata's own displayed output.
"""

from __future__ import annotations

import json

import pytest

from stata_code import (
    build_provenance,
    build_reproducible_do,
    build_submission_package,
    is_available,
    run,
    verify_dataset,
)
from stata_code.core.schema import ErrorKind

pytestmark = [
    pytest.mark.stata_required,
    pytest.mark.skipif(not is_available(), reason="pystata / Stata 17+ not available"),
]


def _run(code: str, sid: str):
    return run(code, session_id=sid, include_full_log=False)


def _skip_if_missing(result, pkg: str) -> None:
    if not result.ok and result.error and result.error.kind == ErrorKind.COMMAND_NOT_FOUND:
        pytest.skip(f"community package {pkg!r} not installed")


# ─────────────────────────────────────────────────────────────────────────────
# Estimation contract
# ─────────────────────────────────────────────────────────────────────────────


class TestEstimationContractReal:
    def test_regress_is_referee_grade_r_table(self):
        r = _run("sysuse auto, clear\nregress price mpg weight", "rs_reg")
        assert r.ok, r.error
        est = r.results.estimation
        assert est is not None
        assert est.source == "r_table"  # copied from Stata's own r(table)
        assert est.command == "regress"
        assert est.command_family == "ols"
        assert est.statistic_kind == "t"
        assert est.n_obs == 74
        terms = {c.term for c in est.coefficients}
        assert {"mpg", "weight", "_cons"} <= terms
        weight = next(c for c in est.coefficients if c.term == "weight")
        # Stata reports weight coef ~1.746, t ~2.72, p ~0.008.
        assert weight.b == pytest.approx(1.7466, abs=1e-2)
        assert weight.se is not None and weight.p_value is not None
        assert weight.ci_low is not None and weight.ci_high is not None
        assert est.model_stats.get("r2") == pytest.approx(0.2934, abs=1e-3)

    def test_logit_family_binary(self):
        r = _run("sysuse auto, clear\nlogit foreign mpg weight", "rs_logit")
        assert r.ok, r.error
        est = r.results.estimation
        assert est is not None
        assert est.command_family == "binary"
        assert est.source == "r_table"
        assert est.statistic_kind == "z"

    def test_xtreg_panel_diagnostics(self):
        # Synthetic panel — no network dependency.
        code = (
            "clear\n"
            "set obs 200\n"
            "set seed 1\n"
            "gen id = ceil(_n/10)\n"
            "bysort id: gen t = _n\n"
            "gen x = t + runiform()\n"
            "gen y = 2*x + id + runiform()\n"
            "xtset id t\n"
            "xtreg y x, fe"
        )
        r = _run(code, "rs_xtreg")
        assert r.ok, r.error
        est = r.results.estimation
        assert est is not None
        assert est.command_family == "panel"
        # rho is the high-signal panel diagnostic; xtreg always stores it.
        assert "rho" in est.diagnostics
        assert est.diagnostics["rho"] is not None

    def test_ivreg2_weak_id_and_overid(self):
        r = _run(
            "sysuse auto, clear\nivreg2 price (mpg = weight length) turn", "rs_iv"
        )
        _skip_if_missing(r, "ivreg2")
        assert r.ok, r.error
        est = r.results.estimation
        assert est is not None
        assert est.command_family == "iv"
        # ivreg2 stores the Kleibergen-Paap weak-ID F as e(widstat).
        assert "weak_id_F" in est.diagnostics
        assert est.diagnostics["weak_id_F"] is not None

    def test_reghdfe_within_r2(self):
        r = _run("sysuse auto, clear\nreghdfe price mpg, absorb(rep78)", "rs_hdfe")
        _skip_if_missing(r, "reghdfe")
        assert r.ok, r.error
        est = r.results.estimation
        assert est is not None
        assert est.command_family == "ols"
        assert "r2_within" in est.diagnostics

    def test_non_estimation_run_has_no_estimation(self):
        r = _run("sysuse auto, clear\nsummarize mpg", "rs_sum")
        assert r.ok, r.error
        assert r.results.estimation is None


# ─────────────────────────────────────────────────────────────────────────────
# Error taxonomy — real rc codes, labels, recovery, suggestions
# ─────────────────────────────────────────────────────────────────────────────


class TestErrorTaxonomyReal:
    def test_varname_not_found_with_did_you_mean(self):
        r = _run("sysuse auto, clear\nsummarize mpgg", "rs_e111")
        assert r.ok is False
        assert r.rc == 111
        assert r.error.kind == ErrorKind.VARNAME_NOT_FOUND
        assert r.error.rc_label == "variable not found"
        assert r.error.recovery is not None
        assert r.error.recovery.category == "user_code"
        assert r.error.recovery.needs_code_change is True
        assert any(
            "Did you mean" in s.action and "mpg" in s.action for s in r.error.suggestions
        )

    def test_command_not_found_with_did_you_mean(self):
        r = _run("regresss price mpg", "rs_e199")
        assert r.ok is False
        assert r.rc == 199
        assert r.error.kind == ErrorKind.COMMAND_NOT_FOUND
        assert r.error.rc_label == "unrecognized command"
        assert any(
            "Did you mean" in s.action and "regress" in s.action
            for s in r.error.suggestions
        ), [s.action for s in r.error.suggestions]

    def test_no_observations(self):
        r = _run("sysuse auto, clear\nregress price mpg if mpg>9000", "rs_e2000")
        assert r.ok is False
        assert r.rc == 2000
        assert r.error.kind == ErrorKind.NO_OBSERVATIONS
        assert r.error.rc_label == "no observations"
        assert r.error.recovery.category == "data"

    def test_explicit_syntax_error(self):
        r = _run("error 198", "rs_e198")
        assert r.ok is False
        assert r.rc == 198
        assert r.error.kind == ErrorKind.SYNTAX
        assert r.error.rc_label == "invalid syntax"


# ─────────────────────────────────────────────────────────────────────────────
# Provenance / reproducibility from a real run
# ─────────────────────────────────────────────────────────────────────────────


class TestProvenanceReal:
    def test_provenance_and_reproducible_do(self):
        code = "sysuse auto, clear\nregress price mpg weight"
        r = _run(code, "rs_prov")
        assert r.ok, r.error
        prov = build_provenance(r, seed=12345, code=code)
        assert prov.stata_version is not None
        assert prov.command == "regress"
        assert prov.stata_code_version

        do = build_reproducible_do(r, code, seed=12345)
        assert "set seed 12345" in do
        assert "regress price mpg weight" in do
        # version pin reflects the live runtime's major version.
        assert prov.stata_version.split(".")[0] in do

    def test_submission_package_with_real_packages(self):
        code = "ssc install reghdfe, replace\nsysuse auto, clear\nregress price mpg"
        r = _run("sysuse auto, clear\nregress price mpg", "rs_sub")
        assert r.ok, r.error
        pkg = build_submission_package(r, code, seed=7, title="Real run")
        assert set(pkg) == {"analysis.do", "PROVENANCE.json", "README.md"}
        prov_data = json.loads(pkg["PROVENANCE.json"])
        assert [p["name"] for p in prov_data["packages"]] == ["reghdfe"]
        assert "reghdfe" in pkg["README.md"]


# ─────────────────────────────────────────────────────────────────────────────
# Data-MCP handoff verification on a real dataset
# ─────────────────────────────────────────────────────────────────────────────


class TestHandoffReal:
    def test_verify_real_dataset(self):
        r = _run("sysuse auto, clear", "rs_handoff")
        assert r.ok, r.error
        ok_check = verify_dataset(
            r.dataset, n_obs=74, required_vars=["mpg", "price", "weight"]
        )
        assert ok_check.ok, ok_check.issues

        bad = verify_dataset(r.dataset, n_obs=100, required_vars=["nope"])
        assert bad.ok is False
        assert len(bad.issues) == 2
