# Recipe: regression discontinuity (turnkey)

*A complete RDD pipeline through stata-code: load → visualize the jump →
local-polynomial estimate with robust bias-corrected CIs → manipulation and
balance placebos → bandwidth sensitivity → report. Mechanics in
[`../causal-inference.md`](../causal-inference.md) §5. Cross-check in StatsPAI via
[`parity-audit.md`](../parity-audit.md).*

RDD identifies a **local** effect at the cutoff (a LATE at the threshold) for units
near the running-variable boundary. External validity past the cutoff is an
assumption, not a result.

## 1. Load and name the parts

```text
stata_run(code="use \"data/rd.dta\", clear", session_id="rd")
inspect_data(session_id="rd")
```

- **y** — outcome
- **x** — running (forcing) variable
- **c** — cutoff value
- **d** — (fuzzy only) actual treatment take-up

## 2. Look before you estimate

```stata
* install_package(name="rdrobust")
rdplot y x, c(0)        // binned scatter + polynomial fit -> graph:// ref (get_graph)
```

The plot is the single most informative output — it shows whether there is a
visible discontinuity and whether the polynomial order is sane.

## 3. Estimate with bias-corrected robust CIs

```stata
* Sharp RD
rdrobust y x, c(0)                 // MSE-optimal bandwidth, robust bias-corrected CI
estimates store rd_sharp

* Fuzzy RD (treatment take-up jumps at the cutoff)
rdrobust y x, c(0) fuzzy(d)
```

Report the **robust** bias-corrected estimate and CI (the default), not the
conventional one. The point estimate lands in `results.e.scalars`.

## 4. The three mandatory placebos

```stata
* (a) Manipulation / sorting at the cutoff (McCrary-style density test)
* install_package(name="rddensity")
rddensity x, c(0)                  // H0: no manipulation; want a non-significant p

* (b) Covariate balance — predetermined covariates should NOT jump
rdrobust covariate x, c(0)         // repeat per covariate; expect null effects

* (c) Bandwidth sensitivity — estimate must be stable across bandwidths
rdbwselect y x, c(0) all           // compare selectors
rdrobust y x, c(0) h(<half>)       // re-run at narrower/wider h
```

A significant `rddensity` (units sorting across the cutoff) or a covariate that
jumps undermines the design — surface it rather than burying it.

## 5. Publication table

```stata
* install_package(name="estout")
esttab rd_sharp using "rd_results.tex", replace ///
    b(%9.3f) se(%9.3f) star(* 0.10 ** 0.05 *** 0.01) ///
    stats(N h_l, labels("Observations" "Bandwidth")) ///
    mtitles("Sharp RD") title("Effect at the cutoff") ///
    note("Local linear, MSE-optimal bandwidth, robust bias-corrected CI.")
```

See [`publication-tables.md`](publication-tables.md). Report the bandwidth and the
manipulation-test p-value alongside the estimate — they are what make an RD
credible.

## 6. Report

From `results.e.scalars`: the (robust) effect at the cutoff and CI, the bandwidth,
the manipulation-test p-value, and a one-line statement on covariate balance and
bandwidth sensitivity. State the estimand is local to the cutoff. For an
independent second estimate, hand the same `y, x, c` to StatsPAI's `rdrobust` /
`rdd` (see [`parity-audit.md`](../parity-audit.md)).

## Pitfalls (RDD-specific)

- **Conventional CIs.** Use the robust bias-corrected CI `rdrobust` reports by
  default, not the conventional one.
- **Skipping the density test.** Always run `rddensity` for manipulation; sorting
  across the cutoff breaks identification.
- **Over-fitting the polynomial.** Prefer local linear/quadratic over high-order
  global polynomials (Gelman & Imbens).
- **Single bandwidth.** Report sensitivity across bandwidths; a result that only
  survives one bandwidth is fragile.
- **Generalizing the LATE.** The effect is local to the cutoff — do not project it
  across the whole running-variable range.
