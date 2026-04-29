"""Unified Stata result schema — shared across all frontends."""

from __future__ import annotations

import base64
import io
import warnings
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StataGraph:
    """Captured graph output from Stata."""

    format: str = "png"  # png | svg | gph | pdf
    data: bytes = b""
    path: str | None = None  # original file path if saved

    def to_base64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")

    def to_data_uri(self) -> str:
        mime = {
            "png": "image/png",
            "svg": "image/svg+xml",
            "pdf": "application/pdf",
            "gph": "application/octet-stream",
        }.get(self.format, "application/octet-stream")
        return f"data:{mime};base64,{self.to_base64()}"

    def save(self, path: str) -> None:
        with open(path, "wb") as f:
            f.write(self.data)
        self.path = path

    @classmethod
    def from_file(cls, path: str, fmt: str | None = None) -> StataGraph:
        with open(path, "rb") as f:
            data = f.read()
        if fmt is None:
            fmt = path.rsplit(".", 1)[-1] if "." in path else "png"
        return cls(format=fmt, data=data, path=path)

    @classmethod
    def from_matplotlib(cls, fig: Any) -> StataGraph:  # matplotlib Figure
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        buf.seek(0)
        return cls(format="png", data=buf.read())


@dataclass
class StataResult:
    """
    Unified result schema returned by every stata_code frontend.

    All fields are optional — a backend may choose not to populate certain
    fields depending on what Stata actually returned.
    """

    # Core outputs
    stdout: str = ""
    stderr: str = ""
    log: str = ""  # streaming log (may be same as stdout for batch)

    # Structured results (return values, e.g. `estimates table`)
    results: dict[str, Any] = field(default_factory=dict)

    # Captured graphs (can be multiple — e.g. `graph combine`)
    graphs: list[StataGraph] = field(default_factory=list)

    # Error
    error: str | None = None
    return_code: int | None = None  # Stata's `_rc`

    # Metadata
    stata_version: str | None = None
    elapsed_seconds: float | None = None

    # Warnings (e.g. "invalid observations", "converged at boundary")
    warnings: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.error is None and (self.return_code is None or self.return_code == 0)

    @property
    def has_graphs(self) -> bool:
        return len(self.graphs) > 0

    def add_warning(self, msg: str) -> None:
        """Append a warning, deduplicating if already seen."""
        if msg not in self.warnings:
            self.warnings.append(msg)

    def summary(self) -> str:
        """One-line summary suitable for agent consumption."""
        status = "OK" if self.success else f"ERR({self.return_code})"
        parts = [f"[stata_code] {status}"]
        if self.elapsed_seconds is not None:
            parts.append(f"{self.elapsed_seconds:.2f}s")
        if self.has_graphs:
            parts.append(f"{len(self.graphs)} graph(s)")
        if self.error:
            parts.append(f"error={self.error[:80]}")
        if self.warnings:
            parts.append(f"{len(self.warnings)} warning(s)")
        return " ".join(parts)
