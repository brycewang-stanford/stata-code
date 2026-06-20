"""Read-only runtime diagnostics for stata-code installations."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import platform
import shutil
import sys
from dataclasses import asdict, dataclass
from typing import Any, Literal

from stata_code import __version__
from stata_code.core._pool import pool_stata_info, shutdown_default_pool
from stata_code.core._runtime import _candidate_pystata_paths

Status = Literal["ok", "warn", "fail", "skip"]


@dataclass(frozen=True)
class DiagnosticCheck:
    """One doctor check, designed for stable JSON output."""

    id: str
    status: Status
    summary: str
    detail: str | None = None
    hint: str | None = None


@dataclass(frozen=True)
class DoctorReport:
    """Aggregate diagnostic report."""

    ok: bool
    checks: list[DiagnosticCheck]

    @property
    def counts(self) -> dict[str, int]:
        out = {"ok": 0, "warn": 0, "fail": 0, "skip": 0}
        for check in self.checks:
            out[check.status] += 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "counts": self.counts,
            "checks": [asdict(check) for check in self.checks],
        }


def run_doctor(
    *,
    probe_stata: bool = True,
    stata_timeout_ms: int = 15_000,
) -> DoctorReport:
    """Run read-only diagnostics without mutating user or editor config."""
    checks = [
        _package_check(),
        _python_check(),
        _optional_module_check(
            "mcp",
            "mcp_extra",
            'MCP SDK importable; `stata-code-mcp` can run in this environment.',
            'MCP SDK missing; install with `python -m pip install "stata-code[mcp]"`.',
        ),
        _optional_module_check(
            "ipykernel",
            "kernel_extra",
            "ipykernel importable; Jupyter kernel support can be registered.",
            'ipykernel missing; install with `python -m pip install "stata-code[kernel]"`.',
        ),
        _pystata_discovery_check(),
        _console_scripts_check(),
        _client_config_check(),
        _stata_probe_check(probe_stata=probe_stata, timeout_ms=stata_timeout_ms),
    ]
    return DoctorReport(
        ok=not any(check.status == "fail" for check in checks),
        checks=checks,
    )


def format_text(report: DoctorReport) -> str:
    """Render a compact human-readable report."""
    lines = [
        "stata-code doctor",
        f"overall: {'ok' if report.ok else 'fail'} "
        f"(ok={report.counts['ok']}, warn={report.counts['warn']}, "
        f"fail={report.counts['fail']}, skip={report.counts['skip']})",
        "",
    ]
    for check in report.checks:
        lines.append(f"[{check.status.upper()}] {check.id}: {check.summary}")
        if check.detail:
            lines.append(f"  detail: {check.detail}")
        if check.hint:
            lines.append(f"  hint: {check.hint}")
    return "\n".join(lines)


def format_json(report: DoctorReport) -> str:
    """Render stable JSON for automated setup checks."""
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


def _package_check() -> DiagnosticCheck:
    try:
        dist_version = importlib.metadata.version("stata-code")
    except importlib.metadata.PackageNotFoundError:
        dist_version = None

    if dist_version is None:
        return DiagnosticCheck(
            id="package",
            status="warn",
            summary=f"importable from source tree as version {__version__}.",
            detail="No installed `stata-code` distribution was found by importlib.metadata.",
            hint="For console scripts, install the package with `python -m pip install -e .` or from PyPI.",
        )
    if dist_version != __version__:
        return DiagnosticCheck(
            id="package",
            status="warn",
            summary=f"imported version {__version__}, installed distribution {dist_version}.",
            hint="Restart the Python environment or reinstall if this mismatch is unexpected.",
        )
    return DiagnosticCheck(
        id="package",
        status="ok",
        summary=f"stata-code {__version__} is installed and importable.",
    )


def _python_check() -> DiagnosticCheck:
    version = platform.python_version()
    detail = f"executable={sys.executable}; platform={platform.platform()}"
    return DiagnosticCheck(
        id="python",
        status="ok",
        summary=f"Python {version} satisfies the 3.10+ requirement.",
        detail=detail,
    )


def _optional_module_check(
    module: str,
    check_id: str,
    ok_summary: str,
    missing_summary: str,
) -> DiagnosticCheck:
    if _module_available(module):
        return DiagnosticCheck(id=check_id, status="ok", summary=ok_summary)
    return DiagnosticCheck(
        id=check_id,
        status="warn",
        summary=missing_summary,
    )


def _pystata_discovery_check() -> DiagnosticCheck:
    if _module_available("pystata"):
        return DiagnosticCheck(
            id="pystata_discovery",
            status="ok",
            summary="pystata is already importable on sys.path.",
        )

    candidate = _first_existing_pystata_candidate()
    if candidate is not None:
        return DiagnosticCheck(
            id="pystata_discovery",
            status="ok",
            summary="pystata found in a standard Stata utilities directory.",
            detail=candidate,
        )

    checked = _format_paths(_candidate_pystata_paths())
    return DiagnosticCheck(
        id="pystata_discovery",
        status="warn",
        summary="pystata was not found on sys.path or standard Stata utilities paths.",
        detail=f"checked: {checked}",
        hint="Install Stata 17+ or set STATA_CODE_PYSTATA_PATH/PYSTATA_PATH to Stata's utilities directory.",
    )


def _console_scripts_check() -> DiagnosticCheck:
    expected = ("stata-code", "stata-code-mcp", "stata-code-kernel")
    found = {name: shutil.which(name) for name in expected}
    missing = [name for name, path in found.items() if path is None]
    detail = "; ".join(
        f"{name}={path if path is not None else '<missing>'}"
        for name, path in found.items()
    )
    if not missing:
        return DiagnosticCheck(
            id="console_scripts",
            status="ok",
            summary="All stata-code console scripts are on PATH.",
            detail=detail,
        )
    return DiagnosticCheck(
        id="console_scripts",
        status="warn",
        summary=f"Missing console script(s) on PATH: {', '.join(missing)}.",
        detail=detail,
        hint="Use absolute paths from the project virtualenv in MCP/VS Code clients when PATH is unreliable.",
    )


def _client_config_check() -> DiagnosticCheck:
    mcp_path = shutil.which("stata-code-mcp")
    if mcp_path:
        summary = "MCP clients can use the discovered `stata-code-mcp` command."
        detail = f"command={mcp_path}"
    else:
        summary = "MCP clients should point at an absolute `stata-code-mcp` path or `python -m stata_code.mcp`."
        detail = (
            "Claude/Cursor/VS Code configs should avoid relying on GUI PATH when "
            "the server lives inside a project virtualenv."
        )
    return DiagnosticCheck(
        id="client_config",
        status="ok",
        summary=summary,
        detail=detail,
        hint='VS Code can also set `stataCode.pythonPath` or `stataCode.serverCommand`.',
    )


def _stata_probe_check(*, probe_stata: bool, timeout_ms: int) -> DiagnosticCheck:
    if not probe_stata:
        return DiagnosticCheck(
            id="stata_probe",
            status="skip",
            summary="Skipped live Stata initialization probe.",
            hint="Run `stata-code doctor` without `--no-stata-probe` for an end-to-end Stata check.",
        )

    try:
        info = pool_stata_info(timeout_ms=timeout_ms)
    except Exception as exc:  # noqa: BLE001
        return DiagnosticCheck(
            id="stata_probe",
            status="fail",
            summary="Could not initialize Stata through pystata.",
            detail=f"{type(exc).__name__}: {exc}",
            hint="Confirm Stata 17+ is installed, licensed, and that pystata is discoverable.",
        )
    finally:
        shutdown_default_pool()

    version = info.get("version") or "unknown"
    edition = info.get("edition") or "unknown"
    backend = info.get("backend") or "unknown"
    return DiagnosticCheck(
        id="stata_probe",
        status="ok",
        summary=f"Stata initialized successfully: version={version}, edition={edition}.",
        detail=f"backend={backend}",
    )


def _module_available(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _first_existing_pystata_candidate() -> str | None:
    from pathlib import Path

    for path in _candidate_pystata_paths():
        if Path(path).joinpath("pystata").is_dir():
            return path
    return None


def _format_paths(paths: list[str], *, limit: int = 6) -> str:
    if not paths:
        return "<none>"
    shown = paths[:limit]
    suffix = "" if len(paths) <= limit else f"; ... +{len(paths) - limit} more"
    return "; ".join(shown) + suffix
