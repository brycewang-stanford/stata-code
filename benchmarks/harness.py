"""Benchmark scoring harness: runner protocol, metrics, and an offline mock.

A *runner* drives one execution surface through the fix-and-rerun loop for a
task and reports what happened. The harness aggregates runners' per-task
:class:`RunnerOutcome`s into a comparable scorecard. Nothing here needs Stata;
the live runners (real agent + stata-code vs. a raw-log competitor) are adapters
supplied when the study is run. The :class:`MockRunner` makes the whole pipeline
executable and testable offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from benchmarks.tasks import TASKS, BenchmarkTask, check_result


@dataclass
class RunnerOutcome:
    """Result of one runner attempting one task."""

    task_id: str
    success: bool
    iterations: int
    tokens: int
    final_result: dict[str, float | None] = field(default_factory=dict)


class Runner(Protocol):
    name: str

    def run_task(self, task: BenchmarkTask) -> RunnerOutcome: ...


@dataclass
class Scorecard:
    runner: str
    outcomes: list[RunnerOutcome]

    @property
    def n(self) -> int:
        return len(self.outcomes)

    @property
    def solved(self) -> int:
        return sum(1 for o in self.outcomes if o.success)

    @property
    def mean_iterations(self) -> float:
        solved = [o.iterations for o in self.outcomes if o.success]
        return sum(solved) / len(solved) if solved else float("nan")

    @property
    def total_tokens(self) -> int:
        return sum(o.tokens for o in self.outcomes)

    def to_dict(self) -> dict[str, object]:
        return {
            "runner": self.runner,
            "tasks": self.n,
            "solved": self.solved,
            "mean_iterations_when_solved": self.mean_iterations,
            "total_tokens": self.total_tokens,
            "outcomes": [
                {
                    "task_id": o.task_id,
                    "success": o.success,
                    "iterations": o.iterations,
                    "tokens": o.tokens,
                }
                for o in self.outcomes
            ],
        }


def score(runner: Runner, tasks: list[BenchmarkTask] | None = None) -> Scorecard:
    tasks = tasks or TASKS
    return Scorecard(runner=runner.name, outcomes=[runner.run_task(t) for t in tasks])


# ─────────────────────────────────────────────────────────────────────────────
# Offline mock runners — model the *structural* difference we expect to measure.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class MockRunner:
    """A deterministic stand-in that models a typed vs. raw-log tool.

    ``typed=True`` mimics stata-code: a typed error tells the agent exactly what
    to fix, so it converges in few iterations and returns compact results.
    ``typed=False`` mimics a raw-log tool: the agent burns extra iterations
    interpreting log text and pays more tokens per turn. This is a *model*, used
    only to exercise the pipeline and its tests — not evidence.
    """

    typed: bool = True

    @property
    def name(self) -> str:
        return "stata-code (typed)" if self.typed else "raw-log competitor"

    def run_task(self, task: BenchmarkTask) -> RunnerOutcome:
        # Canonical answers the mock "knows"; a real runner derives these from
        # the actual Stata run.
        answers: dict[str, dict[str, float]] = {
            "ols-auto-r2": {"r2": 0.6515},
            "ols-auto-coef": {"b_weight": 1.7466},
            "summarize-mean": {"mean_mpg": 21.297},
            "reghdfe-missing-pkg": {"N": 69},
        }
        result = dict(answers.get(task.id, {}))
        has_trap = bool(task.traps)
        if self.typed:
            iterations = 2 if has_trap else 1
            tokens = 400 * iterations
        else:
            # More cycles to interpret raw logs, and larger per-turn payloads.
            iterations = 4 if has_trap else 2
            tokens = 1500 * iterations
        return RunnerOutcome(
            task_id=task.id,
            success=check_result(task, result),
            iterations=iterations,
            tokens=tokens,
            final_result=result,
        )
