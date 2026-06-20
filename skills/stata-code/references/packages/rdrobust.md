# rdrobust

*Read this when the user asks for regression discontinuity estimation, RD
bandwidth sensitivity, RD plots, or manipulation tests.*

Install: `ssc install rdrobust, replace`; for density tests also install
`rddensity`. Via stata-code: `install_package(name="rdrobust")` and
`install_package(name="rddensity")`.

`rdrobust` estimates sharp and fuzzy RD with local-polynomial methods and robust
bias-corrected confidence intervals.

## Basic syntax

```stata
* Sharp RD at cutoff 0
rdrobust y running, c(0)
rdbwselect y running, c(0) all
rdplot y running, c(0)

* Fuzzy RD
rdrobust y running, c(0) fuzzy(take_up)

* Manipulation / sorting check
rddensity running, c(0)
```

## Read results through stata-code

- Main estimates and bandwidths are usually in `results.e.scalars` and
  `results.e.matrices`; exact names vary by package version.
- `rdplot` creates a graph ref; call `get_graph(ref)` only when needed.
- Use `search_log` for bandwidth table labels before fetching full logs.

## Minimal RD audit checklist

- Running variable, cutoff, and treatment rule.
- Sharp vs fuzzy design.
- Chosen bandwidth and alternative bandwidths.
- Polynomial order and kernel.
- Manipulation test (`rddensity`) and covariate-balance placebo checks.
- Graph of binned means around the cutoff.

## Common pitfalls

- Treating the conventional CI as the preferred CI when robust bias-corrected
  inference is available.
- Reporting one bandwidth only.
- Skipping density/manipulation and covariate-balance checks.
- Forgetting that fuzzy RD estimates a local treatment effect at the cutoff.
