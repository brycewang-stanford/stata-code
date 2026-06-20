# stata-code Industry Leadership Roadmap

This roadmap translates the June 2026 empirical-research MCP landscape into
work that fits `stata-code`'s architecture. The project should win by being the
most reliable agent-native Stata execution and audit layer for empirical
economists, not by becoming a grab-bag data platform or a second R/Python
runtime.

## North Star

`stata-code` should be the default way an AI agent runs, inspects, repairs, and
audits Stata work:

- one execution core across Python, MCP, Jupyter, and VS Code;
- stable `RunResult` schema with typed errors and native `r()` / `e()` values;
- token-efficient logs, graphs, matrices, and run bundles;
- economist-facing workflows for DiD, IV, RDD, tables, data handoff, and
  cross-package verification.

## Product Pillars

1. **Reliable execution contract.** Keep `SCHEMA.md` load-bearing. Agents
   branch on `ok`, `error.kind`, `results.e`, refs, and run manifests instead
   of parsing log prose.
2. **Econometrics workflow intelligence.** Ship concise skill references and
   prompts that know the Stata commands economists actually use: `csdid`,
   `did_imputation`, `eventstudyinteract`, `rdrobust`, `ivreg2`,
   `ivreghdfe`, `boottest`, `esttab`, `collect`, and related packages.
3. **Cross-stack parity audits.** Treat R/Python/Stata disagreement as a first
   class research risk. `stata-code` should run the Stata leg and define the
   comparison protocol without pretending to own the R or Python runtimes.
4. **Data-MCP handoff.** External MCP servers can discover and fetch official
   data. `stata-code` should document and validate the handoff into Stata:
   source metadata, stable raw files, key checks, and reproducible imports.
5. **Editor and artifact ergonomics.** VS Code should make sessions, graphs,
   logs, tables, data previews, and run bundles easy to inspect without hiding
   the underlying structured result.
6. **Distribution confidence.** Install and runtime checks should be easy to
   verify without mutating user config. Prefer `doctor`/`verify` diagnostics
   before any automatic config writer.

## Scope Boundaries

`stata-code` should not directly bundle data-provider APIs, R sessions, Python
causal libraries, or paid services. Those are separate tools. The durable
boundary is: external data/model tools produce files or results; `stata-code`
executes and audits the Stata side with traceable artifacts.

## One-Month Execution Plan

### Week 1: Workflow Layer

- Add cross-agent coordination and this roadmap.
- Expand the skill reference library for modern DiD, IV/weak-IV, RDD,
  table-export, data-MCP handoff, and parity audits.
- Add examples that show how agents should use the workflows without claiming
  unsupported automation.
- Add MCP prompts for parity audit planning, data-MCP-to-Stata handoff, and
  turnkey method templates for DiD/event study, IV/2SLS, RDD, and publication
  tables.
- Validate with skill packaging tests, MCP prompt tests, and markdown hygiene.

### Week 2: Diagnostics and Setup Confidence

- Ship a read-only `stata-code doctor` / `verify` command that reports Python,
  `stata-code`, MCP extras, `pystata` discovery, Stata version/edition, PATH
  resolution, and common client config hints.
- Keep config writing out of scope until backups and dry-run behavior exist.
- Add tests for missing `pystata`, missing MCP extra, path mismatch, and JSON
  output.

### Week 3: VS Code and Artifacts

- Improve dataset preview from first-100 text output toward a paged/filterable
  view or a clearly documented intermediate step.
- Surface table/export artifacts from run bundles more explicitly.
- Add tests around formatter and tree-provider behavior before broad UI work.

### Week 4: Release Quality

- Sweep README.md, README.zh.md, vscode/README.md, CHANGELOG.md, examples,
  and skill docs for drift.
- Run release-relevant checks: version guard, schema export, skill zip build,
  MCP tests, core tests that do not require Stata, and VS Code compile/tests if
  touched.
- Prepare release notes that separate shipped features from roadmap items.

## Success Criteria

- Agents can find a documented path for the top empirical workflows without
  loading the whole reference library.
- Parity audits preserve sample definitions, package versions, estimator
  defaults, failure/refusal behavior, and numeric tolerances.
- Data pulled by external MCP servers enters Stata through a reproducible raw
  file plus metadata handoff, not through unstated browser-copy steps.
- User-facing docs explain that `stata-code` runs Stata and coordinates with
  other MCP tools; they do not imply that it directly runs R/Python or hosts
  official data APIs.
- All changed surfaces have targeted validation evidence before handoff.
