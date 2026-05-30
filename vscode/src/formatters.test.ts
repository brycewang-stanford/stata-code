import { strict as assert } from "node:assert";
import { describe, test } from "node:test";

import {
  formatDataPreviewDocument,
  formatLogDocument,
  inlineLogText,
  matrixToTsv,
} from "./formatters";
import type { Matrix, RunResult } from "./types/runResult";

function result(overrides: Partial<RunResult> = {}): RunResult {
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
      head: "head",
      tail: "",
      lines_total: 1,
      bytes_total: 4,
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
      n_obs: 2,
      n_vars: 1,
      changed: false,
      filename: null,
      variables: [{ name: "mpg", type: "int", label: "Mileage" }],
    },
    graphs: [],
    warnings: [],
    error: null,
    schema_version: "1.0",
    capabilities: [],
    ...overrides,
  };
}

describe("inlineLogText", () => {
  test("combines truncated head and tail with a marker", () => {
    const text = inlineLogText(
      result({
        log: {
          ...result().log,
          head: "first",
          tail: "last",
          lines_total: 200,
          truncated: true,
          ref: "log://x",
        },
      }),
    );
    assert.match(text, /first/);
    assert.match(text, /200 lines total/);
    assert.match(text, /last/);
  });
});

describe("formatLogDocument", () => {
  test("includes stable run metadata", () => {
    const text = formatLogDocument(result(), "display 1");
    assert.match(text, /status: OK/);
    assert.match(text, /session: main/);
    assert.match(text, /request: run123456789/);
    assert.match(text, /display 1/);
  });
});

describe("formatDataPreviewDocument", () => {
  test("shows capped row count and variables", () => {
    const text = formatDataPreviewDocument(result(), "1. 22", 1);
    assert.match(text, /showing: 1 of 2 observations/);
    assert.match(text, /mpg\tint\tMileage/);
  });
});

describe("matrixToTsv", () => {
  test("formats rows, columns, nulls, and compact numbers", () => {
    const matrix: Matrix = {
      rows: ["r1", "r2"],
      cols: ["c1", "c2"],
      values: [
        [1, null],
        [1.23456789012345, 2],
      ],
      ref: null,
    };
    assert.equal(
      matrixToTsv("b", matrix),
      "# b\n\tc1\tc2\nr1\t1\t\nr2\t1.23456789012\t2\n",
    );
  });
});
