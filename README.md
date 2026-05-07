# stata_code

> Agent-native Stata bridge — **one core, multiple frontends**.

`stata_code` lets you drive Stata from any modern environment — an LLM agent (Claude Code, Cursor, Claude Desktop), a Jupyter notebook, or (planned) a VSCode editor session — through a single Python core with a stable, **agent-friendly result schema**.

```text
                    ┌────────────────────────────────────────┐
                    │     stata_code core (Python)           │
                    │                                        │
                    │   • pystata adapter (Stata 17+)        │
                    │   • v1.0 unified result schema         │
                    │   • token-economy defaults             │
                    │   • multi-session via Stata frames     │
                    │   • typed errors + suggestions         │
                    └────────────────────────────────────────┘
                       ↑              ↑              ↑
              ┌────────┴────┐  ┌──────┴─────┐  ┌────┴────────────┐
              │  Jupyter    │  │  MCP       │  │  VSCode glue    │
              │  kernel     │  │  server    │  │  (planned)      │
              └─────────────┘  └────────────┘  └─────────────────┘
```

**Status: v0.2 (May 2026)** — core, MCP server, and Jupyter kernel are working end-to-end against Stata 18 MP. 144 tests passing (88 no-Stata + 56 real-Stata integration). License: **MIT**.

---

## Why this exists

The Stata tooling landscape is fragmented (see [References-tools.md](References-tools.md)):

- Existing MCP servers ([SepineTam/stata-mcp](https://github.com/sepinetam/stata-mcp), [tmonk/mcp-stata](https://github.com/tmonk/mcp-stata)) are **AGPL-3.0** — incompatible with closed-source / commercial integration.
- The most-used VSCode-AI extension ([hanlulong/stata-mcp](https://github.com/hanlulong/stata-mcp)) is MIT but **bundles** the MCP server inside the extension, making it awkward to use standalone.
- Each tool wraps `pystata` with its own ad-hoc result shape; agents have to special-case each one.
- Most existing tools were designed for humans first, then bolted onto MCP — they dump 200-line logs and base64 graph blobs in every reply, **burning agent tokens by default**.

`stata_code` fills exactly that gap:

1. **MIT-licensed**, no copyleft contagion.
2. **Single result schema** ([SCHEMA.md](SCHEMA.md)) shared by every frontend.
3. **Agent-native by default**: typed errors, structured `r()`/`e()`, log refs, graph refs, suggestion seeds.
4. **One core**, three frontends (kernel, MCP, planned VSCode glue) — none reinvents the wheel.

See [LICENSE-POLICY.md](LICENSE-POLICY.md) for the project's clean-room policy on AGPL/GPL Stata projects.

---

## Install

Requirements: **Stata 17+** (with `pystata` shipped) and **Python 3.10+**.

```bash
# from PyPI
pip install stata_code

# with the MCP server and Jupyter kernel extras
pip install "stata_code[mcp,kernel]"

# or from source (editable install for development)
git clone https://github.com/brycewang-stanford/stata_code.git
cd stata_code
pip install -e ".[mcp,kernel]"
```

`pystata` itself is **not** on PyPI — it ships with Stata. `stata_code` auto-discovers it on macOS at `/Applications/Stata/utilities/pystata` and at the equivalent paths on Linux / Windows. If your install is elsewhere, add it to `PYTHONPATH` before importing.

---

## Quick start

> See [`examples/`](examples/) for end-to-end cookbook entries (regression, DiD, graphs, multi-session, large matrices).

### As a Python library

```python
from stata_code import run

r = run("sysuse auto, clear")
r = run("regress mpg weight")

if r.ok:
    print(r.results.e.scalars["r2"])           # 0.6515 (native float)
    print(r.results.e.macros["cmd"])           # "regress"
    b = r.results.e.matrices["b"]
    print(dict(zip(b.cols, b.values[0])))      # {"weight": -0.006, "_cons": 39.44}
else:
    print(r.error.kind, r.error.message)       # ErrorKind.VARNAME_NOT_FOUND, "..."
    for s in r.error.suggestions:
        print("hint:", s.action)               # "Did you mean `mpg`?"
```

### As an MCP server (Claude Code, Cursor, Claude Desktop, …)

After install, `stata-code-mcp` is on your PATH. Add to your Claude Code config (`~/.claude/mcp.json` or via the Claude Code settings UI):

```json
{
  "mcpServers": {
    "stata": {
      "command": "stata-code-mcp"
    }
  }
}
```

Or run as a module:

```bash
python -m stata_code.mcp
```

Eight tools are registered:

| Tool | Purpose |
| --- | --- |
| `stata_run` | Execute Stata code; returns a v1.0 RunResult JSON |
| `stata_info` | Report installed Stata edition / version / capabilities |
| `get_log` | Fetch full log behind a `log://` ref |
| `get_graph` | Fetch graph bytes (`ImageContent`) behind a `graph://` ref |
| `get_matrix` | Fetch matrix `{rows, cols, values}` behind a `matrix://` ref |
| `list_sessions` | Enumerate live sessions |
| `cancel_session` | Cooperatively cancel the next `stata_run` for a session |
| `reset_session` | Drop a session's data |

### As a Jupyter kernel

```bash
stata-code-kernel install --user
```

Or run as a module:

```bash
python -m stata_code.kernel install --user
```

Then open a notebook and select the **Stata** kernel. Stata commands run in cells; logs, graphs, and warnings render inline.

---

## What you get (token-economy defaults)

A typical `stata_run` response is **~10x smaller** than what existing MCP servers send. Three design choices drive this:

1. **Logs return `head` + `tail` + `ref`** by default (default 20 lines each). Full log is fetched on demand via `get_log(ref)`. Stata's regression log is ~6,000 tokens; `stata_code` returns ~600 by default.

2. **Graphs return refs**, not inline base64. A 30 KB PNG ≈ 50,000 tokens base64. Returning a ref instead saves them all; the agent fetches the bytes only when it actually wants to render.

3. **Errors are typed**. Instead of `error: "variable mpgg not found\n r(111);\n"`, agents get:

    ```json
    {
      "ok": false,
      "rc": 111,
      "error": {
        "kind": "varname_not_found",
        "varname": "mpgg",
        "line": 3,
        "context": {"before": ["use auto"], "failing": "summarize mpgg", "after": []},
        "suggestions": [
          {"action": "Did you mean `mpg`?", "command": "describe"}
        ]
      }
    }
    ```

    Agents `if (err.kind == "varname_not_found")` instead of regex-ing English.

The full schema is in [SCHEMA.md](SCHEMA.md).

---

## Architecture

```text
stata_code/
├── core/
│   ├── _runtime.py    # process-singleton pystata wrapper
│   ├── _refs.py       # LRU ref store for log/graph/matrix payloads
│   ├── schema.py      # Pydantic v2 models for the v1.0 result schema
│   ├── errors.py      # rc → ErrorKind mapping + suggestion seeds
│   └── runner.py      # the one execute(); collects everything via sfi
├── mcp/
│   └── server.py      # MCP server (6 tools)
└── kernel/
    └── kernel.py      # Jupyter kernel
```

The runner is the only place that touches Stata. Both the Jupyter kernel and the MCP server import from it; they only translate transports.

---

## Comparison

| | stata_code | SepineTam/stata-mcp | hanlulong/stata-mcp | nbstata |
| --- | --- | --- | --- | --- |
| License | **MIT** | AGPL-3.0 | MIT | GPL-3.0 |
| Standalone MCP | ✓ | ✓ | bundled with VSCode | — |
| Jupyter kernel | ✓ | — | — | ✓ |
| Unified result schema | ✓ ([SCHEMA.md](SCHEMA.md)) | per-tool | per-tool | per-tool |
| Token-economy defaults | ✓ (log refs, graph refs) | — | — | — |
| Typed errors + suggestions | ✓ (32 kinds) | — | — | — |
| Multi-session | ✓ (Stata frames) | partial | — | — |
| Mature ecosystem | early | ✓ (statamcp.com, cookbook) | ✓ (11k installs) | ✓ |

`stata_code` is the **younger, MIT-licensed, agent-native** alternative for the same problem space. SepineTam's stata-mcp is the most polished of the AGPL options today; `stata_code` aims to occupy the seat where copyleft contagion is unacceptable.

---

## Roadmap

### Done (v0.2 — May 2026)

- v1.0 result schema (`SCHEMA.md`)
- pystata-based runner with native-typed `r()`, `e()`, matrices
- Multi-session via Stata frames
- Per-line error attribution (line number, context, commands_executed)
- Graph capture (`png` / `svg` / `pdf`) with ref store
- Log truncation with ref store
- Warning extraction (5 categories + generic notes)
- 32-kind error taxonomy with canonical suggestions
- MCP server (8 tools)
- Jupyter kernel (rewired to v1.0 pipeline)
- Matrix size cap + `get_matrix(ref)` for large matrices (>10k cells)
- Cooperative cancellation (`cancel(session_id)` / MCP `cancel_session`) — short-circuits the next `execute()` for a session
- JSON Schema artifact auto-generated from `schema.py`
  ([`schema/run_result.schema.json`](schema/run_result.schema.json))
- VSCode extension scaffold ([`vscode/`](vscode/)) — `Run Selection`, graph webview, MCP child-process spawn
- Clean-room license policy ([LICENSE-POLICY.md](LICENSE-POLICY.md))

### Next up

- **v0.3** — Console fallback for Stata 11–16 (re-implemented against the v1.0 schema)
- **v0.3** — Hard timeout / mid-Stata interrupt — design + tradeoffs in [`docs/design/hard_timeout.md`](docs/design/hard_timeout.md); requires a subprocess-based runtime, cooperative cancel exists but doesn't interrupt code already in-flight
- **v0.4** — VSCode Marketplace publishing (the scaffold + graph webview already work in dev host)
- **v1.0** — Stable schema, published to PyPI / VSCode Marketplace

See [SCHEMA.md §7](SCHEMA.md) for explicitly-out-of-scope items.

---

## Testing

```bash
pip install -e ".[dev,mcp,kernel]"
pytest                              # full suite (144 tests)
pytest -m "not stata_required"      # CI subset — no Stata needed
pytest -m "stata_required" -v       # Stata-only integration tests
```

The `stata_required` marker tags the integration tests; CI uses
`pytest -m "not stata_required"` so it doesn't even collect them.
Locally without Stata, those tests still skip cleanly with the
"pystata / Stata 17+ not available" message.

---

## Contributing

- Read [LICENSE-POLICY.md](LICENSE-POLICY.md) before opening a PR.
- Add a one-line acknowledgement to your first PR description (template in the policy file).
- Tests required for any new schema field or runner behavior.

---

## License

[MIT](./LICENSE) for the code. [LICENSE-POLICY.md](LICENSE-POLICY.md) governs how we relate to other Stata projects.

## Trademark Notice

Stata is a registered trademark of StataCorp LLC. This project is independent and not affiliated with or endorsed by StataCorp.

## Acknowledgements

The Stata tooling landscape that this project builds on (and learns from) is surveyed in [References-tools.md](References-tools.md). All listed projects retain their own licenses and authorship; please consult each repo before reuse.
