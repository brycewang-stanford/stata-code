# estout / esttab

*Read this when the user wants publication-ready regression or summary tables exported to LaTeX, RTF, CSV, or the screen.*

Install: `ssc install estout`. Via stata-code: `install_package(name="estout")`. Provides `eststo`, `esttab`, `estadd`, `estpost`, and the lower-level `estout`.

## Workflow

```stata
eststo clear
eststo: regress y x1 x2
eststo: regress y x1 x2 x3
esttab
```

- `eststo` stores the most recent estimates (optionally `eststo name:`). It is a wrapper over `estimates store`.
- `eststo clear` wipes stored models — call it before building a new table.
- `esttab` formats all stored models side by side. With no model list it uses every stored estimate.

You can also use `estimates store m1` directly and `esttab m1 m2`.

## Exporting

```stata
esttab using results.tex, replace      // LaTeX (extension drives format)
esttab using results.rtf, replace      // Word/RTF
esttab using results.csv, replace       // CSV
esttab using results.tex, booktabs replace
```

The **file extension** chooses the output format. Use `replace` to overwrite or `append` to add to an existing file. Without `using`, output goes to the screen.

## Key options

```stata
esttab, se                         // show standard errors (default is t-stats)
esttab, t                          // show t-statistics
esttab, b(%9.3f) se(%9.3f)         // coefficient / SE number format
esttab, star(* 0.10 ** 0.05 *** 0.01)   // significance stars
esttab, stats(N r2_a, fmt(0 3))    // table footer scalars + formats
esttab, label                       // use variable labels not names
esttab, mtitles("Base" "Full")      // column titles
esttab, booktabs nogaps             // LaTeX booktabs rules, no row gaps
esttab, addnotes("SE clustered by firm.")
esttab, nonumbers nomtitles         // strip default (1)(2) headers
```

- `se` / `t` — what goes in parentheses under coefficients.
- `b()`, `se()`, `t()` — Stata `%fmt` number formats.
- `star()` — custom star thresholds (pairs of symbol + p-value cutoff).
- `stats()` — choose footer scalars (`N`, `r2`, `r2_a`, `F`, …) with matching `fmt()`.
- `label` — pull variable/value labels instead of raw names.
- `mtitles()` / `nomtitles`, `mgroups()` — column headers and grouped spanners.
- `booktabs`, `nogaps`, `alignment()`, `fragment` — LaTeX layout.
- `keep()` / `drop()` / `order()` — choose and reorder coefficients.

## Adding custom scalars

```stata
reghdfe y x, absorb(firm year) cluster(firm)
estadd local fe_firm "Yes"
estadd local fe_year "Yes"
estadd scalar within_r2 = e(r2_within)
eststo
esttab, stats(fe_firm fe_year within_r2 N, fmt(%s %s 3 0))
```

`estadd` attaches scalars/locals to the current estimates so they can appear in `stats()`.

## Descriptive / summary tables

```stata
estpost summarize x1 x2 x3
esttab, cells("mean sd min max") noobs nonumber

estpost tabstat x1 x2 x3, statistics(mean sd p50) columns(statistics)
esttab, cells("mean sd p50")

estpost tabulate group        // frequency table
```

`estpost` posts non-estimation results (summary stats, tabulations, correlations) into `e()` so `esttab`/`estout` can format them. Use `cells()` to pick which statistics print.

## Pitfalls

- **Forgetting `eststo`.** If no estimates are stored, `esttab` has nothing to print. Run `eststo:` before each model (or `estimates store`), and `eststo clear` between tables.
- **The extension drives the format.** `esttab using t.tex` makes LaTeX, `.rtf` makes Word, `.csv` makes CSV — there is no separate format flag; mismatched extension gives the wrong output.
- **`replace` vs `append`.** Omitting `replace` errors if the file exists; `append` adds rather than overwrites — easy to accumulate stale tables.
- `se` and `t` are mutually exclusive in the cell; default shows t-stats, so add `se` explicitly when journals want standard errors.
- Stata 17+ ships a built-in alternative, the `collect` / `etable` (and `dtable`) system; estout is the third-party route and still the most common in econ workflows, but don't conflate the two.
- `estpost` must precede `esttab` for summary tables; running `esttab` after a regular `summarize` will not pick up the stats.
