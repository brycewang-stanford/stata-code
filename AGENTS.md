# AGENTS.md — stata-code project notes

Project-specific guidance for Codex sessions in this repo. General publishing
docs live in [PUBLISHING.md](PUBLISHING.md); this file captures what isn't obvious
from reading the workflows.

## Communication

写完代码后，始终用**中文**给出一段简短总结：做了什么、为什么、用户接下来要做什么。即使本轮对话之前是英文，代码改动结束时的总结也要用中文。

## Release coordination

A unified release ships **four artifacts under the same version number**:

| Channel | Tag | Workflow |
| --- | --- | --- |
| TestPyPI (`stata-code`) | `vX.Y.Z` | `publish-testpypi` job in [.github/workflows/release.yml](.github/workflows/release.yml) |
| PyPI (`stata-code`) | `vX.Y.Z` | `publish-pypi` job in [.github/workflows/release.yml](.github/workflows/release.yml) |
| VS Code Marketplace (`stata-code-vscode`) | `vscode-vX.Y.Z` | [.github/workflows/vscode-release.yml](.github/workflows/vscode-release.yml) |
| GitHub Release | `vX.Y.Z` | tail end of `release.yml` |

Five files hold version literals — bump all of them together, or the release will
ship inconsistent metadata:

1. `pyproject.toml` → `[project] version`
2. `stata_code/__init__.py` → `__version__`
3. `stata_code/mcp/server.py` → `__version__`
4. `vscode/package.json` → `version`
5. `vscode/src/mcpClient.ts` → handshake version string

## PyPI / TestPyPI Trusted Publishing — what to know

Both PyPI and TestPyPI use OIDC (no API tokens in repo secrets). They are
**separate sites with separate publisher configs** — each must be set up
independently:

| Site | Manage URL | Environment |
| --- | --- | --- |
| PyPI | <https://pypi.org/manage/project/stata-code/settings/publishing/> | `pypi` |
| TestPyPI | <https://test.pypi.org/manage/project/stata-code/settings/publishing/> | `testpypi` |

For both, the publisher values are:

- Owner: `brycewang-stanford`
- Repository: `stata-code` (hyphen — not the local dir name `stata_code`)
- Workflow: `release.yml`
- Environment: `pypi` or `testpypi` (must match the job's `environment.name`)

Configuring trusted publishers on another project (e.g. `statspai`) does **not**
carry over — each (site, project) pair has its own publisher list.

The `release.yml` flow is `build → publish-testpypi → publish-pypi → github-release`.
Both publish jobs are `continue-on-error: true`, so a missing TestPyPI publisher
does not block the PyPI publish or the GitHub Release.

## Recovery: `invalid-publisher` failure

Symptom: `release.yml` runs, `publish-pypi` fails with
`invalid-publisher: valid token, but no corresponding publisher`. Because
`publish-pypi` has `continue-on-error: true`, the overall run reports success and
the GitHub Release still gets created — but PyPI has nothing.

Fix without re-tagging:

1. Configure / correct the trusted publisher on PyPI (values above).
2. Re-run **only the failed job** — the sdist/wheel artifact is still attached to
   the original run, so no rebuild is needed:
   ```bash
   gh run rerun <run-id> --failed
   gh run watch <run-id> --exit-status
   ```
3. Verify (next section).

Do **not** delete and re-push `vX.Y.Z` to retry. The artifact is already on the
run, the GitHub Release exists, and PyPI rejects re-uploads of the same version
anyway.

## Verifying a PyPI publish

The convenience endpoint `https://pypi.org/pypi/stata-code/json` is heavily
CDN-cached and can lag the actual publish by minutes. To get an authoritative
answer immediately:

```bash
# Per-version JSON (200 = published, 404 = not yet)
curl -s -o /dev/null -w "%{http_code}\n" https://pypi.org/pypi/stata-code/X.Y.Z/json

# Simple index (always fresh)
curl -s -H "Accept: application/vnd.pypi.simple.v1+json" \
  https://pypi.org/simple/stata-code/ | python3 -c \
  "import json,sys; print(json.load(sys.stdin).get('versions', []))"
```

## VS Code extension upgrade

The repo ships a `.vsix` under `vscode/` for sideload installs:

```bash
code --install-extension vscode/stata-code-vscode-X.Y.Z.vsix --force
```

The Marketplace publish runs on the `vscode-vX.Y.Z` tag, independent of the PyPI
release.

## MCP launch resilience (VS Code extension)

The extension auto-discovers a Python interpreter for the MCP server in this order
(see [vscode/src/mcpClient.ts](vscode/src/mcpClient.ts) and
[vscode/src/extension.ts](vscode/src/extension.ts)):

1. Per-workspace `.venv` / `venv` in any workspace folder
2. The Python configured in `stataCode.pythonPath` (settings)
3. System `python3`

The MCP client is reset whenever `stataCode.*` settings change so users don't have
to reload the window.

## Notebook cell repair loop (Phase 1)

`stata_run` is intentionally cell-agnostic: it accepts a code string and a few
optional `origin_*` fields, and returns a `RunResult`. Per-cell repair on
`.ipynb` works by composing it with two read-only helpers and the new
`origin_cell_id` echo:

| Tool | Use |
| --- | --- |
| `notebook_outline(path)` | Compact per-cell index — `cell_id`, type, preview, line/char counts, has-error flag. Read this once; do not pull whole cells into context. |
| `notebook_get_cell(path, cell_id=…)` | Full source of one cell + a token-economic outputs summary (count, types, error ename/evalue, head/tail of stream/text outputs, traceback head/tail). |
| `stata_run(code=<source>, origin_path=<.ipynb>, origin_kind="cell", origin_cell_id=<cell_id>)` | Execute one cell. The runner does not interpret these origin fields — they are echoed in `result.origin` and recorded in the run-bundle manifest, so the agent's call history is traceable back to the cell without the protocol becoming notebook-aware. |

Recommended loop (opt-in; never run without an explicit "fix and rerun" request):

1. Identify the target cell — IDE selection > `cell_id` from `notebook_outline`
   > snippet match the user pasted. If genuinely ambiguous, ask the user.
2. `notebook_get_cell(path, cell_id)` → `source`.
3. `stata_run(code=source, origin_path=path, origin_kind="cell", origin_cell_id=cell_id)`.
4. On failure:
   - `error.line` is **already cell-relative** because the agent submitted the
     cell's source verbatim — no off-by-one math against the notebook file.
   - `error.context.failing` is the failing command line; use it as a content
     fingerprint when the user describes the failure later.
5. If the user authorised repairs, apply the edit via
   `notebook_edit_cell(path, cell_id, new_source, expected_source=<old source>)`.
   The `expected_source` guard is optimistic-concurrency: if the user changed
   the cell while the agent was working, the call fails with
   `edit_source_drift` and the agent must re-read.
6. Repeat from step 3 with a small retry budget (default 3). If still failing,
   stop and recommend `restart kernel + run all from top` — repeated failure
   on a single cell is usually upstream-state pollution, not a code bug.

Phase 2 also adds:

- `notebook_locate(path, snippet=… | regex=… | error_text=…)` — turn a code
  fingerprint or pasted Stata error into one or more candidate cells. Use this
  when the user describes a failure without selecting a cell ("the regression
  cell errored out") instead of asking them to scroll.
- `notebook_insert_cell(path, source, after_cell_id=… | before_cell_id=… |
  at_start=true | at_end=true, cell_type="code"|"markdown"|"raw")` — assigns
  a fresh nbformat 4.5+ UUID. Use sparingly; explicitly tell the user that a
  new cell was added.
- `notebook_delete_cell(path, cell_id, expected_source=…)` — same drift guard
  as edit. Confirm with the user before calling unless the deletion is
  obviously requested.

## Run-bundle index (Phase 3)

Every `stata_run` call with `persist_log_files=true` and `origin_path=…`
writes a manifest under `<origin dir>/log-files/<run-dir>/manifest.json`.
`list_runs` is the read-only query over those manifests:

```python
list_runs(
    log_dir | origin_path,        # one of these is required
    cell_id?,                      # filter by origin_cell_id
    session_id?,
    ok?,
    since?,                        # ISO 8601 UTC, lexicographic >=
    limit=50,                      # max 500
)
```

Returns newest-first compact summaries (request_id, started_at, ok, rc,
origin_*, directory, manifest_path, log_path). For the full manifest, read
the file at `manifest_path` directly. Use cases:

- "What did I last try on this cell?" → `cell_id=…, limit=5`
- "Show me failures in this notebook" → `origin_path=<.ipynb>, ok=false`
- "Anything since 02:00 UTC?" → `since="2026-05-08T02:00:00.000Z"`

Pair with `origin_cell_id` echo on `stata_run` to close the loop: the
agent's runs are now traceable back to specific cells without the MCP
protocol becoming notebook-aware.

Non-goals (still):

- No notebook-wide execution (`notebook_run_all`). Per-cell stays the unit.
- No execution-count tracking in the protocol — that's a kernel/UI concern.
- No new `error` schema fields. `error.line` and `error.context` already work
  cell-relative when the agent submits one cell at a time.
- `list_runs` only sees runs that were persisted (`persist_log_files=true`
  and `origin_path` provided). Ephemeral runs leave no trace, by design.
