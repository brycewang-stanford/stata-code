# References — Existing Stata Tooling Landscape

> A survey of the tools `stata_code` integrates, learns from, and aims to consolidate.

This document catalogues the current Stata tooling ecosystem across editors, notebooks, and AI agents. For each tool we record what it does, how it communicates with Stata, its maintenance status, and where it leaves gaps that `stata_code` can fill.

All metrics captured **April 2026**. Star counts and "last pushed" dates may drift; see linked repos for current state.

---

## Quick Reference Table

| Category | Tool | Stars / Installs | Last Active | License | Stata Comm. |
| --- | --- | --- | --- | --- | --- |
| VSCode (syntax) | [kylebarron.stata-enhanced](https://marketplace.visualstudio.com/items?itemName=kylebarron.stata-enhanced) | 62k installs | — | MIT | none |
| VSCode (MCP + run) | [hanlulong/stata-mcp](https://github.com/hanlulong/stata-mcp) | 323★ / 11k installs | 2026-04 | MIT | pystata |
| VSCode (run + cells) | [kylebutts/vscode-stata](https://github.com/kylebutts/vscode-stata) | 57★ | 2025-06 | MIT | nbstata + AppleScript |
| VSCode (MCP + run) | [tmonk/stata-workbench](https://github.com/tmonk/stata-workbench) | 28★ | 2026-02 | AGPL-3.0 | mcp-stata |
| VSCode (all-in-one) | [ZihaoVistonWang.stata-all-in-one](https://marketplace.visualstudio.com/items?itemName=ZihaoVistonWang.stata-all-in-one) | ~1k installs | — | — | AppleScript / PowerShell |
| VSCode (legacy run) | [amichuda/stataRun](https://github.com/amichuda/stataRun) | 0★ | 2019-01 | — | AppleScript |
| Jupyter kernel | [kylebarron/stata_kernel](https://github.com/kylebarron/stata_kernel) | 278★ | 2026-04 | GPL-3.0 | pexpect / console |
| Jupyter kernel | [hugetim/nbstata](https://github.com/hugetim/nbstata) | 55★ | 2026-01 | GPL-3.0 | pystata |
| Jupyter magic | [TiesdeKok/ipystata](https://github.com/TiesdeKok/ipystata) | 197★ | 2023-09 | — | console |
| MCP server | [SepineTam/stata-mcp](https://github.com/SepineTam/stata-mcp) | 153★ | 2026-04 | AGPL-3.0 | pystata |
| MCP server | [tmonk/mcp-stata](https://github.com/tmonk/mcp-stata) | 54★ | 2026-03 | AGPL-3.0 | pystata |
| Claude Code skill | [dylantmoore/stata-skill](https://github.com/dylantmoore/stata-skill) | 188★ | 2026-04 | Other | (knowledge only) |
| Language server | [euglevi/stata-language-server](https://github.com/euglevi/stata-language-server) | 4★ | 2025-05 | MIT | none |
| Atom (run) | [kylebarron/stata-exec](https://github.com/kylebarron/stata-exec) | 32★ | 2023-03 | MIT | AppleScript / X11 |
| Atom (syntax) | [kylebarron/language-stata](https://github.com/kylebarron/language-stata) | 48★ | 2022-08 | MIT | none |
| Sublime | [mattiasnordin/StataEditor](https://github.com/mattiasnordin/StataEditor) | 48★ | 2022-02 | — | AppleScript |
| Sublime (macOS) | [zizhongyan/StataImproved](https://github.com/zizhongyan/StataImproved) | 49★ | 2026-02 | MIT | AppleScript |
| Python library | [pystata](https://www.stata.com/python/pystata18/) (official) | — | shipped with Stata | proprietary | in-process |
| R bridge | [lbraglia/RStata](https://github.com/lbraglia/RStata) | 117★ | 2025-03 | — | subprocess |

---

## A. VSCode Extensions

VSCode is now the dominant editor for empirical research. Extensions span three generations: pure syntax → AppleScript-based runners → MCP-powered AI integrations.

### A.1 Stata Enhanced (`kylebarron.stata-enhanced`)

- **Marketplace**: 62,401 installs — the de facto baseline syntax highlighter for Stata in VSCode
- **What it does**: Syntax highlighting only. Supports `.do`, `.ado`, system commands, functions, macros, regex, and dynamic Markdown/LaTeX
- **Communication**: None — does not run code
- **Maintenance**: Quiet but stable; the grammar is forked into many downstream extensions
- **Pros**:
  - Highest install count of any Stata-related VSCode extension
  - Grammar is permissively licensed and widely reused
  - Zero dependencies — pure TextMate grammar
- **Cons**:
  - No execution, no language server, no AI integration
  - Hasn't been substantively updated in a while
- **Positioning**: The "minimal viable" syntax layer. Most other VSCode extensions either depend on or duplicate its grammar.

### A.2 Stata MCP — DeepEcon / `hanlulong/stata-mcp`

- **GitHub**: 323★ · **Marketplace**: 11,036 installs · **License**: MIT · **Last pushed**: 2026-04
- **What it does**: Full-featured VSCode/Cursor/Antigravity extension. Runs `.do` files, exposes results in an editor panel, and **simultaneously** runs an MCP server on `localhost:4000` so AI assistants (Copilot, Claude Code, Cursor AI, Codex, Cline) can drive Stata
- **Communication**: `pystata` for execution; built-in MCP server speaks streamable HTTP + SSE
- **Pros**:
  - Most popular Stata-AI integration today; legitimate AI-native UX
  - Multi-session parallel execution
  - Auto-installs `uv` and Python deps; one-click setup
  - Status bar UX, keyboard shortcuts, graph webview
  - Cross-IDE: VS Code + Cursor + Antigravity from a single codebase
- **Cons**:
  - Bundles editor and MCP concerns together — hard to use the MCP server standalone (e.g., from Claude Desktop without VSCode)
  - Stata 17+ only (pystata requirement)
  - Heavy install footprint (auto-installs uv, then a Python venv)
- **Positioning**: The clear leader for **VSCode + AI** users. The reference design for the "editor + MCP in one" pattern.

### A.3 `kylebutts/vscode-stata`

- **GitHub**: 57★ · **License**: MIT · **Last pushed**: 2025-06
- **What it does**: Combines three upstream projects into one extension: Kyle Barron's syntax grammar + `poidstotal/stataRun`'s send-to-Stata commands + an "interactive window" backed by `nbstata`
- **Communication**: `nbstata` (pystata) for the interactive window; AppleScript / etc. for "send to Stata"
- **Pros**:
  - Cell-based workflow (`* %%` markers) — the closest VSCode equivalent to Jupyter cells
  - Reuses well-tested upstream pieces rather than reinventing
  - Honest credit to upstream authors
- **Cons**:
  - Smaller user base than DeepEcon's extension
  - "Send to Stata" still relies on AppleScript-style automation on macOS — fragile
- **Positioning**: The "researcher who wants Jupyter-cell ergonomics inside a `.do` file" choice.

### A.4 `tmonk/stata-workbench`

- **GitHub**: 28★ · **License**: AGPL-3.0 · **Last pushed**: 2026-02
- **What it does**: VSCode/Cursor/Windsurf/Antigravity extension that runs Stata code from the editor and is powered by `tmonk/mcp-stata`. Designed so AI agents can also drive Stata through the same backend
- **Communication**: `mcp-stata` (pystata-based)
- **Pros**:
  - Cleaner separation than `hanlulong`'s — the MCP server (`mcp-stata`) is a standalone project that the workbench wraps
  - Persistent Stata session; can retrieve `r()` / `e()` results and graphs
- **Cons**:
  - Smaller user base; less polish than DeepEcon's
  - AGPL-3.0 — copyleft restricts commercial reuse
- **Positioning**: The cleanest "MCP-first" architecture among VSCode extensions. Worth studying as an architectural reference.

### A.5 Stata All in One (`ZihaoVistonWang.stata-all-in-one`)

- **Marketplace**: ~1,045 installs · **License**: not stated
- **What it does**: Syntax highlighting (built on Stata Enhanced grammar) + smart outline + section/line/selection execution + variable rename + help lookup
- **Communication**: AppleScript on macOS, PowerShell on Windows
- **Pros**:
  - Multi-level outline support (up to 6 header levels) — strong navigation UX
  - Hierarchical section execution (run from header to next equal/higher header)
- **Cons**:
  - No AI / MCP integration
  - Linux unsupported
  - PowerShell automation has timing-sensitive delays
- **Positioning**: Productivity-focused for solo Stata users on macOS/Windows — but skips the AI layer.

### A.6 stataRun (`Yeaoh.stataRun` / `amichuda/stataRun`)

- **Marketplace**: small · **GitHub** (`amichuda`): 0★ · **Last pushed**: 2019
- **What it does**: Minimal "send code to Stata" via AppleScript
- **Pros**: Very small surface; easy to read
- **Cons**: Effectively unmaintained; no longer competitive with the MCP-based options
- **Positioning**: Historical / educational interest only. Most of its design ideas live on inside `kylebutts/vscode-stata`.

---

## B. Jupyter Integrations

Two distinct generations: **subprocess-based** (works with Stata 11+, slower, fragile) and **`pystata`-based** (Stata 17+ only, in-process, fast).

### B.1 `kylebarron/stata_kernel`

- **GitHub**: 278★ · **License**: GPL-3.0 · **Last pushed**: 2026-04
- **What it does**: Full Jupyter kernel for Stata. Works with Stata 11+ on Windows/macOS/Linux
- **Communication**: `pexpect` against Stata's console mode; on Windows uses Automation API
- **Pros**:
  - Most-used Jupyter Stata kernel by a wide margin
  - Works with **older Stata versions** (no `pystata` requirement)
  - Cross-platform with thoughtful per-OS handling
  - Mature feature set: autocomplete, magics, graph display, cache
- **Cons**:
  - Console-driven I/O is inherently slower and more fragile than in-process pystata
  - GPL-3.0 viral
  - Newer features (DataGrid widget, Quarto inline code) live in nbstata, not here
- **Positioning**: The default choice if you have **Stata < 17** or are paranoid about pystata compatibility.

### B.2 `hugetim/nbstata`

- **GitHub**: 55★ · **License**: GPL-3.0 · **Last pushed**: 2026-01
- **What it does**: Modern Jupyter kernel built on top of `pystata`. Works with Stata 17+ only
- **Communication**: `pystata` (in-process)
- **Pros**:
  - Cleaner, faster execution than `stata_kernel`
  - DataGrid widget with `browse`-like interactive filtering
  - Variable / data property side panel
  - Quarto inline code support
  - Auto-completion, rich text help, `#delimit ;` support
  - Compatible with NBClassic, JupyterLab, VS Code, Quarto
- **Cons**:
  - Stata 17+ hard requirement
  - Smaller user base than `stata_kernel`
  - GPL-3.0
- **Positioning**: The **modern default** for Jupyter + Stata. `kylebutts/vscode-stata`'s interactive window is built on it. If you're on Stata 17+, this is what you want.

### B.3 `TiesdeKok/ipystata`

- **GitHub**: 197★ · **License**: not stated · **Last pushed**: 2023-09
- **What it does**: IPython magic (`%%stata`) for embedding Stata cells inside a Python notebook — not a standalone kernel
- **Communication**: subprocess (older approach)
- **Pros**:
  - Lets you mix Python and Stata cells in one notebook — useful for hybrid workflows
  - Lower setup overhead than running a full kernel
- **Cons**:
  - Not actively maintained (last push 2023-09)
  - Mostly superseded by `pystata`'s own IPython magic, which is bundled with Stata 17+
- **Positioning**: Historical — the original "Stata in Python notebooks" idea. Today you'd just use `pystata`'s built-in magic.

### B.4 JupyterLab Syntax Extensions

- [`kylebarron/jupyterlab-stata-highlight`](https://github.com/kylebarron/jupyterlab-stata-highlight) (7★, 2023) — original
- [`hugetim/jupyterlab_stata_highlight2`](https://github.com/hugetim/jupyterlab_stata_highlight2) (4★, 2023) — IDE-style colors
- [`lutherbu/jupyterlab_stata_highlight3`](https://github.com/lutherbu/jupyterlab_stata_highlight3) (2★, 2025) — JupyterLab 4+
- [`ticoneva/codemirror-legacy-stata`](https://github.com/ticoneva/codemirror-legacy-stata) (3★, 2024) — JupyterLab 4 fix

**Positioning**: Small, single-purpose extensions. The fragmentation (3+ near-identical projects across JupyterLab versions) is itself evidence that this layer needs consolidation.

---

## C. MCP Servers (LLM / Agent Integration)

The newest category. As of April 2026 there are at least four serious projects, plus several tiny experiments. The space is fragmenting fast.

### C.1 `SepineTam/stata-mcp`

- **GitHub**: 153★ · **License**: AGPL-3.0 · **Last pushed**: 2026-04 · **PyPI**: `stata-mcp`
- **What it does**: Dedicated MCP server for Stata, plus an "agent mode" CLI and a Claude Code plugin (with companion `stata-language-server`)
- **Communication**: `pystata`
- **Pros**:
  - Cleanest "MCP server only" packaging — installable via `uvx stata-mcp`
  - Official Claude Code plugin (`claude plugin marketplace add sepinetam/stata-mcp`)
  - Agent mode for autonomous Stata workflows
  - Active dev, has its own website (`statamcp.com`), datasets (STOP), and ecosystem (DtaDock, cookbook, SDK)
  - Multilingual (English + Chinese docs)
- **Cons**:
  - AGPL-3.0 — viral copyleft restricts who can build on top
  - Requires Stata 17+ (pystata)
  - Has had attribution issues with copycat forks (DMCA takedown noted in README)
- **Positioning**: The **standalone MCP server** of choice. If you want to plug Stata into Claude Desktop or any non-VSCode MCP client, start here.

### C.2 `hanlulong/stata-mcp`

See **A.2**. Bundles an MCP server inside a VSCode extension. Higher install count than SepineTam but harder to use the MCP component standalone.

### C.3 `tmonk/mcp-stata`

- **GitHub**: 54★ · **License**: AGPL-3.0 · **Last pushed**: 2026-03
- **What it does**: Lightweight MCP server exposing a **persistent** Stata/MP session. Tools to execute commands, inspect data, retrieve `r()`/`e()` stored results, and view graphs
- **Communication**: `pystata` (persistent session)
- **Pros**:
  - Smallest, most focused codebase of the three MCP servers
  - Persistent session is useful for stateful empirical workflows
  - Designed to power `tmonk/stata-workbench` (clean separation of concerns)
- **Cons**:
  - Smaller community, fewer integrations
  - AGPL-3.0
- **Positioning**: A tight, well-designed MCP server. The architectural model (server + thin VSCode wrapper) is what `stata_code` should emulate.

### C.4 `dylantmoore/stata-skill` (Claude Code Skill, not MCP)

- **GitHub**: 188★ · **License**: Other · **Last pushed**: 2026-04
- **What it does**: Not a server — a **knowledge skill** that teaches Claude how to write idiomatic Stata code (37 reference files, 20 community packages, plus a "Stata C plugins" sub-skill)
- **Pros**:
  - Complementary to MCP servers — improves the *quality* of the Stata code an LLM writes, before any execution
  - Covers community packages MCP servers don't know about
- **Cons**:
  - Knowledge only; doesn't run code
  - Claude Code-specific (skill format)
- **Positioning**: A **horizontal** addition to any MCP server. Pair this with one of the execution-capable MCPs above for the best agent experience.

### C.5 Smaller MCP variants

- [`shichengg/stata-mcp`](https://github.com/shichengg/stata-mcp) — 4★, fork-style
- [`menwchen/stata-mcp`](https://github.com/menwchen/stata-mcp) — 0★, persistent-session approach
- [`mhjung0822/stata_mcp-releases`](https://github.com/mhjung0822/stata_mcp-releases) — Java MCP server (release artifacts only)
- [`mkprevo/stata-mcp-server`](https://github.com/mkprevo/stata-mcp-server) — JS, do-file focused

These are mostly experiments. Listed for completeness.

---

## D. Other Editors

### D.1 Atom

- [`kylebarron/language-stata`](https://github.com/kylebarron/language-stata) (48★) — syntax grammar (the upstream that VSCode extensions reuse)
- [`kylebarron/stata-exec`](https://github.com/kylebarron/stata-exec) (32★) — runner via AppleScript / Win32 / X11 automation

**Positioning**: Atom itself is sunset (2022). These projects survive because their grammar/code is reused downstream.

### D.2 Sublime Text

- [`mattiasnordin/StataEditor`](https://github.com/mattiasnordin/StataEditor) (48★, 2022) — syntax + execution
- [`zizhongyan/StataImproved`](https://github.com/zizhongyan/StataImproved) (49★, 2026-02) — actively maintained macOS-focused fork
- [`kylebarron/SublimeStataEnhanced`](https://github.com/kylebarron/SublimeStataEnhanced) (legacy) — original
- [`docsteveharris/stata`](https://github.com/docsteveharris/stata) (12★, 2013) — historical

**Positioning**: A long-tail community for Sublime users. Nothing AI-aware here.

### D.3 Vim / Emacs

- Vim has informal syntax files and `vim-stata`-style plugins, none with significant adoption
- Emacs ESS (`ess-stata-mode`) ships Stata support out of the box for Emacs users — a stable but niche choice

---

## E. Python ↔ Stata Libraries

### E.1 `pystata` (official)

- **Vendor**: StataCorp · **License**: proprietary (ships with Stata) · **Stata version**: 17+
- **What it does**: Official Python ↔ Stata bridge. Two interfaces:
  1. IPython magic (`%%stata`, `%stata`) — works in Jupyter, JupyterLab, Spyder, PyCharm
  2. Python API (`stata.run()`, `stata.pdataframe_*`, frames, Mata) — works anywhere Python runs
- **Pros**:
  - In-process (no subprocess) → fast, no I/O fragility
  - Officially supported by StataCorp
  - Handles graphs, frames, Mata cleanly
  - Foundation for `nbstata`, `SepineTam/stata-mcp`, `tmonk/mcp-stata`, `hanlulong/stata-mcp`
- **Cons**:
  - Requires Stata 17+ and a local install
  - Closed-source (you can read the docs but can't extend the bindings)
- **Positioning**: **The substrate.** Every modern Stata-Python tool is built on this. `stata_code` will be too.

### E.2 `TiesdeKok/ipystata`

See B.3. Older; superseded by `pystata`'s built-in magic.

---

## F. Other Language Bridges

### F.1 `lbraglia/RStata`

- **GitHub**: 117★ · **CRAN**: yes · **Last pushed**: 2025-03
- **What it does**: R package wrapping a Stata subprocess. Lets R users send Stata code, retrieve console output, and pass data frames back and forth
- **Communication**: subprocess (Stata batch mode)
- **Pros**:
  - The standard R ↔ Stata bridge
  - Handles `.dta` round-tripping
- **Cons**:
  - Subprocess overhead per call
  - R-only; doesn't help Python/JS users
- **Positioning**: Out of scope for `stata_code` directly, but worth knowing as a design reference for "thin language bridge over subprocess."

---

## G. Language Server / Tooling

### G.1 `euglevi/stata-language-server`

- **GitHub**: 4★ · **License**: MIT · **Last pushed**: 2025-05
- **What it does**: LSP server for Stata, built on `pygls`. Auto-completion, code style checking
- **Pros**: Standards-based (LSP); editor-agnostic in principle
- **Cons**: Tiny user base; LSP coverage is incomplete
- **Positioning**: An early-stage attempt at proper IDE-style intelligence for Stata. `SepineTam/stata-mcp`'s Claude Code plugin already integrates it.

### G.2 Honorable mentions

- **`stata-syntax`** (TextMate-style grammars) — reused everywhere
- **DAP (debugger)** — no real Stata DAP server exists yet; gap in the ecosystem

---

## H. Where the Gaps Are (Why `stata_code` Exists)

Reading across this landscape, three structural gaps stand out:

1. **MCP servers and editor extensions are tightly coupled.** `hanlulong/stata-mcp` ships its MCP server inside a VSCode extension; using the MCP standalone is awkward. `tmonk` separates them but is small. `SepineTam` separates them but doesn't ship an editor frontend. **No project today gives you "one core, multiple equally-supported frontends."**

2. **Jupyter and VSCode reinvent the same wheel.** `nbstata` and the VSCode extensions both wrap `pystata` with their own result schemas, graph handling, and version detection. A user who switches contexts loses everything.

3. **No unified result schema.** Every tool returns Stata output in its own shape — some give you the log, some give you `r()`/`e()`, some give you graphs as base64, some as files. An agent (or a developer writing tooling on top) has to special-case each one.

`stata_code` aims to occupy exactly the seat none of these tools occupy: **a single Python core with a stable result schema, exposed identically through a Jupyter kernel, a VSCode extension, and an MCP server.**

---

## License of this document

This file is part of the [stata_code](https://github.com/brycewang-stanford/stata_code) project and is released under the same [MIT license](./LICENSE) as the rest of the repository. All linked projects retain their own licenses; please consult each repo before reusing code.
