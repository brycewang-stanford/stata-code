# eventstudyinteract

*Read this when the user wants Sun and Abraham interaction-weighted event-study
estimates for staggered adoption.*

Install: `ssc install eventstudyinteract, replace`; it may require `avar`.
Via stata-code: `install_package(name="eventstudyinteract")` and, if needed,
`install_package(name="avar")`.

`eventstudyinteract` estimates event-study coefficients robust to heterogeneous
treatment effects by interacting relative-time indicators with treatment cohorts.

## Prepare relative-time dummies

```stata
gen rel = year - first_treat
replace rel = -5 if rel < -5
replace rel =  5 if rel >  5

forvalues k = 5(-1)2 {
    gen rel_m`k' = rel == -`k'
}
gen rel_0 = rel == 0
forvalues k = 1/5 {
    gen rel_p`k' = rel == `k'
}
```

Omit the `-1` period as the base.

## Basic syntax

```stata
eventstudyinteract y rel_m5 rel_m4 rel_m3 rel_m2 rel_0 rel_p1-rel_p5, ///
    cohort(first_treat) control_cohort(never_treated) ///
    absorb(i.unit_id i.year) vce(cluster unit_id)
```

`control_cohort()` is usually an indicator for never-treated controls. Check
that it is coded exactly as the command expects.

## Read results through stata-code

- Coefficients are in `e(b)` with columns named after the relative-time
  indicators.
- VCE is in `e(V)`; compute SEs from the diagonal if needed.
- Use `coefplot` for visualization, but summarize numbers from `results.e`.

## Common pitfalls

- Forgetting to omit the base relative period.
- Passing a treatment dummy as `cohort()` rather than first-treatment period.
- Including never-treated units in event-time dummy construction without
  guarding missing relative time.
