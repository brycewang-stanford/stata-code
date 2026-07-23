"""Tests for the console (batch) backend — ``stata_code.core.console``.

Everything here is offline: the wrapper generator and every log/return/matrix
parser is exercised against realistic Stata batch-log samples. The live
:func:`execute` path (which spawns a real Stata) is covered separately under the
``stata_required`` marker in ``test_real_stata.py``.
"""

from __future__ import annotations

import pytest

from stata_code.core import console
from stata_code.core.schema import Backend, ErrorKind, StataEdition

# ── A realistic batch log for: sysuse auto, clear ; regress mpg weight ──────────
REGRESS_LOG = r"""
  ___  ____  ____  ____  ____ (R)
 /__    /   ____/   /   ____/      18.0

. capture noisily {
.     sysuse auto, clear
(1978 automobile data)
.     regress mpg weight
------------------------------------------------------------------------------
         mpg | Coefficient  Std. err.      t    P>|t|     [95% conf. interval]
-------------+----------------------------------------------------------------
      weight |  -.0060087   .0005179   -11.60   0.000    -.0070411   -.0049763
       _cons |   39.44028   1.614003    24.44   0.000     36.22283    42.65774
------------------------------------------------------------------------------
. }

. local __sc_rc = _rc

. display "__STATACODE__|VERSION|" c(stata_version) "|" c(flavor)
__STATACODE__|VERSION|18.0|MP

. display "__STATACODE__|RC|`__sc_rc'"
__STATACODE__|RC|0

. display "__STATACODE__|BEGIN"
__STATACODE__|BEGIN

. display "__STATACODE__|SECTION|R"
__STATACODE__|SECTION|R

. return list

. display "__STATACODE__|SECTION|E"
__STATACODE__|SECTION|E

. ereturn list

scalars:
                  e(N) =  74
               e(df_m) =  1
               e(df_r) =  72
                 e(r2) =  .6515489989240539
                e(rmse) =  3.438935350439123

macros:
                  e(cmd) : "regress"
               e(depvar) : "mpg"

matrices:
                  e(b) :  1 x 2
                  e(V) :  2 x 2

. display "__STATACODE__|SECTION|MATRICES"
__STATACODE__|SECTION|MATRICES

. display "__STATACODE__|MATRIX|e(b)"
__STATACODE__|MATRIX|e(b)

. matrix list e(b), format(%20.15g)

e(b)[1,2]
                   weight                _cons
y1   -.0060086780487887     39.4402797603966

. display "__STATACODE__|MATRIX|e(V)"
__STATACODE__|MATRIX|e(V)

. matrix list e(V), format(%20.15g)

symmetric e(V)[2,2]
                   weight                _cons
weight  2.682755e-07
 _cons  -.00082324159      2.605005494

. display "__STATACODE__|SECTION|DATASET"
__STATACODE__|SECTION|DATASET

. display "__STATACODE__|DS|nobs|" c(N)
__STATACODE__|DS|nobs|74

. display "__STATACODE__|DS|nvars|" c(k)
__STATACODE__|DS|nvars|12

. display "__STATACODE__|DS|filename|" c(filename)
__STATACODE__|DS|filename|/Applications/Stata/ado/base/a/auto.dta

. display "__STATACODE__|VAR|make|..."
__STATACODE__|VAR|make|str18|Make and model

. display "__STATACODE__|VAR|mpg|..."
__STATACODE__|VAR|mpg|int|Mileage (mpg)

. display "__STATACODE__|END"
__STATACODE__|END
"""

ERROR_LOG = r"""
. capture noisily {
.     regress mpg wgt
variable wgt not found
r(111);
. }

. local __sc_rc = _rc

. display "__STATACODE__|VERSION|" c(stata_version) "|" c(flavor)
__STATACODE__|VERSION|17.0|SE

. display "__STATACODE__|RC|`__sc_rc'"
__STATACODE__|RC|111

. display "__STATACODE__|BEGIN"
__STATACODE__|BEGIN

. display "__STATACODE__|SECTION|R"
__STATACODE__|SECTION|R

. display "__STATACODE__|SECTION|E"
__STATACODE__|SECTION|E

. display "__STATACODE__|SECTION|MATRICES"
__STATACODE__|SECTION|MATRICES

. display "__STATACODE__|SECTION|DATASET"
__STATACODE__|SECTION|DATASET

. display "__STATACODE__|DS|nobs|" c(N)
__STATACODE__|DS|nobs|74

. display "__STATACODE__|END"
__STATACODE__|END
"""


def _build(log, code="", exe="/usr/local/stata18/stata-mp"):
    return console.build_run_result(
        code=code,
        raw_log=log,
        exe=exe,
        session_id="main",
        request_id="req",
        started_at="2026-07-23T00:00:00.000Z",
        elapsed_ms=100,
        log_lines_head=100,
        log_lines_tail=50,
        include_full_log=True,
    )


class TestWrapper:
    def test_wrapper_has_capture_and_markers(self):
        do = console.build_wrapper_do("regress mpg weight")
        assert "capture noisily {" in do
        assert "regress mpg weight" in do
        assert console.MARK_RC in do
        assert "return list" in do
        assert "ereturn list" in do
        assert "matrix list e(b)" in do

    def test_wrapper_cd_when_working_dir(self):
        do = console.build_wrapper_do("di 1", working_dir="/tmp/x")
        assert 'capture cd "/tmp/x"' in do


class TestSuccessParse:
    def test_top_level(self):
        r = _build(REGRESS_LOG, code="regress mpg weight")
        assert r.ok is True
        assert r.rc == 0
        assert r.stata.backend == Backend.CONSOLE
        assert r.stata.version == "18.0"
        assert r.stata.edition == StataEdition.MP

    def test_scalars_and_macros(self):
        r = _build(REGRESS_LOG)
        assert r.results.e.scalars["N"] == 74
        assert abs(r.results.e.scalars["r2"] - 0.6515489989240539) < 1e-12
        assert r.results.e.macros["cmd"] == "regress"
        assert r.results.last_estimation_cmd == "regress"

    def test_matrix_b_exact(self):
        r = _build(REGRESS_LOG)
        b = r.results.e.matrices["b"]
        assert b.rows == ["y1"]
        assert b.cols == ["weight", "_cons"]
        assert abs(b.values[0][0] - -0.0060086780487887) < 1e-15
        assert abs(b.values[0][1] - 39.4402797603966) < 1e-12

    def test_symmetric_matrix_v_mirror_filled(self):
        r = _build(REGRESS_LOG)
        v = r.results.e.matrices["V"]
        assert v.rows == ["weight", "_cons"]
        assert v.cols == ["weight", "_cons"]
        # lower-triangular input; upper mirror filled
        assert v.values[0][1] == v.values[1][0]
        assert abs(v.values[0][1] - -0.00082324159) < 1e-9

    def test_estimation_table_derived(self):
        r = _build(REGRESS_LOG)
        est = r.results.estimation
        assert est is not None
        assert est.command_family == "ols"
        terms = {c.term: c for c in est.coefficients}
        assert set(terms) == {"weight", "_cons"}
        # SE and t computed from e(b)/e(V)
        assert terms["weight"].se and terms["weight"].se > 0
        assert terms["weight"].statistic and terms["weight"].statistic < 0

    def test_dataset(self):
        r = _build(REGRESS_LOG)
        assert r.dataset.n_obs == 74
        assert r.dataset.n_vars == 12
        assert r.dataset.filename.endswith("auto.dta")
        names = {v.name for v in r.dataset.variables}
        assert names == {"make", "mpg"}
        make = next(v for v in r.dataset.variables if v.name == "make")
        assert make.type == "str18"
        assert make.label == "Make and model"

    def test_no_marker_leakage_in_log(self):
        r = _build(REGRESS_LOG)
        assert "__STATACODE__" not in r.log.head
        assert "__STATACODE__" not in r.log.tail


class TestErrorParse:
    def test_typed_error(self):
        r = _build(ERROR_LOG, code="regress mpg wgt")
        assert r.ok is False
        assert r.rc == 111
        assert r.error.kind == ErrorKind.VARNAME_NOT_FOUND
        assert r.error.varname == "wgt"
        assert r.stata.edition == StataEdition.SE

    def test_dataset_still_captured_on_error(self):
        r = _build(ERROR_LOG)
        assert r.dataset.n_obs == 74


class TestDiscovery:
    def test_edition_from_exe_name(self):
        assert console._edition_from_exe("/x/stata-mp") == StataEdition.MP
        assert console._edition_from_exe("/x/StataSE-64.exe") == StataEdition.SE
        assert console._edition_from_exe("/x/stata-be") == StataEdition.BE

    def test_stata_cli_env_var(self, tmp_path, monkeypatch):
        fake = tmp_path / "stata-mp"
        fake.write_text("#!/bin/sh\n")
        fake.chmod(0o755)
        monkeypatch.setenv("STATA_CODE_STATA_CLI", str(fake))
        assert console.find_stata_cli() == str(fake)
        assert console.console_available() is True

    def test_not_found_returns_none(self, monkeypatch):
        monkeypatch.delenv("STATA_CODE_STATA_CLI", raising=False)
        monkeypatch.delenv("STATA_CLI", raising=False)
        monkeypatch.delenv("STATA_HOME", raising=False)
        monkeypatch.delenv("STATA_PATH", raising=False)
        monkeypatch.setattr(console.shutil, "which", lambda _: None)
        monkeypatch.setattr(console.platform, "system", lambda: "Linux")
        # No real Stata roots exist in CI.
        assert console.find_stata_cli() is None


class TestExecuteGuards:
    def test_execute_raises_without_cli(self, monkeypatch):
        monkeypatch.setattr(console, "find_stata_cli", lambda: None)
        monkeypatch.setenv("STATA_CODE_COMMAND_POLICY", "off")
        with pytest.raises(console.ConsoleNotAvailable):
            console.execute("regress y x")

    def test_execute_enforces_command_policy(self, monkeypatch):
        monkeypatch.setenv("STATA_CODE_COMMAND_POLICY", "enforce")
        # policy must fire before we even look for a Stata executable
        monkeypatch.setattr(
            console, "find_stata_cli", lambda: (_ for _ in ()).throw(AssertionError())
        )
        result = console.execute("shell rm -rf /")
        assert result.ok is False
        assert result.error.kind == ErrorKind.POLICY_BLOCKED

    def test_batch_argv_platform(self, monkeypatch):
        from pathlib import Path

        monkeypatch.setattr(console.platform, "system", lambda: "Linux")
        assert console._batch_argv("stata-mp", Path("/x/run.do"))[:2] == ["stata-mp", "-b"]
        monkeypatch.setattr(console.platform, "system", lambda: "Windows")
        assert console._batch_argv("StataMP-64", Path("/x/run.do"))[1] == "/e"
