"""Shared pytest hygiene.

Two cross-module sources of leakage justified a single conftest:

1. ``_refs._store`` is a module-level LRU that survives every test. A test
   that asserts ``snapshot()`` size sees pollution from anything that ran
   before it. We snapshot/restore the store per-test so each test gets a
   clean ref namespace without forcing every test to clean up after itself.

2. The default :class:`SessionPool` keeps subprocess workers warm across
   tests. They are intentionally process-scoped (cheap reuse), but we
   shut them down at session end so the test runner exits cleanly even
   when a worker is mid-call.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_refs_store():
    from stata_code.core import _refs

    # ``_store`` is an ``OrderedDict`` whose insertion order doubles as the
    # LRU ordering used by ``_evict_to_capacity_locked``. ``list(_store.items())``
    # preserves that order; ``dict(...)`` would as well in CPython 3.7+,
    # but the explicit list spelling makes the LRU-preservation intent
    # obvious to a future reader and is robust against alternate dict
    # implementations.
    with _refs._lock:  # noqa: SLF001
        saved = list(_refs._store.items())  # noqa: SLF001
        _refs._store.clear()  # noqa: SLF001
    try:
        yield
    finally:
        with _refs._lock:  # noqa: SLF001
            _refs._store.clear()  # noqa: SLF001
            for k, v in saved:
                _refs._store[k] = v  # noqa: SLF001


@pytest.fixture(scope="session", autouse=True)
def _shutdown_default_pool_at_end():
    yield
    try:
        from stata_code.core._pool import shutdown_default_pool

        shutdown_default_pool()
    except Exception:  # noqa: BLE001
        pass
