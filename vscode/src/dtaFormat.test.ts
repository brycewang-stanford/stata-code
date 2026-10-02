import { strict as assert } from "node:assert";
import { describe, test } from "node:test";

import { formatStataNumber, formatWidthHint, isDateFormat } from "./dtaFormat";

// Expected strings are what Stata 18 prints for the same value and format
// (`list` on test-fixtures/dta/modern118.dta, and `display %fmt value`).

describe("date and time formats", () => {
  test("%td", () => {
    assert.equal(formatStataNumber(22001, "%td"), "27mar2020");
    assert.equal(formatStataNumber(0, "%td"), "01jan1960");
    assert.equal(formatStataNumber(-1, "%td"), "31dec1959");
  });

  test("%tc", () => {
    const base = Date.UTC(2020, 2, 14, 15, 9, 26) - Date.UTC(1960, 0, 1);
    assert.equal(formatStataNumber(base, "%tc"), "14mar2020 15:09:26");
    assert.equal(formatStataNumber(base + 240000, "%tc"), "14mar2020 15:13:26");
  });

  test("%tm, %tq, %th, %ty, %tw", () => {
    assert.equal(formatStataNumber(721, "%tm"), "2020m2");
    assert.equal(formatStataNumber(725, "%tm"), "2020m6");
    assert.equal(formatStataNumber(241, "%tq"), "2020q2");
    assert.equal(formatStataNumber(244, "%tq"), "2021q1");
    assert.equal(formatStataNumber(121, "%th"), "2020h2");
    assert.equal(formatStataNumber(2020, "%ty"), "2020");
    assert.equal(formatStataNumber(3120, "%tw"), "2020w1");
    assert.equal(formatStataNumber(-1, "%tm"), "1959m12");
  });

  test("custom detail codes", () => {
    assert.equal(formatStataNumber(22001, "%tdCCYY-NN-DD"), "2020-03-27");
    assert.equal(formatStataNumber(22001, "%tdDD/NN/YY"), "27/03/20");
    assert.equal(formatStataNumber(22001, "%tdMonth_dd,_CCYY"), "March 27, 2020");
    assert.equal(formatStataNumber(22001, "%tdDayname"), "Friday");
    assert.equal(formatStataNumber(22001, "%tdww"), "13");
    assert.equal(formatStataNumber(3171, "%tw"), "2020w52");
    const clock = 86400000 * 22001 + 3723456;
    assert.equal(formatStataNumber(clock, "%tcHH:MM:SS.sss"), "01:02:03.456");
    assert.equal(formatStataNumber(clock + 12 * 3600000, "%tcDDmonCCYY_hh:MM_am"), "27mar2020 1:02 pm");
    assert.equal(formatStataNumber(721, "%tmMon_CCYY"), "Feb 2020");
    assert.equal(formatStataNumber(721, "%tmCCYY!mNN"), "2020m02");
  });

  test("the pre-Stata-10 %d spelling and left-justified variants", () => {
    assert.equal(formatStataNumber(22001, "%d"), "27mar2020");
    assert.equal(formatStataNumber(22001, "%-td"), "27mar2020");
  });

  test("values outside Stata's date range print as plain numbers", () => {
    assert.equal(formatStataNumber(1e12, "%td"), "1.00e+12");
  });

  test("isDateFormat", () => {
    assert.ok(isDateFormat("%td"));
    assert.ok(isDateFormat("%tcDDmonCCYY"));
    assert.ok(isDateFormat("%d"));
    assert.ok(!isDateFormat("%9.0g"));
    assert.ok(!isDateFormat("%9s"));
  });
});

describe("numeric formats", () => {
  test("%f with and without commas", () => {
    assert.equal(formatStataNumber(1234.5, "%12.2fc"), "1,234.50");
    assert.equal(formatStataNumber(6172.5, "%12.2fc"), "6,172.50");
    assert.equal(formatStataNumber(1234.5, "%9.2f"), "1234.50");
    assert.equal(formatStataNumber(-1234567.891, "%15.1fc"), "-1,234,567.9");
    assert.equal(formatStataNumber(0.25, "%9.2f"), "0.25");
    assert.equal(formatStataNumber(3, "%9.0f"), "3");
  });

  test("%g drops the leading zero and trims to the width", () => {
    assert.equal(formatStataNumber(0.25, "%9.0g"), ".25");
    assert.equal(formatStataNumber(-0.5, "%9.0g"), "-.5");
    assert.equal(formatStataNumber(2.2, "%10.0g"), "2.2");
    assert.equal(formatStataNumber(3.3000000000000003, "%10.0g"), "3.3");
    assert.equal(formatStataNumber(1000, "%8.0g"), "1000");
    assert.equal(formatStataNumber(100000, "%12.0g"), "100000");
    assert.equal(formatStataNumber(0, "%9.0g"), "0");
    assert.equal(formatStataNumber(1 / 3, "%9.0g"), ".3333333");
    assert.equal(formatStataNumber(1234567.891, "%9.0g"), "1234568");
    assert.equal(formatStataNumber(1 / 3, "%10.0g"), ".33333333");
    assert.equal(formatStataNumber(0.000012345, "%9.0g"), ".0000123");
  });

  test("%w.dg with d > 0 caps significant digits but keeps the integer part", () => {
    assert.equal(formatStataNumber(1234.5678, "%9.3g"), "1235");
    assert.equal(formatStataNumber(0.00012345, "%9.3g"), ".000123");
  });

  test("%g falls back to exponent notation when the value cannot fit", () => {
    assert.equal(formatStataNumber(1.5e20, "%9.0g"), "1.50e+20");
    assert.equal(formatStataNumber(1.234e-15, "%9.0g"), "1.23e-15");
    assert.equal(formatStataNumber(123456789, "%9.0g"), "1.23e+08");
    assert.equal(formatStataNumber(123456789012, "%10.0g"), "1.235e+11");
    assert.equal(formatStataNumber(12345678, "%8.0g"), "1.2e+07");
  });

  test("%gc groups thousands", () => {
    assert.equal(formatStataNumber(1234567, "%12.0gc"), "1,234,567");
  });

  test("%e", () => {
    assert.equal(formatStataNumber(123456, "%10.3e"), "1.235e+05");
  });

  test("European decimal comma", () => {
    assert.equal(formatStataNumber(1234.5, "%12,2fc"), "1.234,50");
  });

  test("unrecognized formats still print the number", () => {
    assert.equal(formatStataNumber(2.5, "%21x"), "2.5");
    assert.equal(formatStataNumber(7, ""), "7");
  });
});

describe("formatWidthHint", () => {
  test("uses the format's own width", () => {
    assert.equal(formatWidthHint("%9.0g", "float"), 9);
    assert.equal(formatWidthHint("%12.2fc", "double"), 12);
    assert.equal(formatWidthHint("%20s", "str20"), 20);
    assert.equal(formatWidthHint("%-20s", "str20"), 20);
  });

  test("measures date formats by what they print", () => {
    assert.equal(formatWidthHint("%td", "long"), 9);
    assert.equal(formatWidthHint("%tc", "double"), 18);
    assert.equal(formatWidthHint("%tdCCYY-NN-DD", "long"), 10);
  });
});
