# Competitive Landscape & Long-Term Goals

Last updated: 2026-06-23

This document is the **evidence base** behind
[industry-leader-roadmap.md](industry-leader-roadmap.md). The roadmap says *what
we will build*; this file says *who else is in the market, where the open lane
is, and which long-term bets follow from that*. Keep the two in sync: when the
landscape shifts, update this file first, then re-derive the roadmap.

Star counts, install counts, and versions below are point-in-time reads from
June 2026 and will drift. Treat them as relative signal, not live data.

## North-star positioning

> `stata-code` should be the most **reliable, agent-native, typed** way to run,
> inspect, repair, and audit Stata — winning on *fidelity to the authoritative
> Stata runtime* and *referee-grade reproducibility*, not on method count or
> raw breadth.

The single fact that defines our lane: **no competitor ships all three of**

1. a typed **error taxonomy** (stable `error.kind` values an agent branches on,
   not return codes + red text it must string-match);
2. **typed `r()` / `e()` result contracts** (not a generic results dump); and
3. **token-efficient by-reference artifacts** for logs, graphs, *and* matrices.

Everything else is either AGPL/GPL-licensed, editor-bound, raw-log-only, or a
Python/R reimplementation that does not touch the real Stata runtime.

## The field (June 2026)

| Tool | License | Exec | Structured out | Typed error kinds | Frontends | Maturity |
| --- | --- | --- | --- | --- | --- | --- |
| **stata-code** (this) | MIT | pystata | typed `RunResult` + `r()/e()` | **yes** | MCP + kernel + VS Code | pre-1.0 |
| tmonk/mcp-stata + workbench | AGPL-3.0 | pystata | JSON + `r()/e()/s()` | no (rc + red text) | MCP + ext | ~69★, very active |
| hanlulong/stata-mcp | MIT | pystata | no (filtered raw log) | no | VS Code + MCP | ~440★, ~15.6k installs |
| SepineTam/mcp-for-stata | AGPL-3.0 | do-file subprocess | no (raw SMCL) | no | MCP + CLI (7+ agents) | ~204★, very active |
| haoyu-haoyu/stata-ai-fusion | MIT | pexpect | partial (`r()/e()/c()`) | no (text-flagged) | MCP + Skill + ext | ~32★, new |
| stata_kernel (kylebarron) | GPL-3.0 | automation/console | no (raw log) | no | Jupyter | ~278★ |
| nbstata (hugetim) | GPL-3.0 | pystata | no (raw log + widgets) | no | Jupyter | ~59★ |
| pystata (StataCorp) | proprietary | native | `r()/e()`→dict, mat→NumPy | no (Py exceptions) | IPython magics | ships w/ Stata |
| StatsPAI (sibling, Python) | MIT | Python reimpl. | yes (agent cards) | partial (validation) | MCP | ~244★, daily |

### Reading the table

- **The genuine head-to-head is `tmonk/mcp-stata`** — pystata-backed, MCP-native,
  returns `r()/e()`, ships a skills catalog, and has StataCorp-newsletter
  visibility. Its two gaps are our two wedges: **(a) no typed error taxonomy**
  (agents still parse return codes + preserved red text) and **(b) AGPL-3.0**,
  a hard blocker for commercial/embeddable adoption. MIT + typed kinds is the
  clean answer to both.
- **The adoption leader is `hanlulong/stata-mcp`** (~15.6k installs). Despite
  using pystata — which *could* expose stored results — it surfaces **filtered
  raw log text**, not a schema. Distribution, not schema quality, is its moat;
  we must not assume a better contract auto-wins installs.
- **Jupyter kernels and pystata itself are human-facing**, not agent-native:
  raw log + images, Python exceptions, no MCP, no by-reference economy.
- **The real *category* threat is the Python/R reimplementation wave**
  (StatsPAI, rmcp): they took the "first agent-native econometrics" framing.
  We do not beat them on method count — we beat them by being the authoritative
  Stata leg they themselves reach for when cross-validating.

## Why the error taxonomy is the defensible identity

A typed error taxonomy is the one capability **no competitor has** and the
**hardest to retrofit** onto a raw-log design. It is also cheap for us to lead
on because we already have the architecture (`error.kind`, `error.suggestions`,
`error.rc_label`, pinpoint context). The 2026-06-23 core pass made this concrete:

- audited `RC_TO_KIND` against StataCorp `[P] error` (Stata 19) and corrected
  multiple misclassifications (e.g. `not_sorted` is `r(5)`, not the unrelated
  `r(119)`/`r(459)`);
- populated `error.rc_label` with Stata's canonical short message via
  `label_for_rc()` (it was silently empty for every real error before);
- expanded `suggestions_for()` so nearly every common failure ships an
  actionable recovery hint.

The moat is not "we have error kinds" — it is "**our error kinds are correct,
labeled, and paired with a recovery action**, verified against the manual." That
is the kind of trust an empirical economist and a referee both need.

## Long-term goals (6–12 months), by leverage

Ranked. Each ties back to a roadmap pillar and is phrased as a durable outcome,
not a feature list. Status legend: ✅ shipped · 🟡 foundation shipped, stretch
remaining · ⬜ not started.

1. ✅ **Own "agent-native typed Stata errors" as the headline.** *Shipped
   2026-06-23.* `error.kind` is a stable, manual-verified contract (audited
   against `[P] error`); every classified rc ships a canonical `rc_label`
   (`label_for_rc`) and, where actionable, a remediation `suggestion`. The
   **agent recovery contract** (`error.recovery` / `recovery_for`) gives a
   defined next action per kind: retry-as-is, change-code, or escalate.
   → Roadmap pillar 1 (reliable execution contract).
2. ✅ **Per-command typed `r()/e()` result contracts for mandated commands.**
   *Shipped 2026-06-23.* `RunResult.results.estimation` is a typed coefficient
   table (term/b/se/statistic/p/CI + model_stats) from referee-grade `r(table)`
   (or `e(b)`/`e(V)` fallback). It now also carries `command_family`
   (ols/iv/gmm/panel/count/…) and command-aware `diagnostics` — the
   identification/spec tests economists must report (`ivreg2`/`ivreghdfe`
   weak-ID F + Hansen J, `xtabond2` AR(2)/Hansen, `reghdfe` within-R²/absorbed
   FE, `xtreg` rho), surfaced only when present in `e()`. This is the
   StatsPAI-defense: referee-grade numbers from the exact mandated command.
   → Roadmap pillar 2.
3. ✅ **Reproducibility / provenance envelope.** *Shipped 2026-06-23.*
   `build_provenance()` captures Stata version/edition, `e(cmd)`, stata-code +
   schema versions, timestamp, seed, and **per-package dependencies** parsed
   from the script (`ssc`/`net install` → `Provenance.packages`);
   `build_reproducible_do()` renders a `version`-pinned, seed-set re-runnable
   `.do`; and `build_submission_package()` assembles a replication/journal
   bundle (do + `PROVENANCE.json` + README manifest). *Stretch:* package
   *version* pinning (vs. name only) and a journal-specific layout.
   → Roadmap pillar 1 + 3.
4. ✅ **Data-MCP integration bridge** (FRED / World Bank / Census).
   *Shipped 2026-06-23.* `verify_dataset()` enforces the handoff's key checks
   (row/var counts, observation bounds, required columns) on the captured
   `DatasetInfo` — the executable companion to the `data-mcp-handoff` protocol,
   documented in `references/structured-results.md`. *Stretch:* first-class
   adapters that ferry source metadata (row hash, series ids) into the check
   automatically. **No Stata MCP has done this composition.** → Roadmap pillar 4.
5. 🟡 **Typed-schema-anchored skills catalog** — replication audits, robustness
   sweeps, publication QA, legacy `.do` modernization — each anchored to typed
   results + provenance, under MIT. *Foundation shipped:*
   `references/structured-results.md` teaches agents to consume the typed
   contracts (`results.estimation`/`diagnostics`, `error.recovery`/`rc_label`,
   reproducible-do / submission bundles, `verify_dataset`). *Stretch:* the
   audit/robustness/QA recipe set in the skills lane
   (`skills/stata-code/references/recipes/**`). → Roadmap pillar 2.

## Surface coverage & safety (shipped 2026-07-23)

The typed-contract lane (above) is the defensible identity; this cycle closed the
most-cited *surface* and *safety* gaps against SepineTam/hanlulong so "covers every
Stata usage scenario" is literally true, not aspirational.

- ✅ **Bash / plain-terminal surface.** `stata-code run` (a `.do` file, `-e`
  snippets, or stdin → `RunResult`, text or `--json`, exit-coded) puts the full
  structured loop behind any agent that can shell out — Codex CLI, CI, a bare
  terminal — without requiring MCP. Routes through the same subprocess pool, so
  timeout/cancel/policy all apply. `stata-code lint` exposes the linter too.
- ✅ **One-command onboarding.** `stata-code setup --claude|--cursor|--vscode|--all`
  writes the MCP server entry (merging, backing up, `--dry-run`), matching
  SepineTam's `install --all`. `doctor` stays read-only; `setup` is the opt-in
  mutating counterpart. Codex (TOML) / Claude Desktop are emitted as copy-paste
  snippets rather than edited in place.
- ✅ **Command-safety guard** (`core.policy`, `error.kind="policy_blocked"`).
  Blocks `shell` / `winexec` / `erase` / `rm` / `rmdir` / `!` before Stata runs,
  at both the pool boundary and the in-process runner; configurable via
  `STATA_CODE_COMMAND_POLICY` / `_ALLOW` / `_BLOCK`. Parity with SepineTam's
  27-rule guard for the autonomous-overnight use case. A guard rail, not a sandbox.
- ✅ **Static pre-run lint** (`core.lint`, MCP `lint_do`). Unbalanced braces,
  missing `end`, dangling `///` — caught Stata-free before a run is spent. A
  token-economy play no competitor ships.

## Closing the gaps vs. editor-first tools (shipped 2026-07-23)

Against `stata-all-in-one` (a polished, distributor-backed VS Code extension whose
"AI Skill" hands agents raw log text), this cycle neutralized its two adoption
advantages while keeping the typed-contract lane it cannot enter:

- ✅ **Console (batch) backend** (`core.console`, `Backend.CONSOLE`). Drives the
  Stata CLI in batch mode and parses the log into the same typed `RunResult`
  (typed `r()`/`e()`, estimation matrices, the error taxonomy). Covers **Stata
  13+ with no pystata** — removing their "we support 13+, you're pystata-only"
  edge, and unlike their AI Skill the output stays *structured*.
- ✅ **Zero-Python standalone binary** (`scripts/build_standalone.py` +
  `packaging/standalone.github-workflow.yml`). With `--backend console`, a Python-free path to typed
  results — answering their "ready out of the box, no Python" pitch.
- ✅ **One-click VS Code onboarding.** The extension provisions a workspace
  `.venv` + server on a single click / command, matching their frictionless install.
- ✅ **Reliability/token benchmark scaffold** (`benchmarks/`) — the apparatus to
  turn "typed beats raw-log" into a published number.

**Still open (next up), in priority order:**

1. ⬜ **Streaming / progress for long runs.** Real `boottest` / `csdid` jobs run
   for minutes; hanlulong streams, we batch. The schema already reserves
   `log.complete:false`. Highest-leverage remaining surface gap.
2. ⬜ **Hard timeout / cancel for the Jupyter kernel.** The kernel still uses the
   in-process runner (no preemption). Move it onto the subprocess pool or an
   equivalent, resolving the documented interactivity-vs-safety tradeoff.
3. ⬜ **Human IDE polish** to match `stata-all-in-one` where it also serves the
   agent loop: inline graph rendering + DPI export, a scalable data viewer, and an
   optional "attach to a running Stata" backend (shared live session).
4. ⬜ **Console backend depth** — graph capture and persistent sessions; run the
   benchmark for real and publish the numbers.

## Risks & threats

- **StataCorp native AI — LOW near-term, monitor.** Stata 19 shipped classical
  H2O ML only; no LLM/copilot/agent feature is shipped or announced, and
  StataCorp frames AI as community-built. Watch *New in Stata* / StataNow for any
  shift from tutorials to a shipped feature. Our durable hedge: we *require* a
  genuine Stata license, so our incentives align with StataCorp's rather than
  competing with them.
- **Distribution gap.** hanlulong already has ~15.6k installs and tmonk has
  newsletter visibility. A superior contract does not auto-win adoption; the
  typed-error / reproducibility story must reach economists where they are
  (SSC, Statalist, replication and referee communities).
- **Category framing already taken by Python/R.** "Agent-native econometrics
  with structured results + MCP" converged across StatsPAI and rmcp. Defend by
  positioning on **authoritative Stata-runtime fidelity**, not breadth.
- **Generic code-exec substitution.** A Jupyter-MCP + statsmodels sandbox can
  "do econometrics" with zero domain tooling. Defense: *mandated Stata commands +
  verifiable typed `e()` contracts* a generic sandbox cannot provide.
- **License contagion.** The two structured/MCP-native competitors (tmonk,
  SepineTam) are AGPL; the kernels are GPL. To preserve our MIT clean-room
  wedge we must never vendor their code paths (see `LICENSE-POLICY.md`).
- **Naming/trademark.** "Stata" is a StataCorp trademark and the `*stata-mcp`
  namespace is crowded. Keep the "not affiliated with StataCorp" disclaimer
  prominent and avoid implying endorsement.

## Sources

Competitor repos and listings (June 2026): tmonk/mcp-stata, tmonk/stata-workbench;
hanlulong/stata-mcp (VS Code Marketplace: DeepEcon.stata-mcp); SepineTam/mcp-for-stata
(PyPI `stata-mcp`); haoyu-haoyu/stata-ai-fusion; kylebarron/stata_kernel; hugetim/nbstata;
StataCorp pystata docs and *New in Stata 19*; brycewang-stanford/StatsPAI; finite-sample/rmcp;
data MCPs (datacommonsorg/agent-toolkit, stefanoamorelli/fred-mcp-server, worldbank/data360-mcp).
StataCorp `[P] error` (Stata 19, 2025) is the authoritative source for the error-code audit.
