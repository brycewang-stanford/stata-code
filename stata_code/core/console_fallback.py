"""Console fallback for Stata 11–16 (no pystata required)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from stata_code.core.result import StataResult, StataGraph
from stata_code.core.version import detect_stata


class ConsoleFallback:
    """
    Fallback adapter using Stata's batch / console mode.

    Used when pystata is unavailable (Stata < 17) or the user opts out.
    Works with Stata 11+ on macOS, Linux, and Windows.

    Execution model:
    1. Write code to a temp ``.do`` file (UTF-8, Stata-native encoding)
    2. Run ``stata -q do /tmp/tmpXXX.do`` (or platform-specific equivalent)
    3. Capture stdout + stderr; parse logs for graphs and errors
    4. Return a ``StataResult``

    Graph capture: detect ``(file X.png saved)`` lines and collect the files.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self._config = config or {}
        self._stata_path: str | None = None

    @property
    def is_available(self) -> bool:
        return self._resolve_stata_path() is not None

    def _resolve_stata_path(self) -> str | None:
        if self._stata_path:
            return self._stata_path

        candidates = [
            shutil.which("stata"),
            shutil.which("stata-se"),
            shutil.which("stata-mp"),
            shutil.which("stata-ic"),
            shutil.which("stata-be"),
        ]
        for candidate in candidates:
            if candidate:
                self._stata_path = candidate
                return candidate

        # Platform-specific fallbacks
        if hasattr(os, "uname") and os.uname().sysname == "Darwin":
            for name in [
                "/Applications/Stata/Stata.app/Contents/MacOS/StataSE",
                "/Applications/Stata/Stata.app/Contents/MacOS/StataMP",
            ]:
                if os.path.exists(name):
                    self._stata_path = name
                    return name
        elif hasattr(os, "name") and os.name == "nt":
            for root in [
                "C:/Program Files/Stata18",
                "C:/Program Files/Stata17",
            ]:
                for exe in ["StataSE-64.exe", "StataMP-64.exe"]:
                    path = f"{root}/{exe}"
                    if os.path.exists(path):
                        self._stata_path = path
                        return path

        return None

    def run(
        self,
        code: str,
        *,
        capture_graphs: bool = True,
        capture_log: bool = True,
        timeout: float | None = 300.0,
        do_file: Path | None = None,
    ) -> StataResult:
        """
        Execute ``code`` via a Stata do-file in batch/quiet mode.

        Parameters
        ----------
        code: Stata command(s).
        capture_graphs: Detect and attach graph files.
        capture_log: Capture full stdout from Stata.
        timeout: Seconds before raising TimeoutError.
        do_file: Optional pre-existing do-file path (skip temp-file creation).
        """
        stata_path = self._resolve_stata_path()
        if not stata_path:
            return StataResult(
                error="Stata executable not found. Install Stata or add it to PATH.",
                return_code=-1,
            )

        start = time.monotonic()
        result = StataResult()

        # Determine platform flags for quiet/non-interactive batch mode
        platform_flags = self._platform_flags(stata_path)

        # Write do-file
        if do_file:
            do_path = do_file
        else:
            fd, do_path = tempfile.mkstemp(suffix=".do", prefix="stata_code_")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(self._wrap_code(code))
            do_path = Path(do_path)

        cmd = [stata_path, *platform_flags, "do", str(do_path)]
        if timeout:
            cmd = [str(x) for x in cmd]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            result.stdout = proc.stdout
            result.stderr = proc.stderr
            result.return_code = proc.returncode

            if proc.returncode != 0:
                result.error = self._parse_error(proc.stderr or proc.stdout)

        except subprocess.TimeoutExpired as exc:
            result.error = f"Stata timed out after {timeout}s"
            result.return_code = -2
        except Exception as exc:
            result.error = str(exc)
            result.return_code = -1
        finally:
            if not do_file:
                try:
                    do_path.unlink(missing_ok=True)
                except OSError:
                    pass

        result.elapsed_seconds = time.monotonic() - start
        result.log = result.stdout

        if capture_graphs:
            result.graphs = self._extract_graphs(result.stdout)

        return result

    def _platform_flags(self, stata_path: str) -> list[str]:
        """Return platform-specific quiet/batch flags."""
        if hasattr(os, "uname") and os.uname().sysname == "Darwin":
            return ["/q", "-e"]  # quiet + echo off (macOS)
        elif hasattr(os, "name") and os.name == "nt":
            return ["/q", "/e"]  # quiet + echo off (Windows)
        else:
            # Linux
            return ["/q", "-e"]

    def _wrap_code(self, code: str) -> str:
        """Wrap user code with graph settings and exit."""
        graph_fmt = self._config.get("graph_format", "png")
        return (
            f"scalar sc_mixed_dta = 1\n"  # suppress banner
            f"set more off\n"
            f"set graphics {graph_fmt}\n"
            f"set logtype text\n"
            f"{code}\n"
            f"exit\n"
        )

    def _parse_error(self, text: str) -> str:
        """Extract the first actionable error message from Stata output."""
        # Match "r(198);" style return codes
        code_match = re.search(r"\br\((\d+)\);", text)
        if code_match:
            return f"Stata error r({code_match.group(1)})"
        # Fallback: first line that looks like an error
        for line in text.splitlines():
            if "error" in line.lower() or line.strip().startswith("invalid"):
                return line.strip()
        return text.splitlines()[0].strip() if text.splitlines() else "unknown error"

    def _extract_graphs(self, output: str) -> list[StataGraph]:
        """Parse Stata's log for saved-graph notices and collect files."""
        graphs: list[StataGraph] = []
        # Stata prints "(file X.png saved)" when graph is saved
        for m in re.finditer(r"\(file (\S+\.(?:png|svg|gph|pdf)) saved\)", output):
            fpath = m.group(1)
            if os.path.isfile(fpath):
                try:
                    graphs.append(StataGraph.from_file(fpath))
                except OSError:
                    pass
        return graphs
