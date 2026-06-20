# ivreghdfe

*Read this when the user needs instrumental variables with high-dimensional
fixed effects.*

Install: `ssc install ivreghdfe, replace`; it depends on `ivreg2`, `reghdfe`,
and `ftools`. Via stata-code, install all four if needed:
`install_package(name="ivreghdfe")`, `ivreg2`, `reghdfe`, and `ftools`.

`ivreghdfe` combines IV estimation with `reghdfe`-style absorption of multiple
fixed effects.

## Basic syntax

```stata
ivreghdfe y x1 x2 (d = z1 z2), absorb(firm year) cluster(firm)
```

Use `absorb()` for fixed effects and `cluster()` for clustered inference. The
endogenous/instrument syntax follows `ivreg2`.

## Read results through stata-code

- Main coefficient vector and VCE: `results.e.matrices.b` and
  `results.e.matrices.V`.
- Sample size, degrees of freedom, cluster count, and weak-IV diagnostics may
  appear in `results.e.scalars`; inspect before parsing logs.
- If diagnostics are log-only, use `search_log` for `Kleibergen`, `Hansen`,
  `first-stage`, or `Underidentification`.

## Common pitfalls

- Installing `ivreghdfe` without its dependencies.
- Using `vce(cluster ...)` instead of the package's expected `cluster()` syntax
  in examples that follow older versions.
- Forgetting that absorbed fixed effects are not coefficient rows in `e(b)`.
- Comparing to `ivregress` without the same fixed effects and sample.
