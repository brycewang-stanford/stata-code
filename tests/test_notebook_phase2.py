"""Tests for the notebook Phase 2 helpers (locate, edit, insert, delete)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from stata_code.core.notebook import (
    NotebookError,
    delete_cell,
    edit_cell,
    insert_cell,
    locate_cells,
    outline_notebook,
)


def _write_nb(tmp_path: Path, cells: list[dict[str, Any]], **extra: Any) -> Path:
    nb = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {"kernelspec": {"name": "python3"}},
        "cells": cells,
    }
    nb.update(extra)
    path = tmp_path / "nb.ipynb"
    path.write_text(json.dumps(nb), encoding="utf-8")
    return path


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# locate_cells
# ─────────────────────────────────────────────────────────────────────────────


def test_locate_snippet_exact_match(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [
            {"cell_type": "code", "id": "a", "source": "sysuse auto\nsummarize price\n",
             "metadata": {}, "outputs": []},
            {"cell_type": "code", "id": "b", "source": "regress price mpg\n",
             "metadata": {}, "outputs": []},
        ],
    )
    out = locate_cells(path, snippet="summarize price")
    assert out["match_count"] == 1
    m = out["matches"][0]
    assert m["cell_id"] == "a"
    assert m["score"] == 1.0
    assert m["line_in_cell"] == 2


def test_locate_snippet_whitespace_fallback(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [
            {"cell_type": "code", "id": "a", "source": "    regress price mpg",
             "metadata": {}, "outputs": []},
        ],
    )
    out = locate_cells(path, snippet="regress price mpg")  # exact match still works
    assert out["match_count"] == 1
    assert out["matches"][0]["score"] == 1.0


def test_locate_regex_multiline(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [
            {"cell_type": "code", "id": "a",
             "source": "use data.dta\nreg y x1 x2\n",
             "metadata": {}, "outputs": []},
            {"cell_type": "markdown", "id": "m",
             "source": "# Heading", "metadata": {}},
        ],
    )
    out = locate_cells(path, regex=r"^reg\s+y", cell_type="code")
    assert out["match_count"] == 1
    assert out["matches"][0]["cell_id"] == "a"
    assert out["matches"][0]["line_in_cell"] == 2


def test_locate_error_text_uses_longest_codey_line(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [
            {"cell_type": "code", "id": "a",
             "source": "regress mpg foreign weight\n",
             "metadata": {}, "outputs": []},
            {"cell_type": "code", "id": "b",
             "source": "summarize price\n",
             "metadata": {}, "outputs": []},
        ],
    )
    err = (
        "r(111);\n"
        "Variable foreign not found\n"
        ". regress mpg foreign weight\n"
    )
    out = locate_cells(path, error_text=err)
    assert out["match_count"] >= 1
    assert out["matches"][0]["cell_id"] == "a"


def test_locate_query_required(tmp_path: Path) -> None:
    path = _write_nb(tmp_path, [])
    with pytest.raises(NotebookError, match="locate_query_required"):
        locate_cells(path)


def test_locate_query_conflict(tmp_path: Path) -> None:
    path = _write_nb(tmp_path, [])
    with pytest.raises(NotebookError, match="locate_query_conflict"):
        locate_cells(path, snippet="x", regex="y")


def test_locate_regex_invalid(tmp_path: Path) -> None:
    path = _write_nb(tmp_path, [{"cell_type": "code", "id": "a", "source": "",
                                  "metadata": {}, "outputs": []}])
    with pytest.raises(NotebookError, match="locate_regex_invalid"):
        locate_cells(path, regex="[unclosed")


def test_locate_cell_type_filter_invalid(tmp_path: Path) -> None:
    path = _write_nb(tmp_path, [])
    with pytest.raises(NotebookError, match="cell_type_invalid"):
        locate_cells(path, snippet="x", cell_type="bogus")


def test_locate_limit_caps_results(tmp_path: Path) -> None:
    cells = [
        {"cell_type": "code", "id": f"c{i}",
         "source": "common_token here\n", "metadata": {}, "outputs": []}
        for i in range(5)
    ]
    path = _write_nb(tmp_path, cells)
    out = locate_cells(path, snippet="common_token", limit=3)
    assert len(out["matches"]) == 3


def test_locate_no_matches_returns_zero(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [{"cell_type": "code", "id": "a", "source": "hello\n",
          "metadata": {}, "outputs": []}],
    )
    out = locate_cells(path, snippet="nope")
    assert out["match_count"] == 0
    assert out["matches"] == []


# ─────────────────────────────────────────────────────────────────────────────
# edit_cell
# ─────────────────────────────────────────────────────────────────────────────


def test_edit_cell_replaces_source_and_clears_outputs(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [
            {
                "cell_type": "code",
                "id": "x",
                "execution_count": 5,
                "source": "old code",
                "metadata": {"tags": ["keep"]},
                "outputs": [{"output_type": "stream", "text": "noise"}],
            }
        ],
    )
    result = edit_cell(path, cell_id="x", new_source="new code\n")
    assert result["source"] == "new code\n"
    assert result["previous_source"] == "old code"

    nb = _read(path)
    cell = nb["cells"][0]
    assert cell["source"] == "new code\n"
    assert cell["outputs"] == []
    assert cell["execution_count"] is None
    assert cell["metadata"] == {"tags": ["keep"]}
    assert cell["id"] == "x"  # preserved


def test_edit_cell_preserves_markdown_metadata(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [{"cell_type": "markdown", "id": "m", "source": "# Old",
          "metadata": {"slideshow": {"slide_type": "slide"}}}],
    )
    edit_cell(path, cell_id="m", new_source="# New")
    cell = _read(path)["cells"][0]
    assert cell["source"] == "# New"
    assert cell["metadata"] == {"slideshow": {"slide_type": "slide"}}
    assert "outputs" not in cell  # markdown has no outputs key in the source


def test_edit_cell_drift_guard(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [{"cell_type": "code", "id": "x", "source": "current",
          "metadata": {}, "outputs": []}],
    )
    with pytest.raises(NotebookError, match="edit_source_drift"):
        edit_cell(path, cell_id="x", new_source="next", expected_source="STALE")
    # Original source is untouched on drift.
    cell = _read(path)["cells"][0]
    assert cell["source"] == "current"


def test_edit_cell_drift_match_succeeds(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [{"cell_type": "code", "id": "x", "source": "current",
          "metadata": {}, "outputs": []}],
    )
    edit_cell(path, cell_id="x", new_source="next", expected_source="current")
    cell = _read(path)["cells"][0]
    assert cell["source"] == "next"


def test_edit_cell_synth_id_upgrades_to_real_uuid(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [{"cell_type": "code", "source": "x=1", "metadata": {}, "outputs": []}],
    )
    out = outline_notebook(path)
    synth_id = out["cells"][0]["cell_id"]
    assert synth_id.startswith("synth-")

    edit_cell(path, cell_id=synth_id, new_source="x=2")
    cell = _read(path)["cells"][0]
    assert cell["source"] == "x=2"
    assert isinstance(cell["id"], str)
    assert not cell["id"].startswith("synth-")
    # nbformat 4.5+ ids should round-trip a UUID
    assert re.match(r"^[0-9a-f-]{36}$", cell["id"])


def test_edit_cell_unknown_id(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [{"cell_type": "code", "id": "x", "source": "", "metadata": {}, "outputs": []}],
    )
    with pytest.raises(NotebookError, match="cell_not_found"):
        edit_cell(path, cell_id="missing", new_source="y")


def test_edit_cell_new_source_must_be_string(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [{"cell_type": "code", "id": "x", "source": "", "metadata": {}, "outputs": []}],
    )
    with pytest.raises(NotebookError, match="edit_source_invalid"):
        edit_cell(path, cell_id="x", new_source=123)  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# insert_cell
# ─────────────────────────────────────────────────────────────────────────────


def test_insert_cell_after_anchor(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [
            {"cell_type": "code", "id": "a", "source": "x=1", "metadata": {}, "outputs": []},
            {"cell_type": "code", "id": "b", "source": "x=2", "metadata": {}, "outputs": []},
        ],
    )
    out = insert_cell(path, source="y=10", after_cell_id="a")
    assert out["index"] == 1
    nb = _read(path)
    assert [c["source"] for c in nb["cells"]] == ["x=1", "y=10", "x=2"]
    assert nb["cells"][1]["id"] == out["cell_id"]
    assert nb["cells"][1]["execution_count"] is None
    assert nb["cells"][1]["outputs"] == []


def test_insert_cell_before_anchor(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [
            {"cell_type": "code", "id": "a", "source": "x=1", "metadata": {}, "outputs": []},
        ],
    )
    out = insert_cell(path, source="comment", cell_type="markdown", before_cell_id="a")
    assert out["index"] == 0
    cells = _read(path)["cells"]
    assert cells[0]["cell_type"] == "markdown"
    assert "outputs" not in cells[0]


def test_insert_cell_at_start_and_at_end(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [{"cell_type": "code", "id": "a", "source": "x=1", "metadata": {}, "outputs": []}],
    )
    insert_cell(path, source="first", at_start=True)
    insert_cell(path, source="last", at_end=True)
    cells = _read(path)["cells"]
    assert [c["source"] for c in cells] == ["first", "x=1", "last"]


def test_insert_cell_anchor_required(tmp_path: Path) -> None:
    path = _write_nb(tmp_path, [])
    with pytest.raises(NotebookError, match="insert_anchor_required"):
        insert_cell(path, source="x")


def test_insert_cell_anchor_conflict(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [{"cell_type": "code", "id": "a", "source": "", "metadata": {}, "outputs": []}],
    )
    with pytest.raises(NotebookError, match="insert_anchor_required"):
        insert_cell(path, source="x", at_start=True, at_end=True)


def test_insert_cell_unknown_anchor(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [{"cell_type": "code", "id": "a", "source": "", "metadata": {}, "outputs": []}],
    )
    with pytest.raises(NotebookError, match="cell_not_found"):
        insert_cell(path, source="x", after_cell_id="missing")


def test_insert_cell_invalid_type(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [{"cell_type": "code", "id": "a", "source": "", "metadata": {}, "outputs": []}],
    )
    with pytest.raises(NotebookError, match="cell_type_invalid"):
        insert_cell(path, source="x", cell_type="bogus", at_start=True)


def test_insert_cell_before_synth_anchor_upgrades_anchor_id(
    tmp_path: Path,
) -> None:
    """`before_cell_id=<synth-id>` shifts the anchor's index, which would
    invalidate its synth id. Insertion must upgrade the anchor to a real
    UUID first so the caller's prior cell_id keeps working.
    """
    path = _write_nb(
        tmp_path,
        [{"cell_type": "code", "source": "x=1", "metadata": {}, "outputs": []}],
    )
    out = outline_notebook(path)
    anchor_synth_id = out["cells"][0]["cell_id"]
    assert anchor_synth_id.startswith("synth-")

    insert_cell(path, source="# inserted", cell_type="markdown", before_cell_id=anchor_synth_id)

    cells = _read(path)["cells"]
    assert cells[0]["cell_type"] == "markdown"
    # Anchor moved to index 1 and now has a real UUID, NOT a synth id.
    anchor_id_after = cells[1]["id"]
    assert isinstance(anchor_id_after, str)
    assert not anchor_id_after.startswith("synth-")
    assert re.match(r"^[0-9a-f-]{36}$", anchor_id_after)


def test_insert_cell_after_synth_anchor_upgrades_anchor_id(
    tmp_path: Path,
) -> None:
    """`after_cell_id=<synth-id>` does not shift the anchor's index, but a
    later insertion before the anchor would. Upgrading the anchor on every
    synth-id-based insert removes that latent foot-gun.
    """
    path = _write_nb(
        tmp_path,
        [{"cell_type": "code", "source": "x=1", "metadata": {}, "outputs": []}],
    )
    out = outline_notebook(path)
    anchor_synth_id = out["cells"][0]["cell_id"]

    insert_cell(path, source="x=2", after_cell_id=anchor_synth_id)
    anchor_id_after = _read(path)["cells"][0]["id"]
    assert not anchor_id_after.startswith("synth-")
    assert re.match(r"^[0-9a-f-]{36}$", anchor_id_after)


def test_insert_cell_id_is_unique_uuid(tmp_path: Path) -> None:
    path = _write_nb(tmp_path, [{"cell_type": "code", "id": "a", "source": "",
                                  "metadata": {}, "outputs": []}])
    a = insert_cell(path, source="1", at_start=True)
    b = insert_cell(path, source="2", at_start=True)
    assert a["cell_id"] != b["cell_id"]
    assert re.match(r"^[0-9a-f-]{36}$", a["cell_id"])


# ─────────────────────────────────────────────────────────────────────────────
# delete_cell
# ─────────────────────────────────────────────────────────────────────────────


def test_delete_cell_removes_and_returns_summary(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [
            {"cell_type": "code", "id": "a", "source": "keep", "metadata": {}, "outputs": []},
            {"cell_type": "code", "id": "b", "source": "drop", "metadata": {}, "outputs": []},
        ],
    )
    result = delete_cell(path, cell_id="b")
    assert result["deleted_source"] == "drop"
    assert result["remaining_cell_count"] == 1
    cells = _read(path)["cells"]
    assert [c["id"] for c in cells] == ["a"]


def test_delete_cell_drift_guard(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [{"cell_type": "code", "id": "a", "source": "current", "metadata": {}, "outputs": []}],
    )
    with pytest.raises(NotebookError, match="delete_source_drift"):
        delete_cell(path, cell_id="a", expected_source="STALE")
    # Cell is still there.
    assert len(_read(path)["cells"]) == 1


def test_delete_cell_unknown_id(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [{"cell_type": "code", "id": "a", "source": "", "metadata": {}, "outputs": []}],
    )
    with pytest.raises(NotebookError, match="cell_not_found"):
        delete_cell(path, cell_id="missing")


# ─────────────────────────────────────────────────────────────────────────────
# Atomic-write integrity
# ─────────────────────────────────────────────────────────────────────────────


def test_edit_cell_does_not_corrupt_unrelated_cells(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [
            {"cell_type": "code", "id": "a", "source": "x=1",
             "metadata": {"tags": ["one"]}, "outputs": []},
            {"cell_type": "markdown", "id": "b", "source": "# H",
             "metadata": {"tags": ["two"]}},
            {"cell_type": "code", "id": "c", "source": "x=3",
             "execution_count": 9, "metadata": {}, "outputs": [
                 {"output_type": "stream", "text": "preserved"}
             ]},
        ],
    )
    edit_cell(path, cell_id="a", new_source="x=99")
    nb = _read(path)
    assert nb["cells"][1]["source"] == "# H"
    assert nb["cells"][1]["metadata"] == {"tags": ["two"]}
    assert nb["cells"][2]["source"] == "x=3"
    assert nb["cells"][2]["execution_count"] == 9
    assert nb["cells"][2]["outputs"][0]["text"] == "preserved"
    assert nb["nbformat"] == 4
    assert nb["nbformat_minor"] == 5


def test_edit_cell_no_temp_files_left_behind(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [{"cell_type": "code", "id": "a", "source": "old", "metadata": {}, "outputs": []}],
    )
    edit_cell(path, cell_id="a", new_source="new")
    # Only the notebook file should remain.
    leftover = [p.name for p in tmp_path.iterdir()]
    assert leftover == ["nb.ipynb"]


# ─────────────────────────────────────────────────────────────────────────────
# Pre-4.5 id stability across structural mutations
# ─────────────────────────────────────────────────────────────────────────────


def test_insert_at_start_upgrades_all_synth_ids(tmp_path: Path) -> None:
    """`at_start` shifts every existing cell down one slot, which silently
    invalidates every index-derived synth id the caller is holding. Inserting
    must upgrade every pre-4.5 cell to a fresh UUID so previously-cached
    handles either still resolve (if the caller re-reads) or fail loudly.
    """
    path = _write_nb(
        tmp_path,
        [
            {"cell_type": "code", "source": "x=1", "metadata": {}, "outputs": []},
            {"cell_type": "code", "source": "x=2", "metadata": {}, "outputs": []},
            {"cell_type": "markdown", "source": "# H", "metadata": {}},
        ],
    )
    insert_cell(path, source="first", at_start=True)
    cells = _read(path)["cells"]
    assert len(cells) == 4
    # Every cell now carries a real UUID — no synth ids survive on disk.
    for cell in cells:
        cid = cell["id"]
        assert isinstance(cid, str)
        assert not cid.startswith("synth-")
        assert re.match(r"^[0-9a-f-]{36}$", cid)
    # Ids are unique.
    assert len({c["id"] for c in cells}) == 4


def test_insert_at_end_upgrades_all_synth_ids(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [
            {"cell_type": "code", "source": "x=1", "metadata": {}, "outputs": []},
            {"cell_type": "code", "source": "x=2", "metadata": {}, "outputs": []},
        ],
    )
    insert_cell(path, source="last", at_end=True)
    cells = _read(path)["cells"]
    for cell in cells:
        assert not cell["id"].startswith("synth-")


def test_insert_after_upgrades_unrelated_synth_ids(tmp_path: Path) -> None:
    """The anchor's synth id was already upgraded by prior versions, but
    other synth-id cells past the insertion point would silently shift.
    All cells must end up with stable native ids.
    """
    path = _write_nb(
        tmp_path,
        [
            {"cell_type": "code", "source": "x=1", "metadata": {}, "outputs": []},
            {"cell_type": "code", "source": "x=2", "metadata": {}, "outputs": []},
            {"cell_type": "code", "source": "x=3", "metadata": {}, "outputs": []},
        ],
    )
    out = outline_notebook(path)
    anchor_synth = out["cells"][0]["cell_id"]
    third_synth = out["cells"][2]["cell_id"]
    assert anchor_synth.startswith("synth-")
    assert third_synth.startswith("synth-")

    insert_cell(path, source="x=99", after_cell_id=anchor_synth)

    cells = _read(path)["cells"]
    # Cell that was at index 2 ('x=3') is now at index 3 — its synth id
    # would have changed silently. The upgrade made it stable.
    assert cells[3]["source"] == "x=3"
    assert not cells[3]["id"].startswith("synth-")
    assert re.match(r"^[0-9a-f-]{36}$", cells[3]["id"])


def test_delete_cell_upgrades_remaining_synth_ids(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [
            {"cell_type": "code", "source": "drop", "metadata": {}, "outputs": []},
            {"cell_type": "code", "source": "keep1", "metadata": {}, "outputs": []},
            {"cell_type": "code", "source": "keep2", "metadata": {}, "outputs": []},
        ],
    )
    out = outline_notebook(path)
    target = out["cells"][0]["cell_id"]

    delete_cell(path, cell_id=target)
    cells = _read(path)["cells"]
    assert [c["source"] for c in cells] == ["keep1", "keep2"]
    for cell in cells:
        assert not cell["id"].startswith("synth-")


def test_edit_cell_upgrades_target_synth_id_only(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [
            {"cell_type": "code", "source": "x=1", "metadata": {}, "outputs": []},
            {"cell_type": "code", "source": "x=2", "metadata": {}, "outputs": []},
        ],
    )
    out = outline_notebook(path)
    target = out["cells"][0]["cell_id"]

    edit_cell(path, cell_id=target, new_source="x=99")
    cells = _read(path)["cells"]
    assert not cells[0]["id"].startswith("synth-")
    assert "id" not in cells[1]


def test_edit_cell_keeps_other_synth_ids_usable_for_same_outline(
    tmp_path: Path,
) -> None:
    path = _write_nb(
        tmp_path,
        [
            {"cell_type": "code", "source": "x=1", "metadata": {}, "outputs": []},
            {"cell_type": "code", "source": "x=2", "metadata": {}, "outputs": []},
        ],
    )
    out = outline_notebook(path)
    first_id = out["cells"][0]["cell_id"]
    second_id = out["cells"][1]["cell_id"]

    edit_cell(path, cell_id=first_id, new_source="x=10")
    edit_cell(path, cell_id=second_id, new_source="x=20")

    cells = _read(path)["cells"]
    assert [c["source"] for c in cells] == ["x=10", "x=20"]
    assert not cells[0]["id"].startswith("synth-")
    assert not cells[1]["id"].startswith("synth-")
