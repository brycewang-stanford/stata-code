"""Tests for persistent run log artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from stata_code.core.log_artifacts import (
    changed_output_files,
    copy_output_artifacts,
    persist_run_log_files,
    snapshot_working_dir_files,
    update_run_artifact_manifest,
)
from stata_code.core.schema import Backend, StataEdition, StataInfo


def _stata() -> StataInfo:
    return StataInfo(version="18.0", edition=StataEdition.MP, backend=Backend.PYSTATA)


def test_persist_run_log_files_creates_immutable_run_bundle(tmp_path: Path) -> None:
    do_file = tmp_path / "test1.do"
    do_file.write_text("di 1+1\n", encoding="utf-8")

    info = persist_run_log_files(
        log_text=" 2\n",
        code="di 1+1\n",
        origin_path=str(do_file),
        origin_kind="file",
        origin_label="demo-tests/test1.do:1",
        request_id="abcdef1234567890",
        session_id="main",
        started_at="2026-05-08T01:22:33.456Z",
        elapsed_ms=12,
        rc=0,
        ok=True,
        stata=_stata(),
        working_dir=str(tmp_path),
    )

    run_dir = Path(info.directory)
    assert run_dir.parent == tmp_path / "log-files"
    assert run_dir.name == "test1__20260508T012233456Z__main__abcdef1234"
    assert info.append is False
    assert info.policy == "per_run_directory"

    log_text = Path(info.log_path).read_text(encoding="utf-8")
    assert "source_do:" in log_text
    assert f"working_dir: {tmp_path}" in log_text
    assert "mcp_server: stata-code" in log_text
    assert "policy: immutable per-run directory under log-files; append=false" in log_text
    assert " 2\n" in log_text

    assert Path(info.smcl_path).read_text(encoding="utf-8").startswith("{smcl}\n")
    assert Path(info.code_path or "").read_text(encoding="utf-8") == "di 1+1\n"

    manifest = json.loads(Path(info.manifest_path).read_text(encoding="utf-8"))
    assert manifest["source_path"] == str(do_file)
    assert manifest["origin_kind"] == "file"
    assert manifest["origin_cell_id"] is None
    assert manifest["working_dir"] == str(tmp_path)
    assert manifest["request_id"] == "abcdef1234567890"
    assert manifest["files"]["log"] == info.log_path


def test_persist_run_log_files_records_origin_cell_id(tmp_path: Path) -> None:
    nb = tmp_path / "analysis.ipynb"
    nb.write_text("{}", encoding="utf-8")  # placeholder; runner only uses the path

    info = persist_run_log_files(
        log_text=" 2\n",
        code="di 1+1\n",
        origin_path=str(nb),
        origin_kind="cell",
        origin_label="analysis.ipynb:cell-3",
        origin_cell_id="8f2c1a40-1f3d-4b7e-9a1b-bd3a17a90c33",
        request_id="cellrun01abcdef",
        session_id="main",
        started_at="2026-05-08T02:00:00.000Z",
        elapsed_ms=5,
        rc=0,
        ok=True,
        stata=_stata(),
        working_dir=str(tmp_path),
    )

    manifest = json.loads(Path(info.manifest_path).read_text(encoding="utf-8"))
    assert manifest["origin_kind"] == "cell"
    assert manifest["origin_cell_id"] == "8f2c1a40-1f3d-4b7e-9a1b-bd3a17a90c33"

    log_text = Path(info.log_path).read_text(encoding="utf-8")
    assert "origin_cell_id: 8f2c1a40-1f3d-4b7e-9a1b-bd3a17a90c33" in log_text


def test_persist_run_log_files_does_not_overwrite_existing_bundle(tmp_path: Path) -> None:
    do_file = tmp_path / "test1.do"
    do_file.write_text("di 1+1\n", encoding="utf-8")
    kwargs = dict(
        code="di 1+1\n",
        origin_path=str(do_file),
        origin_kind="line",
        origin_label="test1.do:1",
        request_id="abcdef1234567890",
        session_id="main",
        started_at="2026-05-08T01:22:33.456Z",
        elapsed_ms=12,
        rc=0,
        ok=True,
        stata=_stata(),
    )

    first = persist_run_log_files(log_text="first\n", **kwargs)
    second = persist_run_log_files(log_text="second\n", **kwargs)

    assert Path(first.directory).name == "test1__20260508T012233456Z__main__abcdef1234"
    assert Path(second.directory).name == "test1__20260508T012233456Z__main__abcdef1234__2"
    assert "first" in Path(first.log_path).read_text(encoding="utf-8")
    assert "second" in Path(second.log_path).read_text(encoding="utf-8")


def test_smcl_escapes_literal_braces(tmp_path: Path) -> None:
    do_file = tmp_path / "curly.do"
    do_file.write_text('display "{txt}"\n', encoding="utf-8")

    info = persist_run_log_files(
        log_text="{txt} should be literal\n",
        code='display "{txt}"\n',
        origin_path=str(do_file),
        origin_kind="selection",
        origin_label="curly.do:1",
        request_id="fedcba9876543210",
        session_id="analysis",
        started_at="2026-05-08T01:22:33.456Z",
        elapsed_ms=12,
        rc=0,
        ok=True,
        stata=_stata(),
    )

    smcl = Path(info.smcl_path).read_text(encoding="utf-8")
    assert "{txt}{c -(}txt{c )-} should be literal" in smcl


def test_generated_outputs_are_copied_into_run_bundle(tmp_path: Path) -> None:
    do_file = tmp_path / "test1.do"
    do_file.write_text("di 1+1\n", encoding="utf-8")
    before = snapshot_working_dir_files(tmp_path, origin_path=do_file)

    table = tmp_path / "table.csv"
    table.write_text("x,y\n1,2\n", encoding="utf-8")
    ignored = tmp_path / "scratch.tmp"
    ignored.write_text("ignore\n", encoding="utf-8")
    nested = tmp_path / "exports" / "summary.tex"
    nested.parent.mkdir()
    nested.write_text("\\begin{tabular}{c}x\\end{tabular}\n", encoding="utf-8")

    changed = changed_output_files(before, tmp_path, origin_path=do_file)
    assert changed == [nested, table]

    info = persist_run_log_files(
        log_text="done\n",
        code="di 1+1\n",
        origin_path=str(do_file),
        origin_kind="file",
        origin_label="test1.do:1",
        request_id="abcdef1234567890",
        session_id="main",
        started_at="2026-05-08T01:22:33.456Z",
        elapsed_ms=12,
        rc=0,
        ok=True,
        stata=_stata(),
        working_dir=str(tmp_path),
    )
    info = copy_output_artifacts(info, changed, working_dir=tmp_path)
    update_run_artifact_manifest(info)

    copied = {Path(p).relative_to(Path(info.outputs_dir or "")) for p in info.output_paths}
    assert copied == {Path("table.csv"), Path("exports/summary.tex")}

    manifest = json.loads(Path(info.manifest_path).read_text(encoding="utf-8"))
    assert manifest["files"]["outputs_dir"] == info.outputs_dir
    assert manifest["files"]["output_paths"] == info.output_paths


def test_manifest_writes_are_atomic_and_leave_no_temp(tmp_path: Path) -> None:
    """Both the initial manifest write and the post-copy rewrite go through the
    atomic temp+rename helper, so a concurrent run-index reader never sees a
    torn file and no `.tmp` scratch files are left behind."""
    do_file = tmp_path / "t.do"
    do_file.write_text("di 1\n", encoding="utf-8")
    info = persist_run_log_files(
        log_text="ok\n",
        code="di 1\n",
        origin_path=str(do_file),
        origin_kind="file",
        origin_label="t.do:1",
        request_id="0123456789abcdef",
        session_id="main",
        started_at="2026-05-08T01:22:33.456Z",
        elapsed_ms=7,
        rc=0,
        ok=True,
        stata=_stata(),
        working_dir=str(tmp_path),
    )
    # Force the in-place rewrite path too.
    update_run_artifact_manifest(info)

    run_dir = Path(info.directory)
    names = [p.name for p in run_dir.iterdir()]
    assert not any(n.endswith(".tmp") for n in names)
    assert "manifest.json" in names
    # Manifest is complete, valid JSON after both writes.
    manifest = json.loads(Path(info.manifest_path).read_text(encoding="utf-8"))
    assert manifest["request_id"] == "0123456789abcdef"
