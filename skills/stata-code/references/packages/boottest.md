# boottest

*Read this when the user has few clusters, wants wild-cluster bootstrap
inference, or asks for robust p-values after clustered regressions.*

Install: `ssc install boottest, replace`. Via stata-code:
`install_package(name="boottest")`.

`boottest` performs wild bootstrap tests after many Stata estimation commands,
including `regress`, `reghdfe`, `ivreg2`, and related models.

## Basic syntax

```stata
reghdfe y x, absorb(firm year) vce(cluster firm)
boottest x, cluster(firm) reps(9999) seed(12345)
```

For joint tests:

```stata
boottest x1 x2, cluster(firm) reps(9999) seed(12345)
```

## Read results through stata-code

- `boottest` may store p-values and test statistics in `r()`; inspect
  `results.r.scalars`.
- The printed output is useful context, but use structured scalars when present.
- Always record `reps()` and `seed()`.

## Common pitfalls

- Running `boottest` without the same cluster dimension used in estimation.
- Too few reps for final reported inference.
- Treating wild bootstrap as a fix for invalid research design rather than a
  small-cluster inference correction.
