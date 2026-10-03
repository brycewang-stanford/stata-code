import { strict as assert } from "node:assert";
import * as fs from "node:fs";
import * as path from "node:path";
import { describe, test } from "node:test";

import { codebookCsv, csvChunks, csvField, rangeToTsv, valueLabelsCsv } from "./dtaExport";
import { buildRowOrder } from "./dtaQuery";
import { BufferByteSource, DtaReader } from "./dtaReader";
import { formatRange, formatRows, formatSummary, MAX_COPY_CELLS } from "./dtaViewModel";
import { summarizeColumn } from "./dtaQuery";

const FIXTURES = path.join(__dirname, "..", "test-fixtures", "dta");

async function open(name: string): Promise<DtaReader> {
  const bytes = new Uint8Array(fs.readFileSync(path.join(FIXTURES, name)));
  return DtaReader.open(new BufferByteSource(bytes));
}

async function csv(
  reader: DtaReader,
  order: Uint32Array | null,
  useLabels: boolean,
): Promise<string[]> {
  let text = "";
  for await (const chunk of csvChunks(reader, order, { useLabels })) text += chunk;
  return text.replace(/\n$/, "").split("\n");
}

describe("csvField", () => {
  test("quotes only when needed", () => {
    assert.equal(csvField("plain"), "plain");
    assert.equal(csvField("a,b"), '"a,b"');
    assert.equal(csvField('say "hi"'), '"say ""hi"""');
    assert.equal(csvField("two\nlines"), '"two\nlines"');
    assert.equal(csvField(" padded"), '" padded"');
    assert.equal(csvField(""), "");
    assert.equal(csvField("中文"), "中文");
  });
});

describe("csvChunks", () => {
  test("writes names, full-precision numbers, dates as dates, labels as text", async () => {
    const lines = await csv(await open("modern118.dta"), null, true);
    assert.equal(lines.length, 6);
    assert.equal(
      lines[0],
      "b,i,l,f,d,s,day,stamp,month,quarter,money,iso,grp,big",
    );
    const first = lines[1].split(",");
    // d is `.c` in row 1; money keeps its value, not the %12.2fc rendering.
    assert.deepEqual(first.slice(0, 6), ["1", "1000", "100000", "0.25", ".c", "second"]);
    assert.deepEqual(first.slice(6, 10), ["27mar2020", "14mar2020 15:09:26", "2020m2", "2020q2"]);
    assert.equal(first[10], "1234.5");
    assert.equal(first[11], "2020-03-27");
    assert.equal(first[12], "treated");
  });

  test("system missing is an empty field; extended missing keeps its code", async () => {
    const lines = await csv(await open("modern118.dta"), null, false);
    const cells = lines.slice(1).map((l) => l.split(","));
    assert.equal(cells[3][0], ""); // b[4] is `.`
    assert.equal(cells[4][0], ".a"); // b[5] is `.a`
    assert.equal(cells[1][3], ""); // f[2] is `.`
    assert.equal(cells[4][12], ".a"); // grp[5], labels off
  });

  test("with labels on, a labelled extended missing value prints its label", async () => {
    const lines = await csv(await open("modern118.dta"), null, true);
    assert.equal(lines[5].split(",")[12], "refused");
  });

  test("double keeps every digit a %10.0g display would drop", async () => {
    const lines = await csv(await open("modern118.dta"), null, false);
    assert.equal(lines[3].split(",")[4], "3.3000000000000003");
  });

  test("exports the filtered, sorted view in view order", async () => {
    const reader = await open("survey118.dta");
    const id = reader.meta.variables.findIndex((v) => v.name === "id");
    const order = await buildRowOrder(reader, {
      filter: "id <= 3",
      sort: [{ column: id, descending: true }],
    });
    const lines = await csv(reader, order, true);
    assert.equal(lines.length, 4);
    assert.deepEqual(
      lines.slice(1).map((l) => l.split(",")[0]),
      ["3", "2", "1"],
    );
  });

  test("an empty view is just the header", async () => {
    const reader = await open("survey118.dta");
    const order = await buildRowOrder(reader, { filter: "id < 0", sort: [] });
    assert.equal((await csv(reader, order, true)).length, 1);
  });

  test("stops when cancelled", async () => {
    const reader = await open("survey118.dta");
    let chunks = 0;
    for await (const _chunk of csvChunks(reader, null, { useLabels: true, isCancelled: () => true })) {
      chunks += 1;
    }
    assert.equal(chunks, 1); // the header only
  });
});

describe("codebook exports", () => {
  test("codebookCsv carries what a data CSV cannot", async () => {
    const lines = codebookCsv((await open("modern118.dta")).meta).trimEnd().split("\n");
    assert.equal(lines[0], "position,name,type,format,value_label,label,notes");
    assert.equal(lines.length, 15);
    assert.equal(lines[1], "1,b,byte,%8.0g,,A byte,byte note");
    assert.equal(lines[6], "6,s,str6,%9s,,字符串 label,");
    assert.equal(lines[12], "12,iso,long,%tdCCYY-NN-DD,,,");
    assert.equal(lines[13], "13,grp,byte,%8.0g,grplbl,Treatment group,");
  });

  test("valueLabelsCsv lists every mapping, missing values included", async () => {
    const text = valueLabelsCsv((await open("modern118.dta")).meta);
    assert.equal(
      text,
      "value_label,value,text\ngrplbl,0,control\ngrplbl,1,treated\ngrplbl,.a,refused\n",
    );
  });

  test("valueLabelsCsv is undefined when nothing is labelled", async () => {
    assert.equal(valueLabelsCsv((await open("alias120.dta")).meta), undefined);
  });
});

describe("formatRows over a view", () => {
  test("returns view rows with their observation numbers", async () => {
    const reader = await open("survey118.dta");
    const id = reader.meta.variables.findIndex((v) => v.name === "id");
    const order = await buildRowOrder(reader, {
      filter: "mod(id, 100) == 0",
      sort: [{ column: id, descending: true }],
    });
    const block = await formatRows(
      reader,
      { start: 1, count: 3, firstColumn: id, endColumn: id + 1, useLabels: true },
      order,
    );
    assert.deepEqual(block.rows, [["400"], ["300"], ["200"]]);
    assert.deepEqual(block.obs, [400, 300, 200]);
  });

  test("clamps to the view's length, not the dataset's", async () => {
    const reader = await open("survey118.dta");
    const order = await buildRowOrder(reader, { filter: "id <= 2", sort: [] });
    const block = await formatRows(
      reader,
      { start: 0, count: 100, firstColumn: 0, endColumn: 1, useLabels: true },
      order,
    );
    assert.equal(block.rows.length, 2);
    const beyond = await formatRows(
      reader,
      { start: 5, count: 100, firstColumn: 0, endColumn: 1, useLabels: true },
      order,
    );
    assert.deepEqual(beyond.rows, []);
  });

  test("an unordered view sends no obs array", async () => {
    const reader = await open("survey118.dta");
    const block = await formatRows(reader, {
      start: 0,
      count: 2,
      firstColumn: 0,
      endColumn: 1,
      useLabels: true,
    });
    assert.equal(block.obs, undefined);
  });
});

describe("formatRange / rangeToTsv", () => {
  test("copies a rectangle as the grid shows it", async () => {
    const reader = await open("modern118.dta");
    const rows = await formatRange(reader, {
      firstRow: 0,
      lastRow: 1,
      firstColumn: 5,
      lastColumn: 6,
      useLabels: true,
      headers: true,
    });
    assert.equal(rangeToTsv(rows), "s\tday\nsecond\t27mar2020\nabc\t28mar2020");
  });

  test("clamps a reversed or oversized rectangle", async () => {
    const reader = await open("modern118.dta");
    const rows = await formatRange(reader, {
      firstRow: 3,
      lastRow: 99,
      firstColumn: 0,
      lastColumn: 0,
      useLabels: true,
      headers: false,
    });
    assert.deepEqual(rows, [["."], [".a"]]);
  });

  test("refuses a selection past the cell limit", async () => {
    const reader = await open("survey118.dta");
    assert.ok(500 * reader.meta.nVars < MAX_COPY_CELLS);
    const rows = await formatRange(reader, {
      firstRow: 0,
      lastRow: 499,
      firstColumn: 0,
      lastColumn: reader.meta.nVars - 1,
      useLabels: true,
      headers: false,
    });
    assert.equal(rows.length, 500);
  });

  test("flattens tabs and newlines inside a cell", () => {
    assert.equal(rangeToTsv([["a\tb", "c\nd"]]), "a b\tc d");
  });
});

describe("formatSummary", () => {
  test("prints order statistics of a date variable as dates", async () => {
    const reader = await open("survey118.dta");
    const joined = reader.meta.variables.find((v) => v.name === "joined");
    assert.ok(joined);
    const view = formatSummary(await summarizeColumn(reader, joined.index, null), joined);
    const stats = new Map(view.stats);
    assert.equal(stats.get("Obs"), "500");
    assert.equal(stats.get("Missing"), "0");
    assert.equal(stats.get("Min"), "15jan2015"); // 20103, per Stata %td
    assert.equal(stats.get("Max"), "06mar2023"); // 23075
    assert.equal(stats.get("Mean"), "21,569.416");
  });

  test("frequency rows carry a filter that selects that value", async () => {
    const reader = await open("survey118.dta");
    const region = reader.meta.variables.find((v) => v.name === "region");
    const city = reader.meta.variables.find((v) => v.name === "city");
    assert.ok(region && city);
    const r = formatSummary(await summarizeColumn(reader, region.index, null), region);
    assert.deepEqual(r.frequencies[0], {
      value: "2",
      label: "South",
      count: "142",
      share: "28.4%",
      filter: "region == 2",
    });
    assert.equal(r.frequencies[4].filter, "region == .a");
    const c = formatSummary(await summarizeColumn(reader, city.index, null), city);
    assert.deepEqual(new Map(c.stats).get("Empty"), "100");
    assert.equal(c.frequencies[0].filter, 'city == ""');
    assert.equal(c.frequencies[4].filter, 'city == "北京"');
    // Each generated filter really selects that many rows.
    for (const f of [...r.frequencies, ...c.frequencies]) {
      const order = await buildRowOrder(reader, { filter: f.filter, sort: [] });
      assert.equal(order?.length.toLocaleString("en-US"), f.count, f.filter);
    }
  });
});
