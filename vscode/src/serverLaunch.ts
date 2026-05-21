import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import type { StataServerLaunch } from "./mcpClient";

export const DEFAULT_SERVER_COMMAND = "stata-code-mcp";

export interface ServerLaunchConfig {
  configuredCommand: string;
  configuredArgs: string[];
  workspaceRoot?: string;
  extensionRoot?: string;
  stataPythonPath?: string;
  pythonDefaultInterpreterPath?: string;
  pythonPythonPath?: string;
  processEnv?: NodeJS.ProcessEnv;
  envPath?: string;
  envPythonPath?: string;
  homeDir?: string;
  platform?: NodeJS.Platform;
  exists?: (target: string) => boolean;
}

export function buildServerLaunchCandidates(config: ServerLaunchConfig): StataServerLaunch[] {
  const {
    configuredCommand,
    configuredArgs,
    workspaceRoot,
    extensionRoot,
    processEnv = process.env,
    homeDir = os.homedir(),
    platform = process.platform,
    exists = fs.existsSync,
  } = config;
  const envPath = config.envPath ?? processEnv.PATH ?? processEnv.Path;
  const envPythonPath = config.envPythonPath ?? processEnv.PYTHONPATH;
  const cwd = workspaceRoot;
  const sourceRoots = localSourceRoots(workspaceRoot, extensionRoot, exists);
  const venvDirs = localVenvDirs(workspaceRoot, sourceRoots);
  const env = serverEnvironment(
    sourceRoots,
    envPath,
    envPythonPath,
    homeDir,
    platform,
    processEnv,
  );
  const [command, inlineArgs] = normalizeConfiguredCommand(
    configuredCommand,
    configuredArgs,
    homeDir,
    exists,
  );
  const args = inlineArgs;
  const usesDefaultCommand = command === DEFAULT_SERVER_COMMAND && args.length === 0;

  if (!usesDefaultCommand) {
    return [{ command, args, cwd, env }];
  }

  const candidates: StataServerLaunch[] = [];
  const add = (candidate: StataServerLaunch): void => {
    const key = `${candidate.command}\0${candidate.args.join("\0")}`;
    if (!candidates.some((existing) => `${existing.command}\0${existing.args.join("\0")}` === key)) {
      candidates.push(candidate);
    }
  };

  for (const venvDir of venvDirs) {
    const venvScript = venvExecutable(venvDir, DEFAULT_SERVER_COMMAND, platform);
    if (exists(venvScript)) {
      add({ command: venvScript, args: [], cwd, env });
    }
  }

  add({ command: DEFAULT_SERVER_COMMAND, args: [], cwd, env });

  for (const venvDir of venvDirs) {
    const venvPython = venvExecutable(venvDir, "python", platform);
    if (exists(venvPython)) {
      add({ command: venvPython, args: ["-m", "stata_code.mcp"], cwd, env });
    }
  }

  for (const pythonCommand of configuredPythonCommands(config, homeDir)) {
    add({ command: pythonCommand, args: ["-m", "stata_code.mcp"], cwd, env });
  }

  for (const pythonCommand of commonPythonCommands(platform)) {
    if (exists(pythonCommand)) {
      add({ command: pythonCommand, args: ["-m", "stata_code.mcp"], cwd, env });
    }
  }

  if (platform === "win32") {
    add({ command: "py", args: ["-3", "-m", "stata_code.mcp"], cwd, env });
    add({ command: "python", args: ["-m", "stata_code.mcp"], cwd, env });
  } else {
    add({ command: "python3", args: ["-m", "stata_code.mcp"], cwd, env });
    add({ command: "python", args: ["-m", "stata_code.mcp"], cwd, env });
  }

  return candidates;
}

export function normalizeConfiguredCommand(
  command: string,
  configuredArgs: string[],
  homeDir: string = os.homedir(),
  exists: (target: string) => boolean = fs.existsSync,
): [string, string[]] {
  const raw = command.trim();
  if (!raw) {
    return [DEFAULT_SERVER_COMMAND, configuredArgs];
  }
  const expanded = expandHome(raw, homeDir);
  if (!/\s/.test(expanded) || exists(expanded)) {
    return [expanded, configuredArgs];
  }
  const parts = parseCommandLine(raw);
  if (parts.length === 0) return [expanded, configuredArgs];
  if (parts.length === 1) return [expandHome(parts[0], homeDir), configuredArgs];
  return [expandHome(parts[0], homeDir), [...parts.slice(1), ...configuredArgs]];
}

export function parseCommandLine(value: string): string[] {
  const parts: string[] = [];
  let current = "";
  let quote: "'" | "\"" | undefined;
  let escaping = false;

  for (let i = 0; i < value.length; i += 1) {
    const ch = value[i];
    if (escaping) {
      current += ch;
      escaping = false;
      continue;
    }
    const next = value[i + 1];
    if (ch === "\\" && shouldEscapeNext(next)) {
      escaping = true;
      continue;
    }
    if (quote) {
      if (ch === quote) {
        quote = undefined;
      } else {
        current += ch;
      }
      continue;
    }
    if (ch === "'" || ch === "\"") {
      quote = ch;
      continue;
    }
    if (/\s/.test(ch)) {
      if (current) {
        parts.push(current);
        current = "";
      }
      continue;
    }
    current += ch;
  }

  if (escaping) current += "\\";
  if (current) parts.push(current);
  return parts;
}

function shouldEscapeNext(next: string | undefined): boolean {
  return next !== undefined && (/\s/.test(next) || next === "\\" || next === "'" || next === "\"");
}

function serverEnvironment(
  sourceRoots: string[],
  envPath: string | undefined,
  envPythonPath: string | undefined,
  homeDir: string,
  platform: NodeJS.Platform,
  processEnv: NodeJS.ProcessEnv,
): Record<string, string> {
  const env: Record<string, string> = {
    ...runtimeEnvironment(processEnv),
    PATH: expandedPath(envPath, homeDir, platform),
  };
  const pythonPath = uniqueStrings([...sourceRoots, ...splitPathList(envPythonPath)]);
  if (pythonPath.length > 0) {
    env.PYTHONPATH = pythonPath.join(path.delimiter);
  }
  return env;
}

function runtimeEnvironment(processEnv: NodeJS.ProcessEnv): Record<string, string> {
  const env: Record<string, string> = {};
  const exactKeys = new Set([
    "PYTHONHOME",
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "CONDA_DEFAULT_ENV",
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
  ]);
  for (const [key, value] of Object.entries(processEnv)) {
    if (value === undefined) continue;
    const upper = key.toUpperCase();
    if (upper.startsWith("STATA") || upper.startsWith("PYSTATA") || exactKeys.has(upper)) {
      env[key] = value;
    }
  }
  return env;
}

function expandedPath(
  envPath: string | undefined,
  homeDir: string,
  platform: NodeJS.Platform,
): string {
  const entries = [...splitPathList(envPath)];
  if (platform === "win32") {
    entries.push(...pythonUserScriptDirs(homeDir, platform));
  } else {
    entries.push(path.join(homeDir, ".local", "bin"));
    entries.push(...pythonUserScriptDirs(homeDir, platform));
    if (platform === "darwin") {
      entries.push("/opt/homebrew/bin", "/usr/local/bin");
    }
  }
  return uniqueStrings(entries).join(path.delimiter);
}

function localSourceRoots(
  workspaceRoot: string | undefined,
  extensionRoot: string | undefined,
  exists: (target: string) => boolean,
): string[] {
  const candidates = [
    workspaceRoot,
    workspaceRoot ? path.dirname(workspaceRoot) : undefined,
    extensionRoot,
    extensionRoot ? path.dirname(extensionRoot) : undefined,
  ].filter((entry): entry is string => Boolean(entry));
  return uniqueStrings(candidates).filter((root) =>
    exists(path.join(root, "stata_code", "mcp", "__main__.py")),
  );
}

function localVenvDirs(
  workspaceRoot: string | undefined,
  sourceRoots: string[],
): string[] {
  const roots = [workspaceRoot, ...sourceRoots].filter((entry): entry is string =>
    Boolean(entry),
  );
  return uniqueStrings(
    roots.flatMap((root) => [path.join(root, ".venv"), path.join(root, "venv")]),
  );
}

function splitPathList(value: string | undefined): string[] {
  return value?.split(path.delimiter).filter(Boolean) ?? [];
}

function uniqueStrings(values: string[]): string[] {
  return Array.from(new Set(values));
}

function pythonUserScriptDirs(homeDir: string, platform: NodeJS.Platform): string[] {
  const versions = ["3.13", "3.12", "3.11", "3.10"];
  if (platform === "win32") {
    return versions.flatMap((version) => {
      const compact = version.replace(".", "");
      return [
        path.join(homeDir, "AppData", "Roaming", "Python", `Python${compact}`, "Scripts"),
        path.join(homeDir, "AppData", "Local", "Programs", "Python", `Python${compact}`, "Scripts"),
      ];
    });
  }
  if (platform !== "darwin") return [];
  return versions.map((version) =>
    path.join(homeDir, "Library", "Python", version, "bin"),
  );
}

function configuredPythonCommands(config: ServerLaunchConfig, homeDir: string): string[] {
  return [
    config.stataPythonPath,
    config.pythonDefaultInterpreterPath,
    config.pythonPythonPath,
  ]
    .filter((value): value is string => Boolean(value?.trim()))
    .map((value) => expandHome(value.trim(), homeDir));
}

function commonPythonCommands(platform: NodeJS.Platform): string[] {
  if (platform === "win32") return [];
  return [
    "/opt/homebrew/bin/python3",
    "/usr/local/bin/python3",
    "/opt/homebrew/bin/python3.13",
    "/usr/local/bin/python3.13",
    "/opt/homebrew/bin/python3.12",
    "/usr/local/bin/python3.12",
    "/opt/homebrew/bin/python3.11",
    "/usr/local/bin/python3.11",
    "/opt/homebrew/bin/python3.10",
    "/usr/local/bin/python3.10",
  ];
}

function venvExecutable(venvDir: string, name: string, platform: NodeJS.Platform): string {
  if (platform === "win32") {
    const executable = name === "python" ? "python.exe" : `${name}.exe`;
    return path.join(venvDir, "Scripts", executable);
  }
  return path.join(venvDir, "bin", name);
}

function expandHome(value: string, homeDir: string): string {
  if (value === "~") return homeDir;
  if (value.startsWith(`~${path.sep}`)) {
    return path.join(homeDir, value.slice(2));
  }
  return value;
}
