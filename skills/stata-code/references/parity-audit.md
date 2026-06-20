# Cross-stack parity audits

*Read this when the user wants to compare Stata results with R/Python packages,
or asks whether an AI-generated causal estimate is robust across software.*

`stata-code` owns the Stata execution leg. It should not pretend to run R or
Python unless those tools are separately available. The job here is to make the
comparison disciplined: same data, same sample, same target parameter, same
covariates, same estimator family, explicit defaults, and a clear refusal/failure
record.

## Why this matters

Modern DiD, DML, IV, and robust-inference packages often differ in defaults:
propensity-score models, trimming, cohort handling, variance estimators,
normalization, failure behavior, and numeric handling of large IDs. Treat package
choice as a reproducibility dimension, not as an implementation detail.

## Minimum parity contract

Before running any package comparison, write down:

| Field | Required detail |
| --- | --- |
| Dataset | File path, hash if available, source metadata, import code |
| Sample | Exact `if` condition, missing-value rule, balance rule, time window |
| IDs | Unit id, time id, treatment cohort, cluster id; note if IDs were recoded |
| Target | ATT, ATET, event-time ATT, LATE, ATE, CATE, etc. |
| Estimator | Package command, method option, covariate adjustment, control group |
| Inference | VCE, clustering, bootstrap/wild bootstrap, seed, reps |
| Versions | Stata version, package versions, R/Python package versions |
| Failure rule | What counts as refusal, fallback, warning, or invalid estimate |
| Tolerance | Exact match fields and numeric tolerances for coefficients/SEs |

## Stabilize the analysis sample in Stata

Create an explicit audit sample before exporting to other languages. Do not let
each package silently choose its own missing-value sample.

```stata
* Example: freeze a common sample for DiD parity work
use data/panel.dta, clear

* Recode very large or string ids into compact integer ids so every stack sees
* the same identifiers.
egen unit_id = group(original_unit_id), label
egen time_id = group(year), label

* Define the exact sample once.
gen byte audit_sample = !missing(y, first_treat, time_id, unit_id, x1, x2)
keep if audit_sample

isid unit_id time_id
compress
datasignature set, reset

save data/derived/parity_sample.dta, replace
export delimited using data/derived/parity_sample.csv, replace
```

If a package requires never-treated controls, balanced panels, nonnegative event
time dummies, or no singleton groups, check that condition explicitly and record
the result.

## Stata leg template: modern DiD

Use one Stata session per estimator if state pollution is possible.

```stata
use data/derived/parity_sample.dta, clear

* Callaway and Sant'Anna via csdid.
csdid y x1 x2, ivar(unit_id) time(time_id) gvar(first_treat) method(dripw)
estat simple
estat event
```

Record:

- `results.e.macros.cmd` and key method/control-group macros if present;
- `e(b)` and `e(V)` from `results.e.matrices`;
- `e(N)` and any group/cohort counts in `results.e.scalars`;
- warnings, notes, and graph refs;
- whether the command refused, dropped groups, changed method, or failed.

## Comparison table

Do not pick a winner by which estimate is more convenient. Produce a table like:

| Stack | Package | Version | Estimator/options | N | Effect | SE | Warning/refusal | Notes |
| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |
| Stata | `csdid` | captured | `method(dripw)` |  |  |  |  |  |
| Stata | `did_imputation` | captured | `pretrends(5)` |  |  |  |  |  |
| R | `did` | captured externally | same target |  |  |  |  |  |
| Python | selected package | captured externally | same target |  |  |  |  |  |

Exact-match fields: sample `N`, unit count, time count, cohort count, treatment
definition, and target parameter. Numeric fields can use a predeclared tolerance
such as `abs(diff) <= 1e-6` for deterministic OLS-style outputs and looser
tolerances for bootstrap/simulation outputs.

## Failure taxonomy for parity work

- **Refusal:** package stops because assumptions or data requirements fail.
  This is evidence, not an inconvenience.
- **Silent fallback:** package changes method, drops covariates, drops cohorts,
  or uses a different control group without a hard error. Treat as high risk.
- **Numeric mismatch:** same sample and estimator but different coefficient/SE.
  Inspect IDs, weights, propensity-score optimizer, trimming, and variance
  estimator.
- **Sample mismatch:** first fix the sample. Do not compare coefficients until
  `N` and group counts match.

## Report wording

Use conservative language:

- "The Stata leg estimates X under package/options Y."
- "The R/Python leg must be run by the available R/Python tool; stata-code does
  not execute that stack."
- "The estimates agree within the predeclared tolerance" or "they diverge; the
  likely source is ..."
- "Do not treat multiple packages as a p-hacking menu. Treat disagreement as a
  robustness finding that needs explanation."
