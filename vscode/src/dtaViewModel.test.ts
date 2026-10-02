import { strict as assert } from "node:assert";
import * as fs from "node:fs";
import * as path from "node:path";
import { describe, test } from "node:test";

import { BufferByteSource, DtaReader } from "./dtaReader";
import {
  buildViewerInit,
  formatRows,
  MAX_ROWS_PER_REQUEST,
  valueLabelKeyText,
} from "./dtaViewModel";

const FIXTURES = path.join(__dirname, "..", "test-fixtures", "dta");

async function open(name: string): Promise<DtaReader> {
  const bytes = new Uint8Array(fs.readFileSync(path.join(FIXTURES, name)));
  return DtaReader.open(new BufferByteSource(bytes));
}

function all(reader: DtaReader, useLabels: boolean): Promise<{ rows: string[][] }> {
  return formatRows(reader, {
    start: 0,
    count: 100,
    firstColumn: 0,
    endColumn: reader.meta.nVars,
    useLabels,
  });
}

describe("formatRows", () => {
  // Expected text is Stata 18's own `list, clean noobs` output for the fixture.
  test("matches what Stata's `list` prints, value labels on", async () => {
    const { rows } = await all(await open("modern118.dta"), true);
    const col = (i: number): string[] => rows.map((r) => r[i]);
    assert.deepEqual(col(0), ["1", "2", "3", ".", ".a"]);
    assert.deepEqual(col(1), ["1000", "2000", "3000", "4000", ".z"]);
    assert.deepEqual(col(2), ["100000", "200000", ".b", "400000", "500000"]);
    assert.deepEqual(col(3), [".25", ".", ".75", "1", "1.25"]);
    assert.deepEqual(col(4), [".c", "2.2", "3.3", "4.4", "5.5"]);
    assert.deepEqual(col(5), ["second", "abc", "", "中文", "é"]);
    assert.deepEqual(col(6), ["27mar2020", "28mar2020", "29mar2020", "30mar2020", "31mar2020"]);
    assert.deepEqual(col(7), [
      "14mar2020 15:09:26",
      "14mar2020 15:10:26",
      "14mar2020 15:11:26",
      "14mar2020 15:12:26",
      "14mar2020 15:13:26",
    ]);
    assert.deepEqual(col(8), ["2020m2", "2020m3", "2020m4", "2020m5", "2020m6"]);
    assert.deepEqual(col(9), ["2020q2", "2020q3", "2020q4", "2021q1", "2021q2"]);
    assert.deepEqual(col(10), ["1,234.50", "2,469.00", "3,703.50", "4,938.00", "6,172.50"]);
    assert.deepEqual(col(11), [
      "2020-03-27",
      "2020-03-28",
      "2020-03-29",
      "2020-03-30",
      "2020-03-31",
    ]);
    assert.deepEqual(col(12), ["treated", "control", "treated", "control", "refused"]);
    assert.equal(col(13)[4], "中文 strL");
  });

  test("value labels off shows the underlying codes (`list, nolabel`)", async () => {
    const { rows } = await all(await open("modern118.dta"), false);
    assert.deepEqual(
      rows.map((r) => r[12]),
      ["1", "0", "1", "0", ".a"],
    );
  });

  test("returns only the requested column window", async () => {
    const reader = await open("modern118.dta");
    const block = await formatRows(reader, {
      start: 1,
      count: 2,
      firstColumn: 5,
      endColumn: 7,
      useLabels: true,
    });
    assert.equal(block.start, 1);
    assert.equal(block.firstColumn, 5);
    assert.deepEqual(block.rows, [
      ["abc", "28mar2020"],
      ["", "29mar2020"],
    ]);
  });

  test("clamps hostile or nonsensical requests instead of throwing", async () => {
    const reader = await open("modern118.dta");
    const junk = await formatRows(reader, {
      start: "0; drop",
      count: 1e12,
      firstColumn: -5,
      endColumn: 1e9,
      useLabels: "yes",
    });
    assert.equal(junk.rows.length, 5);
    assert.equal(junk.rows[0].length, reader.meta.nVars);
    assert.ok(MAX_ROWS_PER_REQUEST >= 100);

    const beyond = await formatRows(reader, {
      start: 999,
      count: 10,
      firstColumn: 0,
      endColumn: 3,
      useLabels: true,
    });
    assert.deepEqual(beyond.rows, []);

    const inverted = await formatRows(reader, {
      start: 0,
      count: 10,
      firstColumn: 9,
      endColumn: 2,
      useLabels: true,
    });
    assert.deepEqual(inverted.rows, []);
  });
});

describe("buildViewerInit", () => {
  test("carries the metadata `describe` shows, plus notes and value labels", async () => {
    const reader = await open("modern118.dta");
    const init = buildViewerInit(reader.meta, { title: "modern118.dta", subtitle: "sub" });
    assert.equal(init.type, "init");
    assert.equal(init.title, "modern118.dta");
    assert.equal(init.subtitle, "sub");
    assert.equal(init.nObs, 5);
    assert.equal(init.nVars, 14);
    assert.equal(init.dataLabel, "Fixture dataset");
    assert.deepEqual(init.sortedBy, ["b"]);
    assert.deepEqual(init.notes, ["dataset note one"]);
    assert.deepEqual(init.valueLabels, {
      grplbl: [
        ["0", "control"],
        ["1", "treated"],
        [".a", "refused"],
      ],
    });
    assert.deepEqual(init.valueLabelsTruncated, []);

    const grp = init.variables[12];
    assert.deepEqual(
      { ...grp, width: undefined },
      {
        name: "grp",
        type: "byte",
        format: "%8.0g",
        label: "Treatment group",
        valueLabel: "grplbl",
        numeric: true,
        width: undefined,
        notes: [],
      },
    );
    assert.equal(init.variables[5].numeric, false); // str6
    assert.equal(init.variables[13].numeric, false); // strL
    assert.deepEqual(init.variables[0].notes, ["byte note"]);
    // The payload must survive postMessage's structured clone / JSON.
    assert.deepEqual(JSON.parse(JSON.stringify(init)), init);
  });

  test("column widths stay within sane bounds", async () => {
    const reader = await open("modern118.dta");
    const init = buildViewerInit(reader.meta, { title: "t" });
    for (const v of init.variables) {
      assert.ok(v.width >= 5 && v.width <= 32, `${v.name}: ${v.width}`);
    }
    assert.equal(init.variables[7].width, 18); // %tc
    assert.equal(init.variables[10].width, 12); // %12.2fc
  });

  test("merges caller warnings with the reader's", async () => {
    const reader = await open("modern118.dta");
    const init = buildViewerInit(reader.meta, { title: "t", warnings: ["heads up"] });
    assert.deepEqual(init.warnings, ["heads up"]);
  });
});

describe("valueLabelKeyText", () => {
  test("prints missing-value codes the way Stata does", () => {
    assert.equal(valueLabelKeyText(0), "0");
    assert.equal(valueLabelKeyText(-9), "-9");
    assert.equal(valueLabelKeyText(2147483621), ".");
    assert.equal(valueLabelKeyText(2147483622), ".a");
    assert.equal(valueLabelKeyText(2147483647), ".z");
  });
});
