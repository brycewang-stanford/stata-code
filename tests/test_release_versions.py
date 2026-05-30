"""Release-version guard tests."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_check_versions():
    spec = importlib.util.spec_from_file_location(
        "check_versions_under_test", ROOT / "scripts" / "check_versions.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_minimal_project(root: Path, version: str) -> None:
    (root / "stata_code" / "mcp").mkdir(parents=True)
    (root / "vscode" / "src").mkdir(parents=True)
    (root / ".claude-plugin").mkdir(parents=True)

    (root / "pyproject.toml").write_text(f'version = "{version}"\n', encoding="utf-8")
    (root / "stata_code" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    (root / "stata_code" / "mcp" / "server.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    (root / "vscode" / "package.json").write_text(
        json.dumps({"version": version}), encoding="utf-8"
    )
    (root / "vscode" / "package-lock.json").write_text(
        json.dumps(
            {
                "version": version,
                "packages": {"": {"version": version}},
            }
        ),
        encoding="utf-8",
    )
    (root / "vscode" / "src" / "mcpClient.ts").write_text(
        'import { version } from "../package.json";\n', encoding="utf-8"
    )
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": version}), encoding="utf-8"
    )
    (root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(
            {
                "metadata": {"version": version},
                "plugins": [{"version": version}],
            }
        ),
        encoding="utf-8",
    )


def test_check_versions_accepts_all_release_surfaces(tmp_path, monkeypatch):
    mod = _load_check_versions()
    _write_minimal_project(tmp_path, "1.2.3")

    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["check_versions.py"])

    assert mod.main() == 0


def test_check_versions_rejects_package_lock_drift(tmp_path, monkeypatch):
    mod = _load_check_versions()
    _write_minimal_project(tmp_path, "1.2.3")
    (tmp_path / "vscode" / "package-lock.json").write_text(
        json.dumps(
            {
                "version": "1.2.3",
                "packages": {"": {"version": "1.2.4"}},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["check_versions.py"])

    assert mod.main() == 1


def test_check_versions_rejects_plugin_manifest_drift(tmp_path, monkeypatch):
    mod = _load_check_versions()
    _write_minimal_project(tmp_path, "1.2.3")
    (tmp_path / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(
            {
                "metadata": {"version": "1.2.3"},
                "plugins": [{"version": "1.2.4"}],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["check_versions.py"])

    assert mod.main() == 1
