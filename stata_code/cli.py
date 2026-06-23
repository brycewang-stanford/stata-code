"""Top-level command-line interface for stata-code."""

from __future__ import annotations

import argparse

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

    args = parser.parse_args(argv)
    if args.version:
        from stata_code import __version__

        print(__version__)
        return 0
    if args.command in {"doctor", "verify"}:
        report = run_doctor(
            probe_stata=not args.no_stata_probe,
            stata_timeout_ms=args.stata_timeout_ms,
            workspace=args.workspace,
            include_user_configs=not args.no_user_config_scan,
        )
        print(format_json(report) if args.json else format_text(report))
        return 0 if report.ok else 1

    parser.print_help()
    return 0


def run_main() -> None:
    raise SystemExit(main())


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


if __name__ == "__main__":  # pragma: no cover
    run_main()
