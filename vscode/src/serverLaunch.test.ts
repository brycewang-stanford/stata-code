import { strict as assert } from "node:assert";
import * as path from "node:path";
import { describe, test } from "node:test";

import {
  buildServerLaunchCandidates,
  normalizeConfiguredCommand,
  parseCommandLine,
} from "./serverLaunch";

describe("buildServerLaunchCandidates", () => {
  test("prefers local venv entrypoint and preserves fallback candidates", () => {
    const existing = new Set([
      "/work/project/.venv/bin/stata-code-mcp",
      "/work/project/venv/bin/python",
      "/work/project/stata_code/mcp/__main__.py",
      "/usr/local/bin/python3",
    ]);

    const candidates = buildServerLaunchCandidates({
      configuredCommand: "stata-code-mcp",
      configuredArgs: [],
      workspaceRoot: "/work/project",
      stataPythonPath: "~/stata-python",
      pythonDefaultInterpreterPath: "/opt/python/bin/python",
      pythonPythonPath: "/legacy/python",
      envPath: "/bin",
      envPythonPath: "/existing/pythonpath",
      homeDir: "/home/user",
      platform: "darwin",
      exists: (target) => existing.has(target),
    });

    assert.equal(candidates[0].command, "/work/project/.venv/bin/stata-code-mcp");
    assert.deepEqual(candidates[0].args, []);
    assert.equal(candidates[0].cwd, "/work/project");
    assert.equal(
      candidates[0].env?.PYTHONPATH,
      ["/work/project", "/existing/pythonpath"].join(path.delimiter),
    );

    assert.ok(
      candidates.some(
        (candidate) =>
          candidate.command === "stata-code-mcp" && candidate.args.length === 0,
      ),
    );
    assert.ok(
      candidates.some(
        (candidate) =>
          candidate.command === "/work/project/venv/bin/python" &&
          candidate.args.join(" ") === "-m stata_code.mcp",
      ),
    );
    assert.ok(
      candidates.some((candidate) => candidate.command === "/home/user/stata-python"),
    );
    assert.ok(
      candidates.some((candidate) => candidate.command === "/opt/python/bin/python"),
    );
    assert.ok(
      candidates.some((candidate) => candidate.command === "/usr/local/bin/python3"),
    );
  });

  test("custom inline command returns one explicit candidate", () => {
    const candidates = buildServerLaunchCandidates({
      configuredCommand: "python -m stata_code.mcp",
      configuredArgs: ["--flag"],
      workspaceRoot: "/work/project",
      envPath: "/bin",
      homeDir: "/home/user",
      platform: "darwin",
      exists: () => false,
    });

    assert.equal(candidates.length, 1);
    assert.equal(candidates[0].command, "python");
    assert.deepEqual(candidates[0].args, ["-m", "stata_code.mcp", "--flag"]);
    assert.equal(candidates[0].cwd, "/work/project");
  });
});

describe("normalizeConfiguredCommand", () => {
  test("keeps an existing path with spaces intact", () => {
    const [command, args] = normalizeConfiguredCommand(
      "/Applications/My Python/bin/python",
      ["-m", "stata_code.mcp"],
      "/home/user",
      (target) => target === "/Applications/My Python/bin/python",
    );

    assert.equal(command, "/Applications/My Python/bin/python");
    assert.deepEqual(args, ["-m", "stata_code.mcp"]);
  });
});

describe("parseCommandLine", () => {
  test("parses quoted values and escapes", () => {
    assert.deepEqual(
      parseCommandLine('"/path with spaces/python" -m stata_code.mcp --name "a b"'),
      ["/path with spaces/python", "-m", "stata_code.mcp", "--name", "a b"],
    );
    assert.deepEqual(parseCommandLine("python\\ 3 -m stata_code.mcp"), [
      "python 3",
      "-m",
      "stata_code.mcp",
    ]);
  });
});
