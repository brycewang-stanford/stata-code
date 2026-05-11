# Publishing `stata-code` to PyPI

This project publishes to PyPI via **GitHub Actions Trusted Publishing** (OIDC).
There are **no API tokens** stored in GitHub repository secrets — PyPI verifies
the OIDC identity of the workflow run instead.

The release pipeline is in [`.github/workflows/release.yml`](.github/workflows/release.yml).

---

## One-time setup (maintainer)

You only need to do this once per project, before the very first release.

### 1. Reserve the project name on PyPI

Trusted Publishing has two modes:

- **Pending publisher** (preferred for the first release of a brand-new
  project): you configure the trusted publisher *before* the project exists
  on PyPI, and the first successful publish creates it. Go to
  https://pypi.org/manage/account/publishing/ and add a "pending publisher"
  with the values listed in step 2.
- **Existing project**: if you already pushed an initial release manually
  (e.g. via `twine upload` with a one-off API token), configure trusted
  publishing on the project page at
  https://pypi.org/manage/project/stata-code/settings/publishing/.

Either path lands you in the same place after the first run.

### 2. Configure the Trusted Publisher on PyPI

On the PyPI publishing page, add a GitHub publisher with these values:

| Field | Value |
| --- | --- |
| Owner | `brycewang-stanford` |
| Repository name | `stata-code` |
| Workflow filename | `release.yml` |
| Environment name | `pypi` |

The environment name is **required** and must match the `environment.name`
declared in `release.yml` (`pypi`). PyPI uses it as an extra constraint:
even a malicious workflow change in the repo cannot publish unless it runs
under that exact environment.

### 3. Create the GitHub Environment

In the GitHub repo: **Settings → Environments → New environment → name `pypi`**.

Recommended hardening (all in the environment settings page):

- **Deployment branches and tags**: restrict to tags matching `v*`. This
  prevents anyone from pushing to a branch and triggering an unintended
  publish via `workflow_dispatch`.
- **Required reviewers** (optional): add yourself if you want a manual
  approval gate before each publish.
- **Wait timer** (optional): a few minutes' delay so you can cancel a
  mistaken release.

The environment doesn't need any secrets — Trusted Publishing handles auth.

---

## Cutting a release

Once the one-time setup above is done, every release is just:

1. **Bump the version everywhere**:
   - `pyproject.toml` → `[project] version`
   - `stata_code/__init__.py` → `__version__`
   - `stata_code/mcp/server.py` → `__version__`
   - `vscode/package.json` → `version`
2. **Run the version guard** before tagging:
   ```bash
   python scripts/check_versions.py
   ```
3. **Update `CHANGELOG.md`**: move the `[Unreleased]` entries under a new
   `## [X.Y.Z] - YYYY-MM-DD` heading, leaving an empty `[Unreleased]` shell
   on top.
4. **Commit** the bump:
   ```bash
   git add pyproject.toml stata_code/__init__.py stata_code/mcp/server.py vscode/package.json CHANGELOG.md
   git commit -m "release: vX.Y.Z"
   ```
5. **Tag and push**:
   ```bash
   git tag vX.Y.Z
   git push origin main
   git push origin vX.Y.Z
   ```
5. Watch the **`release` workflow** under the Actions tab. It will:
   - build the sdist + wheel
   - run `twine check` on the artifacts
   - publish to PyPI under the `pypi` environment (no token needed)
   - create a GitHub Release for the tag with the wheel + sdist attached and
     auto-generated release notes

If the publish step fails because of metadata, fix `pyproject.toml`, delete
the tag locally and on the remote, and re-tag:

```bash
git tag -d vX.Y.Z
git push origin :refs/tags/vX.Y.Z
# fix, commit, then re-tag
```

PyPI does **not** allow re-uploading the same version, so always bump the
version if a publish has already succeeded under that number.

---

## Manual / emergency publish

`release.yml` also accepts `workflow_dispatch`, so you can trigger a build
+ publish from the Actions UI without pushing a tag. The `github-release`
job will fail in that mode (no tag to attach to) — that's fine; the PyPI
publish itself will have already happened in `publish-pypi`.

If Trusted Publishing is broken for some reason and you need an emergency
release, generate a short-lived API token at
https://pypi.org/manage/account/token/ and run locally:

```bash
python -m build
python -m twine check dist/*
python -m twine upload dist/*
```

Revoke the token immediately afterwards.
