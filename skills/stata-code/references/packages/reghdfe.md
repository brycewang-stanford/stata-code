# reghdfe

*Read this when the user runs `reghdfe`, or fits OLS with two or more high-dimensional fixed effects (firm + year, individual + time, etc.).*

Install: `ssc install reghdfe` (also requires `ssc install ftools`). Via stata-code: `install_package(name="reghdfe")` and `install_package(name="ftools")`.

`reghdfe` runs OLS absorbing arbitrarily many high-dimensional fixed effects, demeaning the data iteratively instead of dummying out the FE. Faster and lower-memory than `areg`/`xtreg` for multiple FE.

## Basic syntax

```stata
reghdfe y x1 x2, absorb(id year) vce(cluster id)
```

- `absorb()` lists the fixed effects to partial out. Each variable becomes a set of FE.
- `vce()` sets the variance estimator: `robust`, `cluster <var>`, or `unadjusted`.

## Fixed effects in absorb()

```stata
* Two-way FE
reghdfe y x, absorb(firm year)

* Interacted FE (state-by-year)
reghdfe y x, absorb(id year i.state#i.year)

* Continuous slope (group-specific trends): var##c.time style via #
reghdfe y x, absorb(id i.id#c.year)
```

`i.state#i.year` creates a separate intercept for every state×year cell. Use `i.id#c.year` for unit-specific linear time trends.

## Saving the fixed effects

```stata
reghdfe y x, absorb(fe_id = id fe_yr = year)
* or
reghdfe y x, absorb(id year, savefe)
```

Naming inside `absorb()` (`fe_id = id`) saves each FE into the named variable. `savefe` saves them as `__hdfe1__`, `__hdfe2__`, … FE are only identified up to a constant, so the saved values are normalized, not absolute group means.

## Multi-way clustering

```stata
reghdfe y x, absorb(firm year) vce(cluster firm year)
```

Two (or more) variables in `vce(cluster ...)` requests multi-way clustered standard errors (Cameron-Gelbach-Miller).

## Useful options

- `noabsorb` — absorb nothing (FE-free OLS); useful to keep the reghdfe table format.
- `summarize` — report summary statistics for the regression sample.
- `residuals(resid)` — save residuals.
- `keepsingletons` — keep singleton groups (default is to drop them; see Pitfalls).
- `nocons` already implied; the constant is reported as the sample mean.
- `tolerance()` / `maxiterations()` — control the demeaning convergence.
- `old` — fall back to the legacy algorithm if results need to match older runs.

## Reported e() results

After `reghdfe`, key returns include:

- `e(b)`, `e(V)` — coefficient vector and VCE for the **non-absorbed** regressors only.
- `e(N)` — observations; `e(df_m)`, `e(df_r)` — model / residual df.
- `e(r2)`, `e(r2_a)`, `e(r2_within)` — R², adjusted R², within-R².
- `e(F)` — model F.
- `e(N_clust)` — number of clusters; `e(N_hdfe)` — number of absorbed FE sets.
- `e(df_a)` — degrees of freedom absorbed by the FE.

Read small matrices directly from `results.e.matrices`; when a matrix has
`values: null`, fetch its `matrix://` reference with `get_matrix(ref)`.

## Comparison to areg / xtreg

- `areg y x, absorb(id)` — one FE only; reghdfe matches it but scales to many FE.
- `xtreg y x, fe` — one panel FE (the `xtset` id); reghdfe with a single `absorb()` reproduces the coefficients (SE conventions can differ slightly on df).
- For 2+ FE, `areg`/`xtreg` cannot do it directly; `reghdfe` is the standard tool.

## IV / GMM with HDFE

`reghdfe` is OLS only. For instrumental variables with absorbed FE use **`ivreghdfe`** (separate package, `ssc install ivreghdfe`, also needs `ivreg2` and `ranktest`):

```stata
ivreghdfe y x1 (x2 = z1 z2), absorb(firm year) cluster(firm)
```

## Pitfalls

- **Requires `ftools`.** Install both `reghdfe` and `ftools`, or it errors at load.
- **Singletons are dropped by default.** Groups with a single observation are removed (they contribute nothing after demeaning and bias SE/df). This changes `e(N)`; use `keepsingletons` to override, but the default is usually correct.
- **`e(b)` excludes the absorbed FE.** Only the explicit regressors appear; you cannot read FE coefficients off the table — save them via `absorb(name = var)` if needed.
- **Collinear variables are dropped silently.** Regressors collinear with the FE (e.g. a time-invariant covariate when absorbing the unit) are omitted; check that all expected coefficients are present.
- Coefficients are identical across FE specifications, but R² and df reporting differ from `areg`/`xtreg`; don't compare R² blindly across commands.
- Cluster count must be reasonable — few clusters give unreliable cluster-robust SE.
