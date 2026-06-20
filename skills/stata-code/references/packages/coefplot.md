# coefplot

*Read this when the user wants to plot coefficients and confidence intervals from one or more stored estimates — forest plots, event-study lead/lag plots, model comparisons.*

Install: `ssc install coefplot`. Via stata-code: `install_package(name="coefplot")`.

`coefplot` draws point estimates with CI whiskers from stored estimation results. It reads `e(b)` / `e(V)` from the last model or from named stored estimates.

## Basic usage

```stata
regress y x1 x2 x3
coefplot
```

Plots every coefficient (including `_cons`) with its CI as a horizontal dot-and-whisker plot.

In stata-code, the rendered graph is captured as a `graph://` reference — fetch the image with `get_graph`.

## Comparing stored estimates

```stata
regress y x1 x2
estimates store m1
regress y x1 x2 x3
estimates store m2
coefplot m1 m2
```

Each stored model gets its own colored series. Add legend labels with `coefplot (m1, label("Base")) (m2, label("Full"))`.

## Selecting / renaming coefficients

```stata
coefplot, keep(x1 x2)            // keep only these
coefplot, drop(_cons)            // drop the constant (very common)
coefplot, rename(x1 = "Treated") // relabel a coefficient
coefplot, coeflabels(x1 = "Treatment effect" x2 = "Age")
```

- `keep()` / `drop()` accept coefficient names and wildcards (`keep(*.period)`).
- `coeflabels()` sets axis labels for coefficients.
- `order()` fixes the plotting order.

## Orientation and reference lines

```stata
coefplot, vertical            // coefficients along the x-axis
coefplot, drop(_cons) xline(0)  // null line for horizontal layout
coefplot, vertical yline(0)     // null line for vertical layout
```

`xline(0)` for the default horizontal layout, `yline(0)` once you switch to `vertical`.

## Confidence levels

```stata
coefplot, levels(95 90)
```

Draws nested CIs (here 95% outer, 90% inner). Default is the single 95% level.

## Event study (leads and lags)

With factor-variable interactions like `i.period` (period indexed relative to treatment):

```stata
reghdfe y ib0.period##i.treated, absorb(id time) vce(cluster id)
coefplot, keep(*.period#1.treated) vertical yline(0) ///
    xline(0) recast(line) ciopts(recast(rcap)) ///
    coeflabels(*.period = , ) xtitle("Periods relative to treatment")
```

Key idea: `keep(*.period...)` selects the lead/lag terms, `vertical` puts time on the x-axis, `yline(0)` marks the null, and the omitted base period (e.g. `ib0`) shows as a gap at zero.

## Recasting the plot type

```stata
coefplot, recast(bar)         // bars instead of dots
coefplot, recast(line)        // connected line (event studies)
coefplot, vertical recast(bar) barwidth(0.5) citop
```

`recast()` changes the marker rendering; `ciopts(recast(rcap))` changes the CI whisker style.

## Pitfalls

- **Must have estimates available.** Run a model first (and `estimates store name` before comparing multiple). Plotting stale or cleared estimates fails or plots nothing.
- **Factor-variable coefficient names.** With `i.x` the names are `1.x`, `2.x`, etc.; interactions are `2.x#1.z`. Use wildcards (`keep(*.period)`) and inspect names with the matrix tools if `keep()`/`drop()` selects nothing.
- **Baseline level is omitted.** The reference category of a factor variable (e.g. `ib0.period`) has no coefficient and appears as a gap — expected, not a bug; relabel/locate it explicitly if you need a visible zero point.
- `xline(0)` vs `yline(0)` depends on orientation — using the wrong one puts the reference line on the wrong axis.
- `_cons` is plotted by default and usually dwarfs the scale; `drop(_cons)` in almost every real plot.
