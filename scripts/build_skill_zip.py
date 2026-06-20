"""Package the ``stata-code`` skill into a single uploadable ``.zip``.

The skill (``skills/stata-code/SKILL.md`` + the ``references/`` library) is
consumed two ways:

* In-repo / Claude Code — read straight from ``skills/stata-code/``.
* Claude.ai project knowledge — uploaded as a ``.zip``. This script builds
  that archive.

The archive contains a single top-level ``stata-code/`` folder so it extracts
cleanly::

    stata-code/SKILL.md
    stata-code/references/econometrics.md
    stata-code/references/packages/reghdfe.md
    ...

Run::

    python scripts/build_skill_zip.py                 # -> dist/stata-code-skill.zip
    python scripts/build_skill_zip.py -o /tmp/out.zip  # custom destination

The build is deterministic (sorted entries, fixed timestamps) so re-running it
on unchanged inputs produces a byte-identical archive.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "stata-code"
DEFAULT_OUTPUT = REPO_ROOT / "dist" / "stata-code-skill.zip"
ARCHIVE_PREFIX = "stata-code"

# Fixed timestamp for reproducible archives (zip epoch starts at 1980).
_FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def collect_files(skill_dir: Path = SKILL_DIR) -> list[Path]:
    """Return every shippable skill file, sorted, relative-stable.

    Excludes editor/OS cruft so the archive is clean.
    """
    if not skill_dir.is_dir():
        raise FileNotFoundError(f"skill directory not found: {skill_dir}")
    skip = {".DS_Store"}
    files = [
        p
        for p in skill_dir.rglob("*")
        if p.is_file() and p.name not in skip and "__pycache__" not in p.parts
    ]
    return sorted(files)


def build_zip(
    dest: Path = DEFAULT_OUTPUT,
    skill_dir: Path = SKILL_DIR,
) -> list[str]:
    """Write the skill archive to ``dest``; return the arcnames included."""
    files = collect_files(skill_dir)
    if not files:
        raise FileNotFoundError(f"no skill files under {skill_dir}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    arcnames: list[str] = []
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            rel = path.relative_to(skill_dir).as_posix()
            arcname = f"{ARCHIVE_PREFIX}/{rel}"
            info = zipfile.ZipInfo(arcname, date_time=_FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16  # regular file, rw-r--r--
            zf.writestr(info, path.read_bytes())
            arcnames.append(arcname)
    return arcnames


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Destination .zip (default: {DEFAULT_OUTPUT.relative_to(REPO_ROOT)}).",
    )
    args = parser.parse_args()

    try:
        arcnames = build_zip(args.output)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    size = args.output.stat().st_size
    print(f"wrote: {args.output}  ({len(arcnames)} files, {size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
