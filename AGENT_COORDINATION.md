# Agent Coordination

Last updated: 2026-06-20

This file coordinates concurrent agent work in this repository. It is not a
lock file, but agents should read it before editing and update it when they
take or finish a work lane.

## Ground Rules

- Run `git status --short --branch --untracked-files=all` before starting a
  lane and before staging or handing off.
- Prefer additive, narrowly scoped edits. Do not rewrite files another agent
  has changed unless you have re-read the latest diff and the change is needed.
- Keep implementation claims tied to code, tests, or documented workflows.
  Do not turn roadmap ideas into README promises until the behavior exists.
- Avoid destructive git operations. Do not reset, checkout over, or delete
  unrelated changes.
- Validate the touched surface before handoff. At minimum run
  `git diff --check`; run targeted tests for Python, MCP, skill packaging, or
  VS Code when those surfaces change.

## Current Lanes

| Lane | Owner | Status | Primary files |
| --- | --- | --- | --- |
| Industry roadmap and coordination | Codex | Week 1 pass complete; keep updated before handoff | `AGENT_COORDINATION.md`, `docs/industry-leader-roadmap.md` |
| Economist workflow skill expansion | Codex + Claude | Week 1 pass complete; additive references only | `skills/stata-code/SKILL.md`, `skills/stata-code/references/**` |
| MCP prompt discoverability | Codex + Claude | Week 1 pass complete; keep prompt/docs/tests in sync | `stata_code/mcp/server.py`, `tests/test_mcp.py`, `tests/test_method_prompts.py`, READMEs |
| Turnkey method recipes + method prompts | Claude | Week 1 pass complete; maintain as recipe lane | `skills/stata-code/references/recipes/**`, method prompts in `stata_code/mcp/server.py`, `tests/test_method_prompts.py` |
| Core runner / schema changes | Unclaimed | Open | `stata_code/core/**`, `SCHEMA.md`, `schema/**` |
| VS Code UX and table/data preview polish | Claude | Complete — Week 3 data/artifact surfacing: (1) persistent "Data" variables-browser view (`dataBrowser.ts`/`DataProvider`); (2) "Outputs" table/export-artifact view (`outputs.ts`/`OutputsHistoryProvider`, sourced from run-bundle `log.files.output_paths`, open + reveal-in-OS actions). New `dataBrowser.test.ts` + `outputs.test.ts`; 49 VS Code tests pass; esbuild bundle clean | `vscode/src/**`, `vscode/README.md`, VS Code tests |
| Release and distribution hardening | Codex | 2026-06-23 pass complete: read-only MCP client config visibility in `doctor`; keep release/docs checks in sync | `stata_code/doctor.py`, `stata_code/cli.py`, `pyproject.toml`, README install sections, tests |
| README hero & econ-facing positioning | Claude | Complete (additive hero added to README.md + README.zh.md; 378 non-Stata tests + ruff green; 18-tools guard phrases intact) | `README.md`, `README.zh.md` |

## One-Month Milestones

| Window | Target | Evidence |
| --- | --- | --- |
| Week 1 | Economist-facing workflow layer: parity audit, data-MCP handoff, modern DiD/IV/RDD/table package references, examples, prompts | Skill package tests, MCP prompt tests, README/example consistency |
| Week 2 | Installation and runtime confidence: read-only doctor/verify command, clearer client config diagnostics, package availability checks | New tests for doctor output and failure modes |
| Week 3 | Editor/artifact polish: stronger data preview, table/export artifact surfacing, run-bundle ergonomics | VS Code unit/extension-host tests where practical |
| Week 4 | Release hardening and launch quality: docs sweep, demos, changelog, end-to-end validation, draft release notes | Full relevant local gates and manual smoke-test evidence |

## Cross-Agent Notes — Week 1 Integration (2026-06-20)

Claude entered the skill/prompt lanes while Codex was also expanding the
economist workflow layer. Resolution: **integrate and document the division of
labor.** Keep edits additive and re-read current diffs before touching shared
files (`SKILL.md`, READMEs, `tests/test_mcp.py`, `tests/test_method_prompts.py`,
and `stata_code/mcp/server.py`).

Integrated Week 1 surfaces:

- `references/data-mcp-handoff.md` is the operational protocol for external
  data-MCP output entering Stata.
- `references/data-sources.md` is a lightweight source-selection and provenance
  guide. It should not duplicate the handoff protocol.
- `references/parity-audit.md` is the general cross-stack/cross-package audit
  protocol.
- `references/recipes/cross-validation.md` is the turnkey empirical recipe for
  applying that parity discipline to specific estimates. It should link back to
  the general parity protocol instead of redefining it.
- `references/recipes/{did-event-study,iv-2sls,rdd,publication-tables}.md` are
  end-to-end workflows (install → estimate → robustness → esttab), distinct from
  per-command `references/packages/*.md`.
- MCP prompts are intentionally split:
  - `plan_cross_stack_parity_audit`: general planning prompt for Stata/R/Python
    or cross-package parity audits.
  - `cross_validate_did`: DiD-specific execution prompt for the Cunningham-style
    two-implementation check.
  - `did_event_study`, `iv_2sls`, `rdd`, `publication_table`: turnkey method
    templates routed to the recipe files.
  - `data_mcp_to_stata_handoff`: operational data-MCP-to-Stata handoff prompt.

Resolved integration decisions:

1. Keep both `plan_cross_stack_parity_audit` and `cross_validate_did`; the former
   is generic planning, the latter is a DiD-specific execution template.
2. `SKILL.md`, README.md, README.zh.md, CHANGELOG.md, `tests/test_mcp.py`, and
   `tests/test_method_prompts.py` must stay synchronized with the prompt surface.
3. These additions are prompts and references, not new MCP tools; the tool count
   remains 18 unless server tools are actually added.

## Handoff Checklist

- Re-run `git status --short --branch --untracked-files=all`.
- Summarize changed files and intended behavior.
- List tests or checks run, including failures.
- Note any files intentionally left for another agent.
