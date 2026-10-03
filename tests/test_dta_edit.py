"""Tests for Stata-free editing of value labels and the dataset label.

The fixtures are written by a real Stata (vscode/test-fixtures/dta/
make_fixtures.do). The expectations in edit_cases.json are shared with the VS
Code extension's dtaWriter tests: both editors must refuse the same edits and
write the same bytes. That the files written here still load in Stata, with
`datasignature` unchanged and `label list`, `describe` and `notes` showing
the edit and nothing else, was checked against Stata 18 on every fixture.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from stata_code.core import dta_edit
from stata_code.core.dta_edit import DtaEdit, edit_labels, read_metadata
from stata_code.core.dta_labels import DtaLabelError, read_variable_labels

FIXTURES = Path(__file__).resolve().parent.parent / "vscode" / "test-fixtures" / "dta"
CASES = json.loads((FIXTURES / "edit_cases.json").read_text(encoding="utf-8"))["cases"]


@pytest.fixture
def copy_of(tmp_path):
    def _copy(name: str) -> Path:
        target = tmp_path / name
        shutil.copyfile(FIXTURES / name, target)
        return target

    return _copy


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------ reading
def test_reads_value_labels_attachments_and_notes():
    meta = read_metadata(FIXTURES / "survey118.dta")
    assert meta.release == 118
    assert meta.data_label == "Synthetic survey"
    assert meta.value_labels == {
        "yn": {0: "No", 1: "Yes"},
        "regionlbl": {1: "North", 2: "South", 3: "East", 4: "West", ".a": "Refused"},
    }
    attached = dict(zip(meta.names, meta.value_label_names))
    assert attached["region"] == "regionlbl" and attached["female"] == "yn"
    assert attached["id"] == ""
    assert dict(zip(meta.names, meta.types))["city"].startswith("str")


@pytest.mark.parametrize("name", ["modern118.dta", "strl117.dta", "legacy115.dta"])
def test_reads_notes_in_every_layout(name):
    meta = read_metadata(FIXTURES / name)
    assert meta.notes == {"_dta": ["dataset note one"], "b": ["byte note"]}
    assert meta.value_labels == {"grplbl": {0: "control", 1: "treated", ".a": "refused"}}
    payload = meta.to_dict()
    assert payload["notes"] == ["dataset note one"]
    assert payload["value_labels"]["grplbl"] == {"0": "control", "1": "treated", ".a": "refused"}
    by_name = {v["name"]: v for v in payload["variables"]}
    assert by_name["b"]["notes"] == ["byte note"]
    assert by_name["grp"]["value_label"] == "grplbl"


def test_agrees_with_the_variable_label_reader():
    for name in ("modern118.dta", "strl117.dta", "legacy115.dta", "alias120.dta"):
        meta = read_metadata(FIXTURES / name)
        old = read_variable_labels(FIXTURES / name)
        assert (meta.names, meta.labels) == (old.names, old.labels)


# ------------------------------------------------------------- shared cases
@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_shared_edit_case(case, copy_of):
    target = copy_of(case["fixture"])
    before = _sha(target)
    if "error" in case:
        with pytest.raises(DtaLabelError, match=_escape(case["error"])):
            edit_labels(target, **case["edit"])
        assert _sha(target) == before  # a refused edit writes nothing
        return
    dry = edit_labels(target, dry_run=True, **case["edit"])
    assert _sha(target) == before
    result = edit_labels(target, **case["edit"])
    assert result.to_dict() == dry.to_dict() == case["result"]
    assert _sha(target) == case["sha256"]
    assert (_sha(target) == before) == (not result.changed)
    # the file reads back as the edit said, and a second run has nothing to do
    again = edit_labels(target, **case["edit"])
    assert not again.changed
    assert _sha(target) == case["sha256"]
    assert sorted(p.name for p in target.parent.iterdir()) == [target.name]


def _escape(text: str) -> str:
    import re

    return re.escape(text)


# ------------------------------------------------------------ what is kept
def test_only_the_edited_fields_differ_when_patching_in_place(copy_of):
    target = copy_of("survey118.dta")
    original = target.read_bytes()
    result = edit_labels(target, attach={"score": "yn"})
    assert result.rewritten is False
    edited = target.read_bytes()
    assert len(edited) == len(original)
    changed = [i for i, (a, b) in enumerate(zip(original, edited)) if a != b]
    # 'yn' written into one 129-byte name field
    assert len(changed) == 2 and changed[1] - changed[0] == 1


def test_a_rewrite_copies_every_untouched_section(copy_of):
    target = copy_of("modern118.dta")
    before = read_metadata(target)
    original = target.read_bytes()
    edit_labels(target, value_labels={"extra": {1: "one"}})
    after = read_metadata(target)
    edited = target.read_bytes()
    # everything before the value labels is the same bytes, map aside
    data_end = original.index(b"</data>")
    map_at = original.index(b"<map>") + 5
    assert edited[:map_at] == original[:map_at]
    assert edited[map_at + 112 : data_end] == original[map_at + 112 : data_end]
    # the set that was not named keeps its bytes
    start = original.index(b"<lbl>")
    end = original.index(b"</lbl>") + 6
    assert original[start:end] in edited
    assert after.value_labels == {**before.value_labels, "extra": {1: "one"}}
    assert (after.names, after.labels, after.notes) == (
        before.names,
        before.labels,
        before.notes,
    )
    assert edited.endswith(b"</value_labels></stata_dta>")


def test_the_map_follows_a_longer_dataset_label(copy_of):
    import struct

    target = copy_of("modern118.dta")
    original = target.read_bytes()
    edit_labels(target, data_label="A dataset label that is longer")
    edited = target.read_bytes()
    grew = len(edited) - len(original)
    assert grew == len("A dataset label that is longer") - len("Fixture dataset")

    def section_map(raw: bytes) -> tuple[int, ...]:
        return struct.unpack_from("<14Q", raw, raw.index(b"<map>") + 5)

    old, new = section_map(original), section_map(edited)
    assert new[0] == 0 and [n - o for o, n in zip(old[1:], new[1:])] == [grew] * 13
    tags = [
        b"<stata_dta>",
        b"<map>",
        b"<variable_types>",
        b"<varnames>",
        b"<sortlist>",
        b"<formats>",
        b"<value_label_names>",
        b"<variable_labels>",
        b"<characteristics>",
        b"<data>",
        b"<strls>",
        b"<value_labels>",
        b"</stata_dta>",
    ]
    for offset, tag in zip(new, tags):
        assert edited[offset : offset + len(tag)] == tag
    assert new[13] == len(edited)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_file_mode_survives_a_rewrite(copy_of):
    target = copy_of("survey118.dta")
    os.chmod(target, 0o640)
    edit_labels(target, value_labels={"yn": None})
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_a_failed_rewrite_leaves_the_file_and_no_temp_file(copy_of, monkeypatch):
    target = copy_of("survey118.dta")
    before = _sha(target)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(dta_edit.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        edit_labels(target, value_labels={"yn": {1: "Y"}})
    assert _sha(target) == before
    assert [p.name for p in target.parent.iterdir()] == [target.name]


def test_codes_may_be_ints_or_strings(copy_of):
    a, b = copy_of("survey118.dta"), copy_of("modern118.dta")
    shutil.copyfile(a, b)
    edit_labels(a, value_labels={"yn": {0: "n", 1: "y", ".b": "dk"}})
    edit_labels(b, value_labels={"yn": {"1": "y", ".b": "dk", "0": "n"}})
    assert _sha(a) == _sha(b)
    assert read_metadata(a).value_labels["yn"] == {0: "n", 1: "y", ".b": "dk"}


def test_rejects_files_that_are_not_dta(tmp_path):
    junk = tmp_path / "junk.dta"
    junk.write_bytes(b"XX this is not a dataset at all")
    with pytest.raises(DtaLabelError, match="not a Stata .dta file"):
        edit_labels(junk, data_label="x")
    with pytest.raises(DtaLabelError):
        read_metadata(junk)


def test_result_reports_nothing_for_an_empty_call(copy_of):
    target = copy_of("survey118.dta")
    result = edit_labels(target)
    assert isinstance(result, DtaEdit) and not result.changed and not result.rewritten


# ------------------------------------------------------------------ MCP tool
@pytest.fixture
def server():
    pytest.importorskip("mcp")
    from stata_code.mcp import server as module

    return module


def test_tool_is_registered_as_a_writing_tool(server):
    tool = next(t for t in server._tool_definitions() if t.name == "set_value_labels")
    assert tool.annotations.readOnlyHint is False
    assert tool.inputSchema["required"] == ["path"]
    assert tool.inputSchema["additionalProperties"] is False


def test_tool_reads_everything_when_no_edit_is_given(server, copy_of):
    file = copy_of("modern118.dta")
    before = _sha(file)
    payload = server._set_value_labels_tool({"path": str(file)}).structuredContent
    assert payload["ok"] is True and payload["release"] == 118
    assert payload["data_label"] == "Fixture dataset"
    assert payload["value_labels"] == {"grplbl": {"0": "control", "1": "treated", ".a": "refused"}}
    assert payload["notes"] == ["dataset note one"]
    grp = next(v for v in payload["variables"] if v["name"] == "grp")
    assert grp["value_label"] == "grplbl" and grp["label"] == "Treatment group"
    assert _sha(file) == before


def test_tool_edits_and_reports(server, copy_of):
    file = copy_of("survey118.dta")
    result = server._set_value_labels_tool(
        {
            "path": str(file),
            "value_labels": {"agree": {"1": "Agree", "2": "Disagree"}, "yn": None},
            "attach": {"score": "agree"},
            "data_label": "Survey, wave 2",
        }
    )
    assert not result.isError
    payload = result.structuredContent
    assert payload["changed"]["value_labels"] == {"agree": "defined", "yn": "dropped"}
    assert payload["changed"]["attached"] == [
        {"name": "female", "before": "yn", "after": ""},
        {"name": "score", "before": "", "after": "agree"},
    ]
    assert payload["changed"]["data_label"]["after"] == "Survey, wave 2"
    assert payload["rewritten"] is True
    meta = read_metadata(file)
    assert meta.value_labels["agree"] == {1: "Agree", 2: "Disagree"}
    assert "yn" not in meta.value_labels and meta.data_label == "Survey, wave 2"


def test_tool_dry_run_and_errors(server, copy_of):
    file = copy_of("survey118.dta")
    before = _sha(file)
    dry = server._set_value_labels_tool(
        {"path": str(file), "value_labels": {"yn": {"1": "Y"}}, "dry_run": True}
    ).structuredContent
    assert dry["changed"]["value_labels"] == {"yn": "modified"}
    assert dry["rewritten"] is False and dry["dry_run"] is True
    refused = server._set_value_labels_tool({"path": str(file), "attach": {"city": "yn"}})
    assert refused.isError and "string variable" in refused.structuredContent["error"]
    assert _sha(file) == before
    missing = server._set_value_labels_tool({"path": str(file.with_name("nope.dta"))})
    assert missing.structuredContent["kind"] == "file_not_found"
    assert server._set_value_labels_tool({}).structuredContent["kind"] == "missing_argument"
    bad = server._set_value_labels_tool({"path": str(file), "attach": {"score": 3}})
    assert bad.structuredContent["kind"] == "invalid_request"


def test_tool_dispatch_checks_arguments(server, copy_of):
    file = copy_of("survey118.dta")
    ok = asyncio.run(
        server._dispatch("set_value_labels", {"path": str(file), "attach": {"score": "yn"}})
    )
    assert not ok.isError
    assert read_metadata(file).value_label_names[read_metadata(file).names.index("score")] == "yn"
    typo = asyncio.run(
        server._dispatch("set_value_labels", {"path": str(file), "attached": {"score": "yn"}})
    )
    assert typo.isError


# ---------------------------------------------------------------- real Stata
def _stata_literal(text: str) -> str:
    return '`"' + text + "\"'"


@pytest.mark.stata_required
@pytest.mark.parametrize(
    "case",
    [c for c in CASES if "error" not in c],
    ids=[c["name"] for c in CASES if "error" not in c],
)
def test_stata_reads_the_edited_file(case, copy_of):
    """Stata itself opens every edited fixture and reports the edit."""
    from stata_code import is_available, run

    if not is_available():
        pytest.skip("pystata / Stata 17+ not available")
    target = copy_of(case["fixture"])
    edit_labels(target, **case["edit"])
    meta = read_metadata(target)
    lines = [
        f'quietly use "{FIXTURES / case["fixture"]}", clear',
        "capture datasignature",
        "local rc0 = _rc",
        "local sig0 = r(datasignature)",
        "quietly notes _count n0 : _dta",
        f'quietly use "{target}", clear',
        "capture datasignature",
        # the observations are the ones Stata wrote
        "if `rc0' == 0 assert _rc == 0 & \"`sig0'\" == r(datasignature)",
        "quietly notes _count n1 : _dta",
        "assert `n0' == `n1'",
        f"assert `\"`: data label'\"' == {_stata_literal(meta.data_label)}",
    ]
    for name, label, set_name in zip(meta.names, meta.labels, meta.value_label_names):
        lines.append(f"assert `\"`: variable label {name}'\"' == {_stata_literal(label)}")
        lines.append(f'assert "`: value label {name}\'" == "{set_name}"')
    for name, table in meta.value_labels.items():
        lines.append(f"quietly label list {name}")
        lines.append(f"assert r(k) == {len(table)}")
        for code, text in table.items():
            lines.append(f"assert `\"`: label {name} {code}'\"' == {_stata_literal(text)}")
    for name, what in case["result"]["value_labels"].items():
        if what == "dropped":
            lines += [f"capture label list {name}", "assert _rc == 111"]
    # a failed `assert` stops the run with r(9)
    result = run("\n".join(lines), session_id="dta_edit_check", include_full_log=False)
    assert result.ok and result.rc == 0, result.error
