# Recipe: cross-validate an estimate across independent implementations

*Use this when a result is important enough that one package is not enough. The
goal is not to shop for the nicest estimate; it is to hold the specification fixed
and see whether two independent implementations agree.*

This is the discipline behind the Cunningham-style robustness check discussed in
the article: the same causal design can produce different numbers across packages
when defaults, samples, base periods, aggregation, or variance estimators differ.
Treat disagreement as evidence to reconcile, not as noise to hide.

## When to use it

- Staggered DiD / event studies where estimator defaults differ.
- IV with weak-instrument or clustered-inference concerns.
- RDD where bandwidth, bias correction, or density tests matter.
- Any table that will be interpreted as a main causal estimate.

## Protocol

1. **Freeze one common analysis sample.**
   Define missing-value rules, ID/time keys, treatment timing, controls, and
   clustering before running any estimator. Save a `.dta` for Stata and a CSV
   handoff for external R/Python tools.
2. **Run the Stata leg through `stata-code`.**
   Use `stata_run(..., persist_log_files=true, origin_path=...)` and read
   estimates from structured `results.e` / `results.r` fields where available.
3. **Run the independent leg only if a real external tool is available.**
   Do not claim `stata-code` ran R or Python. If the external stack is unavailable,
   fall back to a second Stata package and label it as a weaker same-language
   check.
4. **Compare the same estimand.**
   Align control group, aggregation, base period, covariates, clustering, weights,
   and variance estimator. State the tolerance before looking at the difference.
5. **Report agreement or disagreement.**
   Agreement lets you report the estimate with more confidence. Disagreement
   means the workflow should stop for reconciliation unless the gap itself is the
   robustness finding.

## DiD example

| Leg | Implementation | Target |
| --- | --- | --- |
| Stata | `csdid`, `did_imputation`, or `did_multiplegt_dyn` | overall ATT and event-study path |
| R / Python / StatsPAI | independent Callaway-Sant'Anna or imputation implementation | same ATT, same sample |

Stata side:

```stata
use "data/analysis_sample.dta", clear
csdid y x1 x2, ivar(unit) time(year) gvar(first_treat) method(dripw)
estat simple
estat event
```

Comparison table columns:

| Field | Why it matters |
| --- | --- |
| Package and version | Defaults drift over time |
| Sample N and unique IDs | Most mismatches are sample mismatches |
| Estimand and aggregation | Overall ATT is not an event-time coefficient |
| Controls and fixed effects | Different design matrix, different number |
| Cluster / SE method | Point estimate may agree while inference differs |
| Estimate, SE, CI | Main numerical comparison |
| Warnings or refusals | Do not bury package diagnostics |

## Common reconciliation order

1. Same sample after missing-value handling?
2. Same treatment cohort definition and never-treated / not-yet-treated control?
3. Same base period and event-time binning?
4. Same covariates, weights, and fixed effects?
5. Same aggregation target?
6. Same clustering and variance estimator?

Only after these match should a remaining gap be treated as a substantive
implementation difference.
