# Recipe: instrumental variables / 2SLS (turnkey)

*A complete IV pipeline through stata-code: load → first-stage relevance →
2SLS/LIML → weak-instrument and overid diagnostics → publication table →
interpret as a LATE. Mechanics live in [`../econometrics.md`](../econometrics.md)
and the causal framing in [`../causal-inference.md`](../causal-inference.md) §4.
Cross-check the point estimate in StatsPAI via
[`parity-audit.md`](../parity-audit.md).*

IV identifies the **LATE** — the effect for compliers shifted by the instrument —
under relevance, exclusion, monotonicity, and independence. It is *not* the ATE.
Say so when you report.

## 1. Load and name the parts

```text
stata_run(code="use \"data/wage.dta\", clear", session_id="iv")
inspect_data(session_id="iv")
```

Pin down four roles before writing the model:

- **y** — outcome
- **d** — endogenous regressor(s)
- **z** — excluded instrument(s); need `#z ≥ #d` (exactly identified if equal)
- **x** — exogenous controls (included in both stages)

## 2. First stage first — relevance is the load-bearing assumption

Never skip this. A weak first stage makes 2SLS biased toward OLS with badly-sized
tests.

```stata
reg d z x, vce(robust)        // eyeball the instrument's partial F
```

## 3. 2SLS + diagnostics

```stata
* Built-in
ivregress 2sls y x (d = z), vce(robust)
estat firststage              // first-stage / effective F, weak-IV diagnostics
estat endogenous              // Durbin-Wu-Hausman: is d actually endogenous?
estat overid                  // Sargan/Hansen J — only when #z > #d
estimates store iv2sls
```

For high-dimensional fixed effects, clustering, and the Kleibergen-Paap rk
statistic under non-iid errors, use the community `ivreghdfe`
(`install_package(name="ivreghdfe")`, also pulls `ivreg2`, `ftools`):

```stata
ivreghdfe y x (d = z), absorb(unit time) cluster(unit)
* e(widstat) = Kleibergen-Paap F under clustering/robust errors
```

## 4. Weak-instrument decision rule

- One endogenous regressor: compare the **effective F** (Olea-Pflueger) to
  Montiel-Olea / Stock-Yogo critical values — the old "F > 10" rule is not safe.
  `weakivtest` (`install_package(name="weakivtest")`) reports it.
- If F is marginal, report **Anderson-Rubin** weak-IV-robust confidence intervals
  rather than the 2SLS Wald CI (`weakiv`, community; or StatsPAI's
  `anderson_rubin_ci` — see [`parity-audit.md`](../parity-audit.md)).
- Just-identified models are *median-unbiased* under weak instruments, so LIML
  (`ivregress liml`) is a useful robustness column when over-identified.

## 5. Publication table

```stata
* install_package(name="estout")
esttab iv2sls using "iv_results.tex", replace ///
    b(%9.3f) se(%9.3f) star(* 0.10 ** 0.05 *** 0.01) ///
    stats(N widstat, labels("Observations" "First-stage F")) ///
    mtitles("2SLS") title("LATE of d on y") ///
    note("Excluded instrument: z. Robust SEs. Estimand: LATE.")
```

Reporting the first-stage F *in the table* is the discipline that signals you
checked relevance. Swap the extension (`.tex/.rtf/.csv/.md`) for the format the
user wants; see [`publication-tables.md`](publication-tables.md).

## 6. Report

From `results.e.scalars`: the LATE point estimate and CI, the first-stage /
effective F, the endogeneity test result, and (if over-identified) the overid
p-value. State the estimand is a LATE for compliers, and flag explicitly if the
instrument is weak. Offer the StatsPAI cross-check
([`parity-audit.md`](../parity-audit.md)) when robustness matters — `ivreg`
plus `anderson_rubin_ci` there is the independent second implementation.

## Pitfalls (IV-specific)

- **Skipping the first stage.** Always report relevance; a weak IV is worse than
  OLS.
- **Exclusion by assertion.** Exclusion (z affects y *only* through d) is
  untestable — argue it, don't test it. The overid test assumes at least one
  instrument is valid.
- **Wrong estimand.** IV is LATE, not ATE; do not generalize past compliers.
- **Marginal F.** Report effective F and Anderson-Rubin CIs when F is near the
  threshold; do not lean on the 2SLS Wald CI.
- **Controls in one stage only.** Exogenous `x` must appear in *both* stages.
