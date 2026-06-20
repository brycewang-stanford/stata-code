# Econometrics (regression & estimation)

*Read this when the task is cross-section regression, GLM, limited dependent variables, fixed effects, IV, or comparing/exporting estimation results.*

After any estimation command, do NOT parse the printed table. The coefficient vector `e(b)` and VCE `e(V)` come back in the structured `results.e.matrices` block (large ones as a `matrix://` ref — fetch with `get_matrix`); scalars like `e(r2_a)`, `e(N)`, `e(F)` are in `results.e.scalars`. `results.last_estimation_cmd` tells you whether `e()` is populated.

## 1. `regress` basics

```stata
sysuse auto, clear
regress price mpg weight foreign
```

Key returns (read from `results.e.scalars`, not the log):

| Scalar | Meaning |
| --- | --- |
| `e(N)` | observations used |
| `e(r2)` | R-squared |
| `e(r2_a)` | adjusted R-squared |
| `e(F)` | model F statistic |
| `e(df_m)`, `e(df_r)` | model / residual df |
| `e(rmse)` | root MSE |

Matrices: `e(b)` (1×k row vector of coefficients), `e(V)` (k×k VCE). SEs are `sqrt(diag(e(V)))`. The constant is the last column `_cons` unless `, noconstant`.

```stata
regress price mpg weight, noconstant   // drop intercept
regress price mpg weight, level(90)    // 90% CIs
```

## 2. Robust & cluster standard errors

```stata
regress price mpg weight, vce(robust)            // heteroskedasticity-robust (HC1)
regress price mpg weight, vce(cluster id)         // cluster-robust on var `id`
regress price mpg weight, robust                  // legacy form == vce(robust)
```

- `vce(robust)` only changes `e(V)`; coefficients in `e(b)` are unchanged from OLS.
- `vce(cluster id)` requires a cluster id variable; `e(N_clust)` (cluster count) appears in scalars — check it. With clustering, model test becomes a Wald F, and residual df is set to `e(N_clust) - 1`.
- Bootstrap VCE: `vce(bootstrap, reps(500))`.

## 3. Factor variables in models

Use operators directly in the varlist — do not pre-generate dummies.

| Syntax | Meaning |
| --- | --- |
| `i.cat` | dummies for each level of `cat` (omits base) |
| `c.x` | treat `x` as continuous (explicit) |
| `c.x##c.x` | `x`, `x²`, expanded; `##` adds main + interaction |
| `i.cat#c.x` | interaction only (no main effects) |
| `i.cat##c.x` | main effects + interaction |
| `ib2.cat` | set level 2 as the base/omitted category |

```stata
regress price i.rep78 c.mpg##c.mpg i.foreign#c.weight
margins, dydx(mpg)              // avg marginal effect of mpg (handles the square)
margins rep78                   // predictive margins by rep78 level
margins, at(mpg=(10(5)40))      // margins across an mpg grid
marginsplot                     // plot the last margins (graph -> get_graph)
```

`i.rep78` expands to indicators named `2.rep78`, `3.rep78`, …; these are the column names in `e(b)`. `1b.rep78` marks the omitted base. To change base: `ib3.rep78`.

## 4. Postestimation

```stata
regress price mpg weight i.foreign
predict yhat                       // default = xb (linear prediction)
predict double resid, residuals    // residuals
predict stdf, stdf                 // SE of forecast
predict lev, leverage              // hat values
```

Tests and combinations (each leaves results in `r()` / `e()`):

```stata
test mpg weight                    // joint H0: both coefs = 0  (r(F), r(p))
test mpg = weight                  // equality of coefficients
testparm i.foreign                 // joint test of all i.foreign terms
lincom mpg - weight                // linear combo + SE/CI
nlcom _b[mpg]/_b[weight]           // nonlinear combo (delta method)
```

Diagnostics:

```stata
estat vif                          // variance inflation factors (after regress)
estat hettest                      // Breusch–Pagan heteroskedasticity test
estat hettest, iid                 // IM/Koenker version
estat ovtest                       // Ramsey RESET (omitted-variable / functional form)
estat imtest, white                // White's general test
```

`estat vif` writes to the log only; the test commands (`hettest`, `ovtest`) leave `r(chi2)`/`r(p)` in `results.r.scalars`.

## 5. Absorbing fixed effects

Built-in `areg` — single absorbed dimension:

```stata
areg price mpg weight, absorb(rep78) vce(cluster rep78)
```

`areg` reports an `e(df_a)` (absorbed groups) and does NOT report the FE coefficients. For one high-dimensional FE only.

Community `reghdfe` — multi-way / high-dimensional FE (preferred for panels with several FE):

```stata
ssc install reghdfe       // requires ftools:  ssc install ftools
reghdfe price mpg weight, absorb(rep78 foreign) vce(cluster rep78)
reghdfe y x1 x2, absorb(firm year) cluster(firm)
```

- Multi-way FE via multiple terms in `absorb()`; saved FE with `absorb(fe1=firm)`.
- `e(r2_within)` is in scalars; check `e(N_hdfe_extended)` / `e(df_a)` for absorbed dims.
- For Poisson with HDFE use community `ppmlhdfe` (`ssc install ppmlhdfe`).

## 6. Limited dependent variables

| Outcome | Command | Effects helper |
| --- | --- | --- |
| Binary | `logit` / `logistic` / `probit` | `margins, dydx(*)` |
| Binary, odds ratios | `logit … , or` or `logistic` | `, or` reports OR in `e(b)` exp scale |
| Ordered | `ologit` / `oprobit` | `margins, dydx(*) predict(outcome(k))` |
| Multinomial | `mlogit` | `margins, dydx(*) predict(outcome(k))` |
| Count | `poisson` / `nbreg` | `, irr` for incidence-rate ratios |

```stata
logit foreign mpg weight i.rep78
logit foreign mpg weight, or             // odds ratios
margins, dydx(*)                          // average marginal effects (probability scale)
margins, dydx(mpg) at(weight=(2000 4000))

probit foreign mpg weight
poisson count x1 x2, vce(robust)
nbreg count x1 x2                         // overdispersion; check e(alpha)/lnalpha
```

- `logistic` reports odds ratios by default; `logit` reports log-odds coefficients by default. `e(b)` holds whatever scale was requested — note the `or`/`irr` flag when reading it.
- After any of these, `margins, dydx(*)` returns marginal effects with their own `e(b)`/`e(V)` (delta-method SEs) — read those, not the table.
- `e(r2_p)` (pseudo-R²), `e(ll)`, `e(ll_0)` are in scalars; `lrtest` compares nested ML models.

## 7. Instrumental variables

Built-in `ivregress` (syntax: `depvar exog (endog = instruments)`):

```stata
ivregress 2sls price mpg (weight = length displacement)
ivregress liml  price mpg (weight = length displacement)
ivregress gmm   price mpg (weight = length displacement), vce(robust)

estat firststage      // first-stage F / partial R² (weak-instrument check)
estat endogenous      // Durbin–Wu–Hausman test of endogeneity
estat overid          // overid (GMM/2SLS, requires overidentification)
```

`estat firststage` returns the first-stage F in `r()`; rule of thumb F>10, but prefer the effective-F / weak-IV stats from community tools.

Community `ivreg2` / `ivreghdfe` (richer weak-IV and overid diagnostics, HDFE support):

```stata
ssc install ivreg2          // also: ranktest
ivreg2 price mpg (weight = length displacement), robust first
// reports Kleibergen–Paap rk Wald F, Hansen J, Cragg–Donald, Stock–Yogo critical values

ssc install ivreghdfe       // ivreg2 + reghdfe absorb()
ivreghdfe y x1 (x2 = z1 z2), absorb(firm year) cluster(firm)
```

For design framing (relevance, exclusion, LATE interpretation, just-identified vs overidentified strategy) see **causal-inference.md**.

## 8. Storing & comparing models

```stata
regress price mpg weight
estimates store m1
regress price mpg weight i.foreign
estimates store m2

estimates table m1 m2, b(%9.3f) se stats(N r2_a)   // side-by-side in the log
lrtest m1 m2                                         // nested LR test (ML models)
```

Export to publication tables (all community — `ssc install`):

```stata
ssc install estout
esttab m1 m2 using results.rtf, se r2 ar2 star(* 0.10 ** 0.05 *** 0.01)
esttab m1 m2 using results.tex, booktabs label

ssc install outreg2
regress price mpg weight
outreg2 using results.doc, replace
```

For formatting, cells, p-value stars, and LaTeX/Word/Excel targets see **tables-export.md**.

## Common pitfalls

- **Forgetting `i.`** — `regress y rep78` treats a categorical as one continuous slope. Write `regress y i.rep78`. Same trap inside interactions: use `i.cat#c.x`, not `cat#x`.
- **Too few clusters** — `vce(cluster id)` with a small number of clusters (`e(N_clust)`, say <30–50) gives unreliable SEs. Check `e(N_clust)`; consider wild-cluster bootstrap (community `boottest`).
- **Comparing models on different samples** — adding a regressor with missing values drops observations, so `e(N)` differs and `lrtest`/`estimates table` compare apples to oranges. Restrict to the common sample first (e.g. `regress … if e(sample)` from the larger model, or pre-filter missings) and verify `e(N)` matches.
- **Reading numbers from log text** — pull coefficients, SEs, and fit stats from `results.e.matrices` (`e(b)`, `e(V)`) and `results.e.scalars` (`e(r2_a)`, `e(N)`, `e(F)`). Large matrices arrive as a `matrix://` ref → `get_matrix`. The printed table rounds and can mislead; `results.last_estimation_cmd` confirms `e()` is current.
