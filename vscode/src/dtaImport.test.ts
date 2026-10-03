import { strict as assert } from "node:assert";
import * as fs from "node:fs";
import * as path from "node:path";
import { describe, test } from "node:test";

import { codebookCsv, valueLabelsCsv } from "./dtaExport";
import { CodebookError, editFromCodebook, parseCsv } from "./dtaImport";
import { BufferByteSource, DtaReader } from "./dtaReader";
import { applySplices, describeEdit, editChanged, planEdits } from "./dtaWriter";

const FIXTURES = path.join(__dirname, "..", "test-fixtures", "dta");

async function open(name: string): Promise<{ bytes: Buffer; reader: DtaReader }> {
  const bytes = fs.readFileSync(path.join(FIXTURES, name));
  return { bytes, reader: await DtaReader.open(new BufferByteSource(bytes)) };
}

describe("parseCsv", () => {
  test("quotes, doubled quotes, line breaks in quotes, CRLF, BOM", () => {
    const text = '﻿a,b,c\r\n1,"x, y","say ""hi"""\n2,"two\nlines",\n\n3,,z';
    assert.deepEqual(parseCsv(text), [
      ["a", "b", "c"],
      ["1", "x, y", 'say "hi"'],
      ["2", "two\nlines", ""],
      ["3", "", "z"],
    ]);
  });

  test("an unterminated quote is an error", () => {
    assert.throws(() => parseCsv('a\n"open'), CodebookError);
  });
});

describe("editFromCodebook", () => {
  for (const name of ["survey118.dta", "modern118.dta", "legacy115.dta", "strl117.dta"]) {
    test(`${name}: importing what was exported changes nothing`, async () => {
      const { reader } = await open(name);
      const edit = editFromCodebook(codebookCsv(reader.meta), valueLabelsCsv(reader.meta));
      const { splices, result } = planEdits(reader, edit);
      assert.equal(editChanged(result), false, describeEdit(result));
      assert.equal(splices.length, 0);
    });
  }

  test("an edited codebook becomes one edit", async () => {
    const { bytes, reader } = await open("survey118.dta");
    const codebook = codebookCsv(reader.meta)
      .replace(",Annual income,", ',"Income, annual (USD)",')
      .replace(/^(\d+),wage,float,%9\.0g,,,/m, "$1,wage,float,%9.0g,,Hourly wage,")
      .replace(/^(\d+),score,int,%8\.0g,,/m, "$1,score,int,%8.0g,yn,");
    const labels = (valueLabelsCsv(reader.meta) as string) + "yn,.a,Refused\n";
    const { splices, result } = planEdits(reader, editFromCodebook(codebook, labels));
    assert.equal(
      describeEdit(result),
      "Relabeled income; labeled wage; modified value label yn; attached yn to score",
    );
    const edited = await DtaReader.open(new BufferByteSource(applySplices(bytes, splices)));
    const byName = new Map(edited.meta.variables.map((v) => [v.name, v]));
    assert.equal(byName.get("income")?.label, "Income, annual (USD)");
    assert.equal(byName.get("wage")?.label, "Hourly wage");
    assert.equal(byName.get("score")?.valueLabel, "yn");
    assert.equal(edited.meta.valueLabels.get("yn")?.size, 3);
    // sets the file does not mention are left alone
    assert.deepEqual(edited.meta.valueLabels.get("regionlbl"), reader.meta.valueLabels.get("regionlbl"));
  });

  test("only the columns present are applied", async () => {
    const { reader } = await open("survey118.dta");
    const edit = editFromCodebook("name,label\nwage,Hourly wage\n");
    assert.deepEqual(edit, { variableLabels: { wage: "Hourly wage" } });
    assert.equal(describeEdit(planEdits(reader, edit).result), "Labeled wage");
    assert.deepEqual(editFromCodebook("Name,Value_Label\nscore,yn\n"), { attach: { score: "yn" } });
  });

  test("a codebook the file cannot take is refused by the planner", async () => {
    const { reader } = await open("survey118.dta");
    assert.throws(
      () => planEdits(reader, editFromCodebook("name,label\nnope,x\n")),
      /nope: no such variable/,
    );
    assert.throws(
      () => planEdits(reader, editFromCodebook("name,value_label\ncity,yn\n")),
      /city: is a string variable/,
    );
  });

  test("malformed files say what is wrong", () => {
    assert.throws(() => editFromCodebook(""), /empty/);
    assert.throws(() => editFromCodebook("variable,label\nwage,x\n"), /no "name" column/);
    assert.throws(() => editFromCodebook("name,type\nwage,float\n"), /neither/);
    assert.throws(() => editFromCodebook("name,label\nwage,a\nwage,b\n"), /wage appears twice/);
    assert.throws(() => editFromCodebook("name,label\nwage,a\n", "value_label,value\nyn,1\n"), /columns/);
    assert.throws(
      () => editFromCodebook("name,label\nwage,a\n", "value_label,value,text\nyn,1,a\nyn,1,b\n"),
      /yn lists 1 twice/,
    );
  });
});
