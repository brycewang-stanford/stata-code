"""Tests for Stata-free variable-label editing (core + MCP tool).

The fixtures are written by a real Stata (vscode/test-fixtures/dta/
make_fixtures.do) and are shared with the VS Code extension's dtaWriter tests,
so the two implementations are held to the same files. That edited files still
load in Stata with the new labels, and with `notes`, `label list` and
`datasignature` unchanged, was checked against Stata 18.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from stata_code.core.dta_labels import (
    DtaLabelError,
    LabelChange,
    read_variable_labels,
    set_variable_labels,
)

FIXTURES = Path(__file__).resolve().parent.parent / "vscode" / "test-fixtures" / "dta"


@pytest.fixture
def copy_of(tmp_path):
    def _copy(name: str) -> Path:
        target = tmp_path / name
        shutil.copyfile(FIXTURES / name, target)
        return target

    return _copy


def _differing_offsets(a: bytes, b: bytes) -> list[int]:
    assert len(a) == len(b)
    return [i for i, (x, y) in enumerate(zip(a, b)) if x != y]


@pytest.mark.parametrize(
    ("name", "release", "width", "n_vars"),
    [
        ("modern118.dta", 118, 321, 14),
        ("strl117.dta", 117, 81, 14),
        ("legacy115.dta", 115, 81, 13),
        ("alias120.dta", 120, 321, 4),
        ("empty118.dta", 118, 321, 14),
    ],
)
def test_reads_names_and_labels(name, release, width, n_vars):
    found = read_variable_labels(FIXTURES / name)
    assert found.release == release
    assert found.width == width
    assert len(found.names) == n_vars


def test_reads_the_labels_stata_wrote():
    labels = read_variable_labels(FIXTURES / "modern118.dta").as_dict()
    assert labels["b"] == "A byte"
    assert labels["s"] == "字符串 label"
    assert labels["grp"] == "Treatment group"
    assert labels["i"] == ""
    survey = read_variable_labels(FIXTURES / "survey118.dta").as_dict()
    assert survey["city"] == "City of residence"
    assert survey["wage"] == ""


def test_changes_only_the_requested_label_fields(copy_of):
    file = copy_of("modern118.dta")
    before = file.read_bytes()
    located = read_variable_labels(file)
    changes = set_variable_labels(
        file, {"b": "年龄（周岁）", "i": "Income, thousands", "grp": ""}
    )
    assert changes == [
        LabelChange("b", "A byte", "年龄（周岁）"),
        LabelChange("i", "", "Income, thousands"),
        LabelChange("grp", "Treatment group", ""),
    ]
    allowed: set[int] = set()
    for name in ("b", "i", "grp"):
        start = located.offset + located.names.index(name) * located.width
        allowed.update(range(start, start + located.width))
    changed = _differing_offsets(before, file.read_bytes())
    assert changed
    assert set(changed) <= allowed

    labels = read_variable_labels(file).as_dict()
    assert labels["b"] == "年龄（周岁）"
    assert labels["i"] == "Income, thousands"
    assert labels["grp"] == ""
    assert labels["s"] == "字符串 label"


@pytest.mark.parametrize("name", ["strl117.dta", "legacy115.dta", "alias120.dta"])
def test_round_trips_in_every_supported_layout(copy_of, name):
    file = copy_of(name)
    original = read_variable_labels(file).as_dict()
    target = list(original)[-1]
    assert set_variable_labels(file, {target: "Edited label"}) == [
        LabelChange(target, original[target], "Edited label")
    ]
    assert read_variable_labels(file).as_dict() == {**original, target: "Edited label"}


def test_an_invalid_edit_writes_nothing(copy_of):
    file = copy_of("modern118.dta")
    before = file.read_bytes()
    with pytest.raises(DtaLabelError) as excinfo:
        set_variable_labels(file, {"b": "ok", "nope": "x", "i": "y" * 81})
    assert "nope: no such variable" in str(excinfo.value)
    assert "i: label is 81 characters" in str(excinfo.value)
    assert file.read_bytes() == before


def test_dry_run_reports_without_writing(copy_of):
    file = copy_of("modern118.dta")
    before = file.read_bytes()
    assert set_variable_labels(file, {"b": "New"}, dry_run=True) == [
        LabelChange("b", "A byte", "New")
    ]
    assert file.read_bytes() == before


def test_an_unchanged_label_is_not_a_change(copy_of):
    file = copy_of("modern118.dta")
    before = file.read_bytes()
    assert set_variable_labels(file, {"b": "A byte"}) == []
    assert file.read_bytes() == before


def test_length_limit_counts_characters(copy_of):
    file = copy_of("modern118.dta")
    set_variable_labels(file, {"b": "😀" * 80})  # 320 bytes: exactly fits
    assert read_variable_labels(file).as_dict()["b"] == "😀" * 80
    with pytest.raises(DtaLabelError, match="at most 80"):
        set_variable_labels(file, {"b": "中" * 81})


def test_older_formats_take_ascii_only(copy_of):
    for name in ("strl117.dta", "legacy115.dta"):
        file = copy_of(name)
        with pytest.raises(DtaLabelError, match="plain-ASCII"):
            set_variable_labels(file, {"b": "年龄"})
        set_variable_labels(file, {"b": "Age (years)"})
        assert read_variable_labels(file).as_dict()["b"] == "Age (years)"


def test_control_characters_are_refused(copy_of):
    file = copy_of("modern118.dta")
    with pytest.raises(DtaLabelError, match="control character"):
        set_variable_labels(file, {"b": "line one\nline two"})


def test_a_shorter_label_leaves_no_tail(copy_of):
    file = copy_of("survey118.dta")
    set_variable_labels(file, {"city": "A much longer label than the one before it"})
    set_variable_labels(file, {"city": "Short"})
    located = read_variable_labels(file)
    start = located.offset + located.names.index("city") * located.width
    field = file.read_bytes()[start : start + located.width]
    assert field == b"Short".ljust(located.width, b"\0")


def test_rejects_files_that_are_not_dta(tmp_path):
    bogus = tmp_path / "bogus.dta"
    bogus.write_bytes(b"PK\x03\x04 a renamed zip, not a dataset")
    with pytest.raises(DtaLabelError, match="not a Stata .dta file"):
        read_variable_labels(bogus)
    cut = tmp_path / "cut.dta"
    cut.write_bytes((FIXTURES / "modern118.dta").read_bytes()[:300])
    with pytest.raises(DtaLabelError, match="malformed or truncated"):
        set_variable_labels(cut, {"b": "x"})


# ── MCP tool ────────────────────────────────────────────────────────────────


@pytest.fixture
def server():
    pytest.importorskip("mcp", reason="mcp package not installed")
    from stata_code.mcp import server as module

    return module


def test_tool_is_registered_as_a_writing_tool(server):
    tool = next(t for t in server._tool_definitions() if t.name == "set_variable_labels")
    assert tool.annotations.readOnlyHint is False
    assert tool.inputSchema["required"] == ["path"]


def test_tool_sets_labels(server, copy_of):
    file = copy_of("survey118.dta")
    result = server._set_variable_labels_tool(
        {"path": str(file), "labels": {"wage": "时薪（美元）", "age": "Age in years"}}
    )
    assert not result.isError
    payload = result.structuredContent
    assert payload["ok"] is True
    assert payload["release"] == 118
    assert payload["changed"] == [{"name": "wage", "before": "", "after": "时薪（美元）"}]
    assert payload["unchanged"] == ["age"]
    assert read_variable_labels(file).as_dict()["wage"] == "时薪（美元）"


def test_tool_reads_labels_when_none_are_given(server, copy_of):
    file = copy_of("survey118.dta")
    before = file.read_bytes()
    payload = server._set_variable_labels_tool({"path": str(file)}).structuredContent
    assert payload["labels"]["id"] == "Respondent id"
    assert payload["labels"]["wage"] == ""
    assert file.read_bytes() == before


def test_tool_dry_run(server, copy_of):
    file = copy_of("survey118.dta")
    before = file.read_bytes()
    payload = server._set_variable_labels_tool(
        {"path": str(file), "labels": {"wage": "Hourly wage"}, "dry_run": True}
    ).structuredContent
    assert payload["dry_run"] is True
    assert payload["changed"] == [{"name": "wage", "before": "", "after": "Hourly wage"}]
    assert file.read_bytes() == before


def test_tool_errors(server, copy_of, tmp_path):
    file = copy_of("survey118.dta")
    unknown = server._set_variable_labels_tool({"path": str(file), "labels": {"zz": "x"}})
    assert unknown.isError
    assert unknown.structuredContent["kind"] == "invalid_request"
    assert "zz: no such variable" in unknown.structuredContent["error"]

    missing = server._set_variable_labels_tool(
        {"path": str(tmp_path / "none.dta"), "labels": {"a": "b"}}
    )
    assert missing.structuredContent["kind"] == "file_not_found"

    bad = server._set_variable_labels_tool({"path": str(file), "labels": {"wage": 3}})
    assert bad.structuredContent["kind"] == "invalid_request"
    assert server._set_variable_labels_tool({}).structuredContent["kind"] == "missing_argument"


def test_tool_dispatch_end_to_end(server, copy_of):
    file = copy_of("survey118.dta")
    result = asyncio.run(
        server._dispatch("set_variable_labels", {"path": str(file), "labels": {"wage": "W"}})
    )
    assert result.structuredContent["changed"][0]["after"] == "W"
    typo = asyncio.run(
        server._dispatch("set_variable_labels", {"path": str(file), "label": {"wage": "W"}})
    )
    assert typo.isError
