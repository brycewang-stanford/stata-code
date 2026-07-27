"""Typed per-command estimation contract.

Stata stores estimation output in two places an agent should never have to
parse from log prose:

* ``r(table)`` — the canonical coefficient table the command *displayed*, with
  rows ``b``, ``se``, ``t``/``z``, ``pvalue``, ``ll``, ``ul``, ``df``, ... and
  one column per term. When present, this is referee-grade: the numbers are
  exactly what Stata printed, so we copy them verbatim.
* ``e(b)`` / ``e(V)`` — the coefficient vector and its variance–covariance
  matrix. Always present after an estimation command. We use these as a
  fallback, computing ``se`` from ``diag(V)`` and ``z``/``p``/CI under a normal
  approximation (clearly flagged via ``source`` / ``statistic_kind``).

The result is :class:`EstimationResult`, attached to ``RunResult.results.
estimation`` so every frontend (MCP, kernel, VS Code) gets the same typed
table without re-running anything. This module is pure Python (only the stdlib
``math``) and is fully unit-testable without Stata.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from stata_code.core.schema import (
    Coefficient,
    EstimationResult,
    IncludeEstimation,
    Matrix,
    ResultsInfo,
    StataReturns,
)

# Resolves a ``matrix://`` ref to its row-major values, or ``None`` when the
# ref is unknown. Supplied by the runner so this module stays Stata-free.
MatrixResolver = Callable[[str], list[list[float | None]] | None]

# 95% two-sided normal critical value, used only on the e(b)/e(V) fallback path.
_Z_CRIT_95 = 1.959963984540054

# Model-level e() scalars worth surfacing as a compact summary. The full set is
# always available in ``results.e.scalars``; this is the high-signal subset an
# agent or a table writer most often reports.
_MODEL_STAT_KEYS: tuple[str, ...] = (
    "N",
    "N_clust",
    "N_g",
    "df_m",
    "df_r",
    "r2",
    "r2_a",
    "r2_w",
    "r2_o",
    "r2_b",
    "F",
    "chi2",
    "p",
    "ll",
    "ll_0",
    "rmse",
    "rss",
    "mss",
    "sigma",
    "rank",
)

# Coarse estimator family per e(cmd), so an agent can branch on the *kind* of
# model without a command lookup table. Best-effort; unknown commands map to
# None. Keys are the canonical e(cmd) names of commands economists use most.
_COMMAND_FAMILY: dict[str, str] = {
    # OLS / linear with fixed effects
    "regress": "ols",
    "areg": "ols",
    "reghdfe": "ols",
    "hdfe": "ols",
    # Panel
    "xtreg": "panel",
    "xtgls": "panel",
    "xtscc": "panel",
    # Instrumental variables
    "ivregress": "iv",
    "ivreg": "iv",
    "ivreg2": "iv",
    "ivreghdfe": "iv",
    # Dynamic-panel GMM
    "xtabond": "gmm",
    "xtabond2": "gmm",
    "xtdpdsys": "gmm",
    "xtdpd": "gmm",
    # Count / Poisson (incl. PPML)
    "poisson": "count",
    "nbreg": "count",
    "ppml": "count",
    "ppmlhdfe": "count",
    "fepois": "count",
    # Binary / limited dependent
    "logit": "binary",
    "probit": "binary",
    "cloglog": "binary",
    "tobit": "limited",
    "heckman": "limited",
    # Modern difference-in-differences
    "csdid": "did",
    "did_multiplegt_dyn": "did",
    "did_imputation": "did",
    "eventstudyinteract": "did",
    "didregress": "did",
    "xtdidregress": "did",
}

# Command-specific identification / specification diagnostics, keyed by e(cmd).
# Each entry maps an e() scalar name to a friendly label. Only scalars actually
# present in e() are surfaced, so an entry that does not apply to a given run
# (or Stata version) is silently skipped — we never fabricate a value.
_IV_DIAGNOSTICS: tuple[tuple[str, str], ...] = (
    ("widstat", "weak_id_F"),  # Kleibergen-Paap / Cragg-Donald weak-ID stat
    ("rkf", "kp_rk_wald_F"),
    ("idstat", "underid_stat"),
    ("iddf", "underid_df"),
    ("j", "overid_j"),  # Hansen J / Sargan
    ("jdf", "overid_j_df"),
    ("arf", "anderson_rubin_F"),
)
_GMM_DIAGNOSTICS: tuple[tuple[str, str], ...] = (
    ("ar1p", "ar1_p"),  # Arellano-Bond AR(1) test p-value
    ("ar2p", "ar2_p"),  # AR(2) — the one that must NOT reject
    ("hansenp", "hansen_p"),  # Hansen overid p
    ("sarganp", "sargan_p"),
)
_COMMAND_DIAGNOSTICS: dict[str, tuple[tuple[str, str], ...]] = {
    "reghdfe": (
        ("r2_within", "r2_within"),
        ("N_hdfe", "n_absorbed_fe_dims"),
        ("df_a", "absorbed_df"),
    ),
    "ivregress": (("widstat", "weak_id_F"),),
    "ivreg2": _IV_DIAGNOSTICS,
    "ivreghdfe": _IV_DIAGNOSTICS,
    "ivreg": _IV_DIAGNOSTICS,
    "xtabond2": _GMM_DIAGNOSTICS,
    "xtdpdsys": _GMM_DIAGNOSTICS,
    "xtabond": _GMM_DIAGNOSTICS,
    "xtreg": (
        ("rho", "rho"),  # fraction of variance from u_i
        ("corr", "corr_u_xb"),
        ("sigma_u", "sigma_u"),
        ("sigma_e", "sigma_e"),
    ),
    "ppmlhdfe": (("r2_p", "pseudo_r2"),),
    "fepois": (("r2_p", "pseudo_r2"),),
}


def _normal_cdf(x: float) -> float:
    """Standard normal CDF via the error function (stdlib only)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _two_sided_normal_p(z: float) -> float:
    """Two-sided p-value for a z statistic under the normal approximation."""
    return 2.0 * (1.0 - _normal_cdf(abs(z)))


def _cell(row: list[float | None] | None, j: int) -> float | None:
    if row is None or j >= len(row):
        return None
    return row[j]


def _values(m: Matrix | None, resolve: MatrixResolver | None) -> list[list[float | None]] | None:
    """Row-major values for a matrix, following its ``ref`` when necessary.

    A matrix that exceeded the inline cell cap (or that the caller asked to be
    stubbed) carries ``values=None`` and a ``matrix://`` ref. Historically the
    estimation builder treated that as "no values", which silently blanked
    ``se`` / ``statistic`` / ``p_value`` / CI for every coefficient of any
    model whose ``e(V)`` was large enough to be deferred — precisely the
    fixed-effect-heavy specifications where the table matters most. Resolving
    the ref keeps inference available regardless of wire representation.
    """
    if m is None:
        return None
    if m.values is not None:
        return m.values
    if m.ref and resolve is not None:
        return resolve(m.ref)
    return None


def _labels(m: Matrix, values: list[list[float | None]] | None, axis: str) -> list[str]:
    """Row or column labels, falling back to the ref payload's own labels.

    A stubbed matrix elides ``rows`` / ``cols`` to save tokens, so read them
    back from the resolved payload when the stub's list is empty.
    """
    declared = m.rows if axis == "rows" else m.cols
    if declared:
        return list(declared)
    if values is None:
        return []
    if axis == "rows":
        return [f"r{i + 1}" for i in range(len(values))]
    width = len(values[0]) if values else 0
    return [f"c{j + 1}" for j in range(width)]


def _from_r_table(
    table: Matrix, resolve: MatrixResolver | None = None
) -> tuple[list[Coefficient], str] | None:
    """Parse ``r(table)`` into coefficient rows. Returns (coeffs, stat_kind).

    Returns ``None`` if the matrix does not look like a coefficient table
    (no ``b`` row) or its values cannot be obtained.
    """
    values = _values(table, resolve)
    if values is None:
        return None
    rows = _labels(table, values, "rows")
    cols = _labels(table, values, "cols")
    row_idx = {name: i for i, name in enumerate(rows)}
    if "b" not in row_idx:
        return None

    def get_row(name: str) -> list[float | None] | None:
        i = row_idx.get(name)
        return values[i] if i is not None else None

    b_row = get_row("b")
    se_row = get_row("se")
    t_row = get_row("t")
    z_row = get_row("z")
    stat_row = t_row if t_row is not None else z_row
    stat_kind = "t" if t_row is not None else "z"
    p_row = get_row("pvalue")
    ll_row = get_row("ll")
    ul_row = get_row("ul")

    coeffs = [
        Coefficient(
            term=term,
            b=_cell(b_row, j),
            se=_cell(se_row, j),
            statistic=_cell(stat_row, j),
            p_value=_cell(p_row, j),
            ci_low=_cell(ll_row, j),
            ci_high=_cell(ul_row, j),
        )
        for j, term in enumerate(cols)
    ]
    return coeffs, stat_kind


def _r_table_matches_b(table: Matrix, b: Matrix, resolve: MatrixResolver | None = None) -> bool:
    """True when ``r(table)`` describes the same coefficients as ``e(b)``."""
    table_values = _values(table, resolve)
    b_values = _values(b, resolve)
    if table_values is None or b_values is None:
        return False
    if _labels(table, table_values, "cols") != _labels(b, b_values, "cols"):
        return False

    row_idx = {name: i for i, name in enumerate(_labels(table, table_values, "rows"))}
    b_row_index = row_idx.get("b")
    if b_row_index is None or not b_values:
        return False

    table_b = table_values[b_row_index]
    e_b = b_values[0]
    if len(table_b) != len(e_b):
        return False

    return all(_same_numeric_or_missing(left, right) for left, right in zip(table_b, e_b))


def _same_numeric_or_missing(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is right
    return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-12)


def _from_b_v(
    b: Matrix,
    v: Matrix | None,
    resolve: MatrixResolver | None = None,
    b_values: list[list[float | None]] | None = None,
) -> list[Coefficient]:
    """Compute coefficient rows from e(b) (and e(V) when available).

    ``se``/``statistic``/``p_value``/CI are filled only when e(V) is
    obtainable — inline or through its ref; otherwise just the point estimates
    are returned. Inference uses the normal approximation — callers flag this
    via ``source="e_b_v"``.
    """
    if b_values is None:
        b_values = _values(b, resolve)
    assert b_values is not None  # caller guarantees resolvability
    b_row = b_values[0] if b_values else []
    terms = _labels(b, b_values, "cols")
    vv = _values(v, resolve)
    v_diag: list[float | None] = []
    if vv is not None:
        for i in range(len(terms)):
            v_diag.append(vv[i][i] if i < len(vv) and i < len(vv[i]) else None)
    else:
        v_diag = [None] * len(terms)

    coeffs: list[Coefficient] = []
    for j, term in enumerate(terms):
        b_val = _cell(b_row, j)
        var = v_diag[j] if j < len(v_diag) else None
        se = math.sqrt(var) if (var is not None and var >= 0.0) else None
        stat: float | None = None
        p_val: float | None = None
        ci_low: float | None = None
        ci_high: float | None = None
        if b_val is not None and se is not None and se > 0.0:
            stat = b_val / se
            p_val = _two_sided_normal_p(stat)
            ci_low = b_val - _Z_CRIT_95 * se
            ci_high = b_val + _Z_CRIT_95 * se
        coeffs.append(
            Coefficient(
                term=term,
                b=b_val,
                se=se,
                statistic=stat,
                p_value=p_val,
                ci_low=ci_low,
                ci_high=ci_high,
            )
        )
    return coeffs


def _model_stats(scalars: dict[str, float | None]) -> dict[str, float | None]:
    return {k: scalars[k] for k in _MODEL_STAT_KEYS if k in scalars}


def _command_family(command: str | None) -> str | None:
    """Coarse estimator family for an e(cmd) name (None if unrecognized)."""
    if not command:
        return None
    return _COMMAND_FAMILY.get(command)


def _command_diagnostics(
    command: str | None, scalars: dict[str, float | None]
) -> dict[str, float | None]:
    """Surface command-specific identification/spec-test scalars.

    Only scalars actually present in e() are included, so an entry that does not
    apply to a given run (or Stata version) is skipped rather than fabricated.
    """
    if not command:
        return {}
    spec = _COMMAND_DIAGNOSTICS.get(command)
    if spec is None:
        return {}
    return {label: scalars[key] for key, label in spec if key in scalars}


def build_estimation_result(
    results: ResultsInfo,
    *,
    resolve_matrix: MatrixResolver | None = None,
) -> EstimationResult | None:
    """Derive a typed coefficient table from captured r()/e() values.

    Returns ``None`` when no estimation is in scope (``e(b)`` absent), so the
    field stays unset for non-estimation runs. Prefers ``r(table)`` when its
    columns and ``b`` row match ``e(b)`` (i.e. it describes the *current*
    estimation); otherwise computes from ``e(b)``/``e(V)``.

    ``resolve_matrix`` lets the builder follow ``matrix://`` refs so a deferred
    ``e(V)`` still yields standard errors.
    """
    return build_estimation_from_returns(
        results.e,
        results.r,
        results.last_estimation_cmd,
        resolve_matrix=resolve_matrix,
    )


def build_estimation_from_returns(
    e: StataReturns,
    r: StataReturns,
    last_estimation_cmd: str | None = None,
    *,
    resolve_matrix: MatrixResolver | None = None,
) -> EstimationResult | None:
    """Core builder, split out so it is trivially unit-testable."""
    b = e.matrices.get("b")
    b_values = _values(b, resolve_matrix)
    if b is None or b_values is None:
        # No coefficient vector in e-scope, and none reachable through a ref:
        # not an estimation result we can type.
        return None

    v = e.matrices.get("V")
    command = e.macros.get("cmd") or last_estimation_cmd
    depvar = e.macros.get("depvar") or None

    coeffs: list[Coefficient]
    statistic_kind: str
    source: str

    table = r.matrices.get("table")
    parsed = _from_r_table(table, resolve_matrix) if table is not None else None
    if parsed is not None and table is not None and _r_table_matches_b(table, b, resolve_matrix):
        coeffs, statistic_kind = parsed
        source = "r_table"
    else:
        coeffs = _from_b_v(b, v, resolve_matrix, b_values)
        statistic_kind = "z"
        source = "e_b_v"

    n_obs: int | None = None
    n_raw = e.scalars.get("N")
    if n_raw is not None:
        try:
            n_obs = int(n_raw)
        except (ValueError, OverflowError):
            n_obs = None

    return EstimationResult(
        command=command,
        command_family=_command_family(command),
        depvar=depvar,
        n_obs=n_obs,
        df_model=e.scalars.get("df_m"),
        df_resid=e.scalars.get("df_r"),
        statistic_kind=statistic_kind,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        coefficients=coeffs,
        model_stats=_model_stats(e.scalars),
        diagnostics=_command_diagnostics(command, e.scalars),
        n_coefficients=len(coeffs),
        coefficients_truncated=False,
    )


def trim_estimation(
    est: EstimationResult | None,
    *,
    mode: IncludeEstimation = IncludeEstimation.FULL,
    max_coefficients: int | None = None,
) -> EstimationResult | None:
    """Apply the caller's estimation-payload budget to a built result.

    ``n_coefficients`` always reports the model's true term count, so an agent
    can tell a 12-term model from the first 12 rows of a 141-term one.
    ``mode="none"`` drops the block entirely; ``"summary"`` keeps the
    model-level fields and diagnostics but no coefficient rows.
    """
    if est is None or mode == IncludeEstimation.NONE:
        return None
    if mode == IncludeEstimation.SUMMARY:
        return est.model_copy(
            update={
                "coefficients": [],
                "coefficients_truncated": bool(est.n_coefficients),
            }
        )
    if max_coefficients is not None and 0 <= max_coefficients < len(est.coefficients):
        return est.model_copy(
            update={
                "coefficients": est.coefficients[:max_coefficients],
                "coefficients_truncated": True,
            }
        )
    return est
