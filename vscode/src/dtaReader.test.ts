import { strict as assert } from "node:assert";
import * as fs from "node:fs";
import * as path from "node:path";
import { describe, test } from "node:test";

import {
  BufferByteSource,
  DtaFormatError,
  DtaReader,
  defaultFormat,
  missingCode,
  missingIndex,
  VALUE_LABEL_MISSING_BASE,
} from "./dtaReader";

// Fixtures are written by a real Stata from test-fixtures/dta/make_fixtures.do;
// the expected values below are what Stata's own `list` / `describe` print.
const FIXTURES = path.join(__dirname, "..", "test-fixtures", "dta");

function fixtureBytes(name: string): Uint8Array {
  return new Uint8Array(fs.readFileSync(path.join(FIXTURES, name)));
}

async function open(name: string, options = {}): Promise<DtaReader> {
  return DtaReader.open(new BufferByteSource(fixtureBytes(name)), options);
}

function column(rows: unknown[][], index: number): unknown[] {
  return rows.map((r) => r[index]);
}

describe("format 118 (Stata 14+)", () => {
  test("reads the header and every variable's metadata", async () => {
    const reader = await open("modern118.dta");
    const m = reader.meta;
    assert.equal(m.release, 118);
    assert.equal(m.byteOrder, "LSF");
    assert.equal(m.nObs, 5);
    assert.equal(m.nVars, 14);
    assert.equal(m.dataLabel, "Fixture dataset");
    assert.match(m.timestamp, /^\d{1,2} \w{3} \d{4} \d{2}:\d{2}$/);
    assert.deepEqual(m.sortedBy, ["b"]);
    assert.deepEqual(m.notes, ["dataset note one"]);
    assert.deepEqual(m.warnings, []);

    assert.deepEqual(
      m.variables.map((v) => [v.name, v.type, v.format]),
      [
        ["b", "byte", "%8.0g"],
        ["i", "int", "%8.0g"],
        ["l", "long", "%12.0g"],
        ["f", "float", "%9.0g"],
        ["d", "double", "%10.0g"],
        ["s", "str6", "%9s"],
        ["day", "long", "%td"],
        ["stamp", "double", "%tc"],
        ["month", "int", "%tm"],
        ["quarter", "int", "%tq"],
        ["money", "double", "%12.2fc"],
        ["iso", "long", "%tdCCYY-NN-DD"],
        ["grp", "byte", "%8.0g"],
        ["big", "strL", "%9s"],
      ],
    );
    const byName = new Map(m.variables.map((v) => [v.name, v]));
    assert.equal(byName.get("b")?.label, "A byte");
    assert.deepEqual(byName.get("b")?.notes, ["byte note"]);
    assert.equal(byName.get("s")?.label, "字符串 label");
    assert.equal(byName.get("grp")?.label, "Treatment group");
    assert.equal(byName.get("grp")?.valueLabel, "grplbl");
    assert.equal(byName.get("i")?.valueLabel, "");
  });

  test("keeps value-label definitions, including labels on missing values", async () => {
    const { valueLabels } = (await open("modern118.dta")).meta;
    assert.deepEqual([...valueLabels.keys()], ["grplbl"]);
    const grp = valueLabels.get("grplbl");
    assert.equal(grp?.get(0), "control");
    assert.equal(grp?.get(1), "treated");
    // `.a` is stored under the long-integer code for .a
    assert.equal(grp?.get(VALUE_LABEL_MISSING_BASE + 1), "refused");
  });

  test("decodes every numeric storage type and its missing values", async () => {
    const rows = await (await open("modern118.dta")).readRows(0, 5);
    assert.deepEqual(column(rows, 0), [1, 2, 3, ".", ".a"]); // byte
    assert.deepEqual(column(rows, 1), [1000, 2000, 3000, 4000, ".z"]); // int
    assert.deepEqual(column(rows, 2), [100000, 200000, ".b", 400000, 500000]); // long
    assert.deepEqual(column(rows, 3), [0.25, ".", 0.75, 1, 1.25]); // float
    assert.deepEqual(column(rows, 4), [".c", 2.2, 3.3000000000000003, 4.4, 5.5]); // double
    assert.deepEqual(column(rows, 12), [1, 0, 1, 0, ".a"]); // grp
  });

  test("decodes fixed-width strings: full width, short, empty, UTF-8", async () => {
    const rows = await (await open("modern118.dta")).readRows(0, 5);
    assert.deepEqual(column(rows, 5), ["second", "abc", "", "中文", "é"]);
  });

  test("resolves strL values through the GSO table", async () => {
    const rows = await (await open("modern118.dta")).readRows(0, 5, [13]);
    assert.equal(rows[0][0], "short");
    assert.equal(rows[1][0], "x".repeat(3000));
    assert.equal(rows[2][0], "");
    assert.equal(rows[3][0], "short");
    assert.equal(rows[4][0], "中文 strL");
  });

  test("cuts strL values longer than maxStrLBytes and marks the cut", async () => {
    const reader = await open("modern118.dta", { maxStrLBytes: 10 });
    const rows = await reader.readRows(1, 1, [13]);
    assert.equal(rows[0][0], `${"x".repeat(10)}…`);
  });

  test("readRows clamps the range and honours column selection order", async () => {
    const reader = await open("modern118.dta");
    assert.deepEqual(await reader.readRows(3, 100, [1, 0]), [
      [4000, "."],
      [".z", ".a"],
    ]);
    assert.deepEqual(await reader.readRows(5, 10), []);
    assert.deepEqual(await reader.readRows(-3, 1, [0]), [[1]]);
    await assert.rejects(reader.readRows(0, 1, [99]), RangeError);
  });

  test("a dataset with variables but no observations", async () => {
    const reader = await open("empty118.dta");
    assert.equal(reader.meta.nObs, 0);
    assert.equal(reader.meta.nVars, 14);
    assert.deepEqual(await reader.readRows(0, 10), []);
  });
});

describe("format 117 (Stata 13)", () => {
  test("reads metadata, numerics and strLs", async () => {
    const reader = await open("strl117.dta");
    assert.equal(reader.meta.release, 117);
    assert.equal(reader.meta.nObs, 5);
    assert.equal(reader.meta.dataLabel, "Fixture dataset");
    assert.deepEqual(reader.meta.sortedBy, ["b"]);
    assert.deepEqual(reader.meta.notes, ["dataset note one"]);
    assert.equal(reader.meta.valueLabels.get("grplbl")?.get(1), "treated");

    const rows = await reader.readRows(0, 5);
    assert.deepEqual(column(rows, 0), [1, 2, 3, ".", ".a"]);
    assert.deepEqual(column(rows, 4), [".c", 2.2, 3.3000000000000003, 4.4, 5.5]);
    assert.deepEqual(column(rows, 13).slice(0, 4), ["short", "x".repeat(3000), "", "short"]);
  });
});

describe("format 115 (Stata 12)", () => {
  test("reads the fixed-header layout", async () => {
    const reader = await open("legacy115.dta");
    const m = reader.meta;
    assert.equal(m.release, 115);
    assert.equal(m.nObs, 5);
    assert.equal(m.nVars, 13);
    assert.equal(m.dataLabel, "Fixture dataset");
    assert.deepEqual(m.sortedBy, ["b"]);
    assert.deepEqual(m.notes, ["dataset note one"]);
    assert.deepEqual(m.variables[0].notes, ["byte note"]);
    assert.equal(m.variables[11].format, "%tdCCYY-NN-DD");
    assert.equal(m.variables[12].valueLabel, "grplbl");
    assert.equal(m.valueLabels.get("grplbl")?.get(0), "control");
    assert.equal(m.valueLabels.get("grplbl")?.get(VALUE_LABEL_MISSING_BASE + 1), "refused");

    const rows = await reader.readRows(0, 5);
    assert.deepEqual(column(rows, 0), [1, 2, 3, ".", ".a"]);
    assert.deepEqual(column(rows, 1), [1000, 2000, 3000, 4000, ".z"]);
    assert.deepEqual(column(rows, 2), [100000, 200000, ".b", 400000, 500000]);
    assert.deepEqual(column(rows, 3), [0.25, ".", 0.75, 1, 1.25]);
    assert.deepEqual(column(rows, 5).slice(0, 3), ["second", "abc", ""]);
    assert.deepEqual(column(rows, 12), [1, 0, 1, 0, ".a"]);
  });

  test("auto-detects UTF-8 text that Stata 14+ wrote into an old-format file", async () => {
    const reader = await open("legacy115.dta");
    const rows = await reader.readRows(3, 1, [5]);
    assert.equal(rows[0][0], "中文");
  });
});

describe("format 120 (Stata 18 alias variables)", () => {
  test("alias variables occupy no bytes in the data section", async () => {
    const reader = await open("alias120.dta");
    assert.equal(reader.meta.release, 120);
    const alias = reader.meta.variables.find((v) => v.kind === "alias");
    assert.equal(alias?.name, "val");
    assert.equal(alias?.width, 0);
    const rows = await reader.readRows(0, 3);
    assert.deepEqual(column(rows, 0), [1, 2, 3]); // id
    assert.deepEqual(column(rows, 1), [7, 7, 7]); // own
    assert.deepEqual(
      column(rows, reader.meta.variables.findIndex((v) => v.kind === "alias")),
      ["", "", ""],
    );
  });
});

// ── synthetic files: byte order and the >32,767-variable layout ─────────────
//
// Stata on this hardware only writes LSF files, and writing format 119 needs
// tens of thousands of variables, so those two paths are exercised with a
// minimal writer that follows the same published layout.

interface SyntheticOptions {
  release: 118 | 119;
  littleEndian: boolean;
}

function synthetic({ release, littleEndian }: SyntheticOptions): Uint8Array {
  const chunks: Uint8Array[] = [];
  let length = 0;
  const push = (b: Uint8Array): void => {
    chunks.push(b);
    length += b.byteLength;
  };
  const ascii = (s: string): void => push(new TextEncoder().encode(s));
  const num = (bytes: number, value: number): void => {
    const b = new Uint8Array(bytes);
    let v = BigInt(value);
    for (let i = 0; i < bytes; i++) {
      b[littleEndian ? i : bytes - 1 - i] = Number(v & 0xffn);
      v >>= 8n;
    }
    push(b);
  };
  const fixed = (text: string, width: number): void => {
    const b = new Uint8Array(width);
    b.set(new TextEncoder().encode(text));
    push(b);
  };
  const f64 = (value: number): void => {
    const b = new Uint8Array(8);
    new DataView(b.buffer).setFloat64(0, value, littleEndian);
    push(b);
  };

  const wide = release === 119;
  const map: number[] = [];
  const mark = (): void => {
    map.push(length);
  };

  mark(); // 1 <stata_dta>
  ascii(`<stata_dta><header><release>${release}</release>`);
  ascii(`<byteorder>${littleEndian ? "LSF" : "MSF"}</byteorder><K>`);
  num(wide ? 4 : 2, 3);
  ascii("</K><N>");
  num(8, 2);
  ascii("</N><label>");
  num(2, 3);
  ascii("syn</label><timestamp>");
  num(1, 0);
  ascii("</timestamp></header>");
  mark(); // 2 <map>
  const mapAt = length + "<map>".length;
  ascii("<map>");
  push(new Uint8Array(14 * 8));
  ascii("</map>");
  mark(); // 3
  ascii("<variable_types>");
  num(2, 65529); // int
  num(2, 65526); // double
  num(2, 32768); // strL
  ascii("</variable_types>");
  mark(); // 4
  ascii("<varnames>");
  for (const n of ["a", "x", "t"]) fixed(n, 129);
  ascii("</varnames>");
  mark(); // 5
  ascii("<sortlist>");
  num(wide ? 4 : 2, 2);
  for (let i = 0; i < 3; i++) num(wide ? 4 : 2, 0);
  ascii("</sortlist>");
  mark(); // 6
  ascii("<formats>");
  for (const f of ["%8.0g", "%10.0g", "%9s"]) fixed(f, 57);
  ascii("</formats>");
  mark(); // 7
  ascii("<value_label_names>");
  for (const n of ["yn", "", ""]) fixed(n, 129);
  ascii("</value_label_names>");
  mark(); // 8
  ascii("<variable_labels>");
  for (const n of ["first", "", ""]) fixed(n, 321);
  ascii("</variable_labels>");
  mark(); // 9
  ascii("<characteristics></characteristics>");
  mark(); // 10
  ascii("<data>");
  const vo = (v: number, o: number): void => {
    // 118 packs (v,o) as 2+6 bytes, 119 as 3+5.
    const vBytes = wide ? 3 : 2;
    if (littleEndian) {
      num(vBytes, v);
      num(8 - vBytes, o);
    } else {
      num(vBytes, v);
      num(8 - vBytes, o);
    }
  };
  num(2, 258);
  f64(1.5);
  vo(3, 1);
  num(2, 32741); // .
  f64(-2.25);
  vo(0, 0);
  ascii("</data>");
  mark(); // 11
  ascii("<strls>GSO");
  num(4, 3);
  num(8, 1);
  num(1, 130);
  num(4, 6);
  ascii("hello\0");
  ascii("</strls>");
  mark(); // 12
  ascii("<value_labels><lbl>");
  const text = new TextEncoder().encode("no\0yes\0");
  num(4, 8 + 8 * 2 + text.byteLength);
  fixed("yn", 129);
  push(new Uint8Array(3));
  num(4, 2);
  num(4, text.byteLength);
  num(4, 0);
  num(4, 3);
  num(4, 0);
  num(4, 258);
  push(text);
  ascii("</lbl></value_labels>");
  mark(); // 13
  ascii("</stata_dta>");
  mark(); // 14 EOF

  const out = new Uint8Array(length);
  let at = 0;
  for (const c of chunks) {
    out.set(c, at);
    at += c.byteLength;
  }
  const view = new DataView(out.buffer);
  map.forEach((offset, i) => view.setBigUint64(mapAt + 8 * i, BigInt(offset), littleEndian));
  return out;
}

describe("synthetic layouts", () => {
  for (const release of [118, 119] as const) {
    for (const littleEndian of [true, false]) {
      test(`format ${release}, ${littleEndian ? "LSF" : "MSF"}`, async () => {
        const reader = await DtaReader.open(
          new BufferByteSource(synthetic({ release, littleEndian })),
        );
        assert.equal(reader.meta.release, release);
        assert.equal(reader.meta.byteOrder, littleEndian ? "LSF" : "MSF");
        assert.equal(reader.meta.dataLabel, "syn");
        assert.deepEqual(reader.meta.sortedBy, ["x"]);
        assert.equal(reader.meta.valueLabels.get("yn")?.get(258), "yes");
        assert.deepEqual(await reader.readRows(0, 2), [
          [258, 1.5, "hello"],
          [".", -2.25, ""],
        ]);
      });
    }
  }

  test("a file cut off inside the data section reports the rows that survive", async () => {
    const full = fixtureBytes("legacy115.dta");
    const reader = await DtaReader.open(new BufferByteSource(full));
    const rowWidth = reader.meta.rowWidth;
    // Drop the value labels and the last two and a half observations.
    const valueLabelBytes = 8 + 33 + 3; // lower bound; cut well inside the data
    const cut = full.subarray(0, full.byteLength - valueLabelBytes - Math.floor(rowWidth * 2.5));
    const truncated = await DtaReader.open(new BufferByteSource(cut));
    assert.ok(truncated.meta.nObs < 5);
    assert.equal(truncated.meta.warnings.length, 1);
    assert.match(truncated.meta.warnings[0], /truncated/);
    const rows = await truncated.readRows(0, 5);
    assert.equal(rows.length, truncated.meta.nObs);
    assert.equal(rows[0][0], 1);
  });
});

describe("rejecting files that are not readable .dta", () => {
  test("an unrelated file", async () => {
    const bytes = new TextEncoder().encode("id,price\n1,2\n");
    await assert.rejects(DtaReader.open(new BufferByteSource(bytes)), DtaFormatError);
  });

  test("an empty file", async () => {
    await assert.rejects(DtaReader.open(new BufferByteSource(new Uint8Array(0))), DtaFormatError);
  });

  test("a format number Stata never released names the way out", async () => {
    const bytes = new Uint8Array(200);
    bytes[0] = 112;
    await assert.rejects(DtaReader.open(new BufferByteSource(bytes)), /save it again/);
  });

  test("an old-format header with no byte order is not a .dta file", async () => {
    const bytes = new Uint8Array(200);
    bytes[0] = 110;
    await assert.rejects(DtaReader.open(new BufferByteSource(bytes)), /unknown byte order/);
  });

  test("a tagged file whose section map points past EOF", async () => {
    const bytes = fixtureBytes("modern118.dta").subarray(0, 600);
    await assert.rejects(DtaReader.open(new BufferByteSource(bytes)), DtaFormatError);
  });
});

describe("helpers", () => {
  test("missingCode / missingIndex round-trip", () => {
    assert.equal(missingCode(0), ".");
    assert.equal(missingCode(1), ".a");
    assert.equal(missingCode(26), ".z");
    for (let i = 0; i <= 26; i++) assert.equal(missingIndex(missingCode(i)), i);
    assert.equal(missingIndex("abc"), -1);
    assert.equal(missingIndex(".A"), -1);
  });

  test("defaultFormat matches what Stata assigns new variables", () => {
    assert.equal(defaultFormat("byte"), "%8.0g");
    assert.equal(defaultFormat("int"), "%8.0g");
    assert.equal(defaultFormat("long"), "%12.0g");
    assert.equal(defaultFormat("float"), "%9.0g");
    assert.equal(defaultFormat("double"), "%10.0g");
    assert.equal(defaultFormat("str5"), "%9s");
    assert.equal(defaultFormat("str80"), "%80s");
    assert.equal(defaultFormat("str2045"), "%2045s");
    assert.equal(defaultFormat("strL"), "%9s");
  });
});

// Formats 102-111 predate StataCorp's published format documentation. The
// reference is Stata itself: each fixture in test-fixtures/dta/old has a twin
// that Stata 18 wrote after reading it, and the two must read the same.
describe("formats 102-111 read as Stata reads them", () => {
  const OLD = path.join(FIXTURES, "old");
  const originals = fs
    .readdirSync(OLD)
    .filter((name) => name.endsWith(".dta") && !name.endsWith("_as118.dta"))
    .sort();

  async function openOld(name: string): Promise<DtaReader> {
    return DtaReader.open(new BufferByteSource(new Uint8Array(fs.readFileSync(path.join(OLD, name)))));
  }

  /** Stata widens a type whose old range does not fit today's (byte → int …). */
  const WIDER: Record<string, string[]> = {
    byte: ["byte", "int"],
    int: ["int", "long"],
    long: ["long", "double"],
    float: ["float", "double"],
    double: ["double"],
  };

  test("every released old format has a fixture", () => {
    const releases = new Set(originals.map((name) => fs.readFileSync(path.join(OLD, name))[0]));
    assert.deepEqual([...releases].sort(), [102, 103, 104, 105, 108, 110, 111]);
    assert.ok(originals.some((name) => fs.readFileSync(path.join(OLD, name))[1] === 1), "a big-endian file");
  });

  for (const name of originals) {
    test(name, async () => {
      const old = await openOld(name);
      const twin = await openOld(name.replace(/\.dta$/, "_as118.dta"));
      assert.equal(old.meta.release, fs.readFileSync(path.join(OLD, name))[0]);
      assert.equal(twin.meta.release, 118);
      assert.equal(old.meta.nObs, twin.meta.nObs);
      assert.equal(old.meta.dataLabel, twin.meta.dataLabel);
      assert.deepEqual(old.meta.warnings, []);
      assert.deepEqual(
        old.meta.variables.map((v) => [v.name, v.label, v.valueLabel]),
        twin.meta.variables.map((v) => [v.name, v.label, v.valueLabel]),
      );
      for (const v of old.meta.variables) {
        const now = twin.meta.variables[v.index].type;
        assert.ok(v.kind === "str" ? now === v.type : WIDER[v.type].includes(now), `${v.name}: ${v.type} → ${now}`);
      }
      // every value label a variable uses, with the same codes and texts
      for (const v of old.meta.variables) {
        if (!v.valueLabel) continue;
        assert.deepEqual(
          [...(old.meta.valueLabels.get(v.valueLabel) ?? [])].sort((a, b) => a[0] - b[0]),
          [...(twin.meta.valueLabels.get(v.valueLabel) ?? [])].sort((a, b) => a[0] - b[0]),
          v.valueLabel,
        );
      }
      const columns = old.meta.variables.map((v) => v.index);
      assert.deepEqual(
        await old.readRows(0, old.meta.nObs, columns),
        await twin.readRows(0, twin.meta.nObs, columns),
      );
    });
  }

  test("a missing value is '.', and the numbers just below it are data", async () => {
    // one row, every numeric type missing
    const missing = await openOld("stata1_105.dta");
    assert.deepEqual(await missing.readRows(0, 1, [0, 1, 2, 3, 4]), [[".", ".", ".", ".", "."]]);
    // old byte / int / long reach further than today's: 126 is a byte here
    const ranges = await openOld("stata_int_validranges_105.dta");
    const rows = await ranges.readRows(0, 2, [0, 1, 2]);
    assert.deepEqual(rows, [
      [-128, -32768, -2147483648],
      [126, 32766, 2147483646],
    ]);
    assert.deepEqual(
      ranges.meta.variables.map((v) => v.type),
      ["byte", "int", "long"],
    );
  });

  test("the header's own fields: timestamp from 105 on, none before", async () => {
    assert.equal((await openOld("stata4_105.dta")).meta.timestamp, "1 Mar 2014 09:44");
    assert.equal((await openOld("stata4_104.dta")).meta.timestamp, "");
    assert.equal((await openOld("stata_int_validranges_102.dta")).meta.dataLabel.length > 0, true);
  });

  test("a format number Stata never released is refused with a reason", async () => {
    for (const release of [106, 107, 109, 112]) {
      const bytes = new Uint8Array(fs.readFileSync(path.join(OLD, "stata4_111.dta")));
      bytes[0] = release;
      await assert.rejects(
        DtaReader.open(new BufferByteSource(bytes)),
        new RegExp(`format ${release} is not a format Stata released`),
      );
    }
  });
});
