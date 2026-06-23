"""Data-MCP -> Stata handoff verification.

When an external data MCP (FRED, World Bank, Census, ...) hands a dataset to
Stata, the handoff is only trustworthy if the imported data matches the source
metadata: the row count the provider reported, the columns the analysis needs,
the shape it expected. ``verify_dataset`` turns that check into a typed verdict
an agent can branch on, instead of eyeballing a ``describe``.

This is the executable companion to the skill's ``data-mcp-handoff`` protocol:
the protocol says *what* to check; this enforces it on the captured
:class:`DatasetInfo`. Pure Python, unit-testable without Stata.
"""

from __future__ import annotations

from pydantic import Field

from stata_code.core.schema import DatasetInfo, _Base


class DatasetCheck(_Base):
    """Verdict of a dataset handoff check.

    ``ok`` is ``True`` only when every requested check passed. ``checks`` lists
    the checks that were actually evaluated (so an agent can tell "passed" from
    "not checked"); ``issues`` holds one human-readable line per failure.
    """

    ok: bool = True
    checks: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


def verify_dataset(
    dataset: DatasetInfo,
    *,
    n_obs: int | None = None,
    min_obs: int | None = None,
    max_obs: int | None = None,
    n_vars: int | None = None,
    required_vars: list[str] | None = None,
) -> DatasetCheck:
    """Verify a loaded dataset against expected handoff metadata.

    Every argument is optional; only the constraints you pass are evaluated.
    Returns a :class:`DatasetCheck` with ``ok=False`` when any constraint fails.

    * ``n_obs`` — exact observation count the source reported.
    * ``min_obs`` / ``max_obs`` - inclusive bounds on the observation count.
    * ``n_vars`` - exact variable count.
    * ``required_vars`` - variable names that must be present. Requires the
      dataset's variable list (run with ``include_dataset_variables=True``);
      if it is unavailable the check is recorded as an issue rather than
      silently skipped.
    """
    checks: list[str] = []
    issues: list[str] = []

    if n_obs is not None:
        checks.append("n_obs")
        if dataset.n_obs != n_obs:
            issues.append(f"expected {n_obs} observations, found {dataset.n_obs}")

    if min_obs is not None:
        checks.append("min_obs")
        if dataset.n_obs < min_obs:
            issues.append(
                f"expected at least {min_obs} observations, found {dataset.n_obs}"
            )

    if max_obs is not None:
        checks.append("max_obs")
        if dataset.n_obs > max_obs:
            issues.append(
                f"expected at most {max_obs} observations, found {dataset.n_obs}"
            )

    if n_vars is not None:
        checks.append("n_vars")
        if dataset.n_vars != n_vars:
            issues.append(f"expected {n_vars} variables, found {dataset.n_vars}")

    if required_vars:
        checks.append("required_vars")
        if dataset.variables is None:
            issues.append(
                "variable list unavailable; re-run with "
                "include_dataset_variables=True to check required_vars"
            )
        else:
            present = {v.name for v in dataset.variables}
            missing = [name for name in required_vars if name not in present]
            if missing:
                issues.append("missing required variables: " + ", ".join(missing))

    return DatasetCheck(ok=not issues, checks=checks, issues=issues)
