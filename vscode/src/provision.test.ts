import assert from "node:assert";
import { test } from "node:test";

import {
  buildProvisionPlan,
  buildTerminalCommand,
  defaultPythonCandidates,
  venvLayout,
} from "./provision";

test("defaultPythonCandidates differ by platform", () => {
  assert.deepEqual(defaultPythonCandidates("linux"), ["python3", "python"]);
  assert.deepEqual(defaultPythonCandidates("win32"), ["python", "py"]);
});

test("venvLayout posix", () => {
  const { venvPython, serverCommand } = venvLayout("/ws/.venv", "linux");
  assert.equal(venvPython, "/ws/.venv/bin/python");
  assert.equal(serverCommand, "/ws/.venv/bin/stata-code-mcp");
});

test("venvLayout windows", () => {
  const { venvPython, serverCommand } = venvLayout("C:\\ws\\.venv", "win32");
  assert.equal(venvPython, "C:\\ws\\.venv\\Scripts\\python.exe");
  assert.equal(serverCommand, "C:\\ws\\.venv\\Scripts\\stata-code-mcp.exe");
});

test("buildProvisionPlan posix uses workspace venv and venv python for install", () => {
  const plan = buildProvisionPlan({ workspaceRoot: "/ws", platform: "linux" });
  assert.equal(plan.venvDir, "/ws/.venv");
  assert.deepEqual(plan.createVenv, ["python3", "-m", "venv", "/ws/.venv"]);
  assert.deepEqual(plan.installPackage, [
    "/ws/.venv/bin/python",
    "-m",
    "pip",
    "install",
    "stata-code[mcp]",
  ]);
  assert.equal(plan.serverCommand, "/ws/.venv/bin/stata-code-mcp");
});

test("buildProvisionPlan honours a custom python and venv name", () => {
  const plan = buildProvisionPlan({
    workspaceRoot: "/ws",
    platform: "linux",
    python: "/opt/py/bin/python3.12",
    venvName: "venv",
  });
  assert.equal(plan.venvDir, "/ws/venv");
  assert.equal(plan.createVenv[0], "/opt/py/bin/python3.12");
});

test("terminal command chains steps with && and quotes spaced paths", () => {
  const cmd = buildTerminalCommand(
    {
      createVenv: ["python3", "-m", "venv", "/a b/.venv"],
      upgradePip: ["/a b/.venv/bin/python", "-m", "pip", "install", "--upgrade", "pip"],
      installPackage: ["/a b/.venv/bin/python", "-m", "pip", "install", "stata-code[mcp]"],
    },
    "linux",
  );
  assert.ok(cmd.includes(" && "));
  assert.ok(cmd.includes('"/a b/.venv"'));
  // package spec has brackets -> must be quoted so shells don't glob it
  assert.ok(cmd.includes('"stata-code[mcp]"'));
});
