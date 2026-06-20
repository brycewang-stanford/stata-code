# Recipe: publication-ready tables with esttab (turnkey)

*Turn one or many stored estimates into a LaTeX / Word / Excel / Markdown table in
one pass. This is the "...最后输出 esttab 表" half of nearly every applied request.
Deeper grammar and `estout` low-level control live in
[`../tables-export.md`](../tables-export.md) and
[`../packages/estout.md`](../packages/estout.md).*

`estout`/`esttab` is community: `install_package(name="estout")` (provides
`esttab`, `estout`, `estadd`, `eststo`).

## The two-step pattern: store, then export

```stata
* 1. Store each model under a name (eststo == estimates store)
eststo clear
eststo m1: reghdfe y x1 x2,        absorb(unit) vce(cluster unit)
eststo m2: reghdfe y x1 x2 x3,     absorb(unit time) vce(cluster unit)
eststo m3: reghdfe y x1 x2 x3 x4,  absorb(unit time) vce(cluster unit)

* 2. Export all stored models as columns
esttab m1 m2 m3 using "table.tex", replace booktabs ///
    b(%9.3f) se(%9.3f) star(* 0.10 ** 0.05 *** 0.01) ///
    stats(N r2_within, labels("Observations" "Within R²")) ///
    mtitles("Base" "+ time FE" "+ controls") ///
    keep(x1 x2 x3 x4) order(x1 x2 x3 x4) ///
    title("Main results") ///
    note("Cluster-robust SEs in parentheses.")
```

## Pick the output format by extension

| Want | Extension | Notes |
| --- | --- | --- |
| LaTeX | `.tex` | add `booktabs` for `\toprule`/`\midrule`; `fragment` to `\input` it |
| Word | `.rtf` | opens directly in Word |
| Excel | `.csv` | one cell per number; or `.rtf` for formatting |
| Markdown | `.md` | for notebooks / GitHub / chat |
| Plain text | `.txt` | quick console-style preview |

`esttab` writes to the **Stata working directory** — report the path back to the
user. To preview in chat without a file, run `esttab m1 m2 m3, <opts>` with no
`using`; the formatted table lands in the log.

## Common option cheat-sheet

| Goal | Option |
| --- | --- |
| Coefficients + SEs | `b(%9.3f) se(%9.3f)` |
| t-stats instead of SEs | `t(%9.2f)` |
| Significance stars | `star(* 0.10 ** 0.05 *** 0.01)` |
| Confidence intervals | `ci(%9.3f)` |
| Keep / drop / order terms | `keep(...)` `drop(...)` `order(...)` |
| Rename terms for readers | `coeflabels(x1 "Treatment" x2 "Log income")` |
| Column titles | `mtitles("..." "...")` |
| Bottom stats rows | `stats(N r2, labels("Obs" "R²"))` |
| Add a custom scalar | `estadd scalar fstat = e(F)` then `stats(fstat)` |
| Add group/FE indicator rows | `indicate("Unit FE = ...")` |
| Summary stats table | `estpost summarize ...` then `esttab, cells("mean sd min max")` |

## Summary-statistics table

```stata
estpost summarize y x1 x2 x3, detail
esttab using "summstats.tex", replace ///
    cells("mean(fmt(%9.2f)) sd(fmt(%9.2f)) min max") ///
    label nonumber noobs title("Summary statistics")
```

## Pitfalls

- **`eststo`/`estimates store` is a snapshot.** Store immediately after each model,
  before the next estimation overwrites `e()`.
- **Wrong R² label.** `reghdfe` reports `r2_within`; plain `regress` reports `r2`.
  Pick the scalar that exists in `e()`.
- **Stale `eststo`.** Run `eststo clear` before a new table or old columns leak in.
- **Lost file.** The table goes to Stata's working directory; pass an absolute
  path or `cd` first, then tell the user where it landed.
