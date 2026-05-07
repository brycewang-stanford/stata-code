# Design note: hard timeout / mid-Stata interrupt

> Status: **deferred** — not implemented in v0.2/early-v0.3. This document
> exists so the next person picking it up has the constraints written
> down. After this lands, `cooperative cancellation` (shipping in this
> push) covers the "abort the next call" case; the gap is killing a
> Stata command that is already executing.

## The constraint

`stata_code` runs Stata via **pystata, in-process**. The runner imports
`pystata.config`, calls `pystata.config.init(edition)`, then submits
code through `stata.run(code)`. That call is a synchronous, blocking C
call into Stata's runtime. From Python's perspective, the interpreter
is **inside** Stata until Stata returns control.

There is **no public pystata API** to:

- Cancel a running command (no `interrupt()` / `cancel()` / similar).
- Kill the call from another thread cleanly. Stata's signal handling is
  installed by `pystata.config.init`; sending `SIGINT` to the Python
  process during a `stata.run()` call has unpredictable results in our
  testing (sometimes raises, sometimes hangs the embedded interpreter
  to the point that no further Stata commands work).
- Run multiple Stata "sessions" in one Python process. `pystata.config`
  is process-wide; a second `init()` is rejected. Frames give us
  data isolation, not control isolation.

So `timeout_ms` cannot be enforced in the current architecture.
`cancel(session_id)` (cooperative) catches the case where the agent
hasn't yet submitted the next command, but if the agent already issued
`bootstrap, reps(10000): regress …` and is mid-flight, nothing on the
Python side can do about it short of process-level termination.

## Why this matters

The token-economy story collapses if a single bad command can hang the
MCP server forever. An agent that sends `while 1 { display "" }` (or a
genuine `bootstrap` over a giant dataset) needs the producer to bound
the call and return a `RunResult` with `error.kind="timeout"` so the
agent can recover. SCHEMA.md §3.7 already reserves the `timeout` kind
and a synthetic `rc: -2` for exactly this case.

## Options considered

### Option A — Subprocess pystata worker (recommended)

Move pystata out of the main Python process. One persistent worker
subprocess per session, with a small JSON-over-stdio protocol:

```text
parent (MCP server / kernel / library)
  │
  ├── spawns + supervises ───┐
  │                          ▼
  │                   stata-code-worker
  │                   (one-time pystata.config.init,
  │                    then reads JSON requests on stdin,
  │                    writes JSON responses on stdout)
  │
  ▼
serialized RunResult flows back; parent enforces wall-clock timeout
on each request and SIGKILLs the worker if it overruns. The supervisor
respawns the worker for the next call, paying ~3-5s warm-up.
```

Tradeoffs:

| Concern | Detail |
| --- | --- |
| **Startup cost** | One-time per session. The supervisor pre-warms `"main"` on first use; user-defined sessions warm on demand. |
| **State preservation** | Worker holds Stata state across calls. Timeout-induced kill **loses** all in-memory data, frames, locals, results. The agent must `use` / re-load. This is the same trade-off any subprocess kill makes. |
| **IPC overhead** | Per-call: JSON encode/decode of code + RunResult. For typical sub-MB results, negligible vs Stata wall-clock. |
| **Ref store** | Currently lives in the same process as the runner. With subprocess workers, refs (log://, graph://, matrix://) need to either (a) live in the worker and be fetched via IPC, or (b) be transferred to the parent and held there. Option (b) is simpler and matches the "parent owns the result envelope" model. |
| **Tests** | Need a new `pytest -m subprocess_worker` track. Existing in-process integration tests stay as-is for the eventual library-only mode. |
| **Effort** | ~1–2 weeks of focused work. Not a patch. |

### Option B — Thread-based timer + best-effort kill

Run `stata.run(code)` on a worker thread, watch a deadline timer in the
parent, and on timeout … do what, exactly? `Thread.join(timeout=…)` returns
control but does **not** stop the C call. We cannot raise asynchronously
into a thread that's blocked in foreign code.

The "best-effort" approach is to record `elapsed_ms > timeout_ms` and
return a synthetic `error.kind="timeout"` envelope while the underlying
Stata call continues to consume CPU in the background. That is **lying
to the agent**: subsequent calls will see polluted state from the
runaway command. Reject this option.

### Option C — Cooperative-only timeout

Same as Option B without the lying: don't add `timeout_ms` enforcement
at all, just expose `cancel()`. **This is what we ship today.** Useful
between calls; useless during a call. Documented honestly in
SCHEMA.md §8.

## Recommendation

Option A is the only correct path, but it is a real architectural
change. Don't try to retrofit it onto the existing `_runtime.py`
piecemeal — the worker protocol, ref-transfer story, and supervisor
restart semantics need to be designed together.

Concrete pre-conditions before starting:

1. **Decide the IPC format.** JSON is fine for `RunResult`; matrices
   stream cleanly as nested arrays. Pickle is faster but fragile across
   Python versions and uglier from a security standpoint. Recommend
   JSON for v0.3, with pluggable serializer if benchmarks force the
   issue later.
2. **Decide the ref-transfer story.** Two options: (a) refs live in
   the worker and `get_log/get_graph/get_matrix` round-trip back via
   IPC; (b) the parent receives full payloads inline at run time and
   stores them in its local `_refs`. Option (b) is simpler and keeps
   `cancel(session_id)` semantics intact, at the cost of moving
   payload bytes through the pipe even when the agent never asks for
   them. Recommend (b) initially; revisit if profiling shows IPC
   bandwidth is the bottleneck.
3. **Decide the per-session worker lifecycle.** Spawn-on-first-use,
   keep-warm forever, restart-on-timeout. Worker death between
   `cancel()` and `execute()` should still produce a sensible
   `RunResult` (kind `cancelled` or `adapter_crash` depending on
   cause).
4. **Move ref-store ownership.** With Option (b) above, `_refs` stays
   in the parent process, but its eviction policy (LRU 256 entries)
   may need re-tuning when refs cross IPC boundaries.

When Option A is approved, the rough work breakdown is:

| # | Task | Estimated cost |
| - | --- | --- |
| 1 | Worker protocol (JSON request/response shapes, framing) | 1d |
| 2 | `stata-code-worker` binary: parse request, run, emit response | 1d |
| 3 | Supervisor in `_runtime.py`: spawn / monitor / SIGKILL on timeout / restart | 2d |
| 4 | Re-route `execute()` through the supervisor | 1d |
| 5 | Move ref payloads into the request/response cycle | 1d |
| 6 | New `subprocess_worker` test marker; port a representative subset of `test_runner.py` | 2d |
| 7 | Documentation: SCHEMA.md §3.7 / §8, README, CHANGELOG, this doc → "shipped" | 0.5d |

Total: ~8–10 days of focused work.

## Why not now

This v0.3 push deliberately scoped to changes that do not require
restructuring `_runtime.py`. Shipping Option A as a side-effect of a
multi-feature push would leave it under-tested and ill-thought-through.
It deserves its own milestone where (a) the design above is reviewed
and confirmed, (b) the work is tracked across at least one full
development week, and (c) any breaking change to the runtime model
is announced ahead of time so downstream callers can plan.

In the meantime the ground we did cover is real:

- Cooperative cancellation (`cancel(session_id)`) — in this push.
- Token economy preserved end-to-end (refs, not bytes) — in this push.
- VSCode webview that exercises the same MCP surface a hard-timeout
  agent would — in this push.

The hardest piece is still ahead. This document exists so the next
person picking it up doesn't have to rediscover the constraints.
