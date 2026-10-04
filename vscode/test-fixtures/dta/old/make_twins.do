* Regenerates the *_as118.dta twins. Run from this directory with a real Stata:
*     stata-mp -b do make_twins.do
* Each old-format file is read by Stata and saved again in the current format,
* so the twin holds exactly what Stata makes of the original.
local files : dir "." files "*.dta"
foreach f of local files {
    if strpos("`f'", "_as118") continue
    use "`f'", clear
    local stem = subinstr("`f'", ".dta", "", 1)
    save "`stem'_as118.dta", replace
}
