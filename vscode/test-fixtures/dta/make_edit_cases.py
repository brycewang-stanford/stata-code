"""Regenerate edit_cases.json: label edits and the bytes they must produce.

    python vscode/test-fixtures/dta/make_edit_cases.py

Each case names a fixture and an edit. A case that should succeed records the
SHA-256 of the edited file and what the edit reports; a case that should be
refused records a fragment of the message. The Python editor
(stata_code/core/dta_edit.py) writes the expectations here, and both it
(tests/test_dta_edit.py) and the TypeScript editor
(vscode/src/dtaWriter.test.ts) are tested against them, which is what keeps
the two byte-identical.

Regenerate only after checking the Python output against a real Stata: open
the edited files, compare `datasignature` with the original, read `label
list`, `describe` and `notes`. tests/test_real_stata.py does that when a
Stata is installed.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

from stata_code.core.dta_edit import edit_labels  # noqa: E402
from stata_code.core.dta_labels import DtaLabelError  # noqa: E402

LONG = "x" * 32001

CASES: list[dict] = [
    # ---- fixed-width fields only: patched in place
    {
        "name": "attach an existing set to another variable",
        "fixture": "survey118.dta",
        "edit": {"attach": {"score": "yn"}},
    },
    {
        "name": "detach a set",
        "fixture": "survey118.dta",
        "edit": {"attach": {"female": ""}},
    },
    {
        "name": "dataset label of a legacy file is a fixed field",
        "fixture": "legacy115.dta",
        "edit": {"data_label": "Renamed"},
    },
    {
        "name": "variable label and attachment together, legacy",
        "fixture": "legacy115.dta",
        "edit": {"variable_labels": {"b": "Byte"}, "attach": {"b": "grplbl"}},
    },
    # ---- value-label contents: the file is written again
    {
        "name": "define a new set and attach it",
        "fixture": "survey118.dta",
        "edit": {
            "value_labels": {"agree": {"1": "Agree", "2": "Disagree", ".a": "Refused"}},
            "attach": {"score": "agree"},
        },
    },
    {
        "name": "replace a set, codes given out of order, non-ASCII text",
        "fixture": "survey118.dta",
        "edit": {"value_labels": {"yn": {"1": "是", "0": "否", "-9": "n/a"}}},
    },
    {
        "name": "drop a set detaches it",
        "fixture": "survey118.dta",
        "edit": {"value_labels": {"regionlbl": None}},
    },
    {
        "name": "drop one, define one, modify one",
        "fixture": "survey118.dta",
        "edit": {
            "value_labels": {
                "yn": None,
                "regionlbl": {"1": "N", "2": "S", "3": "E", "4": "W"},
                "fresh": {"0": "zero"},
            },
            "attach": {"female": "fresh"},
        },
    },
    {
        "name": "format 117",
        "fixture": "strl117.dta",
        "edit": {
            "value_labels": {"grplbl": {"0": "ctl", "1": "trt"}, "extra": {"5": "five"}},
            "data_label": "A longer dataset label than before",
        },
    },
    {
        "name": "legacy value labels sit at the end of the file",
        "fixture": "legacy115.dta",
        "edit": {
            "value_labels": {
                "grplbl": {"0": "control group", ".a": "refused"},
                "more": {"1": "one"},
            },
            "attach": {"i": "more"},
        },
    },
    {
        "name": "file without value labels gets its first",
        "fixture": "alias120.dta",
        "edit": {"value_labels": {"first": {"1": "one"}}, "attach": {"id": "first"}},
    },
    {
        "name": "empty dataset",
        "fixture": "empty118.dta",
        "edit": {"value_labels": {"grplbl": None}, "data_label": ""},
    },
    # ---- dataset label of a tagged file moves every section
    {
        "name": "longer dataset label, format 118",
        "fixture": "modern118.dta",
        "edit": {"data_label": "数据集 with a much longer label than the fixture had"},
    },
    {
        "name": "remove the dataset label",
        "fixture": "modern118.dta",
        "edit": {"data_label": ""},
    },
    {
        "name": "everything at once",
        "fixture": "modern118.dta",
        "edit": {
            "variable_labels": {"b": "Byte", "s": ""},
            "value_labels": {"grplbl": {"0": "c", "1": "t", ".a": "r", ".z": "last"}},
            "attach": {"b": "grplbl", "grp": ""},
            "data_label": "All four kinds",
        },
    },
    # ---- nothing to do
    {
        "name": "same values are not a change",
        "fixture": "survey118.dta",
        "edit": {
            "value_labels": {"yn": {"0": "No", "1": "Yes"}},
            "attach": {"female": "yn"},
            "data_label": "Synthetic survey",
        },
    },
    {
        "name": "dropping a set that does not exist is not a change",
        "fixture": "survey118.dta",
        "edit": {"value_labels": {"ghost": None}},
    },
    # ---- refused
    {
        "name": "unknown variable",
        "fixture": "survey118.dta",
        "edit": {"attach": {"nope": "yn"}},
        "error": "nope: no such variable",
    },
    {
        "name": "string variable",
        "fixture": "survey118.dta",
        "edit": {"attach": {"city": "yn"}},
        "error": "city: is a string variable",
    },
    {
        "name": "attaching a set that does not exist",
        "fixture": "survey118.dta",
        "edit": {"attach": {"score": "missing"}},
        "error": "no value label named missing",
    },
    {
        "name": "attaching a set dropped in the same call",
        "fixture": "survey118.dta",
        "edit": {"value_labels": {"yn": None}, "attach": {"score": "yn"}},
        "error": "no value label named yn",
    },
    {
        "name": "bad set name",
        "fixture": "survey118.dta",
        "edit": {"value_labels": {"2bad": {"1": "x"}}},
        "error": "a name is 1-32 letters, digits or _",
    },
    {
        "name": "non-integer code",
        "fixture": "survey118.dta",
        "edit": {"value_labels": {"yn": {"1.5": "x"}}},
        "error": "is not a value-label code",
    },
    {
        "name": "plain missing cannot be labelled",
        "fixture": "survey118.dta",
        "edit": {"value_labels": {"yn": {".": "x"}}},
        "error": "is not a value-label code",
    },
    {
        "name": "code out of range",
        "fixture": "survey118.dta",
        "edit": {"value_labels": {"yn": {"2147483621": "x"}}},
        "error": "outside the range a value label can hold",
    },
    {
        "name": "empty text",
        "fixture": "survey118.dta",
        "edit": {"value_labels": {"yn": {"1": ""}}},
        "error": "must be a non-empty string",
    },
    {
        "name": "empty set",
        "fixture": "survey118.dta",
        "edit": {"value_labels": {"yn": {}}},
        "error": "has no entries; pass null to drop",
    },
    {
        "name": "text too long",
        "fixture": "survey118.dta",
        "edit": {"value_labels": {"yn": {"1": LONG}}},
        "error": "takes 32001 bytes; Stata allows at most 32000",
    },
    {
        "name": "control character",
        "fixture": "survey118.dta",
        "edit": {"value_labels": {"yn": {"1": "a\nb"}}},
        "error": "contains a control character",
    },
    {
        "name": "non-ASCII into an old format",
        "fixture": "legacy115.dta",
        "edit": {"value_labels": {"grplbl": {"1": "是"}}},
        "error": "plain-ASCII labels",
    },
    {
        "name": "dataset label too long",
        "fixture": "modern118.dta",
        "edit": {"data_label": "y" * 81},
        "error": "label is 81 characters; Stata allows at most 80",
    },
    {
        "name": "one bad entry refuses the whole call",
        "fixture": "survey118.dta",
        "edit": {"value_labels": {"ok": {"1": "fine"}}, "attach": {"nope": "ok"}},
        "error": "nope: no such variable",
    },
]


def main() -> None:
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        for case in CASES:
            target = Path(tmp) / case["fixture"]
            shutil.copyfile(HERE / case["fixture"], target)
            entry = {k: case[k] for k in ("name", "fixture", "edit")}
            try:
                result = edit_labels(target, **case["edit"])
            except DtaLabelError as exc:
                if "error" not in case or case["error"] not in str(exc):
                    raise SystemExit(f"{case['name']}: unexpected refusal: {exc}")
                entry["error"] = case["error"]
            else:
                if "error" in case:
                    raise SystemExit(f"{case['name']}: should have been refused")
                entry["result"] = result.to_dict()
                entry["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
            out.append(entry)
    (HERE / "edit_cases.json").write_text(
        json.dumps({"cases": out}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(out)} cases")


if __name__ == "__main__":
    main()
