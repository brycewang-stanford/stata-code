# csdid

*Read this when the user requests Callaway and Sant'Anna staggered DiD in
Stata, group-time ATT, or event-study aggregation robust to heterogeneous
treatment effects.*

Install: `ssc install csdid, replace` and `ssc install drdid, replace`. Via
stata-code: `install_package(name="csdid")` and `install_package(name="drdid")`.

`csdid` estimates group-time ATT(g,t). It is the main Stata workhorse for
Callaway and Sant'Anna style staggered-adoption DiD.

## Required data shape

- Panel or repeated cross-section with an outcome, time variable, and treatment
  cohort.
- `gvar()` is the first treatment period for each unit. It is not a 0/1 treated
  dummy.
- Never-treated units usually use `0` or missing in `gvar()`, depending on the
  package version and data convention. Check the package help for the installed
  version.
- Unit IDs should be compact and stable. If original IDs are large strings or
  64-bit numbers, prefer `egen unit_id = group(original_id)`.

## Basic syntax

```stata
csdid y x1 x2, ivar(unit_id) time(year) gvar(first_treat) method(dripw)
estat simple
estat event
estat calendar
csdid_plot
```

Use `notyet` when the design should use not-yet-treated units as controls and
there are no clean never-treated controls.

## Read results through stata-code

- `results.e.matrices.b` and `results.e.matrices.V` hold coefficient and VCE
  payloads when the command stores them in `e()`.
- `estat simple`, `estat event`, and `estat calendar` may overwrite or extend
  the stored results; run and capture the one you need before summarizing.
- Graphs from `csdid_plot` come back as `graph://` refs.
- Use `log.error_window` and `warnings` for dropped groups, unsupported options,
  or convergence/refusal messages.

## Minimal parity-audit record

Record:

- `method()` option;
- whether controls are never-treated or not-yet-treated;
- sample `N`, unit count, time count, cohort count;
- covariates and missing-value rule;
- all `estat` aggregation commands run after `csdid`;
- package version if available from `which csdid`.

## Common pitfalls

- Passing a 0/1 treatment dummy to `gvar()` instead of first-treatment cohort.
- Comparing `estat simple` to an event-time coefficient from another package.
- Letting Stata, R, and Python silently choose different samples.
- Ignoring warnings about groups with no valid controls.
