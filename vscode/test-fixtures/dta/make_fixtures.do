* Regenerates the .dta fixtures the VS Code dta-reader tests parse.
*
* Run from this directory with a real Stata (>= 18 for the alias fixture):
*     do make_fixtures.do
*
* The fixtures are committed so CI, which has no Stata, can run the tests.
* If this file changes, update the expectations in vscode/src/dtaReader.test.ts.

version 16
clear all
set obs 5

* --- numeric storage types, with system and extended missing values --------
generate byte b = _n
replace b = . in 4
replace b = .a in 5

generate int i = _n * 1000
replace i = .z in 5

generate long l = _n * 100000
replace l = .b in 3

generate float f = _n / 4
replace f = . in 2

generate double d = _n * 1.1
replace d = .c in 1

* --- fixed-width strings: full width, short, empty, and non-ASCII ----------
generate str6 s = ""
replace s = "second" in 1
replace s = "abc" in 2
replace s = "中文" in 4
replace s = "é" in 5

* --- display formats -------------------------------------------------------
generate long day = 22000 + _n
format day %td
generate double stamp = clock("2020-03-14 15:09:26", "YMDhms") + (_n - 1) * 60000
format stamp %tc
generate int month = 720 + _n
format month %tm
generate int quarter = 240 + _n
format quarter %tq
generate double money = _n * 1234.5
format money %12.2fc
generate long iso = 22000 + _n
format iso %tdCCYY-NN-DD

* --- value labels, including a label on an extended missing value ----------
generate byte grp = mod(_n, 2)
replace grp = .a in 5
label define grplbl 0 "control" 1 "treated" .a "refused"
label values grp grplbl

* --- labels, notes, sort order ---------------------------------------------
label variable b "A byte"
label variable s "字符串 label"
label variable grp "Treatment group"
label data "Fixture dataset"
note: dataset note one
note b: byte note
sort b

preserve
* Format 115 predates strL, so it is saved before the strL variable exists.
* (Stata 18 writes 115 for both version(11) and version(12); 114 differs from
* 115 only in its version byte.)
saveold "legacy115.dta", version(12) replace
restore

* --- strL: short, long, empty, and a duplicate that Stata may cross-link ---
generate strL big = ""
replace big = "short" in 1
replace big = 3000 * "x" in 2
replace big = "short" in 4
replace big = "中文 strL" in 5

save "modern118.dta", replace
saveold "strl117.dta", version(13) replace

* --- an empty dataset with variables but no observations -------------------
drop in 1/5
save "empty118.dta", replace

* --- format 120: an alias variable (Stata 18+) -----------------------------
clear all
frame create other
frame other: set obs 3
frame other: generate long id = _n
frame other: generate double val = _n * 2.5
set obs 3
generate long id = _n
generate byte own = 7
frlink 1:1 id, frame(other)
fralias add val, from(other)
save "alias120.dta", replace
