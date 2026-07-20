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
import time
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
    r"C:\Program Files\StataNow\utilities",
    r"C:\Program Files\Stata19\utilities",
    r"C:\Program Files\Stata18\utilities",
    r"C:\Program Files\Stata17\utilities",
)

_RC_RE = re.compile(r"r\((-?\d+)\);")


def _extract_rc(err_text: str) -> int:
    """Pull the Stata return code out of a pystata SystemError transcript.

    The transcript may echo literal ``r(NNN);`` text from earlier, successful
    commands (display strings, help output). The real return code is the
    trailing one Stata appends at failure, so take the LAST match. Returns
    ``-1`` when no return code is present.
    """
    matches = _RC_RE.findall(err_text)
    return int(matches[-1]) if matches else -1
_LATE_OUTPUT_SLEEP_S = 0.005

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
        self._run_lock = threading.RLock()

    @staticmethod
    def _find_pystata_path() -> str | None:
        try:
            from pystata import config as _config  # type: ignore[import-not-found]  # noqa: F401

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
                "pystata is not importable; install Stata 17+ or set "
                "STATA_CODE_PYSTATA_PATH/PYSTATA_PATH to Stata's utilities "
                f"directory. Checked: {_format_checked_paths(_candidate_pystata_paths())}"
            ) from exc

        # `pystata.config.init` succeeds for whichever edition is licensed;
        # we try the most-feature-rich first. When all attempts fail, surface
        # every error rather than only the last one — collapsing to the
        # final error makes "MP errored on license, SE errored on disk
        # space, BE errored on lock" look like a single BE failure.
        edition_errors: list[tuple[str, Exception]] = []
        for ed in ("mp", "se", "be"):
            try:
                cfg.init(ed, splash=False)
                self._edition = ed
                break
            except Exception as exc:  # noqa: BLE001
                edition_errors.append((ed, exc))
        else:
            trail = "; ".join(
                f"{ed}: {type(exc).__name__}: {exc}"
                for ed, exc in edition_errors
            )
            raise PystataNotAvailable(
                f"pystata.config.init failed for all editions ({trail})"
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
        return self._run_redirected(code, quietly=False, echo=False)

    def run_suppressed(
        self,
        code: str,
        *,
        quietly: bool = True,
        echo: bool = False,
    ) -> None:
        """Run Stata code while discarding stdout.

        This is for runner-internal housekeeping commands such as frame
        switching, working-directory changes, and graph export. It preserves
        the old ``stata.run`` failure behavior by raising on non-zero Stata rc.
        """
        _stdout, rc, err_text = self._run_redirected(
            code,
            quietly=quietly,
            echo=echo,
        )
        if rc == 0:
            return
        if rc == -1:
            raise RuntimeError(err_text or "Stata adapter failed")
        raise SystemError(err_text or f"r({rc});")

    def _run_redirected(
        self,
        code: str,
        *,
        quietly: bool,
        echo: bool,
    ) -> tuple[str, int, str | None]:
        """Run ``pystata.stata.run`` under the runtime's process-wide lock.

        ``redirect_stdout`` mutates ``sys.stdout`` for the whole process, and
        pystata itself is process-global. Serializing every in-process call
        prevents helper commands and user code from stealing each other's log
        capture in direct-runner and Jupyter-kernel use.
        """
        buf = io.StringIO()
        with self._run_lock:
            try:
                with redirect_stdout(buf):
                    self._stata.run(code, quietly=quietly, echo=echo)
                    self._drain_output_buffer(buf, wait_if_empty=not quietly)
                return buf.getvalue(), 0, None
            except SystemError as exc:
                self._drain_output_buffer(buf, wait_if_empty=False)
                err_text = str(exc.args[0]) if exc.args else str(exc)
                rc = _extract_rc(err_text)
                return buf.getvalue(), rc, err_text.strip()
            except Exception as exc:  # noqa: BLE001
                return buf.getvalue(), -1, f"{type(exc).__name__}: {exc}"

    def _drain_output_buffer(
        self,
        buf: io.StringIO,
        *,
        wait_if_empty: bool,
    ) -> None:
        """Best-effort drain of pystata's C-side output buffer.

        Official ``pystata.stata.run`` already drains this buffer in the common
        path, but real Stata integration tests have shown occasional successful
        runs where Python stdout only sees an empty line. A short second drain
        catches late-buffered output without rerunning user code.
        """
        if self._config is None:
            return

        drained_any = self._drain_output_buffer_once(buf)
        has_substantive_stdout = bool(buf.getvalue().strip())
        if drained_any or not wait_if_empty or has_substantive_stdout:
            return

        time.sleep(_LATE_OUTPUT_SLEEP_S)
        self._drain_output_buffer_once(buf)

    def _drain_output_buffer_once(self, buf: io.StringIO) -> bool:
        drained_any = False
        while True:
            try:
                output = self._config.get_output()
            except Exception:  # noqa: BLE001
                return drained_any
            if not output:
                return drained_any
            buf.write(output)
            drained_any = True


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
    """Expand one user-supplied Stata/pystata path into ``utilities`` candidates.

    Accepts any of:
      - a direct ``utilities`` directory,
      - a ``utilities/pystata`` package directory,
      - a Stata install root (we append ``/utilities``),
      - a macOS ``.app`` bundle (use its parent's ``utilities``),
      - the Stata executable path (walk up a few levels to reach the root).

    Junk inputs still expand into a few candidate strings; the caller filters
    them by stat-checking ``<candidate>/pystata``. We deliberately do not walk
    the entire ancestor chain — that produced bogus paths like ``/utilities``
    on Linux and slowed discovery on network filesystems.
    """
    value = raw.strip().strip('"').strip("'")
    if not value:
        return []

    path = Path(value).expanduser()
    name = path.name.lower()
    out: list[str] = []

    if name == "pystata":
        out.append(str(path.parent))
        return _dedupe(out)
    if name == "utilities":
        out.append(str(path))
        return _dedupe(out)

    # Treat as a Stata install root or .app bundle.
    out.append(str(path / "utilities"))
    if path.suffix.lower() == ".app":
        out.append(str(path.parent / "utilities"))
    # If the user supplied the Stata executable (e.g.
    # ``…/Stata.app/Contents/MacOS/Stata``), climb a bounded number of
    # parents to find the install root. The ``[:4]`` cap covers the deepest
    # known layout (``Stata.app/Contents/MacOS/<exe>`` is exactly four levels
    # below the install root) without walking the whole filesystem on a deep
    # input.
    parents = list(path.parents)[:4]
    # Pre-scan for a ``.app`` ancestor: when present, the install root is
    # exactly one level above it (``Stata.app/utilities`` is never used by
    # Stata, and ``MacOS/utilities`` / ``Contents/utilities`` are guaranteed
    # garbage), so we can emit the single canonical candidate and skip the
    # in-bundle stat-call noise entirely.
    for parent in parents:
        if parent.suffix.lower() == ".app":
            grandparent = parent.parent
            if grandparent.name:
                out.append(str(grandparent / "utilities"))
            break
    else:
        # No ``.app`` in the climb — typical Linux / Windows install. Emit
        # ``parent/utilities`` for each level, stopping at the filesystem
        # root so we never produce ``/utilities``.
        for parent in parents:
            if not parent.name:
                break
            out.append(str(parent / "utilities"))
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


def _format_checked_paths(paths: list[str], *, limit: int = 8) -> str:
    if not paths:
        return "<none>"
    shown = paths[:limit]
    suffix = "" if len(paths) <= limit else f"; ... +{len(paths) - limit} more"
    return "; ".join(shown) + suffix
