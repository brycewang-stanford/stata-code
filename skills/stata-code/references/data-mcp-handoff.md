# Data-MCP to Stata handoff

*Read this when data is discovered or fetched by an external MCP server such as
OpenEcon, World Bank Data360, FRED, OECD, IMF, Eurostat, or a project-specific
database MCP, and the next step is Stata analysis through `stata-code`.*

`stata-code` should not become a data-provider aggregator. Let data MCP servers
discover and fetch official data; then make the handoff into Stata reproducible,
auditable, and source-attributed.

## Handoff contract

Every external data pull should create or identify:

| Artifact | Purpose |
| --- | --- |
| Raw data file | CSV, TSV, XLSX, or DTA saved under `data/raw/` or another stable path |
| Metadata file | Source URL/API endpoint, provider, indicator IDs, countries, date range, fetch time |
| Import do-file | Stata code that imports raw data and writes a derived `.dta` |
| Validation checks | Key uniqueness, range checks, missingness, units, and source counts |
| Analysis file | Separate do-file that starts from the derived `.dta`, not from a live MCP call |

Avoid one-off browser copies. If the agent fetched data through another MCP, ask
it to persist the returned table and source metadata before running Stata.

## Directory pattern

```text
data/
  raw/
    oecd_youth_unemployment_pisa_2010_2023.csv
    oecd_youth_unemployment_pisa_2010_2023.source.json
  derived/
    oecd_youth_unemployment_pisa_2010_2023.dta
analysis/
  00_import_oecd.do
  01_scatter_corr.do
```

## Stata import template

```stata
version 18
clear all
set more off

local raw "data/raw/oecd_youth_unemployment_pisa_2010_2023.csv"
local out "data/derived/oecd_youth_unemployment_pisa_2010_2023.dta"

capture confirm file "`raw'"
if _rc {
    display as error "Missing raw data file: `raw'"
    exit 601
}

import delimited using "`raw'", varnames(1) clear bindquote(strict) encoding(UTF-8)
compress

* Replace these with dataset-specific keys from the metadata.
isid country year
assert inrange(year, 2010, 2023)
assert !missing(country, year)

notes _dta: Source metadata stored next to the raw file.
datasignature set, reset
save "`out'", replace
```

Run this through `stata_run` with `origin_path` and `persist_log_files=true` so
the import log and any generated `.dta` are captured in a run bundle.

## Analysis template

```stata
version 18
clear all
set more off

use data/derived/oecd_youth_unemployment_pisa_2010_2023.dta, clear

summarize youth_unemployment pisa_math
pwcorr youth_unemployment pisa_math, sig obs

twoway scatter pisa_math youth_unemployment, ///
    xtitle("Youth unemployment rate") ///
    ytitle("PISA math score") ///
    title("OECD countries, 2010-2023")
```

Read correlation results from `results.r.scalars` when available, and graph refs
from `graphs`. Fetch graph bytes only if the user wants to inspect or save the
figure.

## Validation checklist

- Source metadata includes provider, endpoint or URL, query terms, date range,
  fetch timestamp, units, and any transformations done outside Stata.
- Raw file is immutable for the analysis; transformations happen in Stata or in
  a separate tracked script.
- Keys are asserted with `isid` or `duplicates report`.
- Units are explicit: percent vs percentage points, local currency vs real USD,
  index base years, seasonal adjustment, frequency.
- Missing values are counted before modeling.
- The derived `.dta` is recreated from raw data by a do-file.
- The run bundle records import and analysis logs separately.

## What not to do

- Do not cite an LLM's memory as the data source.
- Do not let each model run re-query live data unless the user asked for a
  live-now analysis.
- Do not paste large tables into the prompt when a raw file can be saved.
- Do not claim `stata-code` fetched official data directly unless the fetch was
  performed by a documented tool or script in this repo.
