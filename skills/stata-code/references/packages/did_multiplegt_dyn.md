# did_multiplegt_dyn

*Read this when the user needs de Chaisemartin and D'Haultfoeuille style DiD
with dynamic effects, switching treatment, or non-binary treatment.*

Install: `ssc install did_multiplegt_dyn, replace`. Via stata-code:
`install_package(name="did_multiplegt_dyn")`.

`did_multiplegt_dyn` is useful when treatment can turn on/off, intensity can
vary, or the design is not a simple absorbing binary treatment.

## Basic syntax

```stata
did_multiplegt_dyn y unit_id year treat, effects(5) placebo(3)
```

Use `graph_off` only when you do not want the plot. If a graph is generated,
`stata-code` captures it as a `graph://` ref.

## Read results through stata-code

- Main dynamic effects and placebo estimates may be stored in matrices and/or
  printed output depending on version. Inspect `results.e`, then use
  `search_log` for labels such as `Effect` or `Placebo` if needed.
- Record options: `effects()`, `placebo()`, treatment coding, and cluster/VCE.

## Common pitfalls

- Using a binary absorbing-treatment estimator when treatment switches off.
- Comparing dynamic effects to an overall ATT from another package.
- Forgetting that placebo estimates are diagnostic evidence, not treatment
  effects.
