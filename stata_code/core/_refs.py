"""Process-wide ref store for log/graph/matrix payloads.

`stata_code` returns large payloads (full logs, graph bytes, matrix values)
by reference rather than inline, to keep agent token economy in check. This
module owns the underlying dict; auxiliary tools (`get_log`, `get_graph`,
`get_matrix`) read from it.

Refs are valid only within the lifetime of the producing process. Per
SCHEMA.md §3.3: consumers MUST NOT persist refs across sessions.

Eviction: the store is bounded by `MAX_ENTRIES` (default 256) on an LRU
basis. When inserting beyond the cap, the least-recently-used entry is
dropped. This guards long-running MCP server processes from unbounded
growth. Adjust via `set_capacity(n)` if you need more slack.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any

DEFAULT_MAX_ENTRIES = 256

_lock = threading.Lock()
_store: OrderedDict[str, Any] = OrderedDict()
_max_entries: int = DEFAULT_MAX_ENTRIES


def set_capacity(n: int) -> None:
    """Set the maximum number of refs to retain. Must be ≥ 1."""
    global _max_entries
    if n < 1:
        raise ValueError("capacity must be ≥ 1")
    with _lock:
        _max_entries = n
        _evict_to_capacity_locked()


def get_capacity() -> int:
    with _lock:
        return _max_entries


def put(ref: str, payload: Any) -> None:
    with _lock:
        # If already present, move-to-end keeps the entry "fresh".
        if ref in _store:
            _store.move_to_end(ref)
        _store[ref] = payload
        _evict_to_capacity_locked()


def get(ref: str) -> Any | None:
    """Fetch a payload. Touches LRU order so frequently-used refs survive."""
    with _lock:
        if ref not in _store:
            return None
        _store.move_to_end(ref)
        return _store[ref]


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


def clear_all() -> None:
    """Drop every ref. Mainly for tests."""
    with _lock:
        _store.clear()


def size() -> int:
    with _lock:
        return len(_store)


def keys() -> list[str]:
    """Return current refs in LRU order, oldest first."""
    with _lock:
        return list(_store.keys())


def snapshot() -> list[tuple[str, Any]]:
    """Return current refs and payloads without touching LRU order."""
    with _lock:
        return list(_store.items())


def _evict_to_capacity_locked() -> None:
    """Drop oldest entries until len(_store) <= _max_entries. Caller holds _lock."""
    while len(_store) > _max_entries:
        # popitem(last=False) drops the OLDEST (FIFO end of the OrderedDict)
        _store.popitem(last=False)
