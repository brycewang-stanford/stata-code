import { strict as assert } from "node:assert";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { describe, test } from "node:test";

import { BufferByteSource, DtaReader } from "./dtaReader";
import { createHash } from "node:crypto";

import {
  applySplices,
  describeEdit,
  DtaEditError,
  type DtaLabelEdit,
  editChanged,
  editLabels,
  inverseEdit,
  encodeVariableLabel,
  planEdits,
  planLabelEdits,
  setVariableLabels,
  valueLabelCode,
} from "./dtaWriter";

// Fixtures are written by a real Stata (test-fixtures/dta/make_fixtures.do).
// That the edited files still load in Stata, with `describe` showing the new
// labels and `notes` / `label list` unchanged, was checked against Stata 18.
const FIXTURES = path.join(__dirname, "..", "test-fixtures", "dta");

function fixtureBytes(name: string): Buffer {
  return fs.readFileSync(path.join(FIXTURES, name));
}

async function open(bytes: Uint8Array): Promise<DtaReader> {
  return DtaReader.open(new BufferByteSource(bytes));
}

function withCopy<T>(name: string, run: (file: string) => Promise<T>): Promise<T> {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "dta-writer-"));
  const file = path.join(dir, name);
  fs.copyFileSync(path.join(FIXTURES, name), file);
  return run(file).finally(() => fs.rmSync(dir, { recursive: true, force: true }));
}

/** Byte offsets at which two equally long buffers differ. */
function differingOffsets(a: Uint8Array, b: Uint8Array): number[] {
  assert.equal(a.byteLength, b.byteLength);
  const out: number[] = [];
  for (let i = 0; i < a.byteLength; i++) if (a[i] !== b[i]) out.push(i);
  return out;
}

describe("variableLabelField", () => {
  for (const [name, width] of [
    ["modern118.dta", 321],
    ["strl117.dta", 81],
    ["legacy115.dta", 81],
    ["alias120.dta", 321],
  ] as const) {
    test(`${name}: each field holds the label the reader reports`, async () => {
      const bytes = fixtureBytes(name);
      const reader = await open(bytes);
      for (const v of reader.meta.variables) {
        const field = reader.variableLabelField(v.index);
        assert.equal(field.width, width);
        const raw = bytes.subarray(field.offset, field.offset + field.width);
        const end = raw.indexOf(0);
        assert.equal(raw.subarray(0, end < 0 ? raw.length : end).toString("utf8"), v.label);
      }
    });
  }

  test("rejects an index outside the dataset", async () => {
    const reader = await open(fixtureBytes("modern118.dta"));
    assert.throws(() => reader.variableLabelField(reader.meta.nVars), RangeError);
    assert.throws(() => reader.variableLabelField(-1), RangeError);
  });
});

describe("encodeVariableLabel", () => {
  test("zero-pads to the field width", () => {
    const field = encodeVariableLabel("Age", 118, 321);
    assert.equal(field.byteLength, 321);
    assert.deepEqual([...field.subarray(0, 4)], [0x41, 0x67, 0x65, 0]);
    assert.ok(field.subarray(3).every((b) => b === 0));
  });

  test("takes 80 characters and refuses 81", () => {
    assert.equal(encodeVariableLabel("x".repeat(80), 118, 321).indexOf(0), 80);
    assert.throws(() => encodeVariableLabel("x".repeat(81), 118, 321), /at most 80/);
  });

  test("counts characters, not bytes, in a UTF-8 file", () => {
    // 80 four-byte characters fill the 320 usable bytes exactly.
    const field = encodeVariableLabel("😀".repeat(80), 118, 321);
    assert.equal(field.indexOf(0), 320);
    assert.throws(() => encodeVariableLabel("中".repeat(81), 118, 321), DtaEditError);
  });

  test("refuses non-ASCII in formats older than 118", () => {
    assert.throws(() => encodeVariableLabel("年龄", 117, 81), /plain-ASCII/);
    assert.throws(() => encodeVariableLabel("é", 115, 81), /plain-ASCII/);
    assert.equal(encodeVariableLabel("Age (years)", 115, 81).indexOf(0), 11);
  });

  test("refuses control characters", () => {
    assert.throws(() => encodeVariableLabel("a\nb", 118, 321), /control character/);
    assert.throws(() => encodeVariableLabel("a\0b", 118, 321), /control character/);
  });
});

describe("planLabelEdits", () => {
  test("validates every edit before returning any patch", async () => {
    const reader = await open(fixtureBytes("modern118.dta"));
    assert.throws(
      () => planLabelEdits(reader, { b: "fine", nope: "x", i: "y".repeat(81) }),
      (err: Error) =>
        err instanceof DtaEditError &&
        /nope: no such variable/.test(err.message) &&
        /i: label is 81 characters/.test(err.message),
    );
  });

  test("skips a label that would not change", async () => {
    const reader = await open(fixtureBytes("modern118.dta"));
    assert.deepEqual(planLabelEdits(reader, { b: "A byte" }), []);
  });
});

describe("setVariableLabels", () => {
  test("118: changes only the label fields it was asked to change", async () => {
    await withCopy("modern118.dta", async (file) => {
      const before = fixtureBytes("modern118.dta");
      const changes = await setVariableLabels(file, {
        b: "年龄（周岁）",
        i: "Income, thousands",
        grp: "",
      });
      assert.deepEqual(changes, [
        { name: "b", before: "A byte", after: "年龄（周岁）" },
        { name: "i", before: "", after: "Income, thousands" },
        { name: "grp", before: "Treatment group", after: "" },
      ]);

      const after = fs.readFileSync(file);
      const original = await open(before);
      const allowed = new Set<number>();
      for (const name of ["b", "i", "grp"]) {
        const v = original.meta.variables.find((x) => x.name === name);
        assert.ok(v);
        const field = original.variableLabelField(v.index);
        for (let k = 0; k < field.width; k++) allowed.add(field.offset + k);
      }
      const changed = differingOffsets(before, after);
      assert.ok(changed.length > 0);
      assert.ok(changed.every((offset) => allowed.has(offset)));

      const reader = await open(after);
      const labels = Object.fromEntries(reader.meta.variables.map((v) => [v.name, v.label]));
      assert.equal(labels.b, "年龄（周岁）");
      assert.equal(labels.i, "Income, thousands");
      assert.equal(labels.grp, "");
      assert.equal(labels.s, "字符串 label"); // untouched
      // Everything else the file carries reads back exactly as before.
      assert.deepEqual(reader.meta.notes, original.meta.notes);
      assert.deepEqual(
        reader.meta.variables.map((v) => v.notes),
        original.meta.variables.map((v) => v.notes),
      );
      assert.deepEqual(reader.meta.valueLabels, original.meta.valueLabels);
      assert.equal(reader.meta.dataLabel, original.meta.dataLabel);
      assert.deepEqual(await reader.readRows(0, 5), await original.readRows(0, 5));
    });
  });

  for (const name of ["strl117.dta", "legacy115.dta", "alias120.dta"]) {
    test(`${name}: round-trips through the reader`, async () => {
      await withCopy(name, async (file) => {
        const original = await open(fixtureBytes(name));
        const target = original.meta.variables[original.meta.nVars - 1].name;
        const changes = await setVariableLabels(file, { [target]: "Edited label" });
        assert.equal(changes.length, 1);
        const reader = await open(fs.readFileSync(file));
        const labels = Object.fromEntries(reader.meta.variables.map((v) => [v.name, v.label]));
        assert.equal(labels[target], "Edited label");
        for (const v of original.meta.variables) {
          if (v.name !== target) assert.equal(labels[v.name], v.label);
        }
        assert.deepEqual(
          await reader.readRows(0, reader.meta.nObs),
          await original.readRows(0, original.meta.nObs),
        );
      });
    });
  }

  test("writes nothing when one edit is invalid", async () => {
    await withCopy("modern118.dta", async (file) => {
      await assert.rejects(
        setVariableLabels(file, { b: "ok", missing_var: "x" }),
        /missing_var: no such variable/,
      );
      assert.deepEqual(differingOffsets(fixtureBytes("modern118.dta"), fs.readFileSync(file)), []);
    });
  });

  test("a shorter label leaves no tail of the longer one behind", async () => {
    await withCopy("survey118.dta", async (file) => {
      await setVariableLabels(file, { city: "A much longer label than the one before it" });
      await setVariableLabels(file, { city: "Short" });
      const bytes = fs.readFileSync(file);
      const reader = await open(bytes);
      const v = reader.meta.variables.find((x) => x.name === "city");
      assert.ok(v);
      const field = reader.variableLabelField(v.index);
      const raw = bytes.subarray(field.offset, field.offset + field.width);
      assert.equal(raw.subarray(0, 5).toString("utf8"), "Short");
      assert.ok(raw.subarray(5).every((b) => b === 0));
    });
  });
});

// ── value labels, attachments, dataset label ────────────────────────────────
//
// edit_cases.json is written by the Python editor (make_edit_cases.py) after
// its output was checked against a real Stata. Matching its hashes is what
// keeps this editor and stata_code/core/dta_edit.py byte-identical.

interface EditCase {
  name: string;
  fixture: string;
  edit: {
    variable_labels?: Record<string, string>;
    value_labels?: Record<string, Record<string, string> | null>;
    attach?: Record<string, string>;
    data_label?: string;
  };
  error?: string;
  sha256?: string;
  result?: {
    variable_labels: Array<{ name: string; before: string; after: string }>;
    value_labels: Record<string, string>;
    attached: Array<{ name: string; before: string; after: string }>;
    data_label: { name: string; before: string; after: string } | null;
    rewritten: boolean;
  };
}

const EDIT_CASES: EditCase[] = JSON.parse(
  fs.readFileSync(path.join(FIXTURES, "edit_cases.json"), "utf8"),
).cases;

function toEdit(raw: EditCase["edit"]): DtaLabelEdit {
  return {
    variableLabels: raw.variable_labels,
    valueLabels: raw.value_labels,
    attach: raw.attach,
    dataLabel: raw.data_label,
  };
}

function sha256(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}

describe("editLabels: cases shared with the Python editor", () => {
  for (const c of EDIT_CASES) {
    test(c.name, () =>
      withCopy(c.fixture, async (file) => {
        const before = sha256(fs.readFileSync(file));
        if (c.error !== undefined) {
          await assert.rejects(
            () => editLabels(file, toEdit(c.edit)),
            (err: unknown) => err instanceof DtaEditError && err.message.includes(c.error as string),
          );
          assert.equal(sha256(fs.readFileSync(file)), before, "a refused edit writes nothing");
          return;
        }
        const expected = c.result as NonNullable<EditCase["result"]>;
        const dry = await editLabels(file, toEdit(c.edit), { dryRun: true });
        assert.equal(sha256(fs.readFileSync(file)), before, "a dry run writes nothing");
        const result = await editLabels(file, toEdit(c.edit));
        assert.deepEqual(result, dry);
        assert.deepEqual(
          {
            variable_labels: result.variableLabels,
            value_labels: result.valueLabels,
            attached: result.attached,
            data_label: result.dataLabel,
            rewritten: result.rewritten,
          },
          expected,
        );
        assert.equal(sha256(fs.readFileSync(file)), c.sha256, "same bytes as the Python editor");
        // a second run has nothing left to do, and no temporary file stays behind
        const again = await editLabels(file, toEdit(c.edit));
        assert.equal(again.rewritten, false);
        assert.deepEqual(again.valueLabels, {});
        assert.deepEqual(fs.readdirSync(path.dirname(file)), [c.fixture]);
      }),
    );
  }
});

describe("editLabels", () => {
  test("the reader sees what was written", () =>
    withCopy("survey118.dta", async (file) => {
      await editLabels(file, {
        valueLabels: { agree: { "1": "Agree", "2": "Disagree", ".a": "Refused" } },
        attach: { score: "agree" },
        dataLabel: "调查",
      });
      const reader = await open(fs.readFileSync(file));
      const table = reader.meta.valueLabels.get("agree") as Map<number, string>;
      assert.deepEqual(
        [...table].map(([code, text]) => [valueLabelCode(code), text]),
        [
          ["1", "Agree"],
          ["2", "Disagree"],
          [".a", "Refused"],
        ],
      );
      assert.equal(reader.meta.variables.find((v) => v.name === "score")?.valueLabel, "agree");
      assert.equal(reader.meta.dataLabel, "调查");
      assert.deepEqual([...reader.meta.valueLabels.keys()], ["yn", "regionlbl", "agree"]);
      // the data are untouched
      const original = await open(fixtureBytes("survey118.dta"));
      assert.deepEqual(await reader.readRows(0, 500), await original.readRows(0, 500));
    }));

  test("an attachment alone is patched in place", async () => {
    const bytes = fixtureBytes("survey118.dta");
    const { splices, result } = planEdits(await open(bytes), { attach: { score: "yn" } });
    assert.equal(result.rewritten, false);
    const edited = applySplices(bytes, splices);
    const changed = differingOffsets(bytes, edited);
    assert.equal(changed.length, 2); // "yn" into one name field
  });

  test("a rewrite keeps every other set's bytes and fixes the map", async () => {
    const bytes = fixtureBytes("modern118.dta");
    const { splices, result } = planEdits(await open(bytes), {
      valueLabels: { extra: { "1": "one" } },
      dataLabel: "A dataset label that is longer",
    });
    assert.equal(result.rewritten, true);
    const edited = Buffer.from(applySplices(bytes, splices));
    const reader = await open(edited);
    const { map, fileSize } = reader.editLayout;
    assert.ok(map);
    assert.equal(map[13], fileSize);
    const tags = ["<stata_dta>", "<map>", "<variable_types>", "<varnames>", "<sortlist>"];
    tags.forEach((tag, i) => assert.equal(edited.subarray(map[i], map[i] + tag.length).toString(), tag));
    assert.equal(edited.subarray(map[12]).toString(), "</stata_dta>");
    const start = bytes.indexOf("<lbl>");
    const end = bytes.indexOf("</lbl>") + 6;
    assert.ok(edited.includes(bytes.subarray(start, end)));
    assert.deepEqual(reader.meta.notes, (await open(bytes)).meta.notes);
  });

  // A read-only directory stops the temporary file from being created; that
  // needs POSIX permissions and a user they apply to.
  const canLockDirectory = process.platform !== "win32" && process.getuid?.() !== 0;

  test("a failed rewrite leaves the file and no temporary file", { skip: !canLockDirectory }, () =>
    withCopy("survey118.dta", async (file) => {
      const before = sha256(fs.readFileSync(file));
      const dir = path.dirname(file);
      fs.chmodSync(dir, 0o500); // the temporary file cannot be created
      try {
        await assert.rejects(() => editLabels(file, { valueLabels: { yn: null } }));
      } finally {
        fs.chmodSync(dir, 0o700);
      }
      assert.equal(sha256(fs.readFileSync(file)), before);
      assert.deepEqual(fs.readdirSync(dir), ["survey118.dta"]);
    }));

  test("the file mode survives a rewrite", { skip: process.platform === "win32" }, () =>
    withCopy("survey118.dta", async (file) => {
      fs.chmodSync(file, 0o640);
      await editLabels(file, { valueLabels: { yn: null } });
      assert.equal(fs.statSync(file).mode & 0o777, 0o640);
    }));
});

describe("inverseEdit", () => {
  // Undo in the viewer is the inverse edit applied to the edited file; it has
  // to give back what the file said, whatever mix of changes was made.
  for (const c of EDIT_CASES.filter((x) => x.error === undefined)) {
    test(`undo restores the file: ${c.name}`, () =>
      withCopy(c.fixture, async (file) => {
        const original = fs.readFileSync(file);
        const before = (await open(original)).meta;
        const result = await editLabels(file, toEdit(c.edit));
        const undone = await editLabels(file, inverseEdit(before, result));
        assert.equal(editChanged(undone), editChanged(result));
        const restored = await open(fs.readFileSync(file));
        assert.deepEqual(restored.meta.valueLabels, before.valueLabels);
        assert.deepEqual(
          restored.meta.variables.map((v) => [v.name, v.label, v.valueLabel]),
          before.variables.map((v) => [v.name, v.label, v.valueLabel]),
        );
        assert.equal(restored.meta.dataLabel, before.dataLabel);
        assert.deepEqual(restored.meta.notes, before.notes);
        // Not byte-identical in general: Stata leaves stray bytes after a
        // field's terminator and lays out a label table its own way. What the
        // file says is the same, and so is every observation.
        const rows = Math.min(before.nObs, 50);
        assert.deepEqual(
          await restored.readRows(0, rows),
          await (await open(original)).readRows(0, rows),
        );
      }),
    );
  }

  test("describeEdit says what happened", async () => {
    const reader = await open(fixtureBytes("survey118.dta"));
    const { result } = planEdits(reader, {
      variableLabels: { wage: "Hourly wage", id: "" },
      valueLabels: { yn: null, fresh: { "1": "one" } },
      attach: { score: "fresh" },
      dataLabel: "",
    });
    assert.equal(
      describeEdit(result),
      "Labeled wage; removed the label of id; dropped value label yn; defined value label fresh; " +
        "detached yn from female; attached fresh to score; removed the dataset label",
    );
    assert.equal(describeEdit(planEdits(reader, {}).result), "");
  });
});

describe("formats 102-111 are read-only", () => {
  test("label edits are refused before anything is planned", async () => {
    const file = path.join(__dirname, "..", "test-fixtures", "dta", "old", "stata4_105.dta");
    const reader = await DtaReader.open(new BufferByteSource(new Uint8Array(fs.readFileSync(file))));
    assert.throws(
      () => planLabelEdits(reader, { fulllab: "x" }),
      /format-105 file \(Stata 7 or older\), which can be viewed but not edited/,
    );
    assert.throws(() => planEdits(reader, { dataLabel: "x" }), /can be viewed but not edited/);
  });
});
