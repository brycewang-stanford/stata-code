// Pure logic for the "Outputs" sidebar view (table / export artifact surfacing).
//
// vscode-free so it can be unit-tested under `node --test`.
//
// Two sources feed this view, and both matter:
//
//  * `result.outputs` — files the run wrote in its WORKING DIRECTORY. Reported
//    on every run since server 0.11, so the panel is useful without opting into
//    run bundles. These are the paths a user actually wants to open.
//  * `log.files.output_paths` — COPIES archived inside the run bundle, present
//    only when the caller passed `persist_log_files`. Different paths from the
//    above, and worth keeping: they are the immutable snapshot of that run.
//
// This module flattens both across run history into a newest-first,
// de-duplicated list for the OutputsHistoryProvider in treeProviders.ts.

import type { RunResult } from "./types/runResult";

export interface OutputRun {
  runId: string;
  ts: number;
  result: RunResult;
}

export type OutputKind = "table" | "doc" | "data" | "image" | "other";

export interface OutputNode {
  path: string;
  label: string;
  description: string;
  tooltip: string;
  icon: string;
  kind: OutputKind;
  /** "workdir" = written in place by the run; "bundle" = archived copy. */
  origin: "workdir" | "bundle";
}

/** Human-readable byte size for the node description. */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const TABLE_EXTS = new Set([".tex", ".csv", ".md", ".html", ".htm", ".txt"]);
const DOC_EXTS = new Set([".rtf", ".doc", ".docx", ".xls", ".xlsx"]);
const DATA_EXTS = new Set([".dta", ".parquet"]);
const IMAGE_EXTS = new Set([".png", ".svg", ".pdf", ".eps", ".gph", ".jpg", ".jpeg"]);

export function baseName(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

export function extName(path: string): string {
  const base = baseName(path);
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(dot).toLowerCase() : "";
}

export function outputKind(path: string): OutputKind {
  const ext = extName(path);
  if (TABLE_EXTS.has(ext)) return "table";
  if (DOC_EXTS.has(ext)) return "doc";
  if (DATA_EXTS.has(ext)) return "data";
  if (IMAGE_EXTS.has(ext)) return "image";
  return "other";
}

export function outputIcon(path: string): string {
  switch (outputKind(path)) {
    case "table":
      return "table";
    case "doc":
      return "file-text";
    case "data":
      return "database";
    case "image":
      return "file-media";
    default:
      return "file";
  }
}

/**
 * Flatten the table/export artifacts recorded across run history into display
 * nodes. Newest run first; a path that appears in several runs is shown once,
 * attributed to its most recent run. `runs` is expected oldest-first (the order
 * the run-history store keeps), matching getRunHistory().
 *
 * Within one run, working-directory outputs come before archived bundle copies:
 * the file the user actually wrote is what they want to click.
 */
export function buildOutputNodes(runs: OutputRun[]): OutputNode[] {
  const nodes: OutputNode[] = [];
  const seen = new Set<string>();

  const push = (
    path: string,
    sessionId: string,
    origin: "workdir" | "bundle",
    bytes?: number | null,
    created?: boolean,
  ) => {
    if (!path || seen.has(path)) return;
    seen.add(path);
    const ext = extName(path).replace(".", "").toUpperCase();
    const size = formatBytes(bytes);
    const parts = [ext || "file", sessionId];
    if (size) parts.push(size);
    if (origin === "bundle") parts.push("bundle");
    nodes.push({
      path,
      label: baseName(path),
      description: parts.join(" · "),
      // Tooltip stays exactly the path unless there is something extra worth
      // saying — open/reveal actions read `path`, but users hover the tooltip.
      tooltip: created === false ? `${path}\n(overwritten by this run)` : path,
      icon: outputIcon(path),
      kind: outputKind(path),
      origin,
    });
  };

  for (let i = runs.length - 1; i >= 0; i--) {
    const run = runs[i];
    const sessionId = run.result.session_id;
    for (const out of run.result.outputs ?? []) {
      push(out.path, sessionId, "workdir", out.bytes, out.created);
    }
    for (const path of run.result.log.files?.output_paths ?? []) {
      push(path, sessionId, "bundle");
    }
  }
  return nodes;
}
