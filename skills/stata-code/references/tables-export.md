# Tables & exporting results

*Read this when the task is producing regression tables, summary tables, or exporting results to LaTeX/Word/Excel/Markdown.*

Two worlds coexist in modern Stata: the **built-in `collect`/`table`/`etable`/`dtable`** system (Stata 17/18, no install) and the **community `estout`/`esttab`** suite (the long-standing workhorse). Pick one per document — don't mix.

> **stata-code shortcut:** for a quick table *in chat*, you usually don't need to export anything. After an estimation command, read `results.e.scalars` and `results.e.matrices` (incl. `e(b)`, `e(V)`, `r(table)`) straight off the run result and format the numbers yourself. Export to disk only when the user wants a deliverable **file** — it then lands in the run bundle's `outputs/` when persistence is on.

## 1. Built-in `collect` / `etable` / `table` / `dtable` (Stata 17/18)

No install. Results are gathered into a *collection*, then exported. File extension drives the format.

```stata
sysuse auto, clear
regress price mpg weight foreign
* one-model table, default layout (coef + se, stars)
etable, showstars showstarsnote
* export — extension picks format: .docx .tex .html .md .xlsx .pdf .txt
collect export mytable.docx, replace
```

Multiple models side by side with `etable`:

```stata
quietly regress price mpg weight
estimates store m1
quietly regress price mpg weight foreign
estimates store m2
etable, estimates(m1 m2) ///
    column(estimates) ///
    showstars showstarsnote ///
    mstat(N) mstat(r2_a) ///
    export(models.tex, replace)
```

`table` builds tabulations/summaries; `collect` reshapes/styles them:

```stata
table (var) (result), command(regress price mpg weight foreign)
collect style cell, nformat(%9.3f)
collect export reg.html, replace
```

`dtable` (Stata 18+) — a one-liner descriptive "Table 1":

```stata
dtable mpg weight price, by(foreign) ///
    continuous(mpg weight price, statistic(mean sd)) ///
    export(table1.docx, replace)
```

Export targets for `collect export`: `.docx`, `.tex` (LaTeX), `.html`, `.md` (Markdown), `.xlsx`, `.pdf`, `.txt`. Always add `replace`.

## 2. `estout` / `esttab` — the workhorse (community)

```stata
ssc install estout, replace
```

Pattern: **store** each model with `eststo` (or `estimates store`), then emit with `esttab`. Forgetting `eststo` is the #1 mistake — `esttab` would table nothing or the wrong model.

```stata
sysuse auto, clear
eststo clear
eststo m1: regress price mpg weight
eststo m2: regress price mpg weight foreign
eststo m3: regress price mpg weight foreign turn

* console preview (no using -> screen)
esttab, se r2 ar2 star(* 0.10 ** 0.05 *** 0.01)
```

Write to a file with `using`; the extension/`type` picks the format:

```stata
* LaTeX (booktabs, publication-ready fragment)
esttab m1 m2 m3 using results.tex, replace ///
    booktabs label nogaps ///
    b(%9.3f) se(%9.3f) ///
    star(* 0.10 ** 0.05 *** 0.01) ///
    stats(N r2_a, fmt(%9.0f %9.3f) labels("Observations" "Adj. R\$^2\$")) ///
    title("Determinants of price") ///
    mtitles("Base" "+ Foreign" "+ Turn")

* Word / RTF
esttab m1 m2 m3 using results.rtf, replace ///
    label se b(%9.3f) star(* 0.10 ** 0.05 *** 0.01) stats(N r2_a)

* CSV (for spreadsheets) and Markdown
esttab m1 m2 m3 using results.csv, replace plain se b(3)
esttab m1 m2 m3 using results.md,  replace md se b(3) stats(N r2_a)
```

### Common `esttab` options

| Option | Effect |
| --- | --- |
| `b(%9.3f)` | format coefficients |
| `se` / `t` / `p` / `ci` | show SE / t / p-value / conf. interval below `b` |
| `star(* 0.10 ** 0.05 *** 0.01)` | significance stars + thresholds |
| `nostar` | suppress stars |
| `stats(N r2 r2_a F, labels(...))` | summary rows (any `e()` scalar) |
| `label` | use variable labels, not names |
| `mtitles("A" "B")` / `nomtitles` | column headers |
| `nonumbers` | drop the (1)(2)(3) row |
| `keep(mpg weight)` / `drop(_cons)` / `order(...)` | choose/order coef rows |
| `booktabs` | LaTeX `\toprule`/`\midrule` rules |
| `nogaps` | remove blank lines between coefficients |
| `fragment` | LaTeX body only (no `tabular` wrapper), for `\input` |
| `wide` | put SE/t in a column beside `b`, not below |
| `addnotes("...")` | footnote text |
| `replace` / `append` | overwrite vs. extend the file |
| `rename(_cons Constant)` | relabel terms |

Cross-model rows (e.g. fixed-effects indicators), via `estadd` / `eststo`:

```stata
eststo clear
eststo: regress price mpg weight i.rep78
estadd local hasFE "Yes"
esttab using fe.tex, replace label scalars("hasFE Rep78 FE") booktabs
```

`estout` is the lower-level engine if you need full layout control; `esttab` is the friendly wrapper — prefer `esttab`.

## 3. `outreg2` — quick alternative (community)

```stata
ssc install outreg2, replace
```

```stata
regress price mpg weight
outreg2 using out.doc, replace ctitle("Base") label
regress price mpg weight foreign
outreg2 using out.doc, append  ctitle("+ Foreign") label
```

Targets `.doc` (Word), `.tex`, `.xls`, `.txt`. Simpler than `esttab` but less flexible; `replace` on the first model, `append` thereafter. For new work prefer `esttab` or built-in `collect`.

## 4. `putexcel` — write cells & matrices to `.xlsx`

Built-in. Good for bespoke tables or dropping matrices into a workbook.

```stata
regress price mpg weight foreign
matrix R = r(table)            // coef table (see §7)

putexcel set report.xlsx, replace sheet("reg")
putexcel A1 = "Variable"  B1 = "Coef"  C1 = "Std. err."
putexcel A2 = matrix(R'), names    // transpose so terms are rows
* or place e(b) directly
putexcel A10 = matrix(e(b))
putexcel B12 = 1234.5, nformat(number_d2)
putexcel save
```

`putexcel A1=matrix(r(table))` writes the whole estimation table in one shot. Use `sheet(..., replace)` for multi-sheet workbooks; `modify` instead of `replace` to edit an existing file.

## 5. `putdocx` / `putpdf` — Word & PDF reports (brief)

Built-in. Build a document programmatically; `putdocx table` can embed an `etable`/`collect` or a matrix.

```stata
putdocx begin
putdocx paragraph, style(Heading1)
putdocx text ("Regression results")
regress price mpg weight foreign
putdocx table tbl1 = etable        // captures the last etable
putdocx save report.docx, replace
```

`putpdf` mirrors this API (`putpdf begin` … `putpdf save report.pdf, replace`) for PDF output. Use these when the deliverable is a full narrative document, not just a table.

## 6. Summary-statistics tables

**Built-in `dtable`** (Stata 18+) is the fastest path — see §1.

**`estpost` + `esttab`** (estout suite) for full control over descriptives:

```stata
* summary stats table
estpost summarize price mpg weight, detail
esttab using summ.tex, replace label booktabs nomtitle nonumber ///
    cells("mean(fmt(%9.2f)) sd min max") noobs

* tabstat by group
estpost tabstat price mpg weight, by(foreign) ///
    statistics(mean sd) columns(statistics)
esttab using summ_by.rtf, replace label ///
    cells("mean(fmt(2)) sd(fmt(2))") nostar nonumber

* cross-tab with chi2
estpost tabulate rep78 foreign
esttab using xtab.md, replace md unstack noobs nonumber
```

`estpost` collects results from `summarize`/`tabstat`/`tabulate`/`correlate`/`ttest` etc. into `e()` so `esttab`/`estout` can format them.

## 7. The `r(table)` matrix after estimation

Every estimation command leaves `r(table)` — a matrix with one column per term and these rows: `b`, `se`, `t` (or `z`), `pvalue`, `ll`, `ul` (95% CI), `df`, `crit`, `eform`.

```stata
regress price mpg weight foreign
matrix R = r(table)
matrix list R
scalar b_mpg  = R["b","mpg"]      // coefficient on mpg
scalar p_mpg  = R["pvalue","mpg"]
display %5.3f b_mpg "  (p=" %5.3f p_mpg ")"
```

> **stata-code:** `r(table)` (and `e(b)`, `e(V)`) come back in `results.e.matrices` / `results.r.matrices`. To report "mpg: −49.5 (p=0.42)" in chat, read those rows directly — no file, no `esttab` needed. Reach for an exporter only when the user asks for a saved table file.

Reuse for a custom layout: extract rows from `r(table)`, build a Stata `matrix`, then ship it with `putexcel ... = matrix(...)` or `esttab matrix(...)`.

## Common pitfalls

- **Mixing the two worlds.** `collect`/`etable` and `estout`/`esttab` are independent; styling one won't affect the other. Choose one per document.
- **Forgetting `eststo`/`estimates store`.** `esttab` tables stored estimates — if nothing is stored (or you re-ran the model without re-storing), you get an empty or stale table. Run `eststo clear` at the start of a fresh batch.
- **Extension drives format.** `esttab using x.tex` ≠ `x.rtf` ≠ `x.md`; `collect export` likewise keys off the extension. The wrong extension silently produces the wrong format. Add explicit `tex`/`rtf`/`md`/`csv` to be safe.
- **No `replace`.** Re-running without `replace` errors ("file already exists") or, with `outreg2`/`esttab append`, silently stacks onto the old file. First model: `replace`; subsequent appends: `append`.
- **`r(table)` is overwritten** by the next `r`-class or estimation command. Copy it (`matrix R = r(table)`) immediately if you need it later.
- **Exporting when reading `results.e` would do.** If the user just wants the numbers discussed in chat, pull coefficients/SEs/p-values from `results.e.scalars`/`results.e.matrices` and format them yourself — skip the file entirely. Export only for a deliverable artifact.
- **LaTeX escaping.** `_` and `$` in labels need escaping (`\_`, `\$`) inside `.tex`; `esttab`'s `label` plus `substitute()` handles most cases.
