import { strict as assert } from "node:assert";
import { describe, test } from "node:test";

import {
  buildDataPreviewCode,
  clampPreviewObs,
  DEFAULT_DATA_PREVIEW_OBS,
  formatDataPreviewDocument,
  stripCommandEcho,
} from "./dataPreview";
import type { RunResult } from "./types/runResult";

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
      frame: "main",
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

describe("buildDataPreviewCode", () => {
  test("selects rows with `if _n <=` so short datasets do not error", () => {
    const code = buildDataPreviewCode(50);
    assert.match(code, /list if _n <= 50, clean noobs abbreviate\(24\)/);
    // `in 1/N` is r(198) on any dataset with fewer than N observations.
    assert.equal(/\bin 1\//.test(code), false);
  });

  test("widens linesize for the listing and restores the user's value", () => {
    const lines = buildDataPreviewCode(50).split("\n");
    assert.equal(lines[0], "local _sc_linesize = c(linesize)");
    assert.match(lines[1], /^quietly set linesize \d+$/);
    assert.equal(lines[3], "quietly set linesize `_sc_linesize'");
  });

  test("clamps out-of-range row counts", () => {
    assert.match(buildDataPreviewCode(0), /_n <= 1,/);
    assert.match(buildDataPreviewCode(1e9), /_n <= 10000,/);
    assert.equal(clampPreviewObs(undefined), DEFAULT_DATA_PREVIEW_OBS);
    assert.equal(clampPreviewObs(12.7), 12);
  });
});

describe("stripCommandEcho", () => {
  test("drops echoed commands, their continuations, and blank runs", () => {
    const text = [
      "",
      ". quietly set linesize 250",
      "",
      ". list if _n <= 50, clean noobs abbreviate(24)",
      "> more of the echoed command",
      "",
      "    make       price",
      "    AMC Concord  4,099",
      "",
      ". ",
    ].join("\n");
    assert.equal(stripCommandEcho(text), "    make       price\n    AMC Concord  4,099");
  });

  test("keeps `>` continuations that belong to listed output", () => {
    const text = ". list\n    AMC Concord  4,099\n>     121   3.58";
    assert.equal(stripCommandEcho(text), "    AMC Concord  4,099\n>     121   3.58");
  });
});

describe("formatDataPreviewDocument", () => {
  test("reports a fully listed dataset and its variables", () => {
    const text = formatDataPreviewDocument(result(), ". list\n    1. 22", 50);
    assert.match(text, /stata-code data preview · session main/);
    assert.match(text, /2 obs × 1 vars · showing all 2/);
    assert.match(text, /variables \(1\)/);
    assert.match(text, /mpg {2}int {2}Mileage/);
    assert.match(text, /1\. 22/);
    assert.equal(/^\. list$/m.test(text), false);
  });

  test("says how much of a long dataset is shown", () => {
    const text = formatDataPreviewDocument(
      result({ dataset: { ...result().dataset, n_obs: 500 } }),
      "rows",
      50,
    );
    assert.match(text, /showing first 50 of 500 \(raise stataCode\.dataPreviewObs for more\)/);
  });

  test("surfaces the failure instead of claiming zero rows", () => {
    const failed = result({
      ok: false,
      rc: 198,
      error: {
        kind: "syntax",
        rc: 198,
        rc_label: "invalid syntax",
        message: "observation numbers out of range",
        command: "list",
        line: 1,
        context: { before: [], failing: "list", after: [] },
        commands_executed: 0,
        path: null,
        varname: null,
        name: null,
        suggestions: [],
      },
    });
    const text = formatDataPreviewDocument(failed, "", 50);
    assert.match(text, /preview failed: rc=198 observation numbers out of range/);
    assert.equal(/showing: 0 of/.test(text), false);
  });

  test("names the frame only when it differs from the session", () => {
    const other = result({ dataset: { ...result().dataset, frame: "study1", changed: true } });
    assert.match(formatDataPreviewDocument(other, "rows", 50), /frame study1 · unsaved changes/);
    assert.equal(/frame main/.test(formatDataPreviewDocument(result(), "rows", 50)), false);
  });

  test("handles an empty session", () => {
    const empty = result({
      dataset: { frame: "main", n_obs: 0, n_vars: 0, changed: false, filename: null, variables: [] },
    });
    const text = formatDataPreviewDocument(empty, "", 50);
    assert.match(text, /no observations/);
    assert.match(text, /\(no data in memory\)/);
    assert.match(text, /variables: \(none\)/);
  });
});
