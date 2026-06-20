"""Check GitHub Actions workflow pins for Node 24-compatible official actions."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# These official actions have Node 24 runtime majors available. Keep this
# allowlist narrow so an unrelated action update does not fail this check.
MIN_ACTION_MAJOR = {
    "actions/checkout": 7,
    "actions/setup-python": 6,
    "actions/setup-node": 6,
    "actions/upload-artifact": 7,
    "actions/download-artifact": 8,
}

MIN_NODE_VERSION = 24

_USES_RE = re.compile(r"\buses:\s*[\"']?(actions/[A-Za-z0-9_.-]+)@v(\d+)\b")
_NODE_VERSION_RE = re.compile(r"\bnode-version:\s*[\"']?(\d+)\b")


@dataclass(frozen=True)
class Finding:
    path: Path
    line_no: int
    message: str

    def format(self) -> str:
        rel = self.path.relative_to(REPO_ROOT)
        return f"{rel}:{self.line_no}: {self.message}"


def workflow_files(workflows_dir: Path = WORKFLOWS_DIR) -> list[Path]:
    return sorted(
        path
        for pattern in ("*.yml", "*.yaml")
        for path in workflows_dir.glob(pattern)
    )


def check_file(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        action_match = _USES_RE.search(line)
        if action_match:
            action, major_s = action_match.groups()
            min_major = MIN_ACTION_MAJOR.get(action)
            if min_major is not None and int(major_s) < min_major:
                findings.append(
                    Finding(
                        path,
                        line_no,
                        f"{action}@v{major_s} runs on an older Node runtime; "
                        f"use v{min_major}+",
                    )
                )

        node_match = _NODE_VERSION_RE.search(line)
        if node_match and int(node_match.group(1)) < MIN_NODE_VERSION:
            findings.append(
                Finding(
                    path,
                    line_no,
                    f"node-version {node_match.group(1)} is below "
                    f"{MIN_NODE_VERSION}",
                )
            )
    return findings


def check_workflows(workflows_dir: Path = WORKFLOWS_DIR) -> list[Finding]:
    findings: list[Finding] = []
    for path in workflow_files(workflows_dir):
        findings.extend(check_file(path))
    return findings


def main() -> int:
    findings = check_workflows()
    if findings:
        for finding in findings:
            print(finding.format(), file=sys.stderr)
        return 1
    print("ok: GitHub Actions workflow pins are Node 24-compatible")
    return 0


if __name__ == "__main__":
    sys.exit(main())
