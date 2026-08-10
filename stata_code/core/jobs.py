"""Background execution for long-running Stata runs.

Bootstrap replications, permutation tests and `prodest`-style grouped loops
routinely run for minutes. Executed synchronously they block the caller for
their whole duration — an agent that submits a 543-second bootstrap can do
nothing else until it returns, and a client-side read timeout turns a finished
run into a lost one.

This module runs such a call on a worker thread and hands back a `job_id`
immediately. The run still goes through :func:`stata_code.core._pool.pool_execute`,
so it keeps the same session routing, timeout enforcement, cancellation and
ref-ferrying as a foreground call; only the waiting changes.

Concurrency note: Stata is single-threaded per session. A background job holds
its session's worker for its whole run, so a foreground call to the *same*
`session_id` queues behind it and comes back with `rc=-5`
(`error.kind="session_busy"`) once its own `timeout_ms` elapses. Give a
background job its own `session_id` when you want to keep working meanwhile.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

from stata_code.core._pool import pool_execute
from stata_code.core.schema import RunResult

# How many finished jobs to keep for status queries before evicting the oldest.
# Running jobs are never evicted.
DEFAULT_MAX_JOBS = 64

# Longest a status query may block waiting for a job to finish. Keeps a polling
# caller from turning `stata_run_status` into an unbounded wait of its own.
MAX_WAIT_MS = 60_000

_CODE_PREVIEW_CHARS = 200


def _utc_iso_ms() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


class Job:
    """One background run. Mutated only by its own worker thread."""

    __slots__ = (
        "job_id",
        "session_id",
        "code_preview",
        "submitted_at",
        "finished_at",
        "status",
        "result",
        "error",
        "_done",
        "_started",
        "_elapsed_ms",
    )

    def __init__(self, job_id: str, session_id: str, code: str) -> None:
        self.job_id = job_id
        self.session_id = session_id
        self.code_preview = code[:_CODE_PREVIEW_CHARS]
        self.submitted_at = _utc_iso_ms()
        self.finished_at: str | None = None
        self.status: str = "running"
        self.result: RunResult | None = None
        self.error: str | None = None
        self._done = threading.Event()
        self._started = time.monotonic()
        self._elapsed_ms: int | None = None

    @property
    def done(self) -> bool:
        return self._done.is_set()

    def wait(self, timeout_s: float | None) -> bool:
        """Block until the job finishes or the wait budget expires."""
        return self._done.wait(timeout=timeout_s)

    def elapsed_ms(self) -> int:
        """How long the run took, or has been running so far.

        Frozen once the job reaches a terminal state. Recomputing it on every
        poll made a finished job's reported duration grow without bound — a
        1.7s bootstrap polled ten minutes later reported ten minutes — so
        callers attributing wall time to Stata were reading their own latency.
        """
        if self._elapsed_ms is not None:
            return self._elapsed_ms
        return max(0, int((time.monotonic() - self._started) * 1000))

    def summary(self) -> dict[str, Any]:
        """Status without the (potentially large) result payload."""
        return {
            "job_id": self.job_id,
            "session_id": self.session_id,
            "status": self.status,
            "submitted_at": self.submitted_at,
            "finished_at": self.finished_at,
            "elapsed_ms": self.elapsed_ms(),
            "code_preview": self.code_preview,
        }


class JobRegistry:
    """Thread-safe, bounded registry of background runs."""

    def __init__(self, *, max_jobs: int = DEFAULT_MAX_JOBS) -> None:
        if max_jobs < 1:
            raise ValueError("max_jobs must be ≥ 1")
        self._max_jobs = max_jobs
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()

    def submit(self, code: str, *, session_id: str = "main", **options: Any) -> Job:
        """Start `code` on a worker thread and return its :class:`Job` at once."""
        job = Job(uuid.uuid4().hex, session_id, code)
        with self._lock:
            self._jobs[job.job_id] = job
            self._evict_locked()
        thread = threading.Thread(
            target=self._run,
            args=(job, code, session_id, options),
            name=f"stata-code-job-{job.job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return job

    def _run(self, job: Job, code: str, session_id: str, options: dict[str, Any]) -> None:
        result: RunResult | None = None
        error: str | None = None
        status = "done"
        try:
            result = pool_execute(code, session_id=session_id, **options)
        except BaseException as exc:  # noqa: BLE001 - a job thread must never die silently
            # Includes ValueError for a bad option set. Recorded on the job so
            # the polling caller sees the failure instead of a job that stays
            # "running" forever.
            error = f"{type(exc).__name__}: {exc}"
            status = "error"
        finally:
            # `status` is published LAST of the data fields, so a reader that
            # sees a terminal status is guaranteed to see the payload with it.
            job.result = result
            job.error = error
            # Freeze the clock before publishing a terminal status, so no
            # reader can observe "done" alongside a still-advancing duration.
            job._elapsed_ms = max(  # noqa: SLF001 - same-module private handshake
                0, int((time.monotonic() - job._started) * 1000)  # noqa: SLF001
            )
            job.finished_at = _utc_iso_ms()
            job.status = status
            job._done.set()  # noqa: SLF001 - same-module private handshake

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        """All tracked jobs, newest first."""
        with self._lock:
            return list(reversed(self._jobs.values()))

    def _evict_locked(self) -> None:
        """Drop the oldest FINISHED jobs once the registry is over capacity.

        Running jobs are never evicted — losing the handle to a job that is
        still executing would strand it with no way to collect its result.
        """
        while len(self._jobs) > self._max_jobs:
            victim = next((jid for jid, j in self._jobs.items() if j.done), None)
            if victim is None:
                return
            del self._jobs[victim]


_registry: JobRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> JobRegistry:
    global _registry
    if _registry is not None:
        return _registry
    with _registry_lock:
        if _registry is None:
            _registry = JobRegistry()
        return _registry


def submit(code: str, *, session_id: str = "main", **options: Any) -> Job:
    """Submit a background run through the process-wide registry."""
    return get_registry().submit(code, session_id=session_id, **options)


def get(job_id: str) -> Job | None:
    return get_registry().get(job_id)


def list_jobs() -> list[Job]:
    return get_registry().list()


def reset_registry() -> None:
    """Drop the process-wide registry. For tests and clean shutdown."""
    global _registry
    with _registry_lock:
        _registry = None
