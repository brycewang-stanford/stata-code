"""Process-wide ref store for log/graph/matrix payloads.

`stata_code` returns large payloads (full logs, graph bytes, matrix values)
by reference rather than inline, to keep agent token economy in check. This
module owns the underlying dict; auxiliary tools (`get_log`, `get_graph`,
`get_matrix`) read from it.

Refs are valid only within the lifetime of the producing process. Per
SCHEMA.md §3.3: consumers MUST NOT persist refs across sessions.
"""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_store: dict[str, Any] = {}


def put(ref: str, payload: Any) -> None:
    with _lock:
        _store[ref] = payload


def get(ref: str) -> Any | None:
    with _lock:
        return _store.get(ref)


def has(ref: str) -> bool:
    with _lock:
        return ref in _store


def discard(ref: str) -> None:
    with _lock:
        _store.pop(ref, None)


def clear_prefix(prefix: str) -> int:
    """Drop all refs whose key starts with `prefix`. Returns count dropped."""
    with _lock:
        keys = [k for k in _store if k.startswith(prefix)]
        for k in keys:
            del _store[k]
        return len(keys)


def size() -> int:
    with _lock:
        return len(_store)
