// Pure logic for the "Data" sidebar view (the dataset / variables browser).
//
// Kept free of any `vscode` runtime import so it can be unit-tested under
// `node --test` (same convention as formatters.ts). The DataProvider in
// treeProviders.ts maps these plain DataNode descriptors onto vscode.TreeItem.

import type { RunResult, VariableInfo } from "./types/runResult";

export interface DataNode {
  kind: "summary" | "variable" | "empty";
  label: string;
  description?: string;
  tooltip?: string;
  icon?: string; // vscode ThemeIcon id
  varName?: string; // populated for "variable" nodes (clipboard / actions)
}

/**
 * Choose a codicon for a variable by its Stata storage type. String types
 * (`str#`, `strL`) get a string glyph; everything numeric (byte/int/long/
 * float/double) gets a number glyph.
 */
export function variableIcon(type: string): string {
  return type.trim().toLowerCase().startsWith("str") ? "symbol-string" : "symbol-number";
}

/** Last path component of a Stata filename, for a compact summary label. */
export function baseName(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

/**
 * Build the Data view's node list from the latest run result. Returns a single
 * summary row followed by one row per variable; an "empty" placeholder when no
 * dataset is loaded; and `[]` when there is no result at all (so the view's
 * welcome content shows instead).
 */
export function buildDataNodes(result: RunResult | undefined): DataNode[] {
  if (!result) return [];
  const ds = result.dataset;
  if (!ds || ds.n_vars === 0) {
    return [{ kind: "empty", label: "(no variables in memory)", icon: "info" }];
  }

  const nodes: DataNode[] = [];
  nodes.push({
    kind: "summary",
    label: `${ds.n_obs} obs × ${ds.n_vars} vars`,
    description: `frame ${ds.frame}${ds.changed ? " · modified" : ""}`,
    tooltip: ds.filename ? baseName(ds.filename) : `frame ${ds.frame}`,
    icon: "table",
  });

  for (const v of ds.variables ?? []) {
    nodes.push(variableNode(v));
  }
  return nodes;
}

function variableNode(v: VariableInfo): DataNode {
  const label = v.label?.trim();
  // `format` / `value_label` arrive only when they say something (a date or
  // custom format, an attached value label), so showing them is never noise.
  const storage = [v.type, v.format].filter(Boolean).join(" ");
  const valueLabel = v.value_label ? `[${v.value_label}]` : "";
  return {
    kind: "variable",
    label: v.name,
    description: [storage, valueLabel, label].filter(Boolean).join(" · "),
    tooltip: [v.name, storage, valueLabel, label].filter(Boolean).join(" · "),
    icon: variableIcon(v.type),
    varName: v.name,
  };
}
