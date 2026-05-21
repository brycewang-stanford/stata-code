// Pure, testable helper that decides whether any of the candidate launch
// commands assembled by `serverLaunch.ts` is likely to resolve at runtime.
//
// We do NOT actually spawn `stata-code-mcp --help` — that would block
// activation. We just check whether the executable file exists on disk
// (absolute / relative paths) or on PATH (bare command names). The real
// spawn still happens lazily inside `mcpClient.ts`; this probe only
// drives a one-time install-hint notification.

import * as fs from "node:fs";
import * as path from "node:path";

import type { StataServerLaunch } from "./mcpClient";

export interface ProbeContext {
  candidates: StataServerLaunch[];
  exists?: (target: string) => boolean;
  envPath?: string;
  platform?: NodeJS.Platform;
}

function pathModuleFor(platform: NodeJS.Platform): path.PlatformPath {
  return platform === "win32" ? path.win32 : path.posix;
}

export type ProbeStatus = "found" | "missing";

export interface ProbeResult {
  status: ProbeStatus;
  resolved?: { command: string; args: string[] };
  checkedCandidates: number;
}

export function probeServerLaunch(ctx: ProbeContext): ProbeResult {
  const exists = ctx.exists ?? fs.existsSync;
  const platform = ctx.platform ?? process.platform;
  const pp = pathModuleFor(platform);
  const envPath = ctx.envPath ?? process.env.PATH ?? "";
  const pathDirs = envPath.split(pp.delimiter).filter(Boolean);

  for (const candidate of ctx.candidates) {
    if (commandResolves(candidate.command, pathDirs, exists, platform, pp)) {
      return {
        status: "found",
        resolved: { command: candidate.command, args: candidate.args },
        checkedCandidates: ctx.candidates.length,
      };
    }
  }
  return { status: "missing", checkedCandidates: ctx.candidates.length };
}

function commandResolves(
  command: string,
  pathDirs: string[],
  exists: (target: string) => boolean,
  platform: NodeJS.Platform,
  pp: path.PlatformPath,
): boolean {
  if (!command) return false;
  const hasSeparator =
    platform === "win32" ? /[\\/]/.test(command) : command.includes("/");
  if (pp.isAbsolute(command) || command.startsWith(".") || hasSeparator) {
    return exists(command);
  }
  const variants =
    platform === "win32"
      ? [command, `${command}.exe`, `${command}.cmd`, `${command}.bat`]
      : [command];
  for (const dir of pathDirs) {
    for (const variant of variants) {
      if (exists(pp.join(dir, variant))) return true;
    }
  }
  return false;
}
