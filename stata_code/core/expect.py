"""Caller-declared expectations for a run (``stata_run(expect=...)``).

A Stata run that exits with rc 0 is not the same as a run that produced the
intended result. An `if` added by mistake, a different cluster level, or a
merge that drops rows all leave Stata perfectly happy — the sample size and the
coefficients move, and nothing in ``ok`` / ``rc`` says so. ``expect`` lets the
caller state what the run must produce, and turns a mismatch into a typed
failure (``error.kind="expectation_failed"``, synthetic ``rc=-6``) instead of a
silent success.

Supported expectations::

    {
      "n_obs": 74,                                   # e(N) of this run's estimation
      "coefficients": {
        "mpg":    {"b": -49.51, "tol": 0.01},        # |b - expected| <= tol
        "weight": {"sign": "positive"}               # sign only
      },
      "outputs": ["out/table1.csv", "out/m1.ster"],  # written by this run
      "no_suppressed_errors": true                    # no error hidden by capture
    }

Pure Python, unit-testable without Stata: the runner hands in what it already
collected (the estimation, the suppressed-error warnings, the run's start time).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from stata_code.core.errors import label_for_rc, recovery_for
from stata_code.core.schema import (
    CheckResult,
    ErrorContext,
    ErrorInfo,
    ErrorKind,
    EstimationResult,
    StataWarning,
    Suggestion,
)

EXPECTATION_RC = -6

# Absolute tolerance used when a coefficient expectation gives `b` but no `tol`.
DEFAULT_COEF_TOL = 1e-6

# File mtimes can trail the wall clock read before the run by up to a second on
# filesystems with coarse timestamps (HFS+, FAT, some network mounts).
_MTIME_SLACK_S = 1.0

_KNOWN_KEYS = frozenset({"n_obs", "coefficients", "outputs", "no_suppressed_errors"})
_SIGNS = frozenset({"positive", "negative"})


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def normalize_expect(raw: Any) -> dict[str, Any] | None:
    """Validate an ``expect`` object and return its canonical form.

    Returns ``None`` when there is nothing to check. Raises ``ValueError`` with
    a message naming the offending key, so every frontend reports a malformed
    expectation as an invalid request before Stata runs.
    """
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError(f"expect must be an object; got {type(raw).__name__}")
    unknown = sorted(set(raw) - _KNOWN_KEYS)
    if unknown:
        raise ValueError(
            f"expect has unknown key(s) {unknown}; allowed: {sorted(_KNOWN_KEYS)}"
        )
    out: dict[str, Any] = {}

    if "n_obs" in raw and raw["n_obs"] is not None:
        n_obs = raw["n_obs"]
        if not isinstance(n_obs, int) or isinstance(n_obs, bool) or n_obs < 0:
            raise ValueError(f"expect.n_obs must be a non-negative integer; got {n_obs!r}")
        out["n_obs"] = n_obs

    if "coefficients" in raw and raw["coefficients"] is not None:
        coefs = raw["coefficients"]
        if not isinstance(coefs, Mapping) or not coefs:
            raise ValueError("expect.coefficients must be a non-empty object of term -> spec")
        norm_coefs: dict[str, dict[str, Any]] = {}
        for term, spec in coefs.items():
            if not isinstance(term, str) or not term:
                raise ValueError("expect.coefficients keys must be non-empty term names")
            if not isinstance(spec, Mapping):
                raise ValueError(
                    f"expect.coefficients[{term!r}] must be an object like "
                    '{"b": -49.5, "tol": 0.01} or {"sign": "negative"}'
                )
            extra = sorted(set(spec) - {"b", "tol", "sign"})
            if extra:
                raise ValueError(f"expect.coefficients[{term!r}] has unknown key(s) {extra}")
            has_b = spec.get("b") is not None
            has_sign = spec.get("sign") is not None
            if has_b == has_sign:
                raise ValueError(
                    f"expect.coefficients[{term!r}] needs exactly one of 'b' or 'sign'"
                )
            entry: dict[str, Any] = {}
            if has_b:
                b = spec["b"]
                if not _is_number(b) or not math.isfinite(b):
                    raise ValueError(f"expect.coefficients[{term!r}].b must be a finite number")
                tol = spec.get("tol", DEFAULT_COEF_TOL)
                if tol is None:
                    tol = DEFAULT_COEF_TOL
                if not _is_number(tol) or not math.isfinite(tol) or tol < 0:
                    raise ValueError(
                        f"expect.coefficients[{term!r}].tol must be a non-negative number"
                    )
                entry = {"b": float(b), "tol": float(tol)}
            else:
                if "tol" in spec:
                    raise ValueError(
                        f"expect.coefficients[{term!r}].tol only applies together with 'b'"
                    )
                sign = spec["sign"]
                if sign not in _SIGNS:
                    raise ValueError(
                        f"expect.coefficients[{term!r}].sign must be 'positive' or 'negative'"
                    )
                entry = {"sign": sign}
            norm_coefs[term] = entry
        out["coefficients"] = norm_coefs

    if "outputs" in raw and raw["outputs"] is not None:
        outputs = raw["outputs"]
        if isinstance(outputs, str) or not isinstance(outputs, Iterable):
            raise ValueError("expect.outputs must be a list of file paths")
        paths = list(outputs)
        if not paths or not all(isinstance(p, str) and p.strip() for p in paths):
            raise ValueError("expect.outputs must be a non-empty list of file paths")
        out["outputs"] = paths

    if "no_suppressed_errors" in raw and raw["no_suppressed_errors"] is not None:
        flag = raw["no_suppressed_errors"]
        if not isinstance(flag, bool):
            raise ValueError("expect.no_suppressed_errors must be a boolean")
        if flag:
            out["no_suppressed_errors"] = True

    return out or None


def needs_estimation(expect: Mapping[str, Any] | None) -> bool:
    """True when evaluating ``expect`` reads the run's estimation."""
    return expect is not None and ("n_obs" in expect or "coefficients" in expect)


def evaluate_expectations(
    expect: Mapping[str, Any],
    *,
    estimation: EstimationResult | None,
    estimated_here: bool,
    warnings: Iterable[StataWarning],
    started_wall: float,
    working_dir: Path | None,
) -> list[CheckResult]:
    """Evaluate a normalized ``expect`` against what the run produced.

    ``estimation`` must be the *untrimmed* contract (every coefficient row),
    independent of the caller's ``include_estimation`` payload budget.
    ``estimated_here`` guards against grading an estimation inherited from an
    earlier call: e() outlives the run that set it.
    """
    checks: list[CheckResult] = []
    no_estimation_msg = (
        "this run produced no estimation"
        + ("; e() still holds one from an earlier run" if estimation is not None else "")
    )

    if "n_obs" in expect:
        want = expect["n_obs"]
        if not estimated_here or estimation is None:
            checks.append(
                CheckResult(check="n_obs", passed=False, expected=want, message=no_estimation_msg)
            )
        else:
            got = estimation.n_obs
            passed = got == want
            checks.append(
                CheckResult(
                    check="n_obs",
                    passed=passed,
                    expected=want,
                    actual=got,
                    message=(
                        f"e(N) = {got}"
                        if passed
                        else f"e(N) = {got}, expected {want} — the estimation sample changed"
                    ),
                )
            )

    for term, spec in (expect.get("coefficients") or {}).items():
        expected = spec.get("b", spec.get("sign"))
        if not estimated_here or estimation is None:
            checks.append(
                CheckResult(
                    check="coefficient",
                    target=term,
                    passed=False,
                    expected=expected,
                    message=no_estimation_msg,
                )
            )
            continue
        row = next((c for c in estimation.coefficients if c.term == term), None)
        if row is None or row.b is None:
            available = [c.term for c in estimation.coefficients][:12]
            checks.append(
                CheckResult(
                    check="coefficient",
                    target=term,
                    passed=False,
                    expected=expected,
                    message=(
                        f"term {term!r} is not in the {estimation.command or 'last'} "
                        f"coefficient table (terms: {', '.join(available)})"
                        if row is None
                        else f"term {term!r} has no estimate (omitted or base level)"
                    ),
                )
            )
            continue
        b = row.b
        if "b" in spec:
            diff = abs(b - spec["b"])
            passed = diff <= spec["tol"]
            checks.append(
                CheckResult(
                    check="coefficient",
                    target=term,
                    passed=passed,
                    expected=spec["b"],
                    actual=b,
                    message=(
                        f"b = {b:.6g} (|diff| = {diff:.3g} <= tol {spec['tol']:g})"
                        if passed
                        else f"b = {b:.6g}, expected {spec['b']:.6g} ± {spec['tol']:g}"
                    ),
                )
            )
        else:
            sign = "positive" if b > 0 else "negative" if b < 0 else "zero"
            passed = sign == spec["sign"]
            checks.append(
                CheckResult(
                    check="coefficient",
                    target=term,
                    passed=passed,
                    expected=spec["sign"],
                    actual=b,
                    message=(
                        f"b = {b:.6g} is {sign}"
                        if passed
                        else f"b = {b:.6g} is {sign}, expected {spec['sign']}"
                    ),
                )
            )

    for raw_path in expect.get("outputs") or []:
        path = Path(raw_path).expanduser()
        if not path.is_absolute() and working_dir is not None:
            path = working_dir / path
        try:
            mtime: float | None = path.stat().st_mtime
        except OSError:
            mtime = None
        if mtime is None:
            passed, message = False, f"{path} does not exist"
        elif mtime < started_wall - _MTIME_SLACK_S:
            passed, message = False, f"{path} exists but was not written by this run (stale)"
        else:
            passed, message = True, f"{path} written by this run"
        checks.append(
            CheckResult(check="output", target=str(path), passed=passed, message=message)
        )

    if expect.get("no_suppressed_errors"):
        hidden = [w.message for w in warnings if w.kind == "suppressed_error"]
        checks.append(
            CheckResult(
                check="no_suppressed_errors",
                passed=not hidden,
                expected=0,
                actual=len(hidden),
                message=(
                    "no error was hidden by capture"
                    if not hidden
                    else f"{len(hidden)} error(s) hidden by capture; first: {hidden[0]}"
                ),
            )
        )

    return checks


def expectation_error(checks: list[CheckResult]) -> ErrorInfo | None:
    """Build the ``expectation_failed`` error for failed checks, or ``None``."""
    failed = [c for c in checks if not c.passed]
    if not failed:
        return None

    def _label(c: CheckResult) -> str:
        return f"{c.check}[{c.target}]" if c.target else c.check

    summary = "; ".join(f"{_label(c)}: {c.message}" for c in failed[:4])
    if len(failed) > 4:
        summary += f"; … and {len(failed) - 4} more"
    return ErrorInfo(
        kind=ErrorKind.EXPECTATION_FAILED,
        rc=EXPECTATION_RC,
        rc_label=label_for_rc(EXPECTATION_RC),
        message=(
            f"Stata finished without error, but {len(failed)} of {len(checks)} "
            f"expectation(s) failed: {summary}"
        ),
        context=ErrorContext(),
        suggestions=[
            Suggestion(
                action=(
                    "Compare the specification against the intended one (sample "
                    "restrictions, weights, fixed effects, clustering, merges). If the "
                    "code is right and the expectation is stale, update `expect`; do "
                    "not loosen it just to pass."
                ),
                command=None,
            )
        ],
        recovery=recovery_for(ErrorKind.EXPECTATION_FAILED),
    )
