import { strict as assert } from "node:assert";
import { describe, test } from "node:test";

import { probeServerLaunch } from "./serverProbe";

describe("probeServerLaunch", () => {
  test("returns found when an absolute candidate exists on disk", () => {
    const existing = new Set(["/work/.venv/bin/stata-code-mcp"]);
    const result = probeServerLaunch({
      candidates: [
        { command: "/work/.venv/bin/stata-code-mcp", args: [] },
        { command: "stata-code-mcp", args: [] },
      ],
      envPath: "",
      platform: "darwin",
      exists: (target) => existing.has(target),
    });
    assert.equal(result.status, "found");
    assert.equal(result.resolved?.command, "/work/.venv/bin/stata-code-mcp");
  });

  test("returns found when a bare command resolves on PATH", () => {
    const existing = new Set(["/opt/homebrew/bin/stata-code-mcp"]);
    const result = probeServerLaunch({
      candidates: [{ command: "stata-code-mcp", args: [] }],
      envPath: "/usr/local/bin:/opt/homebrew/bin",
      platform: "darwin",
      exists: (target) => existing.has(target),
    });
    assert.equal(result.status, "found");
    assert.equal(result.resolved?.command, "stata-code-mcp");
  });

  test("returns found when a python module fallback exists", () => {
    const existing = new Set(["/usr/local/bin/python3"]);
    const result = probeServerLaunch({
      candidates: [
        { command: "stata-code-mcp", args: [] },
        { command: "python3", args: ["-m", "stata_code.mcp"] },
      ],
      envPath: "/usr/local/bin",
      platform: "linux",
      exists: (target) => existing.has(target),
    });
    assert.equal(result.status, "found");
    assert.equal(result.resolved?.command, "python3");
    assert.deepEqual(result.resolved?.args, ["-m", "stata_code.mcp"]);
  });

  test("returns missing when nothing on disk matches", () => {
    const result = probeServerLaunch({
      candidates: [
        { command: "stata-code-mcp", args: [] },
        { command: "python3", args: ["-m", "stata_code.mcp"] },
      ],
      envPath: "/usr/bin",
      platform: "linux",
      exists: () => false,
    });
    assert.equal(result.status, "missing");
    assert.equal(result.checkedCandidates, 2);
    assert.equal(result.resolved, undefined);
  });

  test("recognizes Windows executable suffixes", () => {
    const existing = new Set(["C:\\Python\\Scripts\\stata-code-mcp.exe"]);
    const result = probeServerLaunch({
      candidates: [{ command: "stata-code-mcp", args: [] }],
      envPath: "C:\\Python\\Scripts",
      platform: "win32",
      exists: (target) => existing.has(target),
    });
    assert.equal(result.status, "found");
  });

  test("ignores empty PATH entries", () => {
    const existing = new Set(["/usr/local/bin/stata-code-mcp"]);
    const result = probeServerLaunch({
      candidates: [{ command: "stata-code-mcp", args: [] }],
      envPath: "::/usr/local/bin::",
      platform: "linux",
      exists: (target) => existing.has(target),
    });
    assert.equal(result.status, "found");
  });
});
