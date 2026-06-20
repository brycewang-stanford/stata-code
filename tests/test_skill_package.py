"""Tests for the skill-zip packaging script (scripts/build_skill_zip.py).

Guards the claude.ai upload artifact: the archive must exist, be rooted under
a single ``stata-code/`` folder, contain SKILL.md plus the references library,
and build deterministically.
"""

from __future__ import annotations

import importlib
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = REPO_ROOT / "scripts"


def _load_module():
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        return importlib.import_module("build_skill_zip")
    finally:
        sys.path.pop(0)


def test_skill_source_tree_present():
    mod = _load_module()
    files = mod.collect_files()
    names = {p.name for p in files}
    assert "SKILL.md" in names
    assert "error-codes.md" in names
    assert "defensive-coding.md" in names
    # The packages subdir is included with its nested path.
    assert any(p.parts[-2:] == ("packages", "reghdfe.md") for p in files)


def test_build_zip_contains_prefixed_entries(tmp_path):
    mod = _load_module()
    dest = tmp_path / "skill.zip"
    arcnames = mod.build_zip(dest=dest)

    assert dest.exists()
    assert "stata-code/SKILL.md" in arcnames
    assert all(a.startswith("stata-code/") for a in arcnames)
    assert "stata-code/references/error-codes.md" in arcnames

    with zipfile.ZipFile(dest) as zf:
        assert zf.testzip() is None  # archive integrity
        body = zf.read("stata-code/SKILL.md").decode("utf-8")
        assert "# stata-code Skill" in body


def test_build_is_deterministic(tmp_path):
    mod = _load_module()
    a = tmp_path / "a.zip"
    b = tmp_path / "b.zip"
    mod.build_zip(dest=a)
    mod.build_zip(dest=b)
    assert a.read_bytes() == b.read_bytes()
