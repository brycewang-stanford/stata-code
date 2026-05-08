"""Tests for the read-only run-bundle index."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stata_code.core.run_index import RunIndexError, list_runs


def _write_bundle(
    log_dir: Path,
    *,
    name: str,
    request_id: str,
    started_at: str,
    ok: bool = True,
    rc: int = 0,
    session_id: str = "main",
    source_path: str | None = None,
    origin_kind: str | None = None,
    origin_cell_id: str | None = None,
    origin_label: str | None = None,
    elapsed_ms: int = 12,
    extra_files: dict[str, str] | None = None,
    body_overrides: dict[str, Any] | None = None,
) -> Path:
    """Build a minimal but well-formed run bundle on disk and return its dir."""
    bundle_dir = log_dir / name
    bundle_dir.mkdir(parents=True, exist_ok=True)
    log_path = bundle_dir / f"{name}.log"
    log_path.write_text("log\n", encoding="utf-8")
    code_path = bundle_dir / "submitted.do"
    code_path.write_text("di 1\n", encoding="utf-8")

    files = {
        "directory": str(bundle_dir),
        "log": str(log_path),
        "smcl": str(bundle_dir / f"{name}.smcl"),
        "manifest": str(bundle_dir / "manifest.json"),
        "code": str(code_path),
    }
    if extra_files:
        files.update(extra_files)

    body: dict[str, Any] = {
        "artifact_version": "1.0",
        "policy": "per_run_directory",
        "append": False,
        "request_id": request_id,
        "session_id": session_id,
        "started_at": started_at,
        "elapsed_ms": elapsed_ms,
        "ok": ok,
        "rc": rc,
        "source_path": source_path,
        "origin_kind": origin_kind,
        "origin_label": origin_label,
        "origin_cell_id": origin_cell_id,
        "files": files,
    }
    if body_overrides:
        body.update(body_overrides)

    (bundle_dir / "manifest.json").write_text(
        json.dumps(body, indent=2, sort_keys=True), encoding="utf-8"
    )
    return bundle_dir


# ─────────────────────────────────────────────────────────────────────────────
# Resolution & basic listing
# ─────────────────────────────────────────────────────────────────────────────


def test_list_runs_requires_log_dir_or_origin_path() -> None:
    with pytest.raises(RunIndexError, match="log_dir_required"):
        list_runs()


def test_list_runs_returns_empty_when_log_dir_missing(tmp_path: Path) -> None:
    out = list_runs(log_dir=tmp_path / "nope" / "log-files")
    assert out["match_count"] == 0
    assert out["scanned_count"] == 0
    assert out["runs"] == []


def test_list_runs_uses_origin_path_to_locate_log_dir(tmp_path: Path) -> None:
    nb = tmp_path / "analysis.ipynb"
    nb.write_text("{}", encoding="utf-8")
    log_dir = tmp_path / "log-files"
    _write_bundle(
        log_dir,
        name="analysis__20260508T010000000Z__main__abc",
        request_id="req-1",
        started_at="2026-05-08T01:00:00.000Z",
        source_path=str(nb),
    )
    out = list_runs(origin_path=nb)
    assert out["log_dir"] == str(log_dir)
    assert out["match_count"] == 1
    assert out["runs"][0]["request_id"] == "req-1"


def test_list_runs_returns_summary_fields(tmp_path: Path) -> None:
    log_dir = tmp_path / "log-files"
    _write_bundle(
        log_dir,
        name="b1",
        request_id="r1",
        started_at="2026-05-08T01:00:00.000Z",
        source_path="/tmp/x.do",
        origin_kind="cell",
        origin_cell_id="cell-x",
        origin_label="x.do:1",
        elapsed_ms=42,
        rc=0,
        ok=True,
    )
    out = list_runs(log_dir=log_dir)
    r = out["runs"][0]
    assert r["request_id"] == "r1"
    assert r["session_id"] == "main"
    assert r["started_at"] == "2026-05-08T01:00:00.000Z"
    assert r["elapsed_ms"] == 42
    assert r["origin_cell_id"] == "cell-x"
    assert r["origin_label"] == "x.do:1"
    assert r["log_path"].endswith("b1.log")
    assert r["code_path"].endswith("submitted.do")
    assert r["manifest_path"].endswith("manifest.json")


# ─────────────────────────────────────────────────────────────────────────────
# Sorting
# ─────────────────────────────────────────────────────────────────────────────


def test_list_runs_sorts_newest_first(tmp_path: Path) -> None:
    log_dir = tmp_path / "log-files"
    for ts, rid in [
        ("2026-05-08T01:00:00.000Z", "old"),
        ("2026-05-08T03:00:00.000Z", "new"),
        ("2026-05-08T02:00:00.000Z", "mid"),
    ]:
        _write_bundle(log_dir, name=f"b-{rid}", request_id=rid, started_at=ts)
    out = list_runs(log_dir=log_dir)
    assert [r["request_id"] for r in out["runs"]] == ["new", "mid", "old"]


def test_list_runs_sort_tie_break_is_deterministic(tmp_path: Path) -> None:
    """When two bundles share `started_at` (sub-millisecond collision under
    parallel pool workers), ordering must still be stable. The sort uses
    `request_id` as a tiebreaker — descending — so the alphabetically
    larger request_id comes first.
    """
    log_dir = tmp_path / "log-files"
    same_ts = "2026-05-08T01:00:00.000Z"
    _write_bundle(log_dir, name="bundle-a", request_id="aaa", started_at=same_ts)
    _write_bundle(log_dir, name="bundle-b", request_id="zzz", started_at=same_ts)
    out1 = list_runs(log_dir=log_dir)
    out2 = list_runs(log_dir=log_dir)
    order1 = [r["request_id"] for r in out1["runs"]]
    order2 = [r["request_id"] for r in out2["runs"]]
    assert order1 == order2
    # Reverse-sorted on (started_at, request_id), so 'zzz' precedes 'aaa'.
    assert order1 == ["zzz", "aaa"]


# ─────────────────────────────────────────────────────────────────────────────
# Filters
# ─────────────────────────────────────────────────────────────────────────────


def test_filter_by_cell_id(tmp_path: Path) -> None:
    log_dir = tmp_path / "log-files"
    _write_bundle(log_dir, name="a", request_id="a",
                  started_at="2026-05-08T01:00:00.000Z", origin_cell_id="cell-1")
    _write_bundle(log_dir, name="b", request_id="b",
                  started_at="2026-05-08T02:00:00.000Z", origin_cell_id="cell-2")
    out = list_runs(log_dir=log_dir, cell_id="cell-1")
    assert out["match_count"] == 1
    assert out["runs"][0]["request_id"] == "a"


def test_filter_by_origin_path_normalises(tmp_path: Path) -> None:
    nb = tmp_path / "x.ipynb"
    nb.write_text("{}", encoding="utf-8")
    log_dir = tmp_path / "log-files"
    _write_bundle(log_dir, name="a", request_id="a",
                  started_at="2026-05-08T01:00:00.000Z",
                  source_path=str(nb))
    _write_bundle(log_dir, name="b", request_id="b",
                  started_at="2026-05-08T02:00:00.000Z",
                  source_path="/some/other.ipynb")
    # Pass origin_path as a relative-ish form; resolution should match.
    out = list_runs(log_dir=log_dir, origin_path=str(nb))
    assert out["match_count"] == 1
    assert out["runs"][0]["request_id"] == "a"


def test_filter_by_session_id(tmp_path: Path) -> None:
    log_dir = tmp_path / "log-files"
    _write_bundle(log_dir, name="a", request_id="a",
                  started_at="2026-05-08T01:00:00.000Z", session_id="main")
    _write_bundle(log_dir, name="b", request_id="b",
                  started_at="2026-05-08T02:00:00.000Z", session_id="alt")
    out = list_runs(log_dir=log_dir, session_id="alt")
    assert [r["request_id"] for r in out["runs"]] == ["b"]


def test_filter_by_ok(tmp_path: Path) -> None:
    log_dir = tmp_path / "log-files"
    _write_bundle(log_dir, name="a", request_id="a",
                  started_at="2026-05-08T01:00:00.000Z", ok=True, rc=0)
    _write_bundle(log_dir, name="b", request_id="b",
                  started_at="2026-05-08T02:00:00.000Z", ok=False, rc=111)
    out_failed = list_runs(log_dir=log_dir, ok=False)
    out_ok = list_runs(log_dir=log_dir, ok=True)
    assert [r["request_id"] for r in out_failed["runs"]] == ["b"]
    assert [r["request_id"] for r in out_ok["runs"]] == ["a"]


def test_filter_by_since(tmp_path: Path) -> None:
    log_dir = tmp_path / "log-files"
    _write_bundle(log_dir, name="a", request_id="old",
                  started_at="2026-05-07T23:00:00.000Z")
    _write_bundle(log_dir, name="b", request_id="kept",
                  started_at="2026-05-08T00:00:00.000Z")
    _write_bundle(log_dir, name="c", request_id="newer",
                  started_at="2026-05-08T01:00:00.000Z")
    out = list_runs(log_dir=log_dir, since="2026-05-08T00:00:00.000Z")
    assert sorted(r["request_id"] for r in out["runs"]) == ["kept", "newer"]


def test_filters_compose_AND(tmp_path: Path) -> None:
    log_dir = tmp_path / "log-files"
    _write_bundle(log_dir, name="a", request_id="a",
                  started_at="2026-05-08T01:00:00.000Z",
                  origin_cell_id="x", ok=False, rc=111)
    _write_bundle(log_dir, name="b", request_id="b",
                  started_at="2026-05-08T02:00:00.000Z",
                  origin_cell_id="x", ok=True, rc=0)
    out = list_runs(log_dir=log_dir, cell_id="x", ok=False)
    assert [r["request_id"] for r in out["runs"]] == ["a"]


# ─────────────────────────────────────────────────────────────────────────────
# Robustness: malformed / partial manifests
# ─────────────────────────────────────────────────────────────────────────────


def test_skips_dir_without_manifest(tmp_path: Path) -> None:
    log_dir = tmp_path / "log-files"
    log_dir.mkdir()
    (log_dir / "no_manifest_here").mkdir()
    _write_bundle(log_dir, name="b1", request_id="r1",
                  started_at="2026-05-08T01:00:00.000Z")
    out = list_runs(log_dir=log_dir)
    assert out["scanned_count"] == 2
    assert out["skipped_count"] == 1
    assert out["match_count"] == 1


def test_skips_invalid_json_manifest(tmp_path: Path) -> None:
    log_dir = tmp_path / "log-files"
    bad = log_dir / "bad"
    bad.mkdir(parents=True)
    (bad / "manifest.json").write_text("not json", encoding="utf-8")
    _write_bundle(log_dir, name="ok", request_id="r1",
                  started_at="2026-05-08T01:00:00.000Z")
    out = list_runs(log_dir=log_dir)
    assert out["skipped_count"] == 1
    assert out["match_count"] == 1


def test_skips_manifest_missing_required_fields(tmp_path: Path) -> None:
    log_dir = tmp_path / "log-files"
    bad = log_dir / "incomplete"
    bad.mkdir(parents=True)
    # Missing 'started_at' / 'ok' / 'rc' — well-formed JSON, bad shape.
    (bad / "manifest.json").write_text(
        json.dumps({"request_id": "r1", "session_id": "main"}),
        encoding="utf-8",
    )
    out = list_runs(log_dir=log_dir)
    assert out["scanned_count"] == 1
    assert out["skipped_count"] == 1
    assert out["runs"] == []


def test_ignores_files_in_log_dir(tmp_path: Path) -> None:
    log_dir = tmp_path / "log-files"
    log_dir.mkdir()
    (log_dir / "stray.txt").write_text("not a bundle", encoding="utf-8")
    _write_bundle(log_dir, name="b1", request_id="r1",
                  started_at="2026-05-08T01:00:00.000Z")
    out = list_runs(log_dir=log_dir)
    assert out["scanned_count"] == 1
    assert out["match_count"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Limit + truncation
# ─────────────────────────────────────────────────────────────────────────────


def test_limit_caps_returned_runs_and_flags_truncated(tmp_path: Path) -> None:
    log_dir = tmp_path / "log-files"
    for i in range(5):
        ts = f"2026-05-08T0{i}:00:00.000Z"
        _write_bundle(log_dir, name=f"b{i}", request_id=f"r{i}", started_at=ts)
    out = list_runs(log_dir=log_dir, limit=2)
    assert out["match_count"] == 5
    assert len(out["runs"]) == 2
    assert out["truncated"] is True
    # Newest first: r4, r3
    assert [r["request_id"] for r in out["runs"]] == ["r4", "r3"]


def test_invalid_limit_raises(tmp_path: Path) -> None:
    log_dir = tmp_path / "log-files"
    log_dir.mkdir()
    with pytest.raises(RunIndexError, match="limit_invalid"):
        list_runs(log_dir=log_dir, limit=0)
    with pytest.raises(RunIndexError, match="limit_invalid"):
        list_runs(log_dir=log_dir, limit=-3)


def test_bool_limit_rejected(tmp_path: Path) -> None:
    """`bool` is a subclass of `int`; without an explicit guard, `True`
    silently behaves as `limit=1`. Reject explicitly so a misbehaving
    caller gets a clear error rather than a silently-truncated result.
    """
    log_dir = tmp_path / "log-files"
    log_dir.mkdir()
    with pytest.raises(RunIndexError, match="limit_invalid"):
        list_runs(log_dir=log_dir, limit=True)  # type: ignore[arg-type]
    with pytest.raises(RunIndexError, match="limit_invalid"):
        list_runs(log_dir=log_dir, limit=False)  # type: ignore[arg-type]


def test_huge_limit_clamped(tmp_path: Path) -> None:
    log_dir = tmp_path / "log-files"
    _write_bundle(log_dir, name="b", request_id="r",
                  started_at="2026-05-08T01:00:00.000Z")
    out = list_runs(log_dir=log_dir, limit=10_000_000)
    assert out["limit"] == 500
    assert len(out["runs"]) == 1
