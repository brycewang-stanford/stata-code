"""Persistent log-file artifacts for file-backed Stata runs."""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from stata_code.core.schema import LogFileInfo, StataInfo

_SAFE_PART_RE = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_OUTPUT_ARTIFACT_BYTES = 50 * 1024 * 1024
_OUTPUT_EXTENSIONS = {
    ".csv",
    ".doc",
    ".docx",
    ".eps",
    ".gph",
    ".htm",
    ".html",
    ".jpg",
    ".jpeg",
    ".json",
    ".log",
    ".pdf",
    ".png",
    ".rtf",
    ".smcl",
    ".ster",
    ".svg",
    ".tex",
    ".tif",
    ".tiff",
    ".tsv",
    ".txt",
    ".xls",
    ".xlsx",
    ".xml",
}
FileSnapshot = dict[str, tuple[int, int]]


def persist_run_log_files(
    *,
    log_text: str,
    code: str,
    origin_path: str,
    origin_kind: str | None,
    origin_label: str | None,
    request_id: str,
    session_id: str,
    started_at: str,
    elapsed_ms: int,
    rc: int,
    ok: bool,
    stata: StataInfo,
    working_dir: str | None = None,
    server_name: str = "stata-code",
    server_version: str | None = None,
    root_dir_name: str = "log-files",
) -> LogFileInfo:
    """Write immutable per-run ``.log`` and ``.smcl`` files.

    The root folder is stable and human-browsable:

        <do-file-dir>/log-files/<do-stem>__<timestamp>__<session>__<run-id>/

    We intentionally do not append to an older log. Appending makes parallel
    sessions and selection/cell reruns ambiguous, while an immutable run folder
    gives each execution a reproducible source snapshot and manifest.
    """
    source = Path(origin_path).expanduser()
    if not source.is_absolute():
        source = source.resolve()

    source_dir = source.parent
    source_stem = _safe_part(source.stem or "stata")
    safe_session = _safe_part(session_id or "main")
    short_id = _safe_part(request_id[:10] or "run")
    stamp = _timestamp_for_name(started_at)
    run_dir_name = f"{source_stem}__{stamp}__{safe_session}__{short_id}"
    run_dir = _unique_dir(source_dir / root_dir_name / run_dir_name)
    run_dir.mkdir(parents=True, exist_ok=False)

    file_base = run_dir.name
    log_path = run_dir / f"{file_base}.log"
    smcl_path = run_dir / f"{file_base}.smcl"
    code_path = run_dir / "submitted.do"
    manifest_path = run_dir / "manifest.json"

    header = _text_header(
        source=source,
        origin_kind=origin_kind,
        origin_label=origin_label,
        request_id=request_id,
        session_id=session_id,
        started_at=started_at,
        elapsed_ms=elapsed_ms,
        rc=rc,
        ok=ok,
        working_dir=working_dir,
        server_name=server_name,
        server_version=server_version,
        stata=stata,
    )

    normalized_log = _normalize_text(log_text)
    log_path.write_text(f"{header}\n{normalized_log}", encoding="utf-8")
    smcl_path.write_text(
        _smcl_document(f"{header}\n{normalized_log}"), encoding="utf-8"
    )
    code_path.write_text(_normalize_text(code), encoding="utf-8")

    info = LogFileInfo(
        directory=str(run_dir),
        log_path=str(log_path),
        smcl_path=str(smcl_path),
        manifest_path=str(manifest_path),
        code_path=str(code_path),
        working_dir=working_dir,
        policy="per_run_directory",
        append=False,
    )
    manifest_path.write_text(
        json.dumps(
            _manifest(
                info=info,
                source=source,
                origin_kind=origin_kind,
                origin_label=origin_label,
                request_id=request_id,
                session_id=session_id,
                started_at=started_at,
                elapsed_ms=elapsed_ms,
                rc=rc,
                ok=ok,
                working_dir=working_dir,
                server_name=server_name,
                server_version=server_version,
                stata=stata,
            ),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return info


def snapshot_working_dir_files(
    working_dir: str | Path,
    *,
    origin_path: str | Path | None = None,
    max_depth: int = 3,
) -> FileSnapshot:
    """Capture file size/mtime for generated-output detection."""
    root = Path(working_dir).expanduser().resolve()
    origin = Path(origin_path).expanduser().resolve() if origin_path else None
    snapshot: FileSnapshot = {}
    if not root.is_dir():
        return snapshot
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        try:
            rel_dir = current_path.resolve().relative_to(root)
        except ValueError:
            continue
        if "log-files" in rel_dir.parts:
            dirs[:] = []
            continue
        if len(rel_dir.parts) >= max_depth:
            dirs[:] = []
        try:
            dirs[:] = [d for d in dirs if d != "log-files"]
        except OSError:
            dirs[:] = []
        for filename in files:
            path = current_path / filename
            if not path.is_file():
                continue
            try:
                resolved = path.resolve()
                if len(resolved.relative_to(root).parts) > max_depth:
                    continue
                stat = path.stat()
            except OSError:
                continue
            if origin is not None and resolved == origin:
                continue
            snapshot[str(resolved)] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def changed_output_files(
    before: FileSnapshot,
    working_dir: str | Path,
    *,
    origin_path: str | Path | None = None,
    max_depth: int = 3,
) -> list[Path]:
    """Return common output files created or modified since ``before``."""
    root = Path(working_dir).expanduser().resolve()
    after = snapshot_working_dir_files(
        root, origin_path=origin_path, max_depth=max_depth
    )
    out: list[Path] = []
    for raw_path, marker in after.items():
        if before.get(raw_path) == marker:
            continue
        path = Path(raw_path)
        if path.suffix.lower() not in _OUTPUT_EXTENSIONS:
            continue
        try:
            if path.stat().st_size > _MAX_OUTPUT_ARTIFACT_BYTES:
                continue
        except OSError:
            continue
        out.append(path)
    return sorted(out)


def copy_output_artifacts(
    info: LogFileInfo,
    output_files: list[Path],
    *,
    working_dir: str | Path,
) -> LogFileInfo:
    """Copy generated tables/exports into ``outputs/`` inside the run bundle."""
    if not output_files:
        return info
    root = Path(working_dir).expanduser().resolve()
    outputs_dir = Path(info.directory) / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for src in output_files:
        try:
            rel = src.resolve().relative_to(root)
        except ValueError:
            rel = Path(src.name)
        target = _unique_file(outputs_dir / rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, target)
        except OSError:
            continue
        copied.append(str(target))
    if not copied:
        return info
    return info.model_copy(
        update={
            "outputs_dir": str(outputs_dir),
            "output_paths": [*info.output_paths, *copied],
        }
    )


def update_run_artifact_manifest(info: LogFileInfo) -> None:
    """Refresh manifest paths after graphs/outputs are copied."""
    manifest_path = Path(info.manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    files = manifest.setdefault("files", {})
    files.update(
        {
            "graphs_dir": info.graphs_dir,
            "outputs_dir": info.outputs_dir,
            "graph_paths": info.graph_paths,
            "output_paths": info.output_paths,
        }
    )
    manifest["working_dir"] = info.working_dir
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _manifest(
    *,
    info: LogFileInfo,
    source: Path,
    origin_kind: str | None,
    origin_label: str | None,
    request_id: str,
    session_id: str,
    started_at: str,
    elapsed_ms: int,
    rc: int,
    ok: bool,
    working_dir: str | None,
    server_name: str,
    server_version: str | None,
    stata: StataInfo,
) -> dict[str, Any]:
    return {
        "artifact_version": "1.0",
        "policy": info.policy,
        "append": info.append,
        "source_path": str(source),
        "origin_kind": origin_kind,
        "origin_label": origin_label,
        "request_id": request_id,
        "session_id": session_id,
        "started_at": started_at,
        "elapsed_ms": elapsed_ms,
        "ok": ok,
        "rc": rc,
        "working_dir": working_dir,
        "server": {"name": server_name, "version": server_version},
        "stata": stata.model_dump(mode="json"),
        "files": {
            "directory": info.directory,
            "log": info.log_path,
            "smcl": info.smcl_path,
            "manifest": info.manifest_path,
            "code": info.code_path,
            "graphs_dir": info.graphs_dir,
            "outputs_dir": info.outputs_dir,
            "graph_paths": info.graph_paths,
            "output_paths": info.output_paths,
        },
    }


def _text_header(
    *,
    source: Path,
    origin_kind: str | None,
    origin_label: str | None,
    request_id: str,
    session_id: str,
    started_at: str,
    elapsed_ms: int,
    rc: int,
    ok: bool,
    working_dir: str | None,
    server_name: str,
    server_version: str | None,
    stata: StataInfo,
) -> str:
    status = "OK" if ok else f"FAIL rc={rc}"
    server = server_name if server_version is None else f"{server_name} {server_version}"
    lines = [
        "* " + "=" * 76,
        "* stata-code run log",
        f"* source_do: {source}",
        f"* origin: {origin_kind or 'unknown'}",
        f"* origin_label: {origin_label or ''}",
        f"* working_dir: {working_dir or ''}",
        f"* session_id: {session_id}",
        f"* request_id: {request_id}",
        f"* started_at_utc: {started_at}",
        f"* elapsed_ms: {elapsed_ms}",
        f"* status: {status}",
        f"* mcp_server: {server}",
        f"* stata_backend: {stata.backend.value}",
        f"* stata_version: {stata.version or 'unknown'}",
        f"* stata_edition: {stata.edition.value}",
        "* policy: immutable per-run directory under log-files; append=false",
        "* note: .log and .smcl are generated from the same MCP-captured transcript",
        "* " + "=" * 76,
    ]
    return "\n".join(lines)


def _smcl_document(text: str) -> str:
    normalized = _normalize_text(text)
    lines = ["{smcl}"]
    lines.extend("{txt}" + _escape_smcl(line) for line in normalized.split("\n"))
    return "\n".join(lines)


def _escape_smcl(text: str) -> str:
    out: list[str] = []
    for ch in text:
        if ch == "{":
            out.append("{c -(}")
        elif ch == "}":
            out.append("{c )-}")
        else:
            out.append(ch)
    return "".join(out)


def _normalize_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized if normalized.endswith("\n") else normalized + "\n"


def _timestamp_for_name(started_at: str) -> str:
    try:
        dt = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%S.%fZ")
        dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime("%Y%m%dT%H%M%S") + f"{dt.microsecond // 1000:03d}Z"
    except ValueError:
        return _safe_part(started_at.replace(":", "").replace("-", ""))


def _safe_part(value: str, *, limit: int = 80) -> str:
    cleaned = _SAFE_PART_RE.sub("_", value).strip("._-")
    return (cleaned or "run")[:limit]


def _unique_dir(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        candidate = path.with_name(f"{path.name}__{i}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"could not allocate unique log artifact directory under {path.parent}")


def _unique_file(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for i in range(2, 1000):
        candidate = path.with_name(f"{stem}__{i}{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"could not allocate unique artifact path under {path.parent}")

