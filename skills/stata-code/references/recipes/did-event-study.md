# Recipe: difference-in-differences / event study (turnkey)

*A complete DiD pipeline driven through stata-code: install → load → TWFE baseline
→ diagnose staggered bias → modern staggered estimator → event-study plot →
publication table → interpret. This is the "全流程" the user means by "run a DiD
and give me the table." For the underlying mechanics see
[`../causal-inference.md`](../causal-inference.md); for cross-checking the point
estimate against StatsPAI/Python see [`parity-audit.md`](../parity-audit.md).*

The canonical request this recipe answers:

> "Run a two-way fixed-effects regression of monthly wage on the treatment in
> `data/cfps_panel.dta`, control for `age age2 edu industry`, then check
> heterogeneous effects with Callaway-Sant'Anna, and give me an esttab table."

## 0. Decide the design before writing code

Ask (or infer from the data) two questions, because they pick the estimator:

1. **Is treatment timing common or staggered?** One switch-on date for every
   treated unit → a clean 2×2 / TWFE is fine. Different cohorts adopt at
   different times → TWFE is biased (Goodman-Bacon 2021); use a §3 estimator.
2. **Is there a never-treated (or not-yet-treated) control group?** Modern
   estimators need one; `csdid` takes `notyet` when there is no never-treated
   group.

If you do not know, run the Bacon diagnostic in §2 first and let it decide.

## 1. Load and inspect

```text
stata_run(code="use \"data/cfps_panel.dta\", clear", session_id="did")
inspect_data(session_id="did")          # confirm the panel id, time, treat, cohort vars exist
```

Confirm with `inspect_data` (not by guessing variable names): you need a unit id,
a time variable, the outcome, and — for staggered designs — a **cohort variable**
that records the *period each unit is first treated* (`.` or `0` for
never-treated), not a 0/1 post dummy.

```stata
xtset unit time          // declare the panel; rc 459 here means id/time aren't unique
```

## 2. TWFE baseline + staggered-bias diagnostic

```stata
* reghdfe is community — install_package(name="reghdfe") if rc 199
reghdfe wage i.treat##i.post age age2 edu i.industry, absorb(unit time) vce(cluster unit)
estimates store twfe

* Is the single TWFE coefficient trustworthy? Decompose it.
* install_package(name="bacondecomp") first
bacondecomp wage treatdummy, ddetail
```

If `bacondecomp` shows meaningful weight on "already-treated as control" 2×2s
(the forbidden comparisons), **stop trusting the TWFE coefficient** and move to
§3. Report the Bacon weights either way — it is the cheapest evidence that the
modern estimator was necessary.

## 3. Modern staggered estimator — Callaway & Sant'Anna

```stata
* install_package(name="csdid"); install_package(name="drdid")   // drdid is a csdid dependency
csdid wage age age2 edu i.industry, ivar(unit) time(time) gvar(first_treat) method(dripw)
estat simple        // overall ATT  -> results.e
estat event         // dynamic (event-time) ATTs
estat calendar      // by calendar period
csdid_plot          // event-study figure -> graph:// ref  (fetch with get_graph)
estimates store csda
```

`gvar(first_treat)` is the **cohort** (first-treatment period), not a dummy. Use
`csdid ..., notyet` when there is no never-treated group. The overall ATT lands in
`results.e.scalars`; quote it from there rather than grepping the log.

Alternatives (same role, different assumptions — see `../causal-inference.md` §2):
`did_imputation` (Borusyak et al.), `did_multiplegt_dyn` (de Chaisemartin &
D'Haultfœuille), `eventstudyinteract` (Sun & Abraham), `sdid` (synthetic DiD).

## 4. Event study by hand (when you want full control)

If you need explicit leads/lags rather than the estimator's built-in `estat
event`:

```stata
gen event_time = time - first_treat
replace event_time = -4 if event_time < -4 & !missing(event_time)
replace event_time =  4 if event_time >  4
gen et = event_time + 4                       // shift so base period -1 -> level 3
reghdfe wage ib3.et age age2 edu i.industry, absorb(unit time) vce(cluster unit)
* install_package(name="coefplot")
coefplot, keep(*.et) vertical yline(0) xtitle("Event time") ytitle("ATT")
```

Always omit one base period (`ib3.` here = drop t = −1) or the leads/lags are not
identified. Flat pre-period coefficients are *visual* parallel-trends evidence,
not a formal test — for a formal honest-DiD sensitivity bound, hand the estimates
to StatsPAI's `honest_did` (see [`parity-audit.md`](../parity-audit.md)).

## 5. Publication table (the esttab the user asked for)

See [`publication-tables.md`](publication-tables.md) for the full grammar. Minimal
turnkey version stacking the TWFE and CS-DID columns:

```stata
* install_package(name="estout")
esttab twfe csda using "did_results.rtf", replace ///
    b(%9.3f) se(%9.3f) star(* 0.10 ** 0.05 *** 0.01) ///
    stats(N r2, labels("Observations" "R-squared")) ///
    mtitles("TWFE" "Callaway-Sant'Anna") ///
    title("Effect of treatment on monthly wage") ///
    note("Cluster-robust SEs at the unit level.")
```

Swap the extension for the format the user wants: `.tex` (LaTeX), `.rtf` (Word),
`.csv` (Excel), or `.md`. The file is written to the Stata working directory; tell
the user the path.

## 6. Report

Quote from `results.e.scalars`, not the log: the overall ATT, its SE/CI, N, and
the pre-trend evidence. State plainly whether the Bacon diagnostic justified
leaving TWFE for the modern estimator, and whether pre-period coefficients support
parallel trends. If the user cares about robustness, offer the cross-validation
pass in [`parity-audit.md`](../parity-audit.md) — re-estimate the same ATT in
StatsPAI and report whether the two stacks agree (the Cunningham check).

## Pitfalls (DiD-specific)

- **Cohort vs. dummy.** `gvar`/`first_treat` must be the first-treatment *period*.
  Passing a 0/1 post dummy silently produces nonsense.
- **Trusting TWFE under staggering.** Run `bacondecomp` before believing a single
  DiD coefficient on staggered data.
- **No base period.** Event studies must omit one relative-time dummy.
- **Bad controls.** Never control for a post-treatment variable; condition only on
  pre-treatment covariates.
- **Forgot to install.** `csdid` needs `drdid`; `reghdfe` needs `ftools`;
  `eventstudyinteract` needs `avar`. `install_package` resolves them; community
  commands otherwise throw `command_not_found` (rc 199).
