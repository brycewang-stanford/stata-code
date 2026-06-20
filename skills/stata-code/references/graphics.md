# Graphics

*Read this when the task is about plotting or visualizing data in Stata — twoway, distributions, bar/box plots, schemes, `marginsplot`, coefficient plots, or exporting figures.*

In stata-code, **graphs are captured automatically**. Any `stata_run` that draws a graph returns it as a `graph://` ref in the `graphs[]` array. Fetch the image bytes with the `get_graph(ref)` MCP tool and surface it to the user. You almost never need `graph export` just to show a plot — only export when the user wants a file on disk.

## `twoway` family

The workhorse. Each plot type is a subcommand; overlay by stacking parenthesized plots — they share one set of axes.

```stata
sysuse auto, clear
twoway scatter mpg weight
twoway line mpg weight, sort                 // sort: connect in x-order
twoway connected mpg weight, sort            // markers + line
twoway (scatter mpg weight) (lfit mpg weight)   // overlay scatter + linear fit
twoway (scatter mpg weight) (qfit mpg weight)   // quadratic fit
```

| Plot | Draws |
| --- | --- |
| `scatter y x` | markers |
| `line y x` | connected line (use `, sort` unless x already sorted) |
| `connected y x` | markers + line |
| `lfit y x` / `qfit y x` | linear / quadratic fit line |
| `lfitci` / `qfitci` | fit line + CI band |
| `area y x` | shaded area under line |
| `bar y x` | vertical bars (a twoway plot, distinct from `graph bar`) |
| `rarea yhi ylo x` | shaded band between two y vars |
| `rcap hi lo x` | capped-spike CI ranges |
| `rspike hi lo x` | spike ranges |
| `scatteri` | immediate/inline coordinates |

CI band over a fitted mean, plus the raw points on top:

```stata
twoway (lfitci mpg weight) (scatter mpg weight)
twoway (rcap hi lo x) (scatter mean x)        // manual CI bars from collapsed data
```

Common per-plot options inside the parentheses: `msymbol(O)`, `mcolor()`, `lcolor()`, `lpattern(dash)`, `lwidth()`, `color(%40)` (the `%` is opacity).

## `by()` and `over()`; `graph combine`

`by()` makes a **panel of separate subgraphs** (small multiples), one per group. `over()` (used by `graph bar`/`graph box`) draws categories **within a single graph**.

```stata
twoway scatter mpg weight, by(foreign)
twoway scatter mpg weight, by(foreign, total cols(2) note(""))
graph bar (mean) mpg, over(foreign)           // over: categories on one axis
graph bar (mean) mpg, over(rep78) over(foreign)  // nested grouping
```

Combine independently-drawn graphs into one figure:

```stata
twoway scatter mpg weight, name(g1, replace)
twoway scatter price weight, name(g2, replace)
graph combine g1 g2, cols(2) title("Two views")
```

`graph combine` references graphs by **name**, so each source plot needs `name(, replace)` (see below).

## Distributions and other graph types

```stata
histogram mpg, frequency                      // or: , percent  /  , density (default)
histogram mpg, bin(20) normal                 // overlay normal curve
histogram foreign, discrete                   // for integer/categorical x
kdensity mpg                                  // kernel density
twoway (histogram mpg) (kdensity mpg)         // overlay both
graph bar (mean) mpg price, over(foreign)     // summary bars; (median),(sum) etc.
graph hbar (mean) mpg, over(rep78)            // horizontal
graph box mpg, over(foreign)                  // box-and-whisker by group
graph box mpg price                           // multiple vars, no grouping
graph matrix mpg weight price                 // scatterplot matrix
```

`graph bar`/`graph box`/`graph matrix` are their own commands — distinct from `twoway bar`. They take `over()`, not `by()` (though `by()` also works to panel them).

## Titles, axes, legend, notes

These are graph-wide options (after the comma, outside any plot parens).

```stata
twoway (scatter mpg weight) (lfit mpg weight), ///
    title("Mileage vs weight") subtitle("1978 autos") ///
    xtitle("Weight (lbs)") ytitle("MPG") ///
    note("Source: auto.dta") caption("Fig 1") ///
    legend(order(1 "data" 2 "fit") position(6) rows(1)) ///
    xlabel(2000(1000)5000) ylabel(10(10)40, angle(0) grid) ///
    yscale(range(0 .)) xscale(log)
```

| Option | Effect |
| --- | --- |
| `title()` `subtitle()` `note()` `caption()` | text elements |
| `xtitle()` `ytitle()` | axis titles; `xtitle("")` removes |
| `xlabel(#10)` / `xlabel(min(step)max)` | tick labels; `, grid angle(45) format()` |
| `xtick()` `xmtick()` | tick marks (no labels) / minor ticks |
| `legend(off)` | hide legend |
| `legend(order(...) pos(6) col(2) region(lwidth(none)))` | customize |
| `xline(#)` `yline(#)` | reference lines, e.g. `yline(0, lpattern(dash))` |
| `xscale(log)` `yscale(range())` `yscale(reverse)` | axis scaling |
| `aspectratio(1)` `xsize()` `ysize()` | shape/size |
| `plotregion()` `graphregion(color(white))` | region styling |

### `name(, replace)`

```stata
twoway scatter mpg weight, name(myplot, replace)
```

Names the in-memory graph so you can `graph combine`, `graph display`, or redraw it. **`replace` is required** if a graph of that name already exists, or Stata errors with `myplot already exists`.

## Schemes

A scheme controls overall look (colors, fonts, gridlines).

```stata
set scheme stcolor          // Stata 18 default (modern). 17 default: s2color
set scheme s2color          // classic blue-background-free look
graph twoway scatter mpg weight, scheme(economist)   // per-graph override
```

Built-in schemes include: `stcolor`, `stgcolor`, `s2color`, `s1color`, `s1mono`, `s2mono`, `economist`, `sj` (Stata Journal). Set persistently with `set scheme <name>, permanently`.

**Community schemes** (need install, run once):

```stata
ssc install grstyle        // programmatic restyling of the active scheme
ssc install schemepack     // adds white_tableau, tab1-2, neon, etc.
net install grstyle, from(...) // grstyle also via SSC
```

`grstyle` lets you tweak elements without writing a full scheme:

```stata
grstyle init
grstyle set plain, horizontal grid
grstyle set color tableau
```

Cross-reference: see `packages/` notes for install specifics.

## Exporting (only when a file is wanted)

In stata-code you usually return the `graph://` ref instead. Export when the user explicitly asks for a file on disk:

```stata
graph export fig.png, replace width(2000)     // raster; width in pixels
graph export fig.pdf, replace                 // vector
graph export fig.svg, replace                 // vector, web-friendly
graph export fig.eps, replace                 // vector, journals
```

| Format | Type | Use |
| --- | --- | --- |
| png | raster | slides, web, quick share |
| svg | vector | web, scalable |
| pdf | vector | papers, print |
| eps | vector | journal submission |

`width()`/`height()` apply to raster (png/tif). Add `name(g1)` to export a specific in-memory graph rather than the active one: `graph export g1.png, name(g1) replace`.

## `marginsplot`, `coefplot`, `binscatter`

After `margins`, plot predictions/effects directly:

```stata
regress mpg c.weight##c.weight i.foreign
margins, at(weight=(2000(500)5000))
marginsplot                                   // captured automatically as a graph ref
margins foreign
marginsplot, recast(bar)
margins, dydx(foreign) at(weight=(2000(1000)5000))
marginsplot, recast(line) recastci(rarea)
```

**`coefplot`** (community — `ssc install coefplot`) plots coefficients with CIs from stored estimates. See `packages/coefplot.md`.

```stata
regress mpg weight length i.foreign
coefplot, drop(_cons) xline(0)
```

**`binscatter`** (community — `ssc install binscatter`, or `binscatter2`) bins x and plots conditional means — great for big data scatters.

```stata
binscatter mpg weight                         // also: , by()  controls()  nquantiles()
```

All three draw graphs, so each is returned in `graphs[]` — fetch with `get_graph(ref)`, no export needed to show the user.

## `graph dir`, `graph display`, redrawing

```stata
graph dir                          // list named graphs in memory
graph display myplot               // re-render a named graph
graph display myplot, scheme(economist) xsize(4)   // redraw with new look/size
graph drop myplot                  // remove one;  graph drop _all
graph rename old new, replace
```

Redrawing (`graph display`) re-emits the graph — stata-code captures the redrawn version as a fresh ref.

## Common pitfalls

- **Forgetting `, replace` on `name()`** — re-running a block with `name(g1)` errors `g1 already exists`. Always `name(g1, replace)`.
- **Exporting when you only need to show it** — in stata-code the graph is already captured as a `graph://` ref; call `get_graph(ref)`. Don't `graph export` to a temp file just to display.
- **`by()` vs `over()`** — `by()` splits into separate subgraph panels; `over()` (graph bar/box) puts categories side-by-side on one axis. Using `by()` on `graph bar` panels it; using `over()` groups within. Picking the wrong one gives an unexpected layout.
- **Scheme not installed** — `grstyle`/`schemepack`/`coefplot`/`binscatter` are community commands; `set scheme white_tableau` fails until `ssc install schemepack`. Install once per environment.
- **`line` without `, sort`** — connects points in dataset order, producing a tangled mess when x isn't sorted. Use `, sort` or pre-`sort x`.
- **Mixing up `twoway bar` and `graph bar`** — `twoway bar y x` plots y against a continuous x; `graph bar (stat) y, over(cat)` summarizes by category. They are not interchangeable.
