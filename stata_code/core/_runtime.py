"""Private pystata runtime wrapper — process-wide singleton.

pystata initializes one Stata kernel per Python process. This module manages
that lifecycle and exposes a small surface (`run_capture`, sfi accessors) for
the public runner. Not part of the API; subject to change without notice.
"""

from __future__ import annotations

import io
import os
import re
import sys
import threading
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

# Common pystata install locations across platforms. Order is best-guess for
# popularity; the first match wins. Users can also pre-install pystata into
# their Python environment, in which case we skip the path search entirely.
_PYSTATA_PATH_ENV_VARS: tuple[str, ...] = (
    "STATA_CODE_PYSTATA_PATH",
    "PYSTATA_PATH",
)

_STATA_ROOT_ENV_VARS: tuple[str, ...] = (
    "STATA_HOME",
    "STATA_PATH",
    "STATA_CLI",
)

_PYSTATA_SEARCH_PATHS: tuple[str, ...] = (
    "/Applications/StataNow/utilities",
    "/Applications/Stata19/utilities",
    "/Applications/Stata/utilities",
    "/Applications/Stata18/utilities",
    "/Applications/Stata17/utilities",
    "/usr/local/stata/utilities",
    "/usr/local/stata19/utilities",
    "/usr/local/stata18/utilities",
    "/usr/local/stata17/utilities",
    r"C:\Program Files\Stata19\utilities",
    r"C:\Program Files\Stata18\utilities",
    r"C:\Program Files\Stata17\utilities",
)

_RC_RE = re.compile(r"r\((-?\d+)\);")

_lock = threading.Lock()
_runtime: PystataRuntime | None = None
_init_attempted = False
_init_error: Exception | None = None


class PystataNotAvailable(RuntimeError):
    """Raised when pystata cannot be imported or Stata cannot be initialized."""


class PystataRuntime:
    """Wraps a single pystata-initialized Stata kernel.

    Construct via `get_runtime()`; do not instantiate directly. The kernel
    persists for the lifetime of the Python process.
    """

    def __init__(self) -> None:
        self._initialized = False
        self._stata: Any = None
        self._config: Any = None
        self._sfi: Any = None
        self._edition: str = ""

    @staticmethod
    def _find_pystata_path() -> str | None:
        try:
            from pystata import config as _config  # noqa: F401

            return None  # already importable; nothing to add to sys.path
        except ImportError:
            pass
        for p in _candidate_pystata_paths():
            if Path(p).joinpath("pystata").is_dir():
                return p
        return None

    def _initialize(self) -> None:
        if self._initialized:
            return
        path = self._find_pystata_path()
        if path is not None and path not in sys.path:
            sys.path.insert(0, path)
        try:
            from pystata import config as cfg
        except ImportError as exc:
            raise PystataNotAvailable(
                "pystata is not importable; install Stata 17+ or set PYTHONPATH"
            ) from exc

        last_err: Exception | None = None
        for ed in ("mp", "se", "be"):
            try:
                cfg.init(ed, splash=False)
                self._edition = ed
                break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
        else:
            raise PystataNotAvailable(
                f"pystata.config.init failed for all editions: {last_err}"
            )

        cfg.set_graph_show(False)
        cfg.set_graph_format("png")

        import sfi  # type: ignore[import-not-found]
        from pystata import stata as st  # must be imported after config.init

        self._config = cfg
        self._stata = st
        self._sfi = sfi
        self._initialized = True

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def edition(self) -> str:
        return self._edition

    @property
    def stata(self) -> Any:
        return self._stata

    @property
    def sfi(self) -> Any:
        return self._sfi

    def run_capture(self, code: str) -> tuple[str, int, str | None]:
        """Run Stata code and return (captured_stdout, rc, error_message).

        - rc == 0 on success.
        - rc > 0 is Stata's `_rc` (parsed from the SystemError message).
        - rc == -1 is an adapter-level crash (non-Stata exception).
        - error_message is None on success, non-None on failure.
        """
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                self._stata.run(code, quietly=False, echo=False)
            return buf.getvalue(), 0, None
        except SystemError as exc:
            err_text = str(exc.args[0]) if exc.args else str(exc)
            m = _RC_RE.search(err_text)
            rc = int(m.group(1)) if m else -1
            return buf.getvalue(), rc, err_text.strip()
        except Exception as exc:  # noqa: BLE001
            return buf.getvalue(), -1, f"{type(exc).__name__}: {exc}"


def get_runtime() -> PystataRuntime:
    """Return the process-wide runtime, initializing on first call.

    Raises `PystataNotAvailable` if pystata or Stata cannot be initialized.
    The failure is cached: subsequent calls re-raise the same error without
    retrying init (which would print messy errors repeatedly).
    """
    global _runtime, _init_attempted, _init_error
    if _runtime is not None:
        return _runtime
    if _init_attempted and _init_error is not None:
        raise _init_error  # cached failure
    with _lock:
        if _runtime is not None:
            return _runtime
        _init_attempted = True
        try:
            rt = PystataRuntime()
            rt._initialize()
            _runtime = rt
            return _runtime
        except Exception as exc:  # noqa: BLE001
            _init_error = exc if isinstance(exc, PystataNotAvailable) else (
                PystataNotAvailable(f"runtime init failed: {exc}")
            )
            raise _init_error from exc


def is_available() -> bool:
    """Cheap, non-raising check. Initializes on first call."""
    try:
        get_runtime()
        return True
    except PystataNotAvailable:
        return False


def _candidate_pystata_paths() -> list[str]:
    """Return sys.path entries that may contain StataCorp's ``pystata``.

    Accepts either a direct ``utilities`` path, the ``utilities/pystata`` package
    path, a Stata install root, a macOS ``.app`` bundle, or the Stata executable
    path through environment variables. This keeps the public setup story close
    to other Stata tooling while still loading the official StataCorp module.
    """
    candidates: list[str] = []

    for env_name in _PYSTATA_PATH_ENV_VARS:
        raw = os.environ.get(env_name)
        if raw:
            candidates.extend(_normalize_pystata_candidate(raw))

    for env_name in _STATA_ROOT_ENV_VARS:
        raw = os.environ.get(env_name)
        if raw:
            candidates.extend(_normalize_pystata_candidate(raw))

    candidates.extend(_PYSTATA_SEARCH_PATHS)
    return _dedupe(candidates)


def _normalize_pystata_candidate(raw: str) -> list[str]:
    """Expand one user-supplied Stata/pystata path into utilities candidates."""
    value = raw.strip().strip('"').strip("'")
    if not value:
        return []

    path = Path(value).expanduser()
    bases: list[Path] = [path]
    if path.suffix.lower() == ".app":
        bases.append(path.parent)
    if path.name.lower() == "pystata":
        bases.append(path.parent)
    if path.name.lower() != "utilities":
        bases.append(path / "utilities")
    if path.is_file():
        bases.append(path.parent)
    bases.extend(path.parents)

    out: list[str] = []
    for base in bases:
        if base.name.lower() == "pystata":
            out.append(str(base.parent))
        elif base.name.lower() == "utilities":
            out.append(str(base))
        else:
            out.append(str(base / "utilities"))
    return _dedupe(out)


def _dedupe(values: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
