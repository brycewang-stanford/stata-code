import { strict as assert } from "node:assert";
import { describe, test } from "node:test";

import { baseName, buildOutputNodes, extName, outputIcon, outputKind } from "./outputs";
import type { OutputRun } from "./outputs";
import type { RunResult } from "./types/runResult";

function run(runId: string, ts: number, outputPaths: string[], sessionId = "main"): OutputRun {
  const result = {
    ok: true,
    rc: 0,
    session_id: sessionId,
    request_id: runId,
    started_at: "2026-05-30T12:00:00.000Z",
    elapsed_ms: 1,
    stata_elapsed_ms: 1,
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
      files: outputPaths.length
        ? {
            directory: `/bundle/${runId}`,
            log_path: `/bundle/${runId}/run.log`,
            smcl_path: `/bundle/${runId}/run.smcl`,
            manifest_path: `/bundle/${runId}/manifest.json`,
            output_paths: outputPaths,
          }
        : null,
    },
    results: {
      r: { scalars: {}, macros: {}, matrices: {} },
      e: { scalars: {}, macros: {}, matrices: {} },
      last_estimation_cmd: null,
    },
    dataset: { frame: "default", n_obs: 0, n_vars: 0, changed: false, filename: null, variables: [] },
    graphs: [],
    warnings: [],
    error: null,
    schema_version: "1.0",
    capabilities: [],
  } as unknown as RunResult;
  return { runId, ts, result };
}

describe("baseName / extName", () => {
  test("extract filename and lowercased extension", () => {
    assert.equal(baseName("/x/y/table.tex"), "table.tex");
    assert.equal(baseName("C:\\out\\Table.RTF"), "Table.RTF");
    assert.equal(extName("/x/Table.RTF"), ".rtf");
    assert.equal(extName("/x/noext"), "");
  });
});

describe("outputKind / outputIcon", () => {
  test("classifies common econ export formats", () => {
    assert.equal(outputKind("results.tex"), "table");
    assert.equal(outputKind("results.csv"), "table");
    assert.equal(outputKind("results.rtf"), "doc");
    assert.equal(outputKind("results.xlsx"), "doc");
    assert.equal(outputKind("clean.dta"), "data");
    assert.equal(outputKind("fig.png"), "image");
    assert.equal(outputKind("notes.log"), "other");
  });

  test("icons follow the kind", () => {
    assert.equal(outputIcon("a.tex"), "table");
    assert.equal(outputIcon("a.dta"), "database");
    assert.equal(outputIcon("a.png"), "file-media");
    assert.equal(outputIcon("a.bin"), "file");
  });
});

describe("buildOutputNodes", () => {
  test("empty when no run produced artifacts", () => {
    assert.deepEqual(buildOutputNodes([]), []);
    assert.deepEqual(buildOutputNodes([run("r1", 1, [])]), []);
  });

  test("flattens artifacts newest-run-first", () => {
    const nodes = buildOutputNodes([
      run("r1", 1, ["/bundle/r1/outputs/old.tex"]),
      run("r2", 2, ["/bundle/r2/outputs/new.csv"]),
    ]);
    assert.equal(nodes.length, 2);
    assert.equal(nodes[0].label, "new.csv"); // newest run first
    assert.equal(nodes[0].kind, "table");
    assert.match(nodes[0].description, /CSV · main/);
    assert.equal(nodes[1].label, "old.tex");
  });

  test("de-duplicates a repeated path, attributing it to the newest run", () => {
    const shared = "/bundle/outputs/main_table.tex";
    const nodes = buildOutputNodes([
      run("r1", 1, [shared], "first"),
      run("r2", 2, [shared], "second"),
    ]);
    assert.equal(nodes.length, 1);
    assert.match(nodes[0].description, /second/); // newest run wins
  });

  test("carries the full path as tooltip for open/reveal actions", () => {
    const nodes = buildOutputNodes([run("r1", 1, ["/bundle/r1/outputs/t.rtf"])]);
    assert.equal(nodes[0].path, "/bundle/r1/outputs/t.rtf");
    assert.equal(nodes[0].tooltip, "/bundle/r1/outputs/t.rtf");
    assert.equal(nodes[0].kind, "doc");
  });
});
