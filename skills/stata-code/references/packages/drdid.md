# drdid

*Read this when the user runs doubly robust two-period DiD, or when `csdid`
depends on `drdid`.*

Install: `ssc install drdid, replace`. Via stata-code:
`install_package(name="drdid")`.

`drdid` estimates two-period/two-group DiD effects with doubly robust,
IPW, or regression-adjustment variants. It is also a dependency for `csdid`.

## Basic syntax

```stata
drdid y x1 x2, ivar(unit_id) time(post) tr(treat) dripw
```

Option names vary across versions; check `help drdid` in the installed Stata
environment if a command fails with `syntax` or `option_not_allowed`.

## When to use

- Clean 2x2 DiD or a single group-time comparison.
- Debugging the lower-level estimator behind `csdid`.
- Cross-stack parity checks where the target is one ATT for one treated cohort
  and one post period.

## Read results through stata-code

- Branch on `ok`; do not infer success from printed tables.
- Read ATT and standard-error matrices/scalars from `results.e` or
  `results.r`, depending on the installed version and postestimation command.
- If output is only in the log, use `search_log(ref, pattern="ATT")` before
  fetching a full log.

## Common pitfalls

- Using `drdid` for a staggered design without explicitly looping over cohorts
  and time periods. Use `csdid` for that.
- Mixing `ivar()`/`time()` panel syntax with repeated cross-section syntax.
- Comparing `dripw`, `ipw`, and `reg` results as if they were the same
  estimator.
