import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { buildRunArguments } from "./runArgs";

describe("buildRunArguments", () => {
  it("always asks the server for full matrix values", () => {
    // Regression guard: the server default is `include_results: "scalars"`,
    // which returns every matrix as a `matrix://` stub with `values: null`.
    // The sidebar renders `matrix.values` directly, so accepting that default
    // silently empties the "last result" matrix view.
    const args = buildRunArguments("regress mpg weight");
    assert.equal(args.include_results, "full");
    assert.equal(args.code, "regress mpg weight");
  });

  it("omits options the caller did not set", () => {
    const args = buildRunArguments("display 1");
    assert.deepEqual(Object.keys(args).sort(), ["code", "include_results"]);
  });

  it("maps camelCase options onto the wire names", () => {
    const args = buildRunArguments("display 1", {
      sessionId: "alt",
      includeFullLog: true,
      includeGraphs: "inline",
      persistLogFiles: true,
      persistGeneratedFiles: false,
      originPath: "/w/a.do",
      originKind: "selection",
      originLabel: "a.do:3",
      useOriginWorkdir: false,
      workingDir: "/w",
    });
    assert.deepEqual(args, {
      code: "display 1",
      include_results: "full",
      session_id: "alt",
      include_full_log: true,
      include_graphs: "inline",
      persist_log_files: true,
      persist_generated_files: false,
      origin_path: "/w/a.do",
      origin_kind: "selection",
      origin_label: "a.do:3",
      use_origin_workdir: false,
      working_dir: "/w",
    });
  });

  it("preserves explicit false values rather than dropping them", () => {
    const args = buildRunArguments("display 1", { includeFullLog: false });
    assert.equal(args.include_full_log, false);
  });
});
