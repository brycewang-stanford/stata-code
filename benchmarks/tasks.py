"""Benchmark task set: small, checkable empirical problems.

Each task has a natural-language goal, a canonical Stata solution, and an
``expected`` mapping of result keys to target values with tolerances. A runner
produces a ``{key: value}`` result; :func:`check_result` decides success. Keep
tasks tiny and deterministic so the metric reflects the *loop*, not modelling
noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Expected:
    key: str
    value: float
    tol: float = 1e-3


@dataclass(frozen=True)
class BenchmarkTask:
    id: str
    goal: str
    canonical_do: str
    expected: list[Expected]
    # Common agent mistakes this task is designed to surface (documentation).
    traps: tuple[str, ...] = field(default_factory=tuple)


TASKS: list[BenchmarkTask] = [
    BenchmarkTask(
        id="ols-auto-r2",
        goal="Regress mpg on weight in the auto data; report R-squared.",
        canonical_do="sysuse auto, clear\nregress mpg weight",
        expected=[Expected("r2", 0.6515, 1e-3)],
        traps=("misspelling `weight` as `wgt` (rc 111)",),
    ),
    BenchmarkTask(
        id="ols-auto-coef",
        goal="Regress price on mpg and weight; report the weight coefficient.",
        canonical_do="sysuse auto, clear\nregress price mpg weight",
        expected=[Expected("b_weight", 1.7466, 5e-2)],
        traps=("forgetting `sysuse auto, clear` first (rc 111 / no data)",),
    ),
    BenchmarkTask(
        id="summarize-mean",
        goal="Report the mean of mpg in the auto data.",
        canonical_do="sysuse auto, clear\nsummarize mpg",
        expected=[Expected("mean_mpg", 21.297, 1e-2)],
        traps=("reading r(mean) after an e-class command clobbered r()",),
    ),
    BenchmarkTask(
        id="reghdfe-missing-pkg",
        goal="Run a two-way fixed-effects regression with reghdfe.",
        canonical_do=(
            "sysuse auto, clear\n"
            "reghdfe price weight, absorb(foreign rep78)"
        ),
        expected=[Expected("N", 69, 0.5)],
        traps=(
            "reghdfe not installed → rc 199; a typed tool suggests "
            "install_package(name='reghdfe'), a raw-log tool leaves the agent guessing",
        ),
    ),
]


def check_result(task: BenchmarkTask, result: dict[str, float | None]) -> bool:
    """Return True iff every expected key is present and within tolerance."""
    for exp in task.expected:
        got = result.get(exp.key)
        if got is None:
            return False
        if abs(float(got) - exp.value) > exp.tol:
            return False
    return True


def task_by_id(task_id: str) -> BenchmarkTask | None:
    return next((t for t in TASKS if t.id == task_id), None)
