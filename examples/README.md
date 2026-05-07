# `stata_code` Cookbook

This cookbook is for **agent developers** wiring `stata_code` into a host like Claude Code, Cursor, or Claude Desktop. Each entry walks through what an agent actually sees on the wire — the `stata_run` call it makes, the `RunResult` envelope (per [SCHEMA.md](../SCHEMA.md)) that comes back, and how the agent uses the auxiliary tools (`get_log`, `get_graph`, `get_matrix`, `list_sessions`) to fetch heavy payloads on demand rather than up front.

The recurring theme is **token economy**: by default the server returns a 20-line log head + 20-line tail + a `log://` ref, graphs as `graph://` refs (not base64), and large matrices as `matrix://` refs. Every example below highlights how those defaults compare to a "dump-everything" MCP server. Token estimates use the standard `~4 chars per token` heuristic and are labelled as such — they are illustrative, not measured.

| # | Example | What it demonstrates |
| --- | --- | --- |
| 01 | [Basic regression](./01-basic-regression.md) | Hello-world: one `stata_run` call, native-typed `e.scalars`, log-ref follow-up |
| 02 | [Differences-in-differences](./02-did-card-krueger.md) | Multi-turn workflow with a typed-error recovery moment (`varname_not_found`) |
| 03 | [Graphs](./03-graphs.md) | `graph://` refs vs. inline base64; `include_graphs="inline"` opt-in |
| 04 | [Multi-session](./04-multi-session.md) | Two parallel analyses via `session_id` (Stata frames under the hood) |
| 05 | [Large matrices](./05-large-matrix.md) | `matrix://` refs when a result exceeds the 10,000-cell inline cap |

Every Stata command shown is real syntax. Tool names, argument names, and response field names match `stata_code/mcp/server.py` and `SCHEMA.md` v1.0. Where a JSON response is abbreviated for readability, an inline comment marks what was cut.
