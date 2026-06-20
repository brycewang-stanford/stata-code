import { strict as assert } from "node:assert";
import { describe, test } from "node:test";

import { baseName, buildDataNodes, variableIcon } from "./dataBrowser";
import type { DatasetInfo, RunResult } from "./types/runResult";

function result(dataset: Partial<DatasetInfo> = {}): RunResult {
  return {
    ok: true,
    rc: 0,
    session_id: "main",
    request_id: "run123456789",
    started_at: "2026-05-30T12:00:00.000Z",
    elapsed_ms: 12,
    stata_elapsed_ms: 10,
    stata: { version: "18.0", edition: "MP", backend: "pystata" },
    log: {
      head: "",
      tail: "",
      lines_total: 0,
      bytes_total: 0,
      truncated: false,
      complete: true,
      error_window: null,
      ref: null,
    },
    results: {
      r: { scalars: {}, macros: {}, matrices: {} },
      e: { scalars: {}, macros: {}, matrices: {} },
      last_estimation_cmd: null,
    },
    dataset: {
      frame: "default",
      n_obs: 74,
      n_vars: 2,
      changed: false,
      filename: null,
      variables: [
        { name: "mpg", type: "int", label: "Mileage (mpg)" },
        { name: "make", type: "str18", label: "" },
      ],
      ...dataset,
    },
    graphs: [],
    warnings: [],
    error: null,
    schema_version: "1.0",
    capabilities: [],
  };
}

describe("variableIcon", () => {
  test("string storage types get a string glyph", () => {
    assert.equal(variableIcon("str18"), "symbol-string");
    assert.equal(variableIcon("strL"), "symbol-string");
    assert.equal(variableIcon("STR4"), "symbol-string"); // case-insensitive
  });

  test("numeric storage types get a number glyph", () => {
    for (const t of ["byte", "int", "long", "float", "double"]) {
      assert.equal(variableIcon(t), "symbol-number");
    }
  });
});

describe("baseName", () => {
  test("returns the last path component", () => {
    assert.equal(baseName("/Users/x/data/auto.dta"), "auto.dta");
    assert.equal(baseName("C:\\data\\auto.dta"), "auto.dta");
    assert.equal(baseName("auto.dta"), "auto.dta");
  });
});

describe("buildDataNodes", () => {
  test("returns [] with no result so the welcome view shows", () => {
    assert.deepEqual(buildDataNodes(undefined), []);
  });

  test("shows an empty placeholder when no variables are in memory", () => {
    const nodes = buildDataNodes(result({ n_vars: 0, variables: [] }));
    assert.equal(nodes.length, 1);
    assert.equal(nodes[0].kind, "empty");
  });

  test("emits a summary row followed by one row per variable", () => {
    const nodes = buildDataNodes(result());
    assert.equal(nodes[0].kind, "summary");
    assert.match(nodes[0].label, /74 obs × 2 vars/);
    assert.match(nodes[0].description ?? "", /frame default/);

    const vars = nodes.filter((n) => n.kind === "variable");
    assert.equal(vars.length, 2);

    const mpg = vars[0];
    assert.equal(mpg.label, "mpg");
    assert.equal(mpg.varName, "mpg");
    assert.equal(mpg.icon, "symbol-number");
    assert.match(mpg.description ?? "", /int · Mileage \(mpg\)/);

    // A variable with no label falls back to just the type.
    const make = vars[1];
    assert.equal(make.icon, "symbol-string");
    assert.equal(make.description, "str18");
  });

  test("summary marks a modified dataset and surfaces the filename tooltip", () => {
    const nodes = buildDataNodes(
      result({ changed: true, filename: "/data/auto.dta" }),
    );
    assert.match(nodes[0].description ?? "", /modified/);
    assert.equal(nodes[0].tooltip, "auto.dta");
  });
});
