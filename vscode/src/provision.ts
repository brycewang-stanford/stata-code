// Pure, testable planning for one-click provisioning of the stata-code MCP
// server into a workspace-local virtual environment.
//
// The extension already discovers a `.venv` / `venv` inside a workspace folder
// (see serverLaunch.ts). This module builds the exact commands that CREATE such
// a venv and install the package into it, so a user with no Python setup can go
// from "server not found" to "ready" with one click — matching the zero-config
// onboarding of editor-only competitors, without giving up the typed engine.
//
// Kept free of `vscode` and `child_process` so `node --test` can cover the plan
// shape; the extension layer runs the returned command in an integrated
// terminal.

import * as path from "node:path";

export interface ProvisionInputs {
  workspaceRoot: string;
  python?: string; // interpreter to bootstrap the venv (default: python3)
  platform?: NodeJS.Platform;
  venvName?: string; // default ".venv"
}

export interface ProvisionPlan {
  venvDir: string;
  venvPython: string;
  serverCommand: string;
  // argv-style steps (for programmatic execution / assertions)
  createVenv: string[];
  upgradePip: string[];
  installPackage: string[];
  // a single shell line for an integrated terminal
  terminalCommand: string;
}

const PACKAGE_SPEC = "stata-code[mcp]";

function pathFor(platform: NodeJS.Platform): path.PlatformPath {
  return platform === "win32" ? path.win32 : path.posix;
}

/**
 * Default interpreter names to try when bootstrapping, most-preferred first.
 * The extension resolves the first that exists; on Windows `py -3` is common.
 */
export function defaultPythonCandidates(platform: NodeJS.Platform): string[] {
  return platform === "win32" ? ["python", "py"] : ["python3", "python"];
}

/** Build the venv layout for a platform. */
export function venvLayout(
  venvDir: string,
  platform: NodeJS.Platform,
): { venvPython: string; serverCommand: string } {
  const pp = pathFor(platform);
  if (platform === "win32") {
    return {
      venvPython: pp.join(venvDir, "Scripts", "python.exe"),
      serverCommand: pp.join(venvDir, "Scripts", "stata-code-mcp.exe"),
    };
  }
  return {
    venvPython: pp.join(venvDir, "bin", "python"),
    serverCommand: pp.join(venvDir, "bin", "stata-code-mcp"),
  };
}

export function buildProvisionPlan(inputs: ProvisionInputs): ProvisionPlan {
  const platform = inputs.platform ?? process.platform;
  const pp = pathFor(platform);
  const python = inputs.python ?? defaultPythonCandidates(platform)[0];
  const venvName = inputs.venvName ?? ".venv";
  const venvDir = pp.join(inputs.workspaceRoot, venvName);
  const { venvPython, serverCommand } = venvLayout(venvDir, platform);

  const createVenv = [python, "-m", "venv", venvDir];
  const upgradePip = [venvPython, "-m", "pip", "install", "--upgrade", "pip"];
  const installPackage = [venvPython, "-m", "pip", "install", PACKAGE_SPEC];

  return {
    venvDir,
    venvPython,
    serverCommand,
    createVenv,
    upgradePip,
    installPackage,
    terminalCommand: buildTerminalCommand(
      { createVenv, upgradePip, installPackage },
      platform,
    ),
  };
}

/**
 * Compose a single shell command line that runs the three steps in sequence,
 * stopping on the first failure. Uses `&&` on POSIX and `;` chained with
 * short-circuiting emulation via `&&` on PowerShell-compatible shells (VS Code
 * terminals accept `&&` on modern PowerShell and cmd).
 */
export function buildTerminalCommand(
  steps: { createVenv: string[]; upgradePip: string[]; installPackage: string[] },
  _platform: NodeJS.Platform,
): string {
  const quote = (argv: string[]): string => argv.map(quoteArg).join(" ");
  return [
    quote(steps.createVenv),
    quote(steps.upgradePip),
    quote(steps.installPackage),
  ].join(" && ");
}

function quoteArg(arg: string): string {
  // Leave only shell-safe args unquoted; quote anything with whitespace, quotes,
  // parens, or glob/bracket characters (e.g. the `stata-code[mcp]` package spec).
  if (arg.length > 0 && /^[A-Za-z0-9_./=+:@-]+$/.test(arg)) return arg;
  return `"${arg.replace(/"/g, '\\"')}"`;
}
