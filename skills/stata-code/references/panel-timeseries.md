# Panel data & time series

*Read this when the task involves panel/longitudinal data (`xtset`) or time series (`tsset`): lags/differences, fixed/random effects, dynamic panels, unit roots, or autocorrelation-robust inference.*

## 1. Declare the data structure first

Nothing with time-series operators or `xt*` estimators works until you declare structure. The bridge surfaces an undeclared-data failure as a typed `must tsset` / `not sorted` error kind.

```stata
xtset panelvar timevar     // panel (long): N units × T periods
tsset timevar              // pure time series: one series over time
xtset, clear               // drop the declaration
```

- `xtset id year` → panel. Enables `xtreg`, `xtabond`, panel `L.`/`D.`, etc.
- `tsset year` → single series. Enables `arima`, `var`, `newey`, `dfuller`.
- `timevar` must be an integer (or a Stata date/`%t*` format). For irregular spacing, set the unit: `xtset id date, daily` (also `weekly`, `monthly`, `quarterly`, `yearly`, `generic`, `delta()`).
- `panelvar` must be a non-negative integer; `encode` string IDs first.

```stata
xtset firm year
xtdescribe                 // pattern of T per unit; reveals unbalanced panel & gaps
xtsum y x                  // overall / between / within variation
```

`xtdescribe` is the fastest check for whether the panel is balanced and where gaps live.

## 2. Time-series operators

| Op | Meaning | Example |
| --- | --- | --- |
| `L.` | lag (t−1) | `L.y` |
| `L2.` | lag 2 (t−2) | `L2.y` or `L(1/3).y` |
| `F.` | lead (t+1) | `F.y` |
| `D.` | difference yₜ−yₜ₋₁ | `D.y` |
| `D2.` | second difference | `D2.y` (= D of D) |
| `S.` | seasonal/span diff yₜ−yₜ₋ₖ | `S12.y` |

```stata
regress y L.y L(0/2).x D.z          // operators expand inline, no gen needed
gen lag_y = L.y                      // materialize only if you must
```

Operators require an active `tsset`/`xtset`; under `xtset` they reset at each panel boundary (a lag never bleeds across units). They also honor the actual time index, so a **gap breaks the lag**: if 2009 is missing, `L.y` in 2010 is missing, not 2008's value.

```stata
tsfill                      // insert missing rows to make spacing regular
tsfill, full                // balance every panel to the full time range
```

`tsfill` fixes gap-induced lag loss by inserting placeholder observations (covariates become missing — fine for correct lag alignment).

## 3. Panel estimators

```stata
xtset id year
xtreg y x1 x2, fe vce(cluster id)   // within (fixed effects)
xtreg y x1 x2, re                    // random effects (GLS)
xtreg y x1 x2, be                    // between (unit means)
```

- **`fe`** sweeps out each unit's mean (the within transformation: ỹᵢₜ = yᵢₜ − ȳᵢ). Identifies off within-unit variation only — time-invariant regressors drop.
- **`re`** treats unit effects as random, uncorrelated with regressors; more efficient if that holds, biased if not.
- **`be`** regresses unit means; rarely the final spec.
- Always cluster on the panel id for serial-correlation-robust SEs: `vce(cluster id)`. Estimates land in `results.e` (coefs in `e(b)`, VCE in `e(V)`, `e(N_g)` = number of groups).

### Multi-way fixed effects → `reghdfe`

`xtreg, fe` absorbs **one** dimension. For two or more high-dimensional FE (e.g. firm + year, or firm + industry×year), use **`reghdfe`** (community: `ssc install reghdfe`; also needs `ftools`).

```stata
reghdfe y x1 x2, absorb(id year) vce(cluster id)
reghdfe y x1 x2, absorb(id year industry#c.year) vce(cluster id state)
```

Same within estimator, but absorbs many FE efficiently and reports them in `e()`. Prefer it whenever you have ≥2 FE dimensions.

## 4. Choosing FE vs RE

Hausman compares FE (consistent) to RE (efficient if exogeneity holds):

```stata
xtreg y x1 x2, fe
estimates store fe
xtreg y x1 x2, re
estimates store re
hausman fe re               // H0: RE consistent (use FE if rejected)
```

Cautions: classic `hausman` requires the more-efficient estimator under H0 and **breaks under clustering/robust VCE** (can return negative χ²). Modern practice:

- Use the **Mundlak / cluster-robust artificial-regression** form instead: add group means of the regressors to an RE model and joint-test them (the `xtreg, re` + means trick, or community `xtoverid`/`rhausman`). A robust version is more reliable than `hausman` with clustered data.
- Many applied panels default to **FE** regardless, because RE's exogeneity assumption is strong.

Test for any panel effect at all (RE vs pooled OLS):

```stata
xtreg y x1 x2, re
xttest0                     // Breusch-Pagan LM; H0: no random effect (var=0)
```

## 5. `areg` vs `xtreg, fe` vs `reghdfe`

| Command | FE dims | Notes |
| --- | --- | --- |
| `xtreg, fe` | 1 (the `xtset` panel) | Canonical one-way FE; full `xt` postestimation. |
| `areg ... , absorb(id)` | 1 (any var) | Same point estimates; absorbs one FE not tied to `xtset`. DoF differ slightly. |
| `reghdfe ... , absorb(a b ...)` | many | Multi-way FE, fast; preferred for ≥2 dimensions. Community. |

All three give identical coefficients for the one-way case; they differ in degrees-of-freedom accounting and which postestimation tools apply. Use `reghdfe` once you need a second absorbed dimension.

## 6. Dynamic panels (lagged dependent variable)

When `L.y` is a regressor in a panel, FE is biased (Nickell bias, ~1/T). Use GMM estimators built for `n` large, `T` small.

```stata
xtset id year
xtabond y x, lags(1) vce(robust)            // Arellano-Bond difference GMM (built-in)
xtdpdsys y x, lags(1) vce(robust)           // Blundell-Bond system GMM (built-in)
```

Community **`xtabond2`** (`ssc install xtabond2`) is the workhorse — flexible instrument control and the Hansen/AR(2) tests:

```stata
xtabond2 y L.y x, gmm(L.y x) iv(z) twostep robust small
```

**Instrument-proliferation caution:** GMM instrument count grows ~T², easily exceeding N. Too many instruments overfit the endogenous variables and **bias the Hansen test toward 1.0** (a deceptively "good" p-value). Always:

- Report the instrument count and keep it well below N (`xtabond2` prints it).
- Collapse and/or limit lag depth: `gmm(L.y, collapse) gmm(L.y, lag(2 4))`.
- Check **AR(2)** (should fail to reject; AR(1) is expected) and **Hansen** (not too close to 1.0).

## 7. Panel-robust & autocorrelation-robust inference

- **Cluster** on the unit for arbitrary within-panel serial correlation: `vce(cluster id)`. With few clusters (<~40), SEs are unreliable — consider wild cluster bootstrap (`boottest`).
- **Two-way clustering** (e.g. firm and year): use `reghdfe ..., vce(cluster id year)`.
- **Driscoll–Kraay** (robust to cross-sectional dependence + autocorrelation, for large T): community **`xtscc`** (`ssc install xtscc`):

```stata
xtscc y x1 x2, fe lag(2)
```

- **Newey–West** HAC SEs for a single time series (`tsset` first):

```stata
tsset year
newey y x1 x2, lag(3)       // HAC-consistent SEs, lag = max autocorrelation order
```

## 8. Time-series core (single series, `tsset`)

```stata
tsset date
tsline y                          // time plot
corrgram y                        // autocorrelations + Q stats, table form
ac y                              // autocorrelation function plot (+ CI)
pac y                             // partial ACF plot — order of AR
```

### Unit roots

```stata
dfuller y, lags(2) trend          // Augmented Dickey-Fuller; H0: unit root (nonstationary)
pperron y                         // Phillips-Perron; same H0, different lag handling
```

Reject H0 → stationary. If non-stationary, model `D.y` or test for cointegration.

### ARIMA / VAR / VEC

```stata
arima y x, arima(1,1,1)           // ARIMA(p,d,q); d=1 differences once
arima y, ar(1/2) ma(1)            // explicit AR/MA lag lists
var y x, lags(1/2)                // reduced-form VAR (stationary series)
varbasic y x, lags(1/2)           // VAR + IRFs in one step
vec y x, rank(1)                  // VECM when series are cointegrated
```

Post-VAR: `varsoc` (lag selection), `irf create`/`irf graph` (impulse responses), `vargranger` (Granger causality). For VEC, run `vecrank y x` (Johansen trace test) first to pick the cointegration rank.

## 9. Panel unit roots & cointegration (brief)

```stata
xtset id year
xtunitroot llc y                  // Levin-Lin-Chu (common root)
xtunitroot ips y                  // Im-Pesaran-Shin (heterogeneous roots)
xtunitroot fisher y, dfuller lags(1)
xtunitroot hadri y                // H0: stationary (opposite null)
```

For panel cointegration see community `xtcointtest` / `xtpedroni` / `xtwest` (Westerlund). These are pointers — confirm install and exact syntax before use.

## Common pitfalls

- **Forgetting `xtset`/`tsset`.** `L.`, `D.`, and every `xt*`/`ts*` command error out (`must tsset`). Re-declare after any `preserve`/`use`/`clear` — the setting does not survive a new dataset load.
- **`not sorted`.** Operators need the data sorted by panel/time; `xtset`/`tsset` sorts for you, but a manual `sort` afterward can desync — re-`xtset`.
- **Gaps silently kill lags.** In unbalanced panels, `L.y` across a missing period returns missing, quietly shrinking the estimation sample. Check `xtdescribe`; use `tsfill` when regular spacing is required.
- **Time-invariant regressor under `fe`** is dropped (collinear with the unit FE) — don't expect a coefficient.
- **Hausman with robust/clustered VCE** is invalid (negative χ²). Use a Mundlak/robust variant instead.
- **Nickell bias:** never trust `xtreg, fe` with a lagged dependent variable for small T — switch to `xtabond2`/`xtdpdsys`.
- **Too many GMM instruments** overfit and inflate the Hansen p-value toward 1.0; collapse/limit lags and report the count.
- **Wrong clustering dimension.** Cluster at the level of treatment/correlation (often the panel id, sometimes a higher level like state); the SE, not the point estimate, is what changes — and getting it wrong is silent.
