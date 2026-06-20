"""Tests for GitHub Actions workflow pin checks."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = REPO_ROOT / "scripts"


def _load_module():
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        return importlib.import_module("check_github_actions")
    finally:
        sys.path.pop(0)


def test_current_workflows_use_node24_compatible_actions():
    mod = _load_module()
    assert mod.check_workflows() == []


def test_old_action_and_node_versions_are_reported(tmp_path):
    mod = _load_module()
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    workflow = workflows / "old.yml"
    workflow.write_text(
        """
name: old
jobs:
  stale:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - uses: actions/upload-artifact@v4
""",
        encoding="utf-8",
    )

    messages = [finding.message for finding in mod.check_workflows(workflows)]

    assert any("actions/checkout@v4" in message for message in messages)
    assert any("actions/setup-node@v4" in message for message in messages)
    assert any("actions/upload-artifact@v4" in message for message in messages)
    assert any("node-version 20" in message for message in messages)
