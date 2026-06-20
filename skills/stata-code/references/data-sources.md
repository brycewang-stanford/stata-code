# Reference: public data sources and provenance

*Use this file when the user asks where a macro, policy, or cross-country panel
should come from before Stata analysis starts. `stata-code` is the Stata execution
layer; live discovery and download belong to data MCPs, APIs, browser tools, or
files the user already has.*

## Division of labor

| Layer | Responsibility |
| --- | --- |
| Data source / data MCP | Query indicators, series, countries, releases, and metadata |
| Handoff file | Persist the exact returned rows under `data/raw/` or another project path |
| `stata-code` | Import, validate, transform, estimate, graph, and export tables |
| Run bundle | Preserve logs, graph refs, structured `r()` / `e()`, origin metadata |

Do not present an LLM-recalled number as data. If a value matters empirically,
fetch it through a real source, save it, and record provenance.

## What to preserve

For every external pull, keep a small metadata note next to the raw file:

```json
{
  "provider": "OECD",
  "endpoint_or_mcp": "OECD MCP",
  "query": "youth unemployment and PISA math, OECD countries, 2010-2023",
  "indicator_ids": ["..."],
  "countries": ["..."],
  "fetched_at_utc": "2026-06-20T00:00:00Z",
  "units": {"youth_unemp": "percent", "pisa_math": "score"},
  "license_or_terms": "source metadata link or note"
}
```

Then hand the saved file to Stata with the protocol in
[`data-mcp-handoff.md`](data-mcp-handoff.md).

## Source selection heuristics

| Research need | Prefer |
| --- | --- |
| US macro or financial time series | FRED or original agency releases |
| World Bank indicators and cross-country panels | World Bank / Data360-style structured APIs |
| OECD country education, labor, health, inequality | OECD SDMX-backed data |
| Euro-area or EU social statistics | Eurostat |
| IMF macro-financial indicators | IMF data APIs |
| Chinese microdata or restricted panels | User-provided, licensed, local files with codebook notes |

When two sources overlap, prefer the one with the clearest unit definition,
revision policy, and country/time coverage for the estimand. Never silently mix
sources with different units or vintages.

## Before Stata estimation

1. Save raw data unchanged.
2. Save source metadata.
3. Import into Stata and run `inspect_data`.
4. Assert keys: `isid country year`, `duplicates report`, or the relevant panel key.
5. Check units and missingness before reshaping or merging.
6. Save a derived `.dta` that the analysis do-file reads from.

This keeps the empirical chain auditable: source query → raw file → derived Stata
dataset → estimation run bundle.
