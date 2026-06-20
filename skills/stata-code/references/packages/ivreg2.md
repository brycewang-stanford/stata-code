# ivreg2

*Read this when the user needs IV/2SLS with weak-instrument diagnostics,
overidentification tests, robust/clustered VCE, or first-stage output beyond
built-in `ivregress`.*

Install: `ssc install ivreg2, replace`; it may require `ranktest`. Via
stata-code: `install_package(name="ivreg2")` and, if needed,
`install_package(name="ranktest")`.

`ivreg2` is a community IV workhorse. Prefer built-in `ivregress` for simple
cases, but use `ivreg2` when diagnostics matter.

## Basic syntax

```stata
ivreg2 y x1 x2 (d = z1 z2), robust first
ivreg2 y x1 x2 (d = z1 z2), cluster(cluster_id) first
```

Syntax: outcome, exogenous controls, endogenous variables in parentheses, and
excluded instruments after `=`.

## Diagnostics to report

- First-stage output (`first`).
- Kleibergen-Paap rk Wald F under robust/clustered errors when available.
- Hansen J / Sargan overidentification test when overidentified.
- Endogeneity tests when requested.
- Weak-IV-robust inference if first-stage strength is marginal.

## Read results through stata-code

- Coefficients and VCE are in `results.e.matrices.b` and `results.e.matrices.V`.
- Scalars such as weak-IV and overid statistics are version-dependent; inspect
  `results.e.scalars` first, then use `search_log` for labels.
- Record `e(cmd)`/`e(vcetype)` and sample `e(N)`.

## Common pitfalls

- Reporting the old first-stage F>10 rule as sufficient under clustering.
- Treating overidentification tests as proof of instrument validity.
- Comparing IV and OLS without noting LATE/complier interpretation.
- Putting endogenous controls outside the parentheses.
