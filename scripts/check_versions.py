#!/usr/bin/env python3
"""Verify that every release version literal is aligned."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return ROOT.joinpath(rel).read_text(encoding="utf-8")


def _extract(rel: str, pattern: str) -> str:
    match = re.search(pattern, _read(rel), re.MULTILINE)
    if match is None:
        raise RuntimeError(f"could not find version in {rel}")
    return match.group(1)


def _tag_version(tag: str) -> str:
    for prefix in ("vscode-v", "v"):
        if tag.startswith(prefix):
            return tag[len(prefix):]
    raise RuntimeError(f"tag must start with v or vscode-v, got {tag!r}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check pyproject/Python/MCP/VS Code version literals."
    )
    parser.add_argument(
        "--tag",
        help="Optional Git tag to compare against, e.g. v0.6.2 or vscode-v0.6.2.",
    )
    args = parser.parse_args()

    versions = {
        "pyproject.toml": _extract(
            "pyproject.toml", r'(?m)^\s*version\s*=\s*"([^"]+)"'
        ),
        "stata_code/__init__.py": _extract(
            "stata_code/__init__.py", r'(?m)^__version__\s*=\s*"([^"]+)"'
        ),
        "stata_code/mcp/server.py": _extract(
            "stata_code/mcp/server.py", r'(?m)^__version__\s*=\s*"([^"]+)"'
        ),
        "vscode/package.json": json.loads(_read("vscode/package.json"))["version"],
        "vscode/src/mcpClient.ts": _extract(
            "vscode/src/mcpClient.ts",
            r'\{\s*name:\s*"stata-code-vscode",\s*version:\s*"([^"]+)"\s*\}',
        ),
    }

    unique = sorted(set(versions.values()))
    ok = len(unique) == 1
    expected = unique[0] if ok else None

    if args.tag:
        tag_version = _tag_version(args.tag)
        if expected != tag_version:
            ok = False
            versions[f"tag:{args.tag}"] = tag_version

    if ok:
        print(f"ok: all release versions are {expected}")
        return 0

    print("version mismatch:", file=sys.stderr)
    for source, version in versions.items():
        print(f"  {source}: {version}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
