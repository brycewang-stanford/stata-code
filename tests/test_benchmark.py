"""Tests for the offline benchmark harness (``benchmarks/``).

These verify the *pipeline* — task checking, scoring aggregation, and the mock
runners — not any empirical claim. The live runners need Stata + an agent and
are out of scope for CI.
"""

from __future__ import annotations

import json

from benchmarks.harness import MockRunner, score
from benchmarks.run_benchmark import main as bench_main
from benchmarks.tasks import TASKS, check_result, task_by_id


class TestTasks:
    def test_task_set_nonempty_and_unique_ids(self):
        ids = [t.id for t in TASKS]
        assert ids
        assert len(ids) == len(set(ids))

    def test_check_result_tolerance(self):
        task = task_by_id("ols-auto-r2")
        assert check_result(task, {"r2": 0.6515}) is True
        assert check_result(task, {"r2": 0.99}) is False
        assert check_result(task, {}) is False  # missing key


class TestScoring:
    def test_typed_runner_solves_all(self):
        card = score(MockRunner(typed=True))
        assert card.solved == card.n == len(TASKS)

    def test_typed_uses_fewer_tokens_than_raw_log(self):
        typed = score(MockRunner(typed=True))
        raw = score(MockRunner(typed=False))
        assert typed.total_tokens < raw.total_tokens
        assert typed.mean_iterations <= raw.mean_iterations

    def test_scorecard_dict_shape(self):
        d = score(MockRunner()).to_dict()
        assert set(d) >= {"runner", "tasks", "solved", "total_tokens", "outcomes"}


class TestCli:
    def test_mock_run_text(self, capsys):
        rc = bench_main(["--runner", "mock"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "stata-code (typed)" in out
        assert "token ratio" in out

    def test_mock_run_json(self, capsys):
        rc = bench_main(["--runner", "mock", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert len(payload) == 2
        assert payload[0]["solved"] == len(TASKS)
