# License Policy

`stata_code` is released under the **MIT License**. To keep the codebase legally clean and freely usable downstream (including by commercial and closed-source projects), this repository follows a strict **protocol-first, clean-room** development policy. This document is the binding policy; contributors must read it before opening a pull request.

---

## 1. Project license

- **License:** MIT (see `LICENSE`).
- **Goal:** Anyone — including commercial and closed-source projects — can integrate, fork, or redistribute `stata_code` without copyleft obligations.

This goal is incompatible with deriving from AGPL-3.0 / GPL-3.0 source code. The rules below exist to prevent that.

---

## 2. The three categories of references

Every external project relevant to `stata_code` falls into one of three buckets:

### 2.1 Open standards & vendor docs (always allowed)

These define **public protocols and APIs**. Reading them, citing them, and implementing against them does not contaminate our code.

- **Anthropic MCP specification** — protocol shape, message formats, tool registration semantics.
- **Jupyter kernel protocol** — `kernel_info`, `execute_request`, message routing.
- **Language Server Protocol (LSP)** — for any future LSP work.
- **StataCorp pystata documentation** — official Python API surface.
- **StataCorp Stata documentation** (`help`, manuals) — `r()`, `e()`, `_rc`, system values.
- **Stata `.dta` file format documentation** — published by StataCorp.
- **Anthropic / OpenAI tool-use docs** — function-calling shapes.

### 2.2 Permissively-licensed projects (allowed with attribution if reused)

MIT, BSD, Apache 2.0, ISC. Reading source is allowed; copying must follow the license terms (preserve copyright notice, etc.). Even when allowed, we **prefer independent implementation** to keep authorship clean.

- `kylebarron/stata-enhanced` — MIT (TextMate grammar; we do not reuse it).
- `kylebarron/stata-exec` — MIT (Atom; not reused).
- `kylebarron/language-stata` — MIT (Atom grammar; not reused).
- `hanlulong/stata-mcp` — MIT (we do not consult its source; see §4).
- `lbraglia/RStata` — design reference only.
- `euglevi/stata-language-server` — MIT.

### 2.3 Copyleft projects (source code forbidden)

**Source code of these projects must not be read by anyone contributing to `stata_code`.** Their READMEs, public issues, demos, screenshots, and documentation describing user-facing behavior are fine — copyright protects expression, not ideas. But the source itself contaminates.

- `SepineTam/stata-mcp` — AGPL-3.0
- `tmonk/mcp-stata` — AGPL-3.0
- `tmonk/stata-workbench` — AGPL-3.0
- `kylebarron/stata_kernel` — GPL-3.0
- `hugetim/nbstata` — GPL-3.0

If new copyleft Stata projects appear, add them here in the same PR that first references them.

---

## 3. The clean-room rule

When designing or implementing any feature that overlaps with a copyleft project's behavior:

1. **Do not open the copyleft project's source files.** Not in a browser, not in `git clone`, not in an IDE.
2. **You may** read its README, feature list, screenshots, public issues, blog posts, and conference talks describing what it does.
3. **You may** read the underlying public protocol or API spec (MCP, pystata, etc.) and implement against that.
4. **You may** look at the inputs and outputs (call its tools, observe responses) — black-box behavioral observation is fine.
5. **Design from first principles.** Our schema (`SCHEMA.md`) was designed from agent-token-economy principles and the public pystata API. It was not derived by simplifying or rearranging anyone else's schema.

If you find yourself thinking *"how does project X handle Y?"*, the answer is: read its docs and observe its behavior. Do not open its source.

---

## 4. If you accidentally read forbidden source

It happens. Honesty is the only safe response.

1. **Stop reading immediately.** Close the file.
2. **Disclose in the PR or issue.** Note what you read and approximately how much.
3. **Wait at least 30 days** before contributing code in the affected area. If the area is small (one function), a fresh contributor implements it. If broad, that contributor sits out the area indefinitely.
4. **Do not** quote, paraphrase, or rewrite from memory.

This is the same posture used by clean-room reverse-engineering teams. It is conservative on purpose.

---

## 5. Adding a new reference

When introducing any new external project to documentation, code, or discussion:

1. Add it to one of the three lists in §2 of this file in the same PR.
2. State its license explicitly (check `LICENSE` file, not `package.json`/`README` — those drift).
3. If copyleft, the PR must not include any code; only the bucket-3 listing.

Reviewers should reject PRs that mention an external project without classifying it.

---

## 6. Dependencies vs. derivation

Note the difference:

- **Depending** on an MIT/BSD/Apache library at runtime is fine and does not contaminate.
- **Depending** on a GPL/AGPL library at runtime *does* contaminate the distributed package; we don't do that for any package we ship under MIT.
- **Depending** on a GPL/AGPL library only in a separate, GPL-licensed sub-package (e.g., `stata-code-jupyter-glue`) is acceptable as long as the MIT core does not import it. Any such split must be called out at the top of the README and in `pyproject.toml`.

---

## 7. Why this matters

Stata is a small ecosystem with active and vigilant maintainers, several of whom have publicly enforced their AGPL terms. A clean license posture:

- Keeps `stata_code` usable by any downstream — universities, central banks, commercial vendors.
- Prevents "rip-off" accusations that have already been levied at fork-style projects in the space.
- Makes future fundraising, hiring, and acquisitions trivial on the IP side.
- Protects contributors personally — clean-room compliance is auditable.

The cost of this policy is small (some independent design work). The cost of getting it wrong is irreversible: a contaminated codebase cannot be "scrubbed" of AGPL after the fact; only rewritten from scratch by uncontaminated authors.

---

## 8. Acknowledgement on first contribution

Every first-time contributor to `stata_code` adds the following line to their first PR description:

> I have read `LICENSE-POLICY.md` and confirm I have not consulted source code from the copyleft projects listed therein for the purposes of this contribution.

Maintainers may decline contributions without this acknowledgement.
