"""Reproducibility / provenance helpers.

Empirical work is only credible if it can be re-run. These helpers turn a
:class:`RunResult` into (a) a typed :class:`Provenance` envelope - the runtime
identity that produced it — and (b) a self-contained, re-runnable ``.do`` script
header that pins the Stata ``version`` and (optionally) the RNG seed.

Both are pure functions over an already-produced ``RunResult`` plus the original
code; nothing here touches Stata, so the module is fully unit-testable.
"""

from __future__ import annotations

import re

from stata_code.core.schema import PackageInstall, Provenance, RunResult

_VERSION_MAJOR_RE = re.compile(r"^\s*(\d+)")

# Parse `ssc install <pkg>` / `net install <pkg>, from(<url>)` (install may be
# abbreviated to `inst`). Captures the package name at the start of a command;
# trailing comma/options are stripped by the caller.
_SSC_INSTALL_RE = re.compile(r"^\s*ssc\s+inst(?:all)?\s+(\S+)", re.IGNORECASE)
_NET_INSTALL_RE = re.compile(r"^\s*net\s+inst(?:all)?\s+(\S+)", re.IGNORECASE)
_FROM_RE = re.compile(r"from\(\s*([^)]+?)\s*\)", re.IGNORECASE)


def extract_package_installs(code: str) -> list[PackageInstall]:
    """Parse community-package installs (`ssc`/`net install`) from a script.

    Returns one :class:`PackageInstall` per install command, de-duplicated by
    (name, source), in first-seen order. Best-effort line scanning — it records
    what the script *declares* it installs, which is the reproducibility-
    relevant fact, without needing to run anything.
    """
    seen: set[tuple[str, str]] = set()
    out: list[PackageInstall] = []
    for raw in code.splitlines():
        line = raw.strip()
        source = ""
        m = _SSC_INSTALL_RE.match(line)
        if m:
            source = "ssc"
        else:
            m = _NET_INSTALL_RE.match(line)
            if m:
                source = "net"
        if not m:
            continue
        name = m.group(1).rstrip(",").strip()
        if not name or name.startswith(","):
            continue
        url = None
        if source == "net":
            fm = _FROM_RE.search(line)
            if fm:
                url = fm.group(1).strip()
        key = (name, source)
        if key in seen:
            continue
        seen.add(key)
        out.append(PackageInstall(name=name, source=source, url=url))  # type: ignore[arg-type]
    return out


def _stata_code_version() -> str | None:
    """Best-effort package version without a hard import cycle.

    Prefer the source ``__version__`` literal (always reflects the running
    code) over ``importlib.metadata`` (which lags in editable installs).
    """
    try:
        import stata_code

        v = getattr(stata_code, "__version__", None)
        if v:
            return str(v)
    except Exception:  # noqa: BLE001
        pass
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("stata-code")
        except PackageNotFoundError:
            return None
    except Exception:  # noqa: BLE001
        return None


def _version_floor(stata_version: str | None) -> str | None:
    """Map ``"18.0"`` to ``"18"`` for a Stata ``version`` control line."""
    if not stata_version:
        return None
    m = _VERSION_MAJOR_RE.match(stata_version)
    return m.group(1) if m else None


def build_provenance(
    result: RunResult, *, seed: int | None = None, code: str | None = None
) -> Provenance:
    """Derive a :class:`Provenance` envelope from a finished ``RunResult``.

    ``seed`` is accepted explicitly because the RNG seed is not part of the
    result envelope; pass it when the caller set one (``set seed <n>``).
    ``code``, when given, is scanned for ``ssc``/``net install`` lines so the
    envelope records which community packages the script depends on. The
    estimation command, when present, is read from the typed estimation
    contract or ``e(cmd)``.
    """
    command: str | None = None
    if result.results.estimation is not None:
        command = result.results.estimation.command
    if command is None:
        command = result.results.e.macros.get("cmd") or result.results.last_estimation_cmd

    return Provenance(
        stata_version=result.stata.version,
        stata_edition=result.stata.edition,
        stata_code_version=_stata_code_version(),
        schema_version=result.schema_version,
        backend=result.stata.backend,
        generated_at=result.started_at,
        command=command,
        seed=seed,
        packages=extract_package_installs(code) if code else [],
    )


def build_reproducible_do(
    result: RunResult,
    code: str,
    *,
    seed: int | None = None,
    clear_all: bool = False,
    set_more_off: bool = True,
) -> str:
    """Compose a self-contained, re-runnable ``.do`` script for ``code``.

    The header pins Stata's ``version`` (so the code runs under the same
    language semantics it was authored against) and, when ``seed`` is given,
    re-sets the RNG. ``code`` is emitted verbatim — the caller owns its
    correctness; this only wraps it with a reproducibility preamble.

    ``clear_all`` (default off) prepends ``clear all`` for a cold-start script;
    leave it off when ``code`` manages its own dataset state.
    """
    prov = build_provenance(result, seed=seed)
    lines: list[str] = []

    tag = prov.stata_code_version or "stata-code"
    lines.append(f"*! Reproducible Stata script - generated by stata-code {tag}")
    if prov.generated_at:
        lines.append(f"*! Generated: {prov.generated_at}")
    edition = (
        prov.stata_edition.value
        if prov.stata_edition is not None and prov.stata_edition.value != "unknown"
        else ""
    )
    ver_desc = " ".join(p for p in [prov.stata_version, edition] if p) or "unknown"
    lines.append(f"*! Stata: {ver_desc} / result schema {prov.schema_version}")
    lines.append("")

    version_floor = _version_floor(prov.stata_version)
    if version_floor:
        lines.append(f"version {version_floor}")
    if clear_all:
        lines.append("clear all")
    if set_more_off:
        lines.append("set more off")
    if seed is not None:
        lines.append(f"set seed {seed}")
    if version_floor or clear_all or set_more_off or seed is not None:
        lines.append("")

    lines.append(code.rstrip("\n"))
    lines.append("")  # trailing newline
    return "\n".join(lines)


def _render_readme(prov: Provenance, do_filename: str, title: str | None) -> str:
    edition = prov.stata_edition.value if prov.stata_edition is not None else "unknown"
    ver = " ".join(
        p for p in [prov.stata_version, edition if edition != "unknown" else ""] if p
    )
    lines: list[str] = [f"# {title or 'Replication package'}", ""]
    if prov.generated_at:
        lines.append(f"_Generated {prov.generated_at} by stata-code "
                     f"{prov.stata_code_version or ''}_.".replace("  ", " "))
        lines.append("")
    lines.append("## How to reproduce")
    lines.append("")
    lines.append("1. Place the required data file(s) in this directory.")
    if prov.packages:
        lines.append("2. Install the community packages listed below.")
        lines.append(f"3. In Stata, run: `do {do_filename}`")
    else:
        lines.append(f"2. In Stata, run: `do {do_filename}`")
    lines.append("")
    lines.append("## Runtime")
    lines.append("")
    lines.append(f"- Stata: {ver or 'unknown'}")
    lines.append(f"- stata-code: {prov.stata_code_version or 'unknown'}")
    lines.append(f"- Result schema: {prov.schema_version}")
    if prov.seed is not None:
        lines.append(f"- RNG seed: {prov.seed}")
    if prov.command:
        lines.append(f"- Primary estimation command: `{prov.command}`")
    lines.append("")
    if prov.packages:
        lines.append("## Required community packages")
        lines.append("")
        for pkg in prov.packages:
            if pkg.source == "net" and pkg.url:
                lines.append(f"- `{pkg.name}` — `net install {pkg.name}, from({pkg.url})`")
            else:
                lines.append(f"- `{pkg.name}` — `ssc install {pkg.name}`")
        lines.append("")
    return "\n".join(lines)


def build_submission_package(
    result: RunResult,
    code: str,
    *,
    seed: int | None = None,
    title: str | None = None,
    do_filename: str = "analysis.do",
) -> dict[str, str]:
    """Assemble a self-contained replication / journal-submission bundle.

    Returns a mapping of relative filename → text content that a caller can
    write to a submission directory (or zip):

    * ``<do_filename>`` — the re-runnable, version-pinned ``.do`` (see
      :func:`build_reproducible_do`);
    * ``PROVENANCE.json`` — the typed :class:`Provenance` envelope;
    * ``README.md`` — a human-readable manifest (runtime, seed, required
      community packages, how to reproduce).

    Pure: it produces content only and never touches the filesystem, so the
    caller controls where (and whether) it lands on disk.
    """
    prov = build_provenance(result, seed=seed, code=code)
    do = build_reproducible_do(result, code, seed=seed)
    return {
        do_filename: do,
        "PROVENANCE.json": prov.model_dump_json(indent=2),
        "README.md": _render_readme(prov, do_filename, title),
    }
