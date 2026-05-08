# CLAUDE.md — stata-code project notes

Project-specific guidance for Claude Code sessions in this repo. General publishing
docs live in [PUBLISHING.md](PUBLISHING.md); this file captures what isn't obvious
from reading the workflows.

## Release coordination

A unified release ships **three artifacts under the same version number**:

| Channel | Tag | Workflow |
| --- | --- | --- |
| PyPI (`stata-code`) | `vX.Y.Z` | [.github/workflows/release.yml](.github/workflows/release.yml) |
| VS Code Marketplace (`stata-code-vscode`) | `vscode-vX.Y.Z` | [.github/workflows/vscode-release.yml](.github/workflows/vscode-release.yml) |
| GitHub Release | `vX.Y.Z` | tail end of `release.yml` |

Five files hold version literals — bump all of them together, or the release will
ship inconsistent metadata:

1. `pyproject.toml` → `[project] version`
2. `stata_code/__init__.py` → `__version__`
3. `stata_code/mcp/server.py` → `__version__`
4. `vscode/package.json` → `version`
5. `vscode/src/mcpClient.ts` → handshake version string

## PyPI Trusted Publishing — what to know

PyPI uses OIDC (no API tokens in repo secrets). The trusted publisher is configured
**per PyPI project** at <https://pypi.org/manage/project/stata-code/settings/publishing/>
with these exact values:

- Owner: `brycewang-stanford`
- Repository: `stata-code` (hyphen — not the local dir name `stata_code`)
- Workflow: `release.yml`
- Environment: `pypi`

Configuring trusted publishers on another project (e.g. `statspai`) does **not**
carry over — each PyPI project has its own publisher list.

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
