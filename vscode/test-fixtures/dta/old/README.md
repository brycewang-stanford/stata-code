# Old-format fixtures (formats 102 to 111, Stata 1 to 7)

Stata 18 cannot write these formats, so the files here are not ours.

- `stata-compat-*.dta`, `stata1_*.dta`, `stata4_*.dta` and
  `stata_int_validranges_*.dta` come from the pandas test suite
  (`pandas/tests/io/data/stata/`), copyright the pandas development team and
  distributed under the BSD 3-Clause License in `LICENSE-pandas`. They are
  unmodified.
- `*_as118.dta` is each of those files as Stata 18 reads it and saves it
  again (`use`, then `save`). `make_twins.do` regenerates them. They are the
  reference: `dtaReader.test.ts` requires the viewer to read every cell,
  name, label and value label of an old file the way Stata does.

Never edit any of these by hand.
