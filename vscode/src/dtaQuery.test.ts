import { strict as assert } from "node:assert";
import * as fs from "node:fs";
import * as path from "node:path";
import { describe, test } from "node:test";

import { DtaFilterError } from "./dtaFilter";
import { buildRowOrder, DtaQueryCancelled, DtaQueryError, summarizeColumn } from "./dtaQuery";
import { BufferByteSource, DtaReader } from "./dtaReader";

const FIXTURES = path.join(__dirname, "..", "test-fixtures", "dta");

async function open(name: string): Promise<DtaReader> {
  const bytes = new Uint8Array(fs.readFileSync(path.join(FIXTURES, name)));
  return DtaReader.open(new BufferByteSource(bytes));
}

function col(reader: DtaReader, name: string): number {
  const v = reader.meta.variables.find((x) => x.name === name);
  assert.ok(v, name);
  return v.index;
}

/** 1-based `id`s of the view's rows [from, to), as Stata's `list id in` prints. */
function ids(order: Uint32Array | null, from: number, to: number): number[] {
  assert.ok(order);
  return Array.from(order.subarray(from, to), (i) => i + 1);
}

// Expected ids are Stata 18's `list id in …` after the same sort on
// survey118.dta, where id == _n in the saved file.
describe("buildRowOrder matches Stata's sort", () => {
  test("no filter and no sort is the identity (null)", async () => {
    const reader = await open("survey118.dta");
    assert.equal(await buildRowOrder(reader, { filter: "", sort: [] }), null);
    assert.equal(await buildRowOrder(reader, { filter: "   ", sort: [] }), null);
  });

  test("sort income, stable — missing values last", async () => {
    const reader = await open("survey118.dta");
    const order = await buildRowOrder(reader, {
      filter: "",
      sort: [{ column: col(reader, "income"), descending: false }],
    });
    assert.equal(order?.length, 500);
    assert.deepEqual(ids(order, 0, 8), [223, 79, 5, 52, 342, 406, 430, 216]);
    assert.deepEqual(ids(order, 492, 500), [322, 345, 368, 391, 414, 437, 460, 483]);
  });

  test("sort city id — strings in code-unit order, empty first", async () => {
    const reader = await open("survey118.dta");
    const order = await buildRowOrder(reader, {
      filter: "",
      sort: [{ column: col(reader, "city"), descending: false }],
    });
    assert.deepEqual(ids(order, 0, 6), [4, 9, 14, 19, 24, 29]);
    assert.deepEqual(ids(order, 494, 500), [473, 478, 483, 488, 493, 498]);
  });

  test("gsort -score id — descending, ties in dataset order", async () => {
    const reader = await open("survey118.dta");
    const order = await buildRowOrder(reader, {
      filter: "",
      sort: [{ column: col(reader, "score"), descending: true }],
    });
    assert.deepEqual(ids(order, 0, 8), [98, 46, 57, 107, 144, 120, 152, 242]);
  });

  test("gsort -age id — missing values stay last when descending", async () => {
    const reader = await open("survey118.dta");
    const order = await buildRowOrder(reader, {
      filter: "",
      sort: [
        { column: col(reader, "age"), descending: true },
        { column: col(reader, "id"), descending: false },
      ],
    });
    assert.deepEqual(ids(order, 0, 4), [68, 181, 320, 380]);
    // the last age-18 rows, then .a before '.', as Stata lists them
    assert.deepEqual(
      ids(order, 479, 500),
      [293, 325, 346, 455, 97, 194, 291, 388, 485, 41, 82, 123, 164, 205, 246, 287, 328, 369, 410, 451, 492],
    );
  });

  test("gsort -region -age id — each key keeps its missing values last", async () => {
    const reader = await open("survey118.dta");
    const order = await buildRowOrder(reader, {
      filter: "",
      sort: [
        { column: col(reader, "region"), descending: true },
        { column: col(reader, "age"), descending: true },
        { column: col(reader, "id"), descending: false },
      ],
    });
    assert.deepEqual(ids(order, 0, 3), [386, 44, 393]);
    assert.deepEqual(ids(order, 488, 500), [97, 388, 369, 159, 265, 106, 212, 318, 477, 53, 371, 424]);
  });

  test("gsort -city id — empty strings last when descending", async () => {
    const reader = await open("survey118.dta");
    const order = await buildRowOrder(reader, {
      filter: "",
      sort: [
        { column: col(reader, "city"), descending: true },
        { column: col(reader, "id"), descending: false },
      ],
    });
    assert.deepEqual(ids(order, 0, 3), [3, 8, 13]);
    assert.deepEqual(ids(order, 497, 500), [489, 494, 499]);
  });

  test("sort region age id — two keys, extended missing after sysmiss", async () => {
    const reader = await open("survey118.dta");
    const order = await buildRowOrder(reader, {
      filter: "",
      sort: [
        { column: col(reader, "region"), descending: false },
        { column: col(reader, "age"), descending: false },
      ],
    });
    assert.deepEqual(ids(order, 0, 6), [85, 199, 275, 346, 455, 214]);
    assert.deepEqual(ids(order, 494, 500), [477, 318, 212, 106, 265, 159]);
  });
});

describe("buildRowOrder filters", () => {
  test("filter only: kept rows stay in dataset order", async () => {
    const reader = await open("survey118.dta");
    const order = await buildRowOrder(reader, { filter: "mod(id, 100) == 0", sort: [] });
    assert.deepEqual(ids(order, 0, 5), [100, 200, 300, 400, 500]);
  });

  test("filter and sort compose", async () => {
    const reader = await open("survey118.dta");
    const order = await buildRowOrder(reader, {
      filter: 'city == "Boston" & id <= 50',
      sort: [{ column: col(reader, "id"), descending: true }],
    });
    assert.deepEqual(ids(order, 0, 10), [50, 45, 40, 35, 30, 25, 20, 15, 10, 5]);
  });

  test("a filter on _n alone needs no file access", async () => {
    const reader = await open("survey118.dta");
    const order = await buildRowOrder(reader, { filter: "_n > 498", sort: [] });
    assert.deepEqual(ids(order, 0, 2), [499, 500]);
  });

  test("a filter that keeps nothing gives an empty order, not null", async () => {
    const reader = await open("survey118.dta");
    const order = await buildRowOrder(reader, { filter: "id < 0", sort: [] });
    assert.equal(order?.length, 0);
  });

  test("a bad expression surfaces as DtaFilterError", async () => {
    const reader = await open("survey118.dta");
    await assert.rejects(buildRowOrder(reader, { filter: "city > 5", sort: [] }), DtaFilterError);
  });

  test("honours cancellation", async () => {
    const reader = await open("survey118.dta");
    await assert.rejects(
      buildRowOrder(reader, { filter: "age > 1", sort: [] }, () => true),
      DtaQueryCancelled,
    );
  });
});

// Expected values are Stata 18's `summarize, detail` r() results, printed
// with %21.15g, and `tabulate` counts, for survey118.dta.
describe("summarizeColumn matches Stata's summarize, detail", () => {
  const STATA: Array<[string, number, number, number, number, number, number, number, number]> = [
    ["age", 483, 47.703933747412, 17.2016755025546, 18, 77, 33, 48, 63],
    ["income", 479, 34668.0512526096, 43092.5253949778, 1001.08, 364909.38, 10309.43, 21855.09, 39882.23],
    ["wage", 500, 15.085, 5.86007302956402, 5, 25, 9.75, 15.5, 20],
    ["score", 500, -3.036, 56.8198177344379, -100, 99, -51, -7, 48],
    ["joined", 500, 21569.416, 872.038403338864, 20103, 23075, 20779.5, 21569.5, 22328],
    ["id", 500, 250.5, 144.481832767999, 1, 500, 125.5, 250.5, 375.5],
    ["region", 491, 2.4969450101833, 1.08679027276193, 1, 4, 2, 2, 3],
  ];
  const close = (actual: number | undefined, expected: number): void => {
    assert.ok(actual !== undefined);
    // 1e-12 relative: the reference was printed to 15 significant digits.
    assert.ok(
      Math.abs(actual - expected) <= 1e-12 * Math.max(1, Math.abs(expected)),
      `${actual} vs ${expected}`,
    );
  };
  for (const [name, n, mean, sd, min, max, p25, p50, p75] of STATA) {
    test(name, async () => {
      const reader = await open("survey118.dta");
      const s = await summarizeColumn(reader, col(reader, name), null);
      assert.equal(s.n, n);
      assert.equal(s.missing, 500 - n);
      assert.equal(s.rows, 500);
      close(s.mean, mean);
      close(s.sd, sd);
      close(s.min, min);
      close(s.max, max);
      close(s.p25, p25);
      close(s.p50, p50);
      close(s.p75, p75);
    });
  }

  test("distinct counts nonmissing values (Stata's tabulate r(r) minus missing kinds)", async () => {
    const reader = await open("survey118.dta");
    const distinct = async (name: string): Promise<number> =>
      (await summarizeColumn(reader, col(reader, name), null)).distinct;
    assert.equal(await distinct("age"), 60); // tabulate, missing: 62 = 60 + `.` + `.a`
    assert.equal(await distinct("income"), 479); // 480 = 479 + `.`
    assert.equal(await distinct("id"), 500);
    assert.equal(await distinct("region"), 4); // 5 = 4 + `.a`
    assert.equal(await distinct("city"), 4); // 5 = 4 + ""
  });

  test("frequency table for a value-labelled variable (tabulate region, missing)", async () => {
    const reader = await open("survey118.dta");
    const s = await summarizeColumn(reader, col(reader, "region"), null);
    assert.deepEqual(s.frequencies, [
      { value: "2", label: "South", count: 142 },
      { value: "3", label: "East", count: 121 },
      { value: "4", label: "West", count: 117 },
      { value: "1", label: "North", count: 111 },
      { value: ".a", label: "Refused", count: 9 },
    ]);
    assert.equal(s.frequenciesOmitted, 0);
  });

  test("frequency table for a string variable (tabulate city, missing)", async () => {
    const reader = await open("survey118.dta");
    const s = await summarizeColumn(reader, col(reader, "city"), null);
    assert.equal(s.numeric, false);
    assert.equal(s.n, 400);
    assert.equal(s.missing, 100);
    assert.equal(s.mean, undefined);
    assert.deepEqual(
      s.frequencies?.map((f) => [f.value, f.count]),
      [
        ["", 100],
        ["Boston", 100],
        ["New York", 100],
        ["boston", 100],
        ["北京", 100],
      ],
    );
  });

  test("a high-cardinality numeric variable gets no frequency table", async () => {
    const reader = await open("survey118.dta");
    const s = await summarizeColumn(reader, col(reader, "income"), null);
    assert.equal(s.frequencies, undefined);
  });

  test("summarizes only the rows of a filtered view", async () => {
    const reader = await open("survey118.dta");
    const order = await buildRowOrder(reader, { filter: "id <= 10", sort: [] });
    const s = await summarizeColumn(reader, col(reader, "id"), order);
    assert.equal(s.rows, 10);
    assert.equal(s.n, 10);
    assert.equal(s.mean, 5.5);
    assert.equal(s.min, 1);
    assert.equal(s.max, 10);
  });

  test("an all-missing selection has counts but no moments", async () => {
    const reader = await open("survey118.dta");
    const order = await buildRowOrder(reader, { filter: "age == .a", sort: [] });
    const s = await summarizeColumn(reader, col(reader, "age"), order);
    assert.equal(s.n, 0);
    assert.equal(s.missing, 5);
    assert.equal(s.mean, undefined);
    assert.equal(s.distinct, 0);
  });
});

describe("DtaReader.readRowsAt", () => {
  test("returns scattered rows in the order asked", async () => {
    const reader = await open("survey118.dta");
    const id = col(reader, "id");
    const rows = await reader.readRowsAt([499, 0, 250, 1, 498], [id]);
    assert.deepEqual(rows, [[500], [1], [251], [2], [499]]);
    assert.deepEqual(await reader.readRowsAt([], [id]), []);
    await assert.rejects(reader.readRowsAt([500], [id]), RangeError);
  });
});

// The whole-column operations are bounded by memory, not by a row count: a
// small limit stands in here for a file too large for the real one.
describe("memory limit", () => {
  const tight = { memoryBytes: 2000 };
  const never = (): boolean => false;

  test("a filter that keeps few rows runs under a limit a full one would break", async () => {
    const reader = await open("survey118.dta");
    const order = await buildRowOrder(reader, { filter: "mod(id, 100) == 0", sort: [] }, never, tight);
    assert.deepEqual(ids(order, 0, 5), [100, 200, 300, 400, 500]);
    await assert.rejects(
      buildRowOrder(reader, { filter: "id > 0", sort: [] }, never, tight),
      (err: unknown) =>
        err instanceof DtaQueryError &&
        /This filter needs about .* MB .* stataCode\.dtaViewerMemoryMb/.test(err.message),
    );
  });

  test("a sort counts its keys, numeric and string", async () => {
    const reader = await open("survey118.dta");
    for (const name of ["income", "city"]) {
      await assert.rejects(
        buildRowOrder(reader, { filter: "", sort: [{ column: col(reader, name), descending: false }] }, never, tight),
        /Sorting these rows needs about/,
      );
    }
  });

  test("a time-series filter is refused before its pass over the file", async () => {
    const reader = await open("panel118.dta");
    await assert.rejects(
      buildRowOrder(reader, { filter: "L.sales > 100", sort: [] }, never, tight),
      /This time-series filter needs about/,
    );
    const order = await buildRowOrder(reader, { filter: "L.sales > 100", sort: [] });
    assert.equal(order?.length, 123);
  });

  test("a summary past the limit keeps exact moments and drops the percentiles", async () => {
    const reader = await open("survey118.dta");
    for (const name of ["income", "score", "age"]) {
      const full = await summarizeColumn(reader, col(reader, name), null);
      const lean = await summarizeColumn(reader, col(reader, name), null, never, tight);
      assert.equal(lean.percentilesOmitted, true);
      assert.equal(lean.p50, undefined);
      assert.equal(lean.n, full.n);
      assert.equal(lean.missing, full.missing);
      assert.equal(lean.min, full.min);
      assert.equal(lean.max, full.max);
      assert.ok(Math.abs((lean.mean as number) - (full.mean as number)) <= 1e-9 * Math.abs(full.mean as number));
      assert.ok(Math.abs((lean.sd as number) - (full.sd as number)) <= 1e-9 * (full.sd as number));
      assert.equal(full.percentilesOmitted, undefined);
    }
  });
});
