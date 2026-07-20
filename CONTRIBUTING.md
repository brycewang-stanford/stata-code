# Contributing to stata-code

Thanks for your interest in improving `stata-code`! This document explains how to
set up a development environment, run the checks CI runs, and get a change merged.

- **Report a bug / request a feature** → [open an issue](https://github.com/brycewang-stanford/stata-code/issues)
- **Ask a question / seek support** → open a [GitHub issue](https://github.com/brycewang-stanford/stata-code/issues) with the `question` label
- **Security concern** → see [SECURITY.md](SECURITY.md)

Before opening a PR, please also read [LICENSE-POLICY.md](LICENSE-POLICY.md),
which explains how this project relates to the wider Stata tooling ecosystem.

## Development setup

Python 3.10+ is required. Stata 17+ is *optional* — the majority of the test
suite runs without it.

```bash
git clone https://github.com/brycewang-stanford/stata-code.git
cd stata-code
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,mcp,kernel]"
```

## Running the checks

CI runs four gates on every PR. Run them locally first:

```bash
# 1. Tests (no Stata needed for this subset; CI runs exactly this)
pytest -m "not stata_required"

# 2. Lint
ruff check stata_code tests scripts

# 3. Types (the package is mypy-clean; please keep it that way)
mypy stata_code

# 4. Generated artifacts in sync
python scripts/export_schema.py --check   # JSON Schema artifact
python scripts/check_versions.py          # version literals aligned
```

If you have Stata 17+ installed, `pytest` with no marker filter also runs the
real-Stata integration tests (`-m stata_required`).

The VS Code extension has its own checks:

```bash
cd vscode && npm ci && npm test
```

## Guidelines

- **Tests are required** for any new schema field, runner behavior, MCP tool, or
  error-taxonomy change. Offline tests (no Stata) are strongly preferred; use the
  `stata_required` marker only when a real Stata session is genuinely needed.
- **Schema changes**: `RunResult` and friends live in
  `stata_code/core/schema.py`. After editing, regenerate the JSON Schema artifact
  with `python scripts/export_schema.py` and commit the result, and update
  [SCHEMA.md](SCHEMA.md) if the shape changed.
- **Version literals**: do **not** bump versions in a feature PR. Releases move
  eight version literals across six files together; the process is documented in
  [PUBLISHING.md](PUBLISHING.md) and guarded by `scripts/check_versions.py`.
- **Style**: `ruff` (line length 100) and `mypy` settings are in
  `pyproject.toml`. Match the style of the surrounding code.
- **First PR**: add a one-line acknowledgement to your PR description; the
  template is in [LICENSE-POLICY.md](LICENSE-POLICY.md).

## Project layout

| Path | What lives there |
| --- | --- |
| `stata_code/core/` | Runner pipeline, session pool, schema, error taxonomy |
| `stata_code/mcp/` | MCP server frontend |
| `stata_code/kernel/` | Jupyter kernel frontend |
| `vscode/` | VS Code extension (TypeScript) |
| `schema/` | Exported JSON Schema artifact |
| `tests/` | Python test suite (`stata_required` marks real-Stata tests) |
| `examples/` | End-to-end walkthroughs |

## Release process (maintainers)

See [PUBLISHING.md](PUBLISHING.md). Contributors never need to tag or publish.
