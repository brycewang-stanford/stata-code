"""Tests for the notebook reader (Phase 1: outline + get_cell)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stata_code.core.notebook import (
    NotebookError,
    get_cell,
    outline_notebook,
)


def _write_nb(tmp_path: Path, cells: list[dict[str, Any]], **extra: Any) -> Path:
    nb = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {
                "name": "python3",
                "display_name": "Python 3",
                "language": "python",
            }
        },
        "cells": cells,
    }
    nb.update(extra)
    path = tmp_path / "nb.ipynb"
    path.write_text(json.dumps(nb), encoding="utf-8")
    return path


def test_outline_lists_cells_and_kernelspec(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [
            {
                "cell_type": "markdown",
                "id": "mkd-1",
                "source": ["# Title\n", "Intro\n"],
                "metadata": {},
            },
            {
                "cell_type": "code",
                "id": "code-1",
                "execution_count": 7,
                "source": "sysuse auto\nsummarize price\n",
                "metadata": {},
                "outputs": [
                    {"output_type": "stream", "name": "stdout", "text": "ok\n"}
                ],
            },
        ],
    )
    out = outline_notebook(path)
    assert out["cell_count"] == 2
    assert out["nbformat"] == 4
    assert out["kernelspec"]["language"] == "python"
    md, code = out["cells"]
    assert md["cell_type"] == "markdown"
    assert md["id_synthesized"] is False
    assert code["cell_id"] == "code-1"
    assert code["execution_count"] == 7
    assert code["has_outputs"] is True
    assert code["has_error_output"] is False
    assert code["line_count"] == 2
    assert "summarize" in code["source_preview"] or "sysuse" in code["source_preview"]


def test_outline_synthesizes_id_when_missing(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [
            {
                "cell_type": "code",
                "execution_count": None,
                "source": ["regress y x\n"],
                "metadata": {},
                "outputs": [],
            }
        ],
    )
    out = outline_notebook(path)
    cell = out["cells"][0]
    assert cell["id_synthesized"] is True
    assert cell["cell_id"].startswith("synth-0-")


def test_outline_flags_error_output(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [
            {
                "cell_type": "code",
                "id": "boom",
                "execution_count": 1,
                "source": "1/0\n",
                "metadata": {},
                "outputs": [
                    {
                        "output_type": "error",
                        "ename": "ZeroDivisionError",
                        "evalue": "division by zero",
                        "traceback": ["t1", "t2"],
                    }
                ],
            }
        ],
    )
    out = outline_notebook(path)
    assert out["cells"][0]["has_error_output"] is True


def test_get_cell_by_id_returns_full_source_and_outputs_summary(
    tmp_path: Path,
) -> None:
    path = _write_nb(
        tmp_path,
        [
            {
                "cell_type": "code",
                "id": "x",
                "execution_count": 2,
                "source": "print('hello')\n",
                "metadata": {"tags": ["demo"]},
                "outputs": [
                    {"output_type": "stream", "name": "stdout", "text": "hello\n"},
                    {
                        "output_type": "execute_result",
                        "data": {"text/plain": "None", "image/png": "AAA"},
                        "metadata": {},
                        "execution_count": 2,
                    },
                ],
            }
        ],
    )
    detail = get_cell(path, cell_id="x")
    assert detail["source"] == "print('hello')\n"
    assert detail["execution_count"] == 2
    assert detail["metadata"] == {"tags": ["demo"]}
    summary = detail["outputs_summary"]
    assert summary["count"] == 2
    assert summary["has_image"] is True
    assert "stream" in summary["types"]
    assert "execute_result" in summary["types"]
    assert summary["text_truncated"] is False


def test_get_cell_by_index_works(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [
            {"cell_type": "markdown", "id": "m", "source": "x", "metadata": {}},
            {
                "cell_type": "code",
                "id": "y",
                "execution_count": 1,
                "source": "1+1",
                "metadata": {},
                "outputs": [],
            },
        ],
    )
    detail = get_cell(path, cell_index=1)
    assert detail["cell_id"] == "y"


def test_get_cell_id_index_must_agree(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [
            {"cell_type": "code", "id": "a", "source": "", "metadata": {}, "outputs": []},
            {"cell_type": "code", "id": "b", "source": "", "metadata": {}, "outputs": []},
        ],
    )
    with pytest.raises(NotebookError, match="cell_id_index_mismatch"):
        get_cell(path, cell_id="a", cell_index=1)


def test_get_cell_unknown_id(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [{"cell_type": "code", "id": "a", "source": "", "metadata": {}, "outputs": []}],
    )
    with pytest.raises(NotebookError, match="cell_not_found"):
        get_cell(path, cell_id="missing")


def test_get_cell_index_out_of_range(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [{"cell_type": "code", "id": "a", "source": "", "metadata": {}, "outputs": []}],
    )
    with pytest.raises(NotebookError, match="cell_index_out_of_range"):
        get_cell(path, cell_index=5)


def test_get_cell_locator_required(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [{"cell_type": "code", "id": "a", "source": "", "metadata": {}, "outputs": []}],
    )
    with pytest.raises(NotebookError, match="cell_locator_required"):
        get_cell(path)


def test_outline_missing_file() -> None:
    with pytest.raises(NotebookError, match="notebook_not_found"):
        outline_notebook("/this/path/does/not/exist.ipynb")


def test_outline_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "bad.ipynb"
    p.write_text("not valid json", encoding="utf-8")
    with pytest.raises(NotebookError, match="notebook_invalid_json"):
        outline_notebook(p)


def test_outline_invalid_structure(tmp_path: Path) -> None:
    p = tmp_path / "bad.ipynb"
    p.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
    with pytest.raises(NotebookError, match="notebook_invalid_structure"):
        outline_notebook(p)


def test_outline_synth_id_resolves_in_get_cell(tmp_path: Path) -> None:
    path = _write_nb(
        tmp_path,
        [{"cell_type": "code", "source": "x=1", "metadata": {}, "outputs": []}],
    )
    out = outline_notebook(path)
    synth_id = out["cells"][0]["cell_id"]
    detail = get_cell(path, cell_id=synth_id)
    assert detail["index"] == 0
    assert detail["source"] == "x=1"


def test_outputs_summary_streams_and_truncates_text(tmp_path: Path) -> None:
    """A cell with many large stream outputs should not materialise the full
    concatenation in memory — `_summarise_outputs` truncates as it goes.
    The post-fix contract: `text_preview` length ≤ budget + suffix, and
    `text_chars_total` reflects the full pre-truncation size.
    """
    big = "x" * 5_000  # one output well above the 4_000-char budget
    path = _write_nb(
        tmp_path,
        [
            {
                "cell_type": "code",
                "id": "x",
                "execution_count": 1,
                "source": "spam()",
                "metadata": {},
                "outputs": [
                    {"output_type": "stream", "name": "stdout", "text": big}
                    for _ in range(50)
                ],
            }
        ],
    )
    detail = get_cell(path, cell_id="x")
    summary = detail["outputs_summary"]
    assert summary["text_truncated"] is True
    # Budget is 4 000 chars; suffix "…[truncated]" is then appended.
    assert len(summary["text_preview"]) <= 4_000 + len("…[truncated]")
    assert summary["text_chars_total"] == 50 * 5_000


def test_traceback_truncation(tmp_path: Path) -> None:
    long_tb = [f"line {i}" for i in range(100)]
    path = _write_nb(
        tmp_path,
        [
            {
                "cell_type": "code",
                "id": "e",
                "execution_count": 1,
                "source": "boom",
                "metadata": {},
                "outputs": [
                    {
                        "output_type": "error",
                        "ename": "RuntimeError",
                        "evalue": "x",
                        "traceback": long_tb,
                    }
                ],
            }
        ],
    )
    detail = get_cell(path, cell_id="e")
    err = detail["outputs_summary"]["error"]
    assert err["traceback_truncated"] is True
    assert err["traceback_lines_total"] == 100
    assert len(err["traceback_head"]) == 20
    assert len(err["traceback_tail"]) == 20
