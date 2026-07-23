"""Run the benchmark tasks through a runner and print a scorecard.

Today only the offline ``mock`` runner is wired end-to-end (it verifies the
pipeline and backs the unit tests). The live ``stata-code`` and ``competitor``
runners are adapters to implement when running the actual study — they need a
real agent, a Stata install, and API access.

    python benchmarks/run_benchmark.py --runner mock
    python benchmarks/run_benchmark.py --runner mock --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow `python benchmarks/run_benchmark.py` (script dir on sys.path[0]) to find
# the `benchmarks` package by putting the repo root on the path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.harness import MockRunner, Scorecard, score  # noqa: E402


def _print_text(cards: list[Scorecard]) -> None:
    for card in cards:
        d = card.to_dict()
        print(f"# {d['runner']}")
        print(f"  tasks:            {d['tasks']}")
        print(f"  solved:           {d['solved']}/{d['tasks']}")
        print(f"  mean iterations:  {d['mean_iterations_when_solved']:.2f} (when solved)")
        print(f"  total tokens:     {d['total_tokens']}")
        print()
    if len(cards) == 2 and cards[0].total_tokens and cards[1].total_tokens:
        a, b = cards
        if b.total_tokens:
            ratio = b.total_tokens / max(1, a.total_tokens)
            print(f"token ratio ({b.runner} / {a.runner}): {ratio:.1f}x")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", choices=["mock"], default="mock")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.runner == "mock":
        cards = [score(MockRunner(typed=True)), score(MockRunner(typed=False))]
    else:  # pragma: no cover - defensive
        print(f"unknown runner: {args.runner}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps([c.to_dict() for c in cards], indent=2))
    else:
        _print_text(cards)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
