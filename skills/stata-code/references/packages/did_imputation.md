# did_imputation

*Read this when the user asks for Borusyak, Jaravel, and Spiess imputation DiD,
event-study effects, or pre-trend/placebo checks under staggered adoption.*

Install: `ssc install did_imputation, replace`. Via stata-code:
`install_package(name="did_imputation")`.

`did_imputation` imputes untreated potential outcomes from an untreated
fixed-effects model, then estimates treatment effects for treated observations.

## Basic syntax

```stata
did_imputation y unit_id year first_treat, allhorizons pretrends(5)
```

Arguments are positional: outcome, unit id, time id, and treatment cohort.
`first_treat` is the first treated period; never-treated units should follow
the command's documented convention.

## Good uses

- Staggered adoption with no anticipation and parallel trends conditional on
  included fixed effects/covariates.
- Event-study estimates where placebo leads should be reported.
- Parity checks against other modern DiD estimators.

## Read results through stata-code

- Event-time estimates usually appear in `e(b)`; fetch large matrices via
  `get_matrix(ref)` if needed.
- Pretrend/placebo output may require inspecting `results.e` after the command
  and `search_log` for warning text.
- Graphs, when requested by options, arrive as `graph://` refs.

## Common pitfalls

- Treatment cohort is not a 0/1 dummy.
- Sparse tails make long lead/lag horizons unstable. Bin or shorten horizons.
- Do not compare imputation event-time estimates to a single overall ATT
  without aggregating to the same target.
