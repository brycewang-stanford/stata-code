"""Stata edition and version detection."""

from __future__ import annotations

import subprocess
import re
import shutil
from dataclasses import dataclass
from enum import Enum


class StataEdition(Enum):
    MP = "mp"
    SE = "se"
    IC = "ic"
    BE = "be"
    UNKNOWN = "unknown"


@dataclass
class StataVersion:
    edition: StataEdition
    version: str  # e.g. "18.0", "17.5"
    major: int
    minor: int

    @property
    def supports_pystata(self) -> bool:
        """pystata requires Stata 17 or later."""
        return self.major >= 17

    @property
    def is_stata_installed(self) -> bool:
        return self.edition != StataEdition.UNKNOWN


def _find_stata_binary() -> str | None:
    """Return the path to the Stata binary if found, else None."""
    # Try `which stata` / `where` first for user-configured path
    stata_path = shutil.which("stata")
    if stata_path:
        return stata_path

    # Fall back to common install locations
    import os

    candidates: list[str] = []
    if hasattr(os, "uname") and os.uname().sysname == "Darwin":
        candidates = [
            "/Applications/Stata/Stata.app/Contents/MacOS/StataSE",
            "/Applications/Stata/Stata.app/Contents/MacOS/StataMP",
            "/Applications/Stata/Stata.app/Contents/MacOS/StataIC",
            "/Applications/Stata/Stata.app/Contents/MacOS/StataBE",
        ]
    elif hasattr(os, "name") and os.name == "nt":
        candidates = [
            "C:/Program Files/Stata18/StataSE-64.exe",
            "C:/Program Files/Stata18/StataMP-64.exe",
            "C:/Program Files/Stata17/StataSE-64.exe",
            "C:/Program Files/Stata17/StataMP-64.exe",
        ]
    else:
        candidates = [
            "/usr/local/stata/stata-se",
            "/usr/local/stata/stata-mp",
            "/usr/local/stata/stata-ic",
        ]

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


def detect_stata() -> StataVersion:
    """Auto-detect the installed Stata edition and version."""
    stata_path = _find_stata_binary()
    if not stata_path:
        return StataVersion(
            edition=StataEdition.UNKNOWN,
            version="",
            major=0,
            minor=0,
        )

    # Run Stata in quiet batch mode to query version
    # `stata --version` prints version info to stderr on macOS; `display c(version)` works inside Stata
    try:
        result = subprocess.run(
            [stata_path, "/q", "-e", "display c(version)", "exit"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = result.stdout + result.stderr
    except Exception:
        output = ""

    # Parse output like "Version 18.0" or "Stata 17.5"
    version_match = re.search(r"(?:Version|Stata)[^\d]*(\d+)\.(\d+)", output)
    if version_match:
        major = int(version_match.group(1))
        minor = int(version_match.group(2))
    else:
        major, minor = 0, 0

    # Detect edition from binary name
    path_lower = stata_path.lower()
    if "stataSE" in path_lower or "-se" in path_lower:
        edition = StataEdition.SE
    elif "stataMP" in path_lower or "-mp" in path_lower:
        edition = StataEdition.MP
    elif "stataIC" in path_lower or "-ic" in path_lower:
        edition = StataEdition.IC
    elif "stataBE" in path_lower or "-be" in path_lower:
        edition = StataEdition.BE
    else:
        edition = StataEdition.UNKNOWN

    version_str = f"{major}.{minor}" if major else ""
    return StataVersion(edition=edition, version=version_str, major=major, minor=minor)
