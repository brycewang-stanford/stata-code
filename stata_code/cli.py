"""Top-level command-line interface for stata-code.

Subcommands:

* ``doctor`` / ``verify`` — read-only install & runtime diagnostics.
* ``run`` — execute a .do file or inline code through the structured engine and
  print the ``RunResult`` (the Bash / plain-terminal surface: any agent that can
  shell out gets the same typed error loop the MCP server exposes).
* ``setup`` — opt-in, write the MCP server entry into a client's config.
* ``lint`` — static, Stata-free check of do-file source.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from stata_code.doctor import format_json, format_text, run_doctor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stata-code",
        description="Utilities for checking and operating a stata-code install.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the stata-code package version and exit.",
    )
    subparsers = parser.add_subparsers(dest="command")
    _add_doctor_parser(subparsers, "doctor")
    _add_doctor_parser(subparsers, "verify")
    _add_run_parser(subparsers)
    _add_setup_parser(subparsers)
    _add_lint_parser(subparsers)

    args = parser.parse_args(argv)
    if args.version:
        from stata_code import __version__

        print(__version__)
        return 0
    if args.command in {"doctor", "verify"}:
        return _run_doctor_command(args)
    if args.command == "run":
        return _run_command(args)
    if args.command == "setup":
        return _setup_command(args)
    if args.command == "lint":
        return _lint_command(args)

    parser.print_help()
    return 0


def run_main() -> None:
    raise SystemExit(main())


# ─────────────────────────────────────────────────────────────────────────────
# doctor / verify
# ─────────────────────────────────────────────────────────────────────────────


def _run_doctor_command(args: argparse.Namespace) -> int:
    report = run_doctor(
        probe_stata=not args.no_stata_probe,
        stata_timeout_ms=args.stata_timeout_ms,
        workspace=args.workspace,
        include_user_configs=not args.no_user_config_scan,
    )
    print(format_json(report) if args.json else format_text(report))
    return 0 if report.ok else 1


def _add_doctor_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
) -> None:
    parser = subparsers.add_parser(
        name,
        help="Run read-only installation and runtime diagnostics.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of text.",
    )
    parser.add_argument(
        "--no-stata-probe",
        action="store_true",
        help="Skip live Stata initialization; useful for CI or docs-only checks.",
    )
    parser.add_argument(
        "--stata-timeout-ms",
        type=int,
        default=15_000,
        help="Timeout for the live Stata probe (default: 15000).",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="Workspace root to scan for project MCP client configs (default: current directory).",
    )
    parser.add_argument(
        "--no-user-config-scan",
        action="store_true",
        help="Only inspect project-level MCP client configs, not user-level config paths.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# run
# ─────────────────────────────────────────────────────────────────────────────


def _add_run_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "run",
        help="Execute a .do file or inline code and print the structured result.",
        description=(
            "Run Stata code through the same subprocess-backed engine the MCP "
            "server uses, and print the RunResult. Give a FILE, one or more "
            "-e snippets, or pipe code on stdin. Exit code is 0 on success, 1 "
            "on a Stata/adapter error."
        ),
    )
    parser.add_argument(
        "file",
        nargs="?",
        help="Path to a .do file to run, or '-' to read code from stdin.",
    )
    parser.add_argument(
        "-e",
        "--execute",
        action="append",
        metavar="CODE",
        help="Inline Stata code. Repeatable; snippets are joined with newlines.",
    )
    parser.add_argument(
        "--backend",
        choices=["auto", "pystata", "console"],
        default="auto",
        help=(
            "Execution backend. 'pystata' (Stata 17+, in-memory sessions), "
            "'console' (Stata 13+ batch, no pystata, stateless), or 'auto' "
            "(default: pystata when available, else console)."
        ),
    )
    parser.add_argument(
        "--session",
        default="main",
        help="Session id (isolated Stata frame/worker). Defaults to 'main'.",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=600_000,
        help="Hard timeout in milliseconds (default: 600000). 0 disables it.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full RunResult as JSON instead of a text summary.",
    )
    parser.add_argument(
        "--full-log",
        action="store_true",
        help="Include the complete Stata log (text summary and JSON).",
    )
    parser.add_argument(
        "--results",
        choices=["none", "scalars", "full"],
        default="full",
        help=(
            "How much of r()/e() to include. Defaults to 'full' here — unlike "
            "the MCP server, this process exits when the run ends, so a "
            "matrix:// reference would have nothing left to resolve against. "
            "Use 'scalars' or 'none' to shrink --json output."
        ),
    )
    parser.add_argument(
        "--graphs",
        metavar="DIR",
        default=None,
        help="Write any graphs produced by the run into DIR.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the log in the text summary (ignored with --json).",
    )


def _read_run_code(args: argparse.Namespace) -> tuple[str, str | None, str | None]:
    """Return ``(code, origin_path, error)``.

    ``error`` is a message when no code could be resolved; otherwise ``None``.
    """
    if args.execute:
        return "\n".join(args.execute), None, None
    if args.file and args.file != "-":
        path = Path(args.file)
        try:
            return path.read_text(encoding="utf-8"), str(path), None
        except FileNotFoundError:
            return "", None, f"file not found: {args.file}"
        except OSError as exc:
            return "", None, f"could not read {args.file}: {exc}"
    # stdin (explicit '-' or no positional/-e at all)
    data = sys.stdin.read()
    if not data.strip():
        return "", None, "no code provided (give a FILE, -e CODE, or pipe on stdin)"
    return data, None, None


def _resolve_backend(choice: str) -> str:
    if choice != "auto":
        return choice
    from stata_code import is_available

    if is_available():
        return "pystata"
    from stata_code.core.console import console_available

    return "console" if console_available() else "pystata"


def _run_command(args: argparse.Namespace) -> int:
    from stata_code import RefNotFound, get_graph, run, run_console
    from stata_code.core.console import ConsoleNotAvailable

    code, origin_path, err = _read_run_code(args)
    if err is not None:
        print(f"error: {err}", file=sys.stderr)
        return 2

    timeout_ms = args.timeout_ms if args.timeout_ms and args.timeout_ms > 0 else None
    backend = _resolve_backend(args.backend)

    if backend == "console":
        try:
            result = run_console(
                code,
                session_id=args.session,
                timeout_ms=timeout_ms,
                include_full_log=args.full_log,
                origin_path=origin_path,
                origin_kind="do_file" if origin_path else None,
            )
        except ConsoleNotAvailable as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    else:
        result = run(
            code,
            session_id=args.session,
            timeout_ms=timeout_ms,
            include_full_log=args.full_log,
            include_graphs="ref",
            include_results=args.results,
            origin_path=origin_path,
            origin_kind="do_file" if origin_path else None,
        )

    graph_notes: list[str] = []
    if args.graphs and backend != "console":
        graph_notes = _export_graphs(result, args.graphs, get_graph, RefNotFound)

    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        print(_format_run_summary(result, quiet=args.quiet))
        for note in graph_notes:
            print(note)

    return 0 if result.ok else 1


def _export_graphs(
    result: object,
    out_dir: str,
    get_graph: Callable[[str], dict[str, Any]],
    ref_not_found: type[BaseException],
) -> list[str]:
    notes: list[str] = []
    graphs = getattr(result, "graphs", None) or []
    if not graphs:
        return notes
    dest = Path(out_dir)
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return [f"graphs: could not create {out_dir}: {exc}"]
    import base64

    for idx, graph in enumerate(graphs):
        ref = getattr(graph, "ref", None)
        fmt = getattr(graph, "format", None) or "png"
        fmt = getattr(fmt, "value", fmt)
        stem = f"graph_{idx + 1}"
        target = dest / f"{stem}.{fmt}"
        try:
            payload = get_graph(ref) if ref else None
            if payload is None:
                notes.append(f"graphs: {stem} had no fetchable bytes")
                continue
            target.write_bytes(base64.b64decode(payload["bytes_b64"]))
            notes.append(f"graphs: wrote {target}")
        except ref_not_found:
            notes.append(f"graphs: ref for {stem} expired")
        except (OSError, KeyError, ValueError) as exc:
            notes.append(f"graphs: failed to write {stem}: {exc}")
    return notes


def _format_run_summary(result: object, *, quiet: bool) -> str:
    ok = getattr(result, "ok", False)
    rc = getattr(result, "rc", None)
    session = getattr(result, "session_id", "?")
    elapsed = getattr(result, "elapsed_ms", 0)
    lines = [f"ok={ok}  rc={rc}  session={session}  elapsed={elapsed}ms"]

    error = getattr(result, "error", None)
    if error is not None:
        kind = getattr(getattr(error, "kind", None), "value", getattr(error, "kind", "?"))
        line_no = getattr(error, "line", None)
        where = f" line {line_no}" if line_no else ""
        lines.append(f"error [{kind}]{where}: {getattr(error, 'message', '')}")
        for sugg in getattr(error, "suggestions", []) or []:
            action = getattr(sugg, "action", None)
            if action:
                lines.append(f"  hint: {action}")
        if rc == -1:
            lines.append("  (run `stata-code doctor` to check the Stata install)")

    if not quiet:
        log = getattr(result, "log", None)
        log_text = (getattr(log, "tail", "") or getattr(log, "head", "")) if log else ""
        if log_text.strip():
            lines.append("--- log (tail) ---")
            lines.append(log_text.rstrip())
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# setup
# ─────────────────────────────────────────────────────────────────────────────


def _add_setup_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "setup",
        help="Write the stata-code MCP server entry into an agent's config.",
        description=(
            "Opt-in, config-mutating counterpart to `doctor`. Merges a "
            "stata-code MCP server entry into the selected client configs "
            "(preserving other servers, backing up any file it overwrites)."
        ),
    )
    parser.add_argument("--claude", action="store_true", help="Write .mcp.json (Claude Code, project).")
    parser.add_argument("--cursor", action="store_true", help="Write .cursor/mcp.json (Cursor, project).")
    parser.add_argument("--vscode", action="store_true", help="Write .vscode/mcp.json (VS Code, project).")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Write all supported JSON clients (Claude Code, Cursor, VS Code).",
    )
    parser.add_argument(
        "--codex",
        action="store_true",
        help="Print a copy-paste ~/.codex/config.toml snippet (Codex uses TOML; not written).",
    )
    parser.add_argument(
        "--claude-desktop",
        action="store_true",
        help="Print a copy-paste claude_desktop_config.json snippet (not written).",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="Workspace root for project configs (default: current directory).",
    )
    parser.add_argument(
        "--python",
        default=None,
        help="Pin an interpreter: launch the server as '<python> -m stata_code.mcp'.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing any file.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON.",
    )


def _setup_command(args: argparse.Namespace) -> int:
    from dataclasses import asdict

    from stata_code.mcp_setup import (
        CLIENTS,
        apply_client,
        claude_desktop_snippet,
        codex_snippet,
        resolve_server_command,
    )

    workspace = Path(args.workspace).expanduser() if args.workspace else Path.cwd()
    command = resolve_server_command(args.python)

    selected: list[str] = []
    if args.all:
        selected = list(CLIENTS)
    else:
        for name in CLIENTS:
            if getattr(args, name):
                selected.append(name)

    if not selected and not args.codex and not args.claude_desktop:
        msg = (
            "pick at least one target: --claude, --cursor, --vscode, --all, "
            "--codex, or --claude-desktop"
        )
        print(f"error: {msg}", file=sys.stderr)
        return 2

    reports = [
        apply_client(name, workspace=workspace, command=command, dry_run=args.dry_run)
        for name in selected
    ]

    snippets: dict[str, str] = {}
    if args.codex:
        snippets["codex"] = codex_snippet(command)
    if args.claude_desktop:
        snippets["claude_desktop"] = claude_desktop_snippet(command)

    had_error = any(r.action == "error" for r in reports)

    if args.json:
        print(
            json.dumps(
                {
                    "command": command,
                    "workspace": str(workspace),
                    "changes": [asdict(r) for r in reports],
                    "snippets": snippets,
                },
                indent=2,
            )
        )
    else:
        print(f"server command: {' '.join(command)}")
        for r in reports:
            line = f"[{r.action}] {CLIENTS[r.client].label}: {r.path}"
            if r.backup:
                line += f" (backup: {r.backup})"
            if r.detail:
                line += f" — {r.detail}"
            print(line)
        for label, snippet in snippets.items():
            print(f"\n# {label} (manual)\n{snippet}")

    return 1 if had_error else 0


# ─────────────────────────────────────────────────────────────────────────────
# lint
# ─────────────────────────────────────────────────────────────────────────────


def _add_lint_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "lint",
        help="Statically check do-file source without running Stata.",
        description=(
            "Catch unbalanced braces, missing `end`, and dangling `///` before "
            "spending a run. Give a FILE, -e CODE, or pipe on stdin. Exit code "
            "is 0 when there are no error-severity findings, 1 otherwise."
        ),
    )
    parser.add_argument("file", nargs="?", help="Path to a .do file, or '-' for stdin.")
    parser.add_argument(
        "-e", "--execute", action="append", metavar="CODE", help="Inline code to lint."
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def _lint_command(args: argparse.Namespace) -> int:
    from stata_code.core.lint import lint_code

    code, _origin, err = _read_run_code(args)
    if err is not None:
        print(f"error: {err}", file=sys.stderr)
        return 2

    findings = lint_code(code)
    error_count = sum(1 for f in findings if f.severity == "error")
    warning_count = sum(1 for f in findings if f.severity == "warning")

    if args.json:
        print(
            json.dumps(
                {
                    "ok": error_count == 0,
                    "counts": {"error": error_count, "warning": warning_count},
                    "findings": [f.to_dict() for f in findings],
                },
                indent=2,
            )
        )
    else:
        if not findings:
            print("clean: no findings")
        for f in findings:
            print(f"{f.severity.upper():7} line {f.line}: [{f.rule}] {f.message}")
        print(f"({error_count} error, {warning_count} warning)")

    return 0 if error_count == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    run_main()
