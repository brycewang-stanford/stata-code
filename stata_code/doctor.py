"""Read-only runtime diagnostics for stata-code installations."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
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
    workspace: str | Path | None = None,
    include_user_configs: bool = True,
) -> DoctorReport:
    """Run read-only diagnostics without mutating user or editor config."""
    checks = [
        _package_check(),
        _python_check(),
        _optional_module_check(
            "mcp",
            "mcp_extra",
            "MCP SDK importable; `stata-code-mcp` can run in this environment.",
            'MCP SDK missing; install with `python -m pip install "stata-code[mcp]"`.',
        ),
        _optional_module_check(
            "ipykernel",
            "kernel_extra",
            "ipykernel importable; Jupyter kernel support can be registered.",
            'ipykernel missing; install with `python -m pip install "stata-code[kernel]"`.',
        ),
        _pystata_discovery_check(),
        _stata_cli_check(),
        _console_scripts_check(),
        _client_config_check(
            workspace=workspace,
            include_user_configs=include_user_configs,
        ),
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


def _stata_cli_check() -> DiagnosticCheck:
    """Report whether a Stata command-line executable (console backend) is found."""
    from stata_code.core.console import find_stata_cli

    exe = find_stata_cli()
    if exe is not None:
        return DiagnosticCheck(
            id="stata_cli",
            status="ok",
            summary="Stata command-line executable found; the console backend is available.",
            detail=f"executable={exe}",
        )
    return DiagnosticCheck(
        id="stata_cli",
        status="warn",
        summary="No Stata command-line executable found for the console backend.",
        detail="The console backend (Stata 13+, no pystata) needs the Stata CLI binary.",
        hint="Set STATA_CODE_STATA_CLI to the Stata console binary, e.g. /usr/local/stata18/stata-mp.",
    )


def _console_scripts_check() -> DiagnosticCheck:
    expected = ("stata-code", "stata-code-mcp", "stata-code-kernel")
    found = {name: shutil.which(name) for name in expected}
    missing = [name for name, path in found.items() if path is None]
    detail = "; ".join(
        f"{name}={path if path is not None else '<missing>'}" for name, path in found.items()
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


def _client_config_check(
    *,
    workspace: str | Path | None,
    include_user_configs: bool,
) -> DiagnosticCheck:
    mcp_path = shutil.which("stata-code-mcp")
    config_summary = _client_config_summary(
        workspace=workspace,
        include_user_configs=include_user_configs,
    )

    if mcp_path:
        command_summary = "MCP clients can use the discovered `stata-code-mcp` command."
        command_detail = f"command={mcp_path}"
    else:
        command_summary = (
            "MCP clients should point at an absolute `stata-code-mcp` path "
            "or `python -m stata_code.mcp`."
        )
        command_detail = (
            "Claude/Cursor/VS Code configs should avoid relying on GUI PATH when "
            "the server lives inside a project virtualenv."
        )

    summary = command_summary
    status: Status = "ok"
    if config_summary.found:
        if config_summary.errors:
            status = "warn"
            summary = (
                f"Found {config_summary.found} MCP client config file(s); "
                f"{config_summary.errors} could not be read as JSON."
            )
        elif config_summary.configured:
            summary = (
                f"Found {config_summary.configured} MCP client config file(s) "
                f"that mention stata-code. {command_summary}"
            )
        else:
            status = "warn"
            summary = (
                f"Found {config_summary.found} MCP client config file(s), "
                "but none mention stata-code."
            )
    detail = f"{command_detail}; configs={config_summary.detail}"

    return DiagnosticCheck(
        id="client_config",
        status=status,
        summary=summary,
        detail=detail,
        hint="VS Code can also set `stataCode.pythonPath` or `stataCode.serverCommand`.",
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


@dataclass(frozen=True)
class _ClientConfigSummary:
    found: int
    configured: int
    errors: int
    detail: str


_STATA_CODE_CONFIG_MARKERS = (
    "stata-code",
    "stata-code-mcp",
    "stata_code.mcp",
)


def _client_config_summary(
    *,
    workspace: str | Path | None,
    include_user_configs: bool,
) -> _ClientConfigSummary:
    entries: list[str] = []
    found = 0
    configured = 0
    errors = 0

    for path in _candidate_client_config_paths(
        workspace=workspace,
        include_user_configs=include_user_configs,
    ):
        if not path.is_file():
            continue
        found += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors += 1
            entries.append(f"{path}=invalid-json:{type(exc).__name__}")
            continue

        if _json_mentions_stata_code(payload):
            configured += 1
            entries.append(f"{path}=mentions-stata-code")
        else:
            entries.append(f"{path}=no-stata-code-entry")

    if not entries:
        return _ClientConfigSummary(
            found=0,
            configured=0,
            errors=0,
            detail="<none found>",
        )
    return _ClientConfigSummary(
        found=found,
        configured=configured,
        errors=errors,
        detail="; ".join(entries),
    )


def _candidate_client_config_paths(
    *,
    workspace: str | Path | None,
    include_user_configs: bool,
) -> list[Path]:
    root = Path.cwd() if workspace is None else Path(workspace).expanduser()
    candidates = [
        root / ".mcp.json",
        root / ".cursor" / "mcp.json",
        root / ".vscode" / "mcp.json",
    ]

    if include_user_configs:
        home = Path.home()
        candidates.extend(
            [
                home / ".claude" / "mcp.json",
                home / ".cursor" / "mcp.json",
                home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
            ]
        )
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / "Claude" / "claude_desktop_config.json")

    return _dedupe_paths(candidates)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _json_mentions_stata_code(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _text_mentions_stata_code(str(key)) or _json_mentions_stata_code(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_json_mentions_stata_code(child) for child in value)
    if isinstance(value, str):
        return _text_mentions_stata_code(value)
    return False


def _text_mentions_stata_code(value: str) -> bool:
    normalized = value.lower()
    return any(marker in normalized for marker in _STATA_CODE_CONFIG_MARKERS)
