// Pure logic for the "Outputs" sidebar view (table / export artifact surfacing).
//
// vscode-free so it can be unit-tested under `node --test`. The runner snapshots
// the working directory around each run and copies any files written during it
// (esttab tables, exported data, figures saved to disk) into the run bundle,
// recording them on `log.files.output_paths`. This module flattens those across
// run history into a newest-first, de-duplicated list for the
// OutputsHistoryProvider in treeProviders.ts.

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
 */
export function buildOutputNodes(runs: OutputRun[]): OutputNode[] {
  const nodes: OutputNode[] = [];
  const seen = new Set<string>();
  for (let i = runs.length - 1; i >= 0; i--) {
    const run = runs[i];
    for (const path of run.result.log.files?.output_paths ?? []) {
      if (seen.has(path)) continue;
      seen.add(path);
      const ext = extName(path).replace(".", "").toUpperCase();
      nodes.push({
        path,
        label: baseName(path),
        description: `${ext || "file"} · ${run.result.session_id}`,
        tooltip: path,
        icon: outputIcon(path),
        kind: outputKind(path),
      });
    }
  }
  return nodes;
}
