# Causal inference designs

*Read this when the task is a causal design: DiD, event study, IV for identification, RDD, matching/weighting, or synthetic control.*

Most modern estimators here are **community-contributed**: install once with `ssc install <pkg>` (or `net install`). In the stata-code workflow, run `install_package` first if a command throws `command_not_found` (rc 199). All estimates land in `results.e` (read via `e()` scalars/matrices). Modern DiD/event-study/RDD commands usually draw a graph that returns as a `graph://` ref — fetch it with `get_graph`.

## 1. Difference-in-differences — TWFE baseline

Canonical 2×2 and two-way fixed-effects setup. `reghdfe` is community (`ssc install reghdfe`, also needs `ftools`); `xtreg`/`areg` are built in.

```stata
* Built-in TWFE (single high-dim FE absorbed)
xtset unit time
xtreg y i.treat##i.post i.time, fe vce(cluster unit)

* reghdfe: absorb many FE efficiently (community)
reghdfe y i.treat##i.post, absorb(unit time) vce(cluster unit)
* The i.treat#i.post interaction is the DiD (ATT) for a clean 2-period design.
```

For a **single binary treatment switching on once** with common timing, the interaction estimate is unbiased. For **staggered adoption** (units treated at different times), the single `treatGroup` (binary post-by-ever-treated) TWFE coefficient is a variance-weighted average of all 2×2 comparisons — including "already-treated as control" comparisons that can flip sign (Goodman-Bacon 2021). Diagnose the weights with `bacondecomp` (community, `ssc install bacondecomp`):

```stata
bacondecomp y treatdummy, ddetail   // shows good vs. forbidden 2x2 weights
```

If staggered, do **not** trust the single TWFE coefficient — use a modern estimator (§2).

## 2. Modern staggered DiD (all community — install first)

| Estimator | Command | Package | Install |
| --- | --- | --- | --- |
| Callaway & Sant'Anna (2021) | `csdid` | [`csdid`](packages/csdid.md) | `ssc install csdid`; also `drdid` |
| Borusyak, Jaravel & Spiess imputation | `did_imputation` | [`did_imputation`](packages/did_imputation.md) | `ssc install did_imputation` |
| de Chaisemartin & D'Haultfœuille | `did_multiplegt` / `did_multiplegt_dyn` | [`did_multiplegt_dyn`](packages/did_multiplegt_dyn.md) | `ssc install did_multiplegt_dyn` |
| Sun & Abraham (2021) | `eventstudyinteract` | [`eventstudyinteract`](packages/eventstudyinteract.md) | `ssc install eventstudyinteract` (needs `avar`) |
| Doubly-robust 2×2 ATT | `drdid` | [`drdid`](packages/drdid.md) | `ssc install drdid` |

### Callaway & Sant'Anna — `csdid`

Group-time ATT(g,t); aggregate with `estat`; plot with `csdid_plot`.

```stata
ssc install csdid
ssc install drdid          // csdid dependency
csdid y x1 x2, ivar(unit) time(time) gvar(first_treat) method(dripw)
* first_treat = period unit is first treated (0 or . for never-treated controls)
estat simple                 // overall ATT
estat event                  // dynamic (event-time) effects
estat calendar               // by calendar period
csdid_plot                   // event-study figure -> graph:// ref
```

`gvar` must be the **cohort/first-treatment period** (not a 0/1 dummy). Use `notyet` for not-yet-treated controls when there is no never-treated group.

### Borusyak et al. imputation — `did_imputation`

Efficient under parallel trends + no anticipation; imputes untreated potential outcomes from FE model.

```stata
ssc install did_imputation
did_imputation y unit time first_treat, allhorizons pretrends(5)
* horizon estimates in e(b); pretrends() adds placebo leads for a pre-trend test
```

### de Chaisemartin & D'Haultfœuille — `did_multiplegt_dyn`

Handles treatment that turns on/off and non-binary treatment.

```stata
ssc install did_multiplegt_dyn
did_multiplegt_dyn y unit time treat, effects(5) placebo(3) graph_off
* effects() = post periods, placebo() = pre-period placebos; omit graph_off for the figure
```

### Sun & Abraham — `eventstudyinteract`

Interaction-weighted event study robust to heterogeneous effects; you build relative-time dummies yourself (see §3) and pass cohort + never-treated indicators.

```stata
ssc install eventstudyinteract
eventstudyinteract y rel_m3 rel_m2 rel_0 rel_p1 rel_p2, ///
    cohort(first_treat) control_cohort(never_treated)   ///
    absorb(i.unit i.time) vce(cluster unit)
```

## 3. Event study (leads & lags)

Build relative-time dummies around treatment, **omit one base period** (conventionally t = −1) so all effects are relative to it. Forgetting to omit a base period drops the regression into collinearity or silently picks an arbitrary reference.

```stata
* event_time = time - first_treat (missing/large for never-treated)
gen event_time = time - first_treat
* Bin endpoints to avoid sparse extreme leads/lags
replace event_time = -4 if event_time < -4
replace event_time =  4 if event_time >  4
* Shift so factor levels are non-negative; base period = -1
gen et = event_time + 4          // -1 -> level 3
reghdfe y ib3.et, absorb(unit time) vce(cluster unit)   // ib3 omits t=-1
```

Plot coefficients with `coefplot` (community — see [packages/coefplot.md](packages/coefplot.md)) or `marginsplot` (built-in, after `margins`).

```stata
coefplot, keep(*.et) vertical yline(0) xline(<base>) ///
    xtitle("Event time") ytitle("ATT")
```

Pre-period coefficients flat near zero = visual parallel-trends evidence (not a formal test). `csdid_plot`, `did_imputation`, and `did_multiplegt_dyn` produce event-study plots directly as `graph://` refs.

## 4. Instrumental variables (causal framing)

IV identifies the **LATE** — the effect for compliers (units shifted by the instrument), not the ATE — under relevance, exclusion, monotonicity, and independence. `ivregress` is built in; `ivreg2`/`ivreghdfe` are community. See [econometrics.md](econometrics.md) for estimator mechanics.

```stata
* Built-in 2SLS: d is endogenous, z the instrument, x exogenous controls
ivregress 2sls y x (d = z), vce(robust)
estat firststage              // first-stage F, weak-IV diagnostics
estat endogenous              // Durbin-Wu-Hausman test of endogeneity
estat overid                  // overid test (only if instruments > endog)
```

**Weak instruments:** a first-stage F well above the old "10" rule is no longer enough. With one endogenous regressor, compare the **effective F** (Olea-Pflueger) to Stock-Yogo / Montiel Olea critical values; `weakivtest` (community, `ssc install weakivtest`) reports it. `ivreg2`/`ivreghdfe` (community, `ssc install ivreg2`, `ssc install ivreghdfe`) add high-dim FE and the Kleibergen-Paap rk statistic for clustered/robust cases:

```stata
ssc install ivreg2
ssc install ivreghdfe
ivreghdfe y x (d = z), absorb(unit time) cluster(unit)
* report e(widstat) = Kleibergen-Paap F under non-iid errors
```

Weak instruments → biased toward OLS and badly-sized t-tests; report Anderson-Rubin weak-IV-robust CIs (`weakiv`, community) when F is marginal. For package-level syntax notes, see [`ivreg2`](packages/ivreg2.md), [`ivreghdfe`](packages/ivreghdfe.md), and [`boottest`](packages/boottest.md).

## 5. Regression discontinuity

All from the [`rdrobust`](packages/rdrobust.md) suite (community: `ssc install rdrobust`, `ssc install rddensity`; `lpdensity` may be pulled in). Local-polynomial estimation with bias-corrected robust CIs and data-driven bandwidth.

```stata
ssc install rdrobust
ssc install rddensity

* Sharp RD: outcome y, running variable x, cutoff c=0
rdrobust y x, c(0)                 // MSE-optimal bw, robust bias-corrected CI
rdbwselect y x, c(0) all           // compare bandwidth selectors
rdplot y x, c(0)                   // binned scatter + fit -> graph:// ref

* Fuzzy RD: treatment take-up d jumps at the cutoff (LATE at the threshold)
rdrobust y x, c(0) fuzzy(d)

* Manipulation / sorting test (McCrary-style density discontinuity)
rddensity x, c(0)                  // H0: no manipulation; p-value in r()/e()
```

Sharp = treatment deterministic at cutoff; fuzzy = probability of treatment jumps (use `fuzzy()`). Always: (1) `rddensity` for sorting, (2) covariate-balance RD (`rdrobust covar x`) as placebo, (3) report sensitivity to bandwidth.

## 6. Matching / weighting

Built-in `teffects` (no install); `tebalance` checks covariate balance after. Community alternatives: `psmatch2` (`ssc install psmatch2`), `kmatch` (`ssc install kmatch`).

```stata
* Propensity-score matching (built-in)
teffects psmatch (y) (treat x1 x2 i.x3), atet
tebalance summarize               // standardized differences before/after

* Nearest-neighbor (Mahalanobis) matching
teffects nnmatch (y x1 x2) (treat), atet nneighbor(3)

* IPW: weight by inverse propensity score
teffects ipw (y) (treat x1 x2 i.x3), atet

* Doubly-robust AIPW (consistent if either model right)
teffects aipw (y x1 x2) (treat x1 x2 i.x3)

* Regression adjustment only
teffects ra (y x1 x2) (treat)
```

Estimators: `psmatch`, `nnmatch`, `ipw`, `ra`, `aipw`. Check **overlap/common support** (`teffects overlap` after `ipw`/`psmatch`) — IPW blows up when propensity scores approach 0/1.

## 7. Synthetic control

All community. `synth` (`ssc install synth`) builds a weighted donor-pool counterfactual; `synth_runner` (`net install synth_runner`) adds placebo-based inference; `sdid` (`ssc install sdid`) is synthetic DiD (Arkhangelsky et al. 2021).

```stata
ssc install synth
tsset unit time
synth y predictor1 predictor2 y(1990) y(1995), ///
    trunit(7) trperiod(2000) fig            // unit 7 treated in 2000; fig -> graph://

* Placebo-based p-values / inference
net install synth_runner, from(https://raw.github.com/bquistorff/synth_runner/master/) replace
synth_runner y predictor1 predictor2, trunit(7) trperiod(2000) gen_vars

* Synthetic DiD (handles staggered adoption, balances pre-trends)
ssc install sdid
sdid y unit time treat, vce(placebo) seed(1)
```

`synth` needs a balanced panel (`tsset`) and a single treated unit per call. `sdid` accepts multiple treated units / staggered timing and reports a proper variance.

## Choosing a design

| Data shape / assumption | Estimator | Built-in? |
| --- | --- | --- |
| Panel, common treatment timing | `reghdfe` / `xtreg` TWFE | `xtreg` built-in; `reghdfe` community |
| Panel, **staggered** adoption | `csdid`, `did_imputation`, `did_multiplegt_dyn`, `sdid` | community |
| Heterogeneous dynamic effects, want event study | `csdid` + `estat event`, `eventstudyinteract` | community |
| Endogenous regressor + valid instrument | `ivregress 2sls`, `ivreghdfe` | `ivregress` built-in |
| Treatment assigned by a cutoff in a running var | `rdrobust` (sharp/fuzzy) | community |
| Cross-section, selection on observables | `teffects` (psmatch/ipw/aipw) | built-in |
| 1 (or few) treated units, long pre-period | `synth`, `synth_runner`, `sdid` | community |

## Common pitfalls

- **TWFE under staggered adoption:** the single DiD coefficient mixes "forbidden" already-treated-as-control comparisons and can be sign-flipped. Diagnose with `bacondecomp`; switch to a §2 estimator.
- **Bad controls:** never condition on a post-treatment variable (a mediator or collider) — it biases the causal estimate. Control only for pre-treatment covariates.
- **No base period in event studies:** must omit one relative-time dummy (e.g. t = −1) via `ib#.`; otherwise effects aren't identified relative to anything and pre-trend tests are meaningless.
- **Weak instruments:** first-stage F barely above 10 is not safe. Report effective F (`weakivtest`) and weak-IV-robust (Anderson-Rubin) CIs; a weak IV is biased toward OLS with wrong coverage.
- **RDD bandwidth sensitivity:** report `rdrobust` across several bandwidths and use bias-corrected robust CIs (default), not the conventional ones; always run `rddensity` for manipulation and a covariate-balance placebo.
- **Forgot to install:** community commands throw `command_not_found` / rc 199. Run `install_package` (or `ssc install <pkg>`) first — note `csdid` needs `drdid`, `eventstudyinteract` needs `avar`, `reghdfe` needs `ftools`.
