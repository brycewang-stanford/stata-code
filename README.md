# stata_code

> Unified Stata bridge for **Agents**, **VSCode**, and **Jupyter** — one core, multiple frontends.

`stata_code` aims to be the single entry point for programmatically driving Stata from any modern environment: an LLM agent, a VSCode editor session, or a Jupyter notebook. Rather than maintaining three separate tools that each reimplement the same Stata-communication layer, `stata_code` provides one well-tested core engine and thin, focused frontends on top of it.

## Why

The current Stata tooling ecosystem is fragmented:

| Tool | Frontend | Communication | Status |
| --- | --- | --- | --- |
| [Stata Enhanced](https://github.com/kylebarron/stata-enhanced) | VSCode | — (highlighting only) | Maintained |
| [stata-vscode](https://github.com/kylebarron/stata-vscode) | VSCode | Automation API / AppleScript | Legacy |
| [stata_kernel](https://github.com/kylebarron/stata_kernel) | Jupyter | `pexpect` + console mode | Maintained |
| [nbstata](https://github.com/hugetim/nbstata) | Jupyter | `pystata` | Maintained |
| [stata-mcp](https://github.com/sepinetam/stata-mcp) | LLM agents | MCP protocol | New |

Each is a silo. None covers the full surface, and users who switch contexts have to learn a new tool every time. `stata_code` consolidates the surface while keeping each frontend lightweight.

## Architecture

```text
                   ┌────────────────────────────────────┐
                   │       stata_code-core (Python)     │
                   │                                    │
                   │   • pystata (preferred, Stata 17+) │
                   │   • console fallback (older Stata) │
                   │   • unified result schema          │
                   │   • graph capture & streaming log  │
                   └────────────────────────────────────┘
                       ↑              ↑              ↑
              ┌────────┴────┐  ┌──────┴─────┐  ┌─────┴───────┐
              │  Jupyter    │  │  VSCode    │  │  MCP server │
              │  kernel     │  │  extension │  │  (for LLMs) │
              └─────────────┘  └────────────┘  └─────────────┘
```

The core is intentionally Python so it can be embedded directly into a Jupyter kernel and an MCP server, and called as a subprocess from the VSCode TypeScript extension.

## Goals

1. **One install, three frontends.** Users install `stata_code` once and pick their surface.
2. **`pystata`-first.** Use the official Stata Python API where available; fall back to console only when the user is on older Stata.
3. **Consistent result schema.** Every frontend gets the same `{stdout, log, results, graphs, error}` shape — no per-frontend special cases.
4. **Stata version detection.** Auto-detect installed Stata edition (MP/SE/IC/BE) and version, surface it to the caller.
5. **Graph round-trip.** Capture `.gph` / `.svg` / `.png` outputs and ship them to whichever frontend asked.

## Non-goals

- Replacing the Stata GUI. This is a programmatic bridge, not an editor.
- Bundling Stata. Users must have a licensed local Stata install.
- Reimplementing syntax highlighting (Stata Enhanced already does this well).

## Roadmap

- [ ] **v0.1** — Core: `pystata` adapter, result schema, version detection
- [ ] **v0.2** — Jupyter kernel (built on the core)
- [ ] **v0.3** — MCP server (built on the core)
- [ ] **v0.4** — VSCode extension (TypeScript, shells out to core)
- [ ] **v0.5** — Console fallback for Stata 16 and earlier
- [ ] **v1.0** — Stable schema, published to PyPI / VSCode Marketplace / npm

## Requirements

- Python 3.10+
- Stata 17+ (for `pystata` path) or Stata 11+ (for console fallback)
- A valid local Stata license

## Status

Early scaffolding. Architecture and roadmap are settled; implementation has not yet started. Issues and design discussions welcome.

## License

[MIT](./LICENSE)

## Acknowledgements

Built on the shoulders of [stata_kernel](https://github.com/kylebarron/stata_kernel), [nbstata](https://github.com/hugetim/nbstata), [stata-vscode](https://github.com/kylebarron/stata-vscode), and [stata-mcp](https://github.com/sepinetam/stata-mcp). This project is an integration effort, not a replacement.
