# Defensive Stata coding

*Read this when writing or repairing Stata that must be correct and reproducible — not just runnable.*

Stata fails **silently** more often than it errors. A wrong merge, a missing
value that compares as `+∞`, a `=` that should have been `==` — these run
clean and give you wrong numbers. The job is to make mistakes loud. In
`stata-code` terms: prefer code that turns a logic bug into a typed `error`
(rc) you can see, over code that returns a plausible-but-wrong result.

## 1. Missing values are large, not zero

Missing (`.`, `.a`–`.z`) sorts **above every real number**. So a filter that
looks innocent silently keeps missings:

```stata
* WRONG: also keeps everyone with income == . (missing)
keep if income > 100000

* RIGHT: be explicit
keep if income > 100000 & !missing(income)
```

Same trap in `generate`, `replace`, `count if`, and `by` logic. Rules:

- Guard every `>`/`>=` on a variable that can be missing with `& !missing(x)`.
- `egen` row functions: `rowtotal()` treats missing as 0; `rowmean()` ignores
  missings. Know which you want; use `, missing` when you need missing-if-any.
- Recode deliberately: `mvdecode`/`mvencode` and explicit `replace x = . if ...`.

## 2. `==` vs `=`, and `&`/`|`

- `=` assigns (`gen x = 1`); `==` compares (`if x == 1`). Using `=` in an `if`
  is a syntax error (good) — but using `==` where you meant a range is a logic
  bug (silent).
- Combine conditions with `&` / `|`, not `and`/`or`. Parenthesize: `if (a==1 & b==2) | c==3`.
- String compares need quotes: `if name == "CA"`, never `if name == CA`.

## 3. Verify merges — the #1 silent bug

A `merge` that half-matches will quietly produce a dataset where half your
rows have missing covariates. Always state your expectation:

```stata
merge m:1 state year using controls, keepusing(gdp pop)
assert _merge == 3           // every master row matched
* or, if some non-matches are expected:
keep if _merge == 3
drop _merge
```

- Use the modern `1:1` / `m:1` / `1:m` syntax — never bare `merge` (old syntax).
- `assert(match)` / `keep(match)` options encode the same guarantee inline.
- After the merge, `count` and compare to the pre-merge `_N`.

## 4. Assert your assumptions

`assert` turns a wrong assumption into rc 9 (a visible failure) instead of bad
output downstream:

```stata
isid id year                 // panel key is unique (else rc 459/-ish)
assert inrange(age, 0, 120)  // no impossible ages
assert !missing(treatment)   // treatment fully observed
count if price < 0
assert r(N) == 0             // no negative prices
```

Cheap to write, and each one converts a silent data problem into a stop.

## 5. `capture` with care

`capture` swallows the error (and resets `_rc`). Use it for *idempotency*, not
to hide failures:

```stata
capture drop _merge          // fine: "drop if it exists"
capture confirm variable wage
if _rc {
    di as error "wage missing — aborting"
    exit 111
}
```

Never wrap a whole analysis block in `capture` — you lose the typed error that
`stata-code` would otherwise surface. Check `_rc` immediately after a
deliberate `capture`.

## 6. Reproducibility

```stata
version 18                   // pin command behavior to a Stata version
set seed 12345               // before ANY randomness (bootstrap, sample, rng)
set sortseed 12345           // stable tie-breaking in sort/gsort
```

- Set the seed *before* the first random draw, not after.
- `sort` is not stable on ties unless the key is unique or `sortseed` is set —
  this silently changes results that depend on row order. Sort on a unique key
  (`sort id year`) or `isid` it first.
- Avoid global macros for analysis state; they leak across a session. Prefer
  locals (which die with the do-file) — note that in a persistent stata-code
  session, globals persist between `stata_run` calls and can bite you.

## 7. Idempotent do-files

Code that only works on a clean slate is fragile in a persistent session.
Make scripts re-runnable:

```stata
clear all                    // or rely on a fresh stata-code session/reset_session
capture drop result*         // safe re-create
tempvar t                    // auto-dropped names — never collide
tempfile scratch
tempname mymat
```

`tempvar`/`tempfile`/`tempname` give you collision-free, auto-cleaned names —
always prefer them over hardcoded `_tmp1`.

## 8. Loops and `if`/`in`

- The `if` *qualifier* (`summarize x if y>0`) selects rows; the `if`
  *command* (`if "\`x'"==""`) is control flow. Don't confuse them.
- Quote macros in conditionals: `if "\`group'" == "treated"`.
- Empty `foreach`/`forvalues` bodies run zero times silently — log a `count`
  inside if you expect work to happen.

## 9. Let stata-code see the failure

Because every `stata_run` returns a typed `error` block, the most defensive
thing you can do is **not** suppress errors:

- Don't `capture` the command whose success you care about.
- Don't `set more off`-style hacks that mask sub-errors.
- Do let a bad command fail so `error.kind` tells you *why* (see
  `error-codes.md`), then fix the specific cause.

## Checklist before declaring a script correct

- [ ] Every `>`/`>=` on a possibly-missing var has `& !missing(...)`.
- [ ] Every `merge` is followed by an `_merge` assertion or `keep`.
- [ ] Panel/cross-section key verified with `isid`.
- [ ] Seed set before any randomness; sorts are on unique keys.
- [ ] No analysis-critical command hidden inside `capture`.
- [ ] Re-running the script in the same session reproduces the result.
