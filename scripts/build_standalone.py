"""Build the standalone `stata-code` binary with PyInstaller.

Usage::

    python -m pip install pyinstaller
    python scripts/build_standalone.py            # → dist/stata-code[.exe]
    python scripts/build_standalone.py --name stata-code-linux-x64

The result is a single self-contained executable that needs no Python install.
It exposes the full `stata-code` CLI (`run`, `lint`, `doctor`, `setup`); with
`--backend console` it runs Stata 13+ through the Stata CLI with zero Python
dependencies on the target machine.

This script is intentionally dependency-light and CI-friendly: it shells out to
PyInstaller so the same invocation works on Linux, macOS, and Windows runners.
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY = REPO_ROOT / "packaging" / "standalone_entry.py"

# Pydantic v2's core is a compiled extension imported lazily; make sure
# PyInstaller bundles it and the parts of the package graph it can miss.
HIDDEN_IMPORTS = (
    "pydantic",
    "pydantic_core",
    "stata_code.core.console",
    "stata_code.core.policy",
    "stata_code.core.lint",
)


def default_name() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    base = "stata-code"
    if system == "windows":
        return f"{base}-windows-{machine}"
    if system == "darwin":
        return f"{base}-macos-{machine}"
    return f"{base}-linux-{machine}"


def build(name: str, *, clean: bool) -> int:
    argv = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--name",
        name,
        "--console",
        "--noconfirm",
    ]
    if clean:
        argv.append("--clean")
    for mod in HIDDEN_IMPORTS:
        argv += ["--hidden-import", mod]
    argv.append(str(ENTRY))
    print("running:", " ".join(argv))
    return subprocess.call(argv, cwd=str(REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default=None, help="Output binary name (no extension).")
    parser.add_argument("--no-clean", action="store_true", help="Skip PyInstaller --clean.")
    args = parser.parse_args()
    name = args.name or default_name()
    return build(name, clean=not args.no_clean)


if __name__ == "__main__":
    raise SystemExit(main())
