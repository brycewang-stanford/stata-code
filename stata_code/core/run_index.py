"""Read-only index over the on-disk run-bundle manifests.

Companion to :mod:`stata_code.core.log_artifacts`. The writer side of the
contract creates ``<source-dir>/log-files/<run-dir>/manifest.json`` for every
``stata_run`` call where ``persist_log_files=True`` and ``origin_path`` is
supplied. This module reads those manifests back so an agent can answer
questions like:

- "What did I last try on this notebook cell?" — filter by ``cell_id``.
- "Which runs in this notebook failed?" — filter by ``origin_path`` and
  ``ok=False``.
- "Show me runs since 02:00 UTC today." — filter by ``since``.

Notebook-aware agents pair this with ``origin_cell_id`` echo on
:class:`RunResult` to close the loop: a ``stata_run`` call records
``origin_cell_id`` into the manifest, and a later :func:`list_runs` query
surfaces every run for that cell.

Design notes:

- **Read-only.** Never mutates manifests; never re-runs anything.
- **Forgiving.** Malformed or partially-written manifests are skipped silently
  with a counter so the rest of the index is still usable.
- **Token economy.** Returns compact summaries, not full manifests. Callers
  who need the whole manifest read it from the returned ``manifest_path``.
- **Cell-agnostic protocol still.** ``list_runs`` does not interpret the
  ``origin_cell_id`` it filters on — it is a string-equality match.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

# Hard cap on `limit` so a misbehaving caller cannot ask the server to
# materialise an unbounded list of summaries.
_LIMIT_DEFAULT = 50
_LIMIT_MAX = 500


class RunIndexError(ValueError):
    """Raised for any caller-side problem in :func:`list_runs`.

    Like :class:`stata_code.core.notebook.NotebookError`, the message starts
    with a stable ``kind:`` prefix so MCP dispatchers can map it to a typed
    error without parsing free text. Use the :pyattr:`kind` property
    instead of slicing the message manually.
    """

    @property
    def kind(self) -> str:
        """Return the kind token, or ``"run_index_error"`` if the message
        does not follow the ``"<kind>: …"`` convention."""
        message = str(self)
        token, sep, _ = message.partition(":")
        token = token.strip()
        if not sep or not token:
            return "run_index_error"
        return token


# ─────────────────────────────────────────────────────────────────────────────
# Locating the log-files root
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_log_dir(
    *,
    log_dir: str | Path | None,
    origin_path: str | Path | None,
) -> Path:
    """Pick the directory to scan.

    Priority: ``log_dir`` (explicit) > ``<origin_path parent>/log-files``.
    Caller MUST provide at least one of the two.
    """
    if log_dir is not None:
        p = Path(log_dir).expanduser()
        if not p.is_absolute():
            p = p.resolve()
        return p
    if origin_path is None:
        raise RunIndexError(
            "log_dir_required: provide log_dir or origin_path so the index "
            "knows where to look"
        )
    src = Path(origin_path).expanduser()
    if not src.is_absolute():
        src = src.resolve()
    return src.parent / "log-files"


# ─────────────────────────────────────────────────────────────────────────────
# Manifest reading
# ─────────────────────────────────────────────────────────────────────────────


_REQUIRED_MANIFEST_FIELDS = (
    "request_id",
    "session_id",
    "started_at",
    "ok",
    "rc",
)


def _read_manifest(manifest_path: Path) -> dict[str, Any] | None:
    """Best-effort read of one manifest.

    Returns ``None`` (so the caller can count it as skipped) for any of:
    - file missing or unreadable
    - JSON decode error
    - missing one of the required fields
    """
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    for field in _REQUIRED_MANIFEST_FIELDS:
        if field not in data:
            return None
    return data


def _summary_from_manifest(
    manifest_path: Path,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Project a stored manifest down to the wire shape returned by list_runs.

    We deliberately do not echo every field — the manifest is a write-side
    record that may grow over time. Callers who need the whole thing read it
    from disk via the returned ``manifest_path``.
    """
    files = data.get("files") if isinstance(data.get("files"), dict) else {}
    log_path = files.get("log") if isinstance(files, dict) else None
    code_path = files.get("code") if isinstance(files, dict) else None
    return {
        "request_id": data.get("request_id"),
        "session_id": data.get("session_id"),
        "started_at": data.get("started_at"),
        "elapsed_ms": data.get("elapsed_ms"),
        "ok": data.get("ok"),
        "rc": data.get("rc"),
        "source_path": data.get("source_path"),
        "origin_kind": data.get("origin_kind"),
        "origin_label": data.get("origin_label"),
        "origin_cell_id": data.get("origin_cell_id"),
        "directory": str(manifest_path.parent),
        "manifest_path": str(manifest_path),
        "log_path": log_path if isinstance(log_path, str) else None,
        "code_path": code_path if isinstance(code_path, str) else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Filters
# ─────────────────────────────────────────────────────────────────────────────


def _normalise_path(p: str | Path) -> str:
    """Resolve to an absolute string for path equality comparisons.

    Note: ``resolve()`` is only invoked for relative paths. Absolute paths
    are returned as-is to match what ``persist_run_log_files`` writes into
    the manifest (also already-resolved at write time). If the caller passes
    a relative ``origin_path`` whose target moved or whose symlinks now
    differ from when the manifest was written, the equality match may miss.
    Pass an absolute path to avoid this.
    """
    pp = Path(p).expanduser()
    if not pp.is_absolute():
        pp = pp.resolve()
    return str(pp)


# Accepted ``since`` spellings, most specific first. Each is normalised back to
# the canonical millisecond-UTC form so the lexicographic comparison against
# ``started_at`` (also canonical) is sound.
_SINCE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%dT%H:%M:%S.%fZ",  # canonical, e.g. 2026-05-08T01:00:00.000Z
    "%Y-%m-%dT%H:%M:%SZ",     # no milliseconds
    "%Y-%m-%dT%H:%M:%S.%f",   # canonical without trailing Z
    "%Y-%m-%dT%H:%M:%S",      # no milliseconds, no Z
    "%Y-%m-%dT%H:%MZ",        # no seconds
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d",               # date only → start of that UTC day
)


def _normalize_since(since: str) -> str:
    """Normalise a caller-supplied ``since`` to canonical millisecond-UTC.

    ``started_at`` is always emitted as ``YYYY-MM-DDTHH:MM:SS.mmmZ`` and the
    ``since`` filter compares byte-wise against it. That comparison is only
    correct when ``since`` is in the *same* format: a date-only ``"2026-05-08"``
    or a seconds-only ``"2026-05-08T01:00:00Z"`` would silently sort wrong
    (e.g. date-only excludes every run that day because ``.`` > end-of-string).
    Accept the common shorthands, re-emit the canonical form, and reject
    anything unparseable with a typed ``since_invalid`` rather than comparing
    garbage.
    """
    raw = since.strip()
    for fmt in _SINCE_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
    raise RunIndexError(
        "since_invalid: expected an ISO 8601 UTC timestamp such as "
        "'2026-05-08T01:00:00.000Z' (date-only and seconds-only forms are "
        f"also accepted); got {since!r}"
    )


def _matches_filters(
    summary: dict[str, Any],
    *,
    origin_path_norm: str | None,
    cell_id: str | None,
    session_id: str | None,
    ok: bool | None,
    since: str | None,
) -> bool:
    if origin_path_norm is not None:
        sp = summary.get("source_path")
        if not isinstance(sp, str):
            return False
        try:
            if _normalise_path(sp) != origin_path_norm:
                return False
        except OSError:
            return False
    if cell_id is not None:
        if summary.get("origin_cell_id") != cell_id:
            return False
    if session_id is not None:
        if summary.get("session_id") != session_id:
            return False
    if ok is not None:
        if summary.get("ok") is not ok:
            return False
    if since is not None:
        started_at = summary.get("started_at")
        if not isinstance(started_at, str) or started_at < since:
            return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────


def list_runs(
    *,
    log_dir: str | Path | None = None,
    origin_path: str | Path | None = None,
    cell_id: str | None = None,
    session_id: str | None = None,
    ok: bool | None = None,
    since: str | None = None,
    limit: int = _LIMIT_DEFAULT,
    offset: int = 0,
) -> dict[str, Any]:
    """Return compact summaries of run-bundle manifests under ``log_dir``.

    Either ``log_dir`` or ``origin_path`` must be supplied. With
    ``origin_path``, the directory is inferred as
    ``<origin_path parent>/log-files``.

    All filters are AND-ed together; all are optional. ``since`` is an ISO
    8601 UTC timestamp compared lexicographically (**inclusive**) against
    ``started_at`` — runs at the exact ``since`` timestamp are returned. It is
    normalised to canonical millisecond-UTC first (date-only and seconds-only
    forms are accepted); an unparseable value raises ``since_invalid`` rather
    than comparing wrongly.

    ``offset`` skips the first N matches (after newest-first sorting) so a
    caller can page through a long history with repeated ``limit``/``offset``
    calls. The scan itself is O(number of run directories); for very large
    histories, narrow it with the ``origin_path`` / ``cell_id`` / ``session_id``
    / ``since`` filters.

    Result shape::

        {
            "log_dir": str,
            "scanned_count": int,    # subdirectories examined
            "match_count": int,      # passed filters (may exceed limit)
            "skipped_count": int,    # malformed/missing manifests
            "limit": int,
            "truncated": bool,       # True iff more matches remain past
                                     #   offset+limit
            "runs": [<summary>, ...] # ≤ limit, newest first, starting at offset
        }

    Each ``runs`` entry has: request_id, session_id, started_at, elapsed_ms,
    ok, rc, source_path, origin_kind, origin_label, origin_cell_id,
    directory, manifest_path, log_path, code_path.
    """
    # `bool` is a subclass of `int` in Python; `True` would otherwise satisfy
    # `isinstance(limit, int)` and silently mean `limit=1`. Reject explicitly.
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise RunIndexError("limit_invalid: limit must be a positive integer")
    requested_limit = limit
    if limit > _LIMIT_MAX:
        limit = _LIMIT_MAX

    # `bool` is an `int` subclass; reject it explicitly so ``offset=True`` does
    # not silently page by one.
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise RunIndexError("offset_invalid: offset must be a non-negative integer")

    if since is not None and not isinstance(since, str):
        raise RunIndexError("since_invalid: since must be an ISO 8601 string")
    since_norm = _normalize_since(since) if since is not None else None

    target = _resolve_log_dir(log_dir=log_dir, origin_path=origin_path)
    origin_path_norm: str | None = None
    if origin_path is not None:
        try:
            origin_path_norm = _normalise_path(origin_path)
        except OSError as exc:
            raise RunIndexError(f"origin_path_invalid: {exc}") from exc

    if not target.exists() or not target.is_dir():
        # Empty result, not an error — a notebook that has never been run
        # legitimately has no log-files dir.
        empty: dict[str, Any] = {
            "log_dir": str(target),
            "scanned_count": 0,
            "match_count": 0,
            "skipped_count": 0,
            "limit": limit,
            "offset": offset,
            "truncated": False,
            "runs": [],
        }
        if requested_limit != limit:
            empty["requested_limit"] = requested_limit
        return empty

    scanned = 0
    skipped = 0
    matched: list[dict[str, Any]] = []

    for entry in target.iterdir():
        if not entry.is_dir():
            continue
        scanned += 1
        manifest_path = entry / "manifest.json"
        data = _read_manifest(manifest_path)
        if data is None:
            skipped += 1
            continue
        summary = _summary_from_manifest(manifest_path, data)
        if not _matches_filters(
            summary,
            origin_path_norm=origin_path_norm,
            cell_id=cell_id,
            session_id=session_id,
            ok=ok,
            since=since_norm,
        ):
            continue
        matched.append(summary)

    # Newest-first; tie-break on request_id for determinism.
    def _sort_key(s: dict[str, Any]) -> tuple[str, str]:
        return (s.get("started_at") or "", s.get("request_id") or "")

    matched.sort(key=_sort_key, reverse=True)

    page_end = offset + limit
    payload: dict[str, Any] = {
        "log_dir": str(target),
        "scanned_count": scanned,
        "match_count": len(matched),
        "skipped_count": skipped,
        "limit": limit,
        "offset": offset,
        "truncated": len(matched) > page_end,
        "runs": matched[offset:page_end],
    }
    if requested_limit != limit:
        # Caller asked for more than ``_LIMIT_MAX``. Echo the original so a
        # ``truncated=True`` response is unambiguous: did the manifest dir
        # really hold more rows than ``limit``, or did the server silently
        # clamp the request? With ``requested_limit`` present the agent can
        # tell, and either widen the scan or accept the cap.
        payload["requested_limit"] = requested_limit
    return payload
