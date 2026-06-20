# 07 — Data-MCP handoff into Stata

> **Goal:** show how an agent should turn data fetched by an external data MCP
> into a reproducible Stata analysis.

Use this pattern for OpenEcon, World Bank Data360, FRED, OECD, IMF, Eurostat, or
project-specific database MCPs. The data MCP discovers and fetches data;
`stata-code` imports, validates, analyzes, and records the Stata run.

## Step 1: persist the external data pull

The external data MCP should save:

```text
data/raw/oecd_youth_unemployment_pisa_2010_2023.csv
data/raw/oecd_youth_unemployment_pisa_2010_2023.source.json
```

The metadata file should include provider, endpoint or source URL, indicator
IDs, countries, date range, fetch timestamp, units, and any non-Stata
transformations.

## Step 2: import and validate through Stata

**Agent calls:**

```json
{
  "tool": "stata_run",
  "arguments": {
    "code": "version 18\nclear all\nset more off\nlocal raw \"data/raw/oecd_youth_unemployment_pisa_2010_2023.csv\"\nlocal out \"data/derived/oecd_youth_unemployment_pisa_2010_2023.dta\"\ncapture confirm file \"`raw'\"\nif _rc {\n    display as error \"Missing raw data file: `raw'\"\n    exit 601\n}\nimport delimited using \"`raw'\", varnames(1) clear bindquote(strict) encoding(UTF-8)\ncompress\nisid country year\nassert inrange(year, 2010, 2023)\nassert !missing(country, year)\nnotes _dta: Source metadata stored next to raw CSV.\ndatasignature set, reset\nsave \"`out'\", replace",
    "origin_path": "/abs/project/analysis/00_import_oecd.do",
    "origin_kind": "file",
    "persist_log_files": true
  }
}
```

**Agent reads:**

- `ok` and `error.kind` if import/validation failed.
- `dataset.n_obs`, `dataset.n_vars`, and variables.
- run-bundle paths for logs and generated `.dta`.

## Step 3: analyze the derived DTA

**Agent calls:**

```json
{
  "tool": "stata_run",
  "arguments": {
    "code": "use data/derived/oecd_youth_unemployment_pisa_2010_2023.dta, clear\nsummarize youth_unemployment pisa_math\npwcorr youth_unemployment pisa_math, sig obs\ntwoway scatter pisa_math youth_unemployment, xtitle(\"Youth unemployment rate\") ytitle(\"PISA math score\") title(\"OECD countries, 2010-2023\")",
    "origin_path": "/abs/project/analysis/01_scatter_corr.do",
    "origin_kind": "file",
    "persist_log_files": true
  }
}
```

**Agent reads:**

- `results.r.scalars` for correlation/post-command scalars when available.
- `graphs[0].ref` for the scatter plot.
- `log.ref` only if the structured fields do not contain the needed detail.

## Step 4: report provenance

The final answer should cite the metadata file and Stata run bundle, not the
LLM's memory. A good handoff report includes:

- source provider and indicator IDs;
- raw and derived file paths;
- import validation checks;
- Stata commands run;
- graph/table/log artifact paths;
- any missingness, unit, or key warnings.
