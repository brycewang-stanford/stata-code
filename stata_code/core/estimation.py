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

from stata_code.core.schema import (
    Coefficient,
    EstimationResult,
    Matrix,
    ResultsInfo,
    StataReturns,
)

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


def _inline(m: Matrix | None) -> bool:
    """True when a matrix carries inline values we can read directly."""
    return m is not None and m.values is not None


def _from_r_table(table: Matrix) -> tuple[list[Coefficient], str] | None:
    """Parse ``r(table)`` into coefficient rows. Returns (coeffs, stat_kind).

    Returns ``None`` if the matrix does not look like a coefficient table
    (no ``b`` row) or is only available by reference.
    """
    if not _inline(table):
        return None
    values = table.values
    assert values is not None  # narrowed by _inline
    row_idx = {name: i for i, name in enumerate(table.rows)}
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
        for j, term in enumerate(table.cols)
    ]
    return coeffs, stat_kind


def _r_table_matches_b(table: Matrix, b: Matrix) -> bool:
    """True when ``r(table)`` describes the same coefficients as ``e(b)``."""
    if not _inline(table) or not _inline(b) or list(table.cols) != list(b.cols):
        return False

    table_values = table.values
    b_values = b.values
    assert table_values is not None
    assert b_values is not None
    row_idx = {name: i for i, name in enumerate(table.rows)}
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


def _from_b_v(b: Matrix, v: Matrix | None) -> list[Coefficient]:
    """Compute coefficient rows from e(b) (and e(V) when available).

    ``se``/``statistic``/``p_value``/CI are filled only when e(V) is present and
    inline; otherwise just the point estimates are returned. Inference uses the
    normal approximation — callers flag this via ``source="e_b_v"``.
    """
    assert b.values is not None  # caller guarantees inline
    b_row = b.values[0] if b.values else []
    terms = b.cols
    v_diag: list[float | None] = []
    if _inline(v) and v is not None:
        vv = v.values
        assert vv is not None
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


def build_estimation_result(results: ResultsInfo) -> EstimationResult | None:
    """Derive a typed coefficient table from captured r()/e() values.

    Returns ``None`` when no estimation is in scope (``e(b)`` absent), so the
    field stays unset for non-estimation runs. Prefers ``r(table)`` when its
    columns and ``b`` row match ``e(b)`` (i.e. it describes the *current*
    estimation); otherwise computes from ``e(b)``/``e(V)``.
    """
    return build_estimation_from_returns(results.e, results.r, results.last_estimation_cmd)


def build_estimation_from_returns(
    e: StataReturns,
    r: StataReturns,
    last_estimation_cmd: str | None = None,
) -> EstimationResult | None:
    """Core builder, split out so it is trivially unit-testable."""
    b = e.matrices.get("b")
    if b is None or not _inline(b):
        # No coefficient vector in e-scope (or only by reference): not an
        # estimation result we can type. ``e(b)`` is small for any normal model,
        # so the by-reference case is rare and we decline rather than guess.
        return None

    v = e.matrices.get("V")
    command = e.macros.get("cmd") or last_estimation_cmd
    depvar = e.macros.get("depvar") or None

    coeffs: list[Coefficient]
    statistic_kind: str
    source: str

    table = r.matrices.get("table")
    parsed = _from_r_table(table) if table is not None else None
    if parsed is not None and table is not None and _r_table_matches_b(table, b):
        coeffs, statistic_kind = parsed
        source = "r_table"
    else:
        coeffs = _from_b_v(b, v)
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
        depvar=depvar,
        n_obs=n_obs,
        df_model=e.scalars.get("df_m"),
        df_resid=e.scalars.get("df_r"),
        statistic_kind=statistic_kind,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        coefficients=coeffs,
        model_stats=_model_stats(e.scalars),
    )
