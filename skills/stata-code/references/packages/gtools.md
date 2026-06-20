# gtools

*Read this when the user works with large datasets and uses (or should use) fast drop-in replacements for `collapse`, `egen`, `contract`, `isid`, `duplicates`, `levelsof`.*

Install: `ssc install gtools`. Via stata-code: `install_package(name="gtools")`. Compiled plugin commands that mirror Stata built-ins but run much faster (often 5–25×) on big data.

The `g`-prefixed commands are designed to return the **same results** as their built-in counterparts, so you can swap them in directly.

## gcollapse — replace collapse

```stata
gcollapse (mean) y (sum) sales (sd) z, by(firm year)
gcollapse (p50) med = x (count) n = x, by(group)
```

Same syntax/statistics as `collapse` (`mean`, `sum`, `sd`, `count`, `min`, `max`, `first`, `last`, `p#`, `median`, …). Dramatically faster with many `by()` groups. Options: `merge` (write results back without collapsing the dataset), `labelformat()`, `wild` for variable wildcards.

## gegen — replace egen

```stata
gegen tag    = tag(firm year)
gegen gid    = group(firm year)
gegen tot    = total(sales), by(firm)
gegen m      = mean(x), by(group)
gegen nvals  = nunique(id), by(firm)   // fast distinct count
```

Covers the common, by-group `egen` functions (`tag`, `group`, `total`, `mean`, `sum`, `count`, `min`, `max`, `sd`, `pctile`, `nunique`, …). Best speedup is on `group()`/`tag()`/`total()` over large data. Some niche `egen` functions are not implemented — fall back to plain `egen` for those.

## Other commands

```stata
gcontract x1 x2, freq(n)          // frequency dataset, replaces contract
gisid firm year                    // assert unique id, replaces isid
gduplicates report                 // replaces duplicates report/drop/tag
gduplicates drop firm year, force
glevelsof group, local(groups)     // distinct values, replaces levelsof
gquantiles q = x, xtile nq(10)     // fast xtile/pctile
gtoplevelsof group                 // most frequent levels
gunique id                         // count distinct
gsort + gstats                      // additional fast utilities
```

- `gcontract` — collapse to a frequency table of the listed variables.
- `gisid` — error if the variable list is not a unique identifier.
- `gduplicates` — `report` / `tag` / `drop` / `list`, same subcommands as `duplicates`.
- `glevelsof` — store distinct values in a local/global; supports `clean`, `separate()`, `local()`.
- `gquantiles` (alias `gpctile`/`gxtile`) — quantile/bin assignment.

## Why use it

- **`by()` performance:** the speedup grows with the number of `by()` groups and rows — this is the main reason to reach for gtools on millions of observations.
- **Drop-in:** results match the built-ins, so converting `collapse`→`gcollapse` etc. is mechanical.
- **No external dependencies** beyond the compiled plugin shipped with the package.

## Pitfalls

- **Needs install** (`ssc install gtools`) and a supported platform for the compiled plugin; if it won't load, fall back to the built-ins.
- **Slightly different option support.** Not every built-in option/edge case is implemented (e.g. some exotic `egen` functions, certain `collapse` weighting/format quirks). Check the help if an option errors and use the built-in for that step.
- **Memory vs speed tradeoff.** `gcollapse` is fast partly by using extra RAM; on memory-constrained machines use `nochecks`/`forcestrl` cautiously or chunk the data. For modest datasets the built-ins are fine and avoid the dependency.
- Results match the built-ins, but floating-point tie-breaking in percentiles/`group()` ordering can differ trivially — don't expect bit-identical sort order, only equivalent values.
- gtools commands write their own returns; downstream code expecting specific `r()`/`e()` scalars from the built-in should be checked.
