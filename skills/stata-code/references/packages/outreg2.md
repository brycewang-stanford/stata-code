# outreg2

*Read this when the user requests quick Word/Excel/LaTeX regression tables with
`outreg2`, especially in legacy Stata projects.*

Install: `ssc install outreg2, replace`. Via stata-code:
`install_package(name="outreg2")`.

For new work, prefer built-in `collect`/`etable` or `esttab` when you need more
control. Use `outreg2` when the project already depends on it.

## Basic syntax

```stata
regress y x1 x2
outreg2 using results.doc, replace ctitle("Base") label

regress y x1 x2 x3
outreg2 using results.doc, append ctitle("+ Controls") label
```

Targets include `.doc`, `.xls`, `.tex`, and `.txt`.

## stata-code behavior

When `persist_log_files=true` and `persist_generated_files=true`, exported
tables should be copied into the run bundle's `outputs/` directory. Report the
artifact path, not only the command output.

## Common pitfalls

- Use `replace` for the first model and `append` for later models.
- Do not append to a stale file from a previous run unless that is intentional.
- File extension controls the export format; make it explicit.
- If the user only wants numbers in chat, read `results.e` directly and skip
  exporting a file.
