# Core Stata syntax

*Read this when the task is about Stata's command grammar, macros, missing values, `if`/`in`, `by`, loops, factor/time-series notation, or comment/prefix mechanics.*

## Command skeleton

Almost every Stata command follows one grammar:

```
[prefix:] command [varlist] [=exp] [if] [in] [weight] [, options]
```

```stata
bysort group: regress y x1 x2 if year>=2000 & !missing(y) [aw=pop], robust
```

- **prefix** — `by:`, `bysort:`, `capture`, `quietly`, `noisily`, `statsby:`, etc.
- **varlist** — variables; supports `*` wildcards and `x1-x4` ranges (`vars in storage order`).
- **=exp** — assignment, e.g. `generate z = x + y`.
- **if / in** — row qualifiers (below).
- **weight** — `[fw=]` frequency, `[aw=]` analytic, `[pw=]` probability, `[iw=]` importance. Not every command accepts every type.
- **options** — after the single comma; abbreviatable.

Order matters: `if` then `in` then `weight` then `, options`. Stored results from the command return in each RunResult's `results.r` / `results.e` block—read those instead of grepping the log.

## Abbreviations & `version`

Commands, variables, and options can be abbreviated to any unambiguous prefix: `reg`=`regress`, `su`=`summarize`, `gen`=`generate`. **In committed scripts spell them out**—an abbreviation that's unique today can collide after a new variable or ado is added.

`version` pins interpreter semantics for reproducibility. Put it first in any do-file:

```stata
version 18      // run under Stata 18 syntax rules
version 14: reg y x   // run a single command under old semantics
```

Without it, behavior can change across Stata releases. Forgetting `version` is a top reproducibility bug.

## Macros

Macros are named text substituted before a command runs.

| Kind | Set with | Reference | Scope |
| --- | --- | --- | --- |
| local | `local name ...` | `` `name' `` | current do-file / program only |
| global | `global name ...` | `$name` or `${name}` | entire session |

Prefer **locals**—globals leak across files and collide silently.

```stata
local controls age educ income
local n = 5
regress y x `controls'           // expands to: age educ income
display "n is `n'"

global path "/data"
use "$path/wages.dta", clear
```

### Evaluate an expression: `` `=exp' ``

`` `=...' `` computes a value at parse time and inserts the result:

```stata
local k = 3
display "k squared is `=`k'^2'"   // -> k squared is 9
gen flag = (income > `=r(mean)')  // r(mean) from a prior summarize
```

### Macro expansion details

- A local that doesn't exist expands to **empty string** (no error)—a common silent bug.
- ``` `: ... ` ``` are extended macro functions, e.g. `` local nvars : word count `controls' ``, `` local lbl : variable label x ``.
- Nesting works: `` `var`i'' `` builds `var1`, `var2`, …

### Compound double quotes

Use `` `"..."' `` when the text itself may contain `"`. They nest where plain `"..."` cannot:

```stata
local title `"He said "hi" to me"'
display `"`title'"'
```

Reach for compound quotes whenever building strings that embed quotes or other macros.

## Missing values

Numeric missing is `.` (system missing) plus extended `.a` `.b` … `.z`. **Ordering is the trap:**

```
all real numbers  <  .  <  .a  <  .b  < ... < .z
```

So missing is **larger than every number**. `if x>1000` silently keeps missing rows:

```stata
* WRONG: includes x==. because . > 1000
gen big = 1 if x>1000

* RIGHT: exclude missing explicitly
gen big = 1 if x>1000 & !missing(x)
```

Use `missing(x)` / `!missing(x)` (or `x>=.` / `x<.`) rather than `x==.`, since `x==.` misses `.a`–`.z`. For strings, missing is the empty string `""`.

### `==` vs `=`

- `=` assigns: `gen y = 2`, `replace y = 3`.
- `==` tests equality: `if x==2`, `gen d = (x==2)`.

Writing `if x=2` is a syntax error; writing `gen d = x==2 & y==1` is fine (a 0/1 indicator).

## `if` and `in` qualifiers

```stata
summarize income if age>=18 & !missing(income)
list make price in 1/10        // first 10 rows in current sort order
list make price in -5/L        // last 5 rows; f=first, L=last
```

`in` is **positional** and depends on the current sort. `if` is logical.

### Qualifier `if` vs programming `if`

These are different constructs:

```stata
* if QUALIFIER: restrict rows a command acts on
regress y x if treat==1

* if COMMAND: branch on a single scalar condition (programs/do-files)
if _N < 100 {
    display "small sample"
}
else {
    display "ok"
}
```

The `if` *command* evaluates one scalar truth value once—it does **not** loop over observations. To act per row, use the `if` *qualifier*.

## `by`, `bysort`, `_n`, `_N`, `egen`

`by` repeats a command within groups; data must be sorted on the by-variables (`bysort` sorts first).

```stata
bysort id (year): gen lag_y = y[_n-1]   // within id, ordered by year
bysort id: egen mean_y = mean(y)        // group mean, broadcast to all rows
```

- `_n` = current observation number (within the by-group when under `by:`).
- `_N` = total obs (within the by-group under `by:`).
- `y[_n-1]` / `y[_N]` index other rows; out-of-range subscripts yield missing.

The `(year)` in parentheses sets sort order **without** making `year` a by-group—essential for correct lags.

`egen` adds group/row functions that `generate` lacks: `mean()`, `total()`, `sd()`, `min()`, `max()`, `count()`, `rank()`, `tag()`, `group()`, `rowmean()`, `rowtotal()`.

## generate / replace / egen

```stata
gen double rate = num/den           // double precision
replace rate = 0 if missing(rate)
gen byte adult = age>=18 & !missing(age)
egen pid = group(state county)      // dense integer id from a key
```

- `generate` creates; `replace` modifies existing. Optional storage type (`byte double str20 …`) goes right after the keyword.
- `egen` is for the functions above; for plain arithmetic use `generate`.

Full data-management coverage is in data-management.md.

## Loops

### `foreach` — iterate a list

```stata
foreach v of varlist x1 x2 x3 {
    summarize `v'
}
foreach c of local controls {        // over a local's words
    display "`c'"
}
foreach n of numlist 1 5 10/12 {     // 1 5 10 11 12
    display `n'
}
foreach w in alpha beta gamma {      // literal words
    display "`w'"
}
```

List types: `of varlist`, `of local`, `of global`, `of numlist`, `of newlist`, or `in` for literal tokens.

### `forvalues` — numeric range

```stata
forvalues i = 1/10 {
    gen v`i' = .
}
forvalues y = 2000(5)2020 {          // 2000 2005 2010 2015 2020
    display `y'
}
```

### `while` — condition loop

```stata
local i = 1
while `i' <= 5 {
    display `i'
    local ++i
}
```

The open brace `{` must be at the end of the loop line; the closing `}` on its own line.

### `levelsof` — distinct values into a macro

```stata
levelsof region, local(regs)
foreach r of local regs {
    summarize income if region==`r'
}
```

`levelsof` also stores them in `r(levels)` (in `results.r`).

## Factor variables & time-series operators

Factor-variable notation (no need to pre-create dummies) works in modeling commands:

| Notation | Meaning |
| --- | --- |
| `i.var` | indicators for each level (one omitted as base) |
| `c.var` | treat as continuous (default for a bare var) |
| `i.a#i.b` | interaction only |
| `i.a##i.b` | main effects + interaction (factorial) |
| `c.x#c.x` | `x` squared |
| `ib2.var` | set level 2 as base |

```stata
regress y i.region c.age c.age#c.age i.sex##i.region
```

Time-series operators require `tsset`/`xtset` first:

| Op | Meaning |
| --- | --- |
| `L.x` | lag (`L2.x` = 2 periods back) |
| `F.x` | lead |
| `D.x` | first difference (`x - L.x`) |
| `S.x` | seasonal difference |

```stata
tsset year
regress y L.y L(1/3).x D.z
```

## Prefixes: `capture`, `quietly`, `noisily`

```stata
capture drop tempvar        // swallow the error if tempvar doesn't exist
quietly summarize income    // suppress output; r() still populated
noisily display r(mean)     // force output inside a quiet block
```

- `capture` runs the command, suppresses output, and traps any error into `_rc` (0 = success). Test with `if _rc { ... }`.
- `quietly` hides output but keeps stored results—useful in scripts; `results.r`/`results.e` still come back in the RunResult.
- `noisily` re-enables output within an otherwise quiet context.

### `assert` and `confirm`

Fail fast on broken assumptions:

```stata
assert age>=0 & age<120 if !missing(age)   // error + nonzero _rc if false
assert _N==1000
confirm variable income                     // error unless var exists
confirm numeric variable age
confirm file "wages.dta"
```

Pair with `capture` to branch: `capture confirm variable z` then `if _rc { ... }`.

## Comments

```stata
* whole-line comment (only at start of a line)
summarize income   // end-of-line comment, needs preceding whitespace
regress y x1 ///
        x2 x3      // /// continues the command onto the next line
/* block
   comment */
gen z = x /* inline */ + y
```

- `*` only starts a comment at the beginning of a line.
- `//` needs whitespace before it; `///` both comments and continues the logical line.
- `/* */` works inline and across lines.

## Common pitfalls

- **Missing as +∞**: `if x>k` silently includes `.`/`.a`–`.z`. Always add `& !missing(x)` (or use `x<.`).
- **`=` vs `==`**: `=` assigns, `==` compares. `if x=1` is a syntax error; an unintended `=` elsewhere corrupts data.
- **`x==.` misses extended missings**: use `missing(x)` to catch `.a`–`.z` too.
- **Unquoted strings**: string literals and file paths with spaces must be quoted (`"New York"`, `"my data.dta"`); use compound quotes `` `"..."' `` when the text contains quotes.
- **Empty local = empty string**: a misspelled or unset local expands to nothing with no error—double-check macro names.
- **Global name collisions**: globals persist for the whole session and clobber each other across files. Prefer locals.
- **Forgetting `version`**: omit it and results may silently change across Stata releases. Pin it at the top of every do-file.
- **`bysort id: gen lag=y[_n-1]` without `(sortvar)`**: lags depend on row order—use `bysort id (year):` to fix the order explicitly.
