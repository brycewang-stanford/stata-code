# stata-code reliability & token benchmark

The defensible claim behind `stata-code` is that a **typed execution contract**
(structured `RunResult`, a 32-kind error taxonomy, recovery contracts, a static
linter, and a command-safety guard) lets an agent reach a correct empirical
result in **fewer iterations and fewer tokens** than a tool that hands the agent
raw Stata log text. This harness is how we turn that claim into a number instead
of a slogan.

It is deliberately tool-agnostic: each **task** is a small, checkable empirical
problem; each **runner** is an adapter that drives one execution surface
(stata-code MCP, stata-code console/CLI, or a raw-log competitor); each **metric**
is computed the same way regardless of runner.

## What it measures

- **iterations-to-correct** — how many run→error→fix cycles until the target
  numeric result is produced within tolerance.
- **tokens spent** — total tokens the runner returned to the agent (proxy: bytes
  of tool output, since typed results and log refs are far smaller than raw logs).
- **success** — whether the final result matches the task's expected value.

## Layout

```
benchmarks/
├── README.md            # this file
├── tasks.py             # the task set (BenchmarkTask objects + a checker)
├── harness.py           # metric computation + a MockRunner for offline CI
└── run_benchmark.py     # CLI: run tasks through a runner, emit a scorecard
```

## Status

The task set, the scoring harness, and an offline `MockRunner` (used by the unit
tests) are implemented and CI-covered. The **live runners** — one that drives a
real agent + `stata-code`, one that drives the same agent against a raw-log
competitor — require a Stata install and API access and are intentionally left as
adapters to fill in when running the study; `run_benchmark.py --runner mock`
executes end-to-end today so the pipeline itself is verified.

## Running

```bash
python benchmarks/run_benchmark.py --runner mock          # offline demo
python benchmarks/run_benchmark.py --runner mock --json    # machine-readable
```
