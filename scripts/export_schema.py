"""Export the v1.0 RunResult Pydantic model as a JSON Schema artifact.

The artifact lives at ``schema/run_result.schema.json`` and is the
machine-readable companion to ``SCHEMA.md`` (the normative contract).
External consumers — TypeScript clients, doc generators, the planned
VSCode extension — should consume this file rather than re-deriving
the shape.

Run::

    python scripts/export_schema.py            # write schema/run_result.schema.json
    python scripts/export_schema.py --check    # exit non-zero if drifted

The drift check is what ``tests/test_schema_artifact.py`` calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from stata_code.core.schema import RunResult

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_PATH = REPO_ROOT / "schema" / "run_result.schema.json"


def build_schema() -> dict:
    schema = RunResult.model_json_schema()
    # Stable, human-friendly framing for downstream consumers.
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = (
        "https://raw.githubusercontent.com/brycewang-stanford/stata-code/"
        "main/schema/run_result.schema.json"
    )
    schema["title"] = "stata_code RunResult (v1.0)"
    schema["description"] = (
        "Machine-readable JSON Schema for the v1.0 result envelope returned by "
        "stata_code.run(). The normative contract is SCHEMA.md; this file is "
        "auto-generated from stata_code.core.schema.RunResult and kept in sync "
        "by tests/test_schema_artifact.py."
    )
    return schema


def serialize(schema: dict) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the on-disk artifact is out of sync.",
    )
    args = parser.parse_args()

    rendered = serialize(build_schema())

    if args.check:
        if not ARTIFACT_PATH.exists():
            print(f"missing: {ARTIFACT_PATH}", file=sys.stderr)
            return 1
        on_disk = ARTIFACT_PATH.read_text(encoding="utf-8")
        if on_disk != rendered:
            print(
                f"drift: {ARTIFACT_PATH} is out of sync with RunResult; "
                "re-run `python scripts/export_schema.py` and commit.",
                file=sys.stderr,
            )
            return 1
        print(f"ok: {ARTIFACT_PATH} is in sync")
        return 0

    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT_PATH.write_text(rendered, encoding="utf-8")
    print(f"wrote: {ARTIFACT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
