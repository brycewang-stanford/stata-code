# Data management

*Read this when the task is loading, cleaning, inspecting, merging, reshaping, or aggregating data in Stata.*

Every `stata_run` RunResult carries a structured `dataset` block (`frame`, `n_obs`, `n_vars`, `variables`) — read that instead of parsing the log to confirm row/column counts. For a fuller picture (types, labels, summary stats) call the `inspect_data` MCP tool, which runs `describe` + `codebook` for you.

## Loading and saving

```stata
sysuse auto, clear            // bundled example datasets
sysuse dir                    // list available sysuse datasets
webuse nlswork, clear         // datasets from stata-press.com
use "path/to/data.dta", clear // local .dta; clear drops data in memory
use mpg price if foreign==1 using "data.dta", clear  // selective load

save "out.dta", replace        // replace overwrites silently
save "out.dta", replace orphans // keep value labels for absent vars too
compress                       // shrink storage types before saving
```

`clear` is almost always required before `use`/`import` — Stata refuses to discard unsaved data otherwise. In a stata-code session the in-memory dataset persists across `stata_run` calls, so load once and keep operating.

### Importing / exporting text and Excel

```stata
import delimited "f.csv", clear                         // auto-detect delimiter
import delimited "f.csv", delimiter(tab) varnames(1) clear
import delimited "f.csv", rowrange(2:) colrange(1:5) stringcols(3) clear
import delimited using "f.txt", delimiter(";") encoding("utf-8") clear

import excel "book.xlsx", sheet("Sheet1") firstrow clear
import excel "book.xlsx", cellrange(A1:F100) firstrow clear

export delimited "out.csv", replace
export delimited id y using "out.csv", replace          // subset of vars
export excel "out.xlsx", sheet("res") firstrow(variables) replace
```

Watch numeric-looking strings: `import delimited` will read a column with any non-numeric value as string. Use `stringcols()`/`numericcols()` to force, or fix later with `destring`.

### Frames

Frames are independent in-memory datasets. stata-code maps each **session to one default frame** (`default`); spin up extra frames for side-loads instead of `preserve`/`restore` gymnastics.

```stata
frames dir                    // list frames
frame create aux              // new empty frame
frame copy default backup     // duplicate current frame
frame change aux              // switch active frame
use "lookup.dta", clear       // populates aux
frame change default
frlink m:1 id, frame(aux)     // link frames on a key
frget price, from(aux)        // pull a var across the link
```

## Inspecting

```stata
describe                      // var names, types, formats, labels
describe, short               // just the header (obs/vars/size)
codebook                      // per-var: type, range, missings, examples
codebook, compact             // one row per var — great quick scan
codebook price, detail
summarize                     // mean/sd/min/max for all numerics
summarize price, detail       // percentiles, skewness, kurtosis
tabulate foreign              // one-way freq table
tabulate foreign rep78, missing   // two-way; show missing as a category
tab1 foreign rep78            // one-way tables for several vars
inspect price                 // values/missing/negatives at a glance
misstable summarize           // pattern of missing values across vars
misstable patterns
```

Prefer the `inspect_data` MCP tool over running `describe`/`codebook` yourself when you just need to understand the dataset before acting.

### Integrity checks (fail loud)

```stata
assert price > 0              // errors (rc 9) if any obs violates
assert mpg<. if foreign==0    // assert on a subset
isid id                       // errors if id is not a unique identifier
isid hh_id pid                // composite key uniqueness
duplicates report             // count of duplicated rows
duplicates list id            // show dup rows on key
duplicates drop id, force     // keep first per id (force needed w/ vars)
duplicates tag id, gen(dup)   // dup = # of other rows sharing id
```

`assert` and `isid` raise nonzero return codes, which surface as errors in the RunResult — use them as guardrails after cleaning steps.

## Creating and changing variables

```stata
generate double rev = price*qty          // always type doubles for money/ids
generate byte hi = price > 5000          // 1/0; NOTE: missing>num is TRUE
replace hi = 0 if price >= .             // guard against missing inflation
generate lprice = ln(price)
egen pmean = mean(price)                 // scalar broadcast to all rows
egen ptot  = rowtotal(x1 x2 x3)          // row-wise sum, treats . as 0
egen pmax  = rowmax(x1 x2 x3)
egen grp   = group(make foreign)         // dense integer id from combos
egen tag   = tag(industry year)         // 1 once per industry-year group
egen miss  = rowmiss(x1 x2 x3)
```

`generate` computes element-wise; `egen` calls a named function (often cross-observation). When you want a group statistic, `egen ... , by()`:

```stata
egen mpg_by = mean(mpg), by(foreign)     // group mean, broadcast back
bysort foreign (mpg): gen rank = _n      // ranked within group, sorted
```

### Dedup-safe aggregates with `egen tag`

When a key repeats but you want one row per group (e.g. counting distinct firms):

```stata
egen firsttag = tag(firm)
count if firsttag                        // number of distinct firms
egen nfirm = total(firsttag), by(industry)  // distinct firms per industry
```

### recode, encode/decode, destring/tostring

```stata
recode rep78 (1/2 = 1 "low") (3 = 2 "mid") (4/5 = 3 "high"), gen(quality)
recode age (min/18 = 0) (19/max = 1), gen(adult)

encode make, gen(make_id)                // string -> labeled numeric
decode make_id, gen(make_str)            // labeled numeric -> string
labmask make_id, values(make)            // (community: ssc install labutil)

destring year, replace                   // string "2020" -> numeric
destring price, gen(pricen) force        // force: non-numeric -> missing
destring code, replace ignore("$,")      // strip chars before converting
tostring id, gen(id_s) format(%09.0f)    // numeric -> zero-padded string
```

`destring ... , force` silently nulls anything non-numeric — check the result count via the RunResult `dataset` block before trusting it.

## Labels

```stata
label variable price "Price (USD)"
label define yesno 0 "No" 1 "Yes"
label values foreign yesno
label list yesno                          // show a value label's mappings
label dir                                 // all defined value labels
label define yesno 2 "Unknown", add       // extend an existing label
labelbook                                 // audit value labels (gaps, dups)
```

## merge — the #1 source of silent bugs

Use the modern key-typed syntax. The merge **type asserts the key cardinality on each side** and Stata errors if reality disagrees — that check is your friend.

| Type | Master key | Using key | Use for |
| --- | --- | --- | --- |
| `1:1` | unique | unique | aligning two tables row-for-row |
| `m:1` | repeats | unique | attaching lookup attributes to a panel |
| `1:m` | unique | repeats | the reverse |
| `m:m` | — | — | almost never correct; avoid |

```stata
use panel.dta, clear
merge m:1 country year using gdp.dta, keepusing(gdp pop) assert(match) nogen
```

- `_merge` (created unless `nogen`): `1` master-only, `2` using-only, `3` matched.
- `keepusing(varlist)` pulls only the columns you need — avoids accidental clobbering and bloat.
- `assert(match)` errors unless **every** row matches `3` (rc 9). Strict and ideal when you expect a complete join.
- `keep(match)` keeps only matched rows (inner join). `keep(master match)` = left join.
- `update replace` lets the using data fill/overwrite missing master values (changes `_merge` codes 4/5).

Always verify a merge — it is trivial to silently drop or duplicate rows:

```stata
merge m:1 id using lookup.dta
tab _merge                                 // inspect the join outcome
assert _merge != 2                         // no unexpected using-only rows
drop if _merge==2
drop _merge                                // required before the next merge
```

Confirm `n_obs` in the RunResult `dataset` block matches your expectation after every merge.

## append

Stacks datasets vertically (rows). Variables match by name; type mismatches coerce, missing vars become `.`.

```stata
use wave1.dta, clear
append using wave2.dta wave3.dta, gen(src)   // src flags origin file (0,1,2)
append using extra.dta, keep(id y) force     // force: allow type clashes
```

## reshape — long ↔ wide

`reshape` pivots between **long** (one row per id-by-time) and **wide** (one row per id, time in column names). It is fragile: the data must be uniquely identified and sorted on the `i()`/`j()` keys.

Wide → long (stub `inc` spans `inc1980 inc1981 ...`, `j` is the year):

```stata
* wide:  id  inc1980  inc1981  age
reshape long inc, i(id) j(year)
* long:  id  year  inc  age
```

Long → wide (inverse):

```stata
reshape wide inc, i(id) j(year)
```

Multiple stubs and string `j` values:

```stata
reshape long inc emp, i(id) j(year)
reshape long inc, i(id) j(region) string    // region holds strings, e.g. "N"
```

Rules and gotchas:

- `i()` must uniquely identify a wide-form row; `i() j()` together must uniquely identify a long-form row. Run `isid id year` first.
- Every variable must be either a constant within `i()` or one of the stubs — a stray time-varying var not listed errors out.
- Variable names after the stub must share a consistent suffix pattern; irregular names (`inc_1980`) break detection.
- `reshape error` diagnoses why a reshape failed.

## collapse and contract

`collapse` replaces the dataset with group-level statistics. Default statistic is `mean`.

```stata
collapse (mean) price mpg (sd) sdp=price (sum) totw=weight ///
    (count) n=price (p50) medprice=price, by(foreign rep78)
collapse (mean) price [aw=pop], by(state)    // weighted
```

Common statistics: `mean sd sum count min max median p25 p75 first last`. Rename inline as `newname=oldvar`. **`collapse` discards variable and value labels** and any var not listed or in `by()` — re-label afterward.

`contract` makes a frequency dataset (one row per unique combination):

```stata
contract foreign rep78, freq(n) percent(pct)   // n = count per cell
```

## keep / drop / order / rename

```stata
keep id year price                         // keep only these vars
drop temp*                                 // drop by wildcard
keep if foreign==1                         // keep rows matching condition
drop if missing(price)                     // drop rows
order id year, first                       // move vars to front
order price, after(mpg)
rename oldname newname
rename inc* income*                        // wildcard batch rename
rename (mpg price) (mileage cost)          // group form: positional pairs
```

`keep`/`drop` with a varlist act on **columns**; with `if`/`in` they act on **rows** — don't mix the two intents in one command.

## Sorting

```stata
sort id year                               // ascending, missing last
gsort -price                               // gsort allows -desc per key
gsort +id -year                            // mixed direction
bysort id (year): gen lag = price[_n-1]    // sort inside bysort parens
```

The `not sorted` error (**rc 119**) appears when you use `by varlist:` without the data being sorted on `varlist`. Fixes: use `bysort varlist:` (sorts then runs), or `sort` explicitly first. Subscripting (`x[_n-1]`, `x[_N]`) is only meaningful after an explicit sort — order is otherwise undefined.

## Common pitfalls

- **Unverified merges.** Never trust a `merge` blindly. Use `assert(match)` or `keep()`, `tab _merge`, and confirm `n_obs` in the RunResult `dataset` block. Forgetting to `drop _merge` breaks the next merge.
- **reshape on non-unique ids.** If `i()`/`j()` don't uniquely identify rows, reshape errors or silently mangles. Run `isid` first; use `reshape error` to debug.
- **`destring` on non-numeric.** Plain `destring` refuses columns with non-numeric content; `force` converts them to missing silently. Check the resulting missing count, don't assume.
- **`egen` vs `generate`.** `generate` is element-wise; `egen` calls a function, often across observations. `gen x = mean(price)` is a syntax error (or wrong) — use `egen x = mean(price)`. Inline `sum()` in `generate` is a running cumulative total, not the grand total.
- **`collapse`/`contract` drop labels.** Both rebuild the dataset and strip variable and value labels and any unlisted variable. Re-apply `label variable`/`label values` afterward.
- **Missing is large.** In Stata `.` is bigger than any number, so `price > 5000` is TRUE for missing `price`. Guard conditions with `if !missing(price)` or `if price < .`.
