"""Drift guard for the generated JSON Schema artifact.

The artifact at ``schema/run_result.schema.json`` is generated from
``stata_code.core.schema.RunResult`` by ``scripts/export_schema.py``.
This test asserts the on-disk file matches what the current Pydantic
model would produce, so a model change without a re-export fails CI.

To fix a failure: ``python scripts/export_schema.py`` and commit.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_PATH = REPO_ROOT / "schema" / "run_result.schema.json"
SCRIPT_PATH = REPO_ROOT / "scripts" / "export_schema.py"


@pytest.fixture(scope="module")
def export_module():
    sys.path.insert(0, str(SCRIPT_PATH.parent))
    try:
        import importlib

        module = importlib.import_module("export_schema")
    finally:
        sys.path.pop(0)
    return module


def test_artifact_exists() -> None:
    assert ARTIFACT_PATH.exists(), (
        f"{ARTIFACT_PATH} missing; run `python scripts/export_schema.py`"
    )


def test_artifact_is_valid_json() -> None:
    json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))


def test_artifact_matches_current_model(export_module) -> None:
    rendered = export_module.serialize(export_module.build_schema())
    on_disk = ARTIFACT_PATH.read_text(encoding="utf-8")
    assert on_disk == rendered, (
        "schema/run_result.schema.json is out of sync with RunResult; "
        "re-run `python scripts/export_schema.py` and commit."
    )


def test_artifact_carries_envelope_top_level_fields() -> None:
    """Sanity check: the artifact actually describes the v1.0 envelope."""
    schema = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    props = schema["properties"]
    for required in ("ok", "rc", "session_id", "log", "results", "schema_version"):
        assert required in props, f"top-level field missing from artifact: {required}"
    assert schema.get("title") == "stata_code RunResult (v1.0)"
