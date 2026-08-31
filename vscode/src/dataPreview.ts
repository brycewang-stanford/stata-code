// Pure logic behind the "View data preview" command: the Stata snippet that
// produces the listing, and the document rendered from its result.
//
// Kept free of any `vscode` runtime import so it can be unit-tested under
// `node --test` (same convention as formatters.ts / dataBrowser.ts).

import type { RunResult } from "./types/runResult";

/** Rows requested when the user has not configured `stataCode.dataPreviewObs`. */
export const DEFAULT_DATA_PREVIEW_OBS = 50;

/** Bounds applied to the configured row count before it reaches Stata. */
export const MIN_DATA_PREVIEW_OBS = 1;
export const MAX_DATA_PREVIEW_OBS = 10000;

/**
 * Width `list` is given while the preview runs. Stata wraps its output at
 * `c(linesize)` (79 by default) and folds the overflow into `>` continuation
 * lines, which makes any dataset past a handful of variables unreadable.
 */
const PREVIEW_LINESIZE = 250;

export function clampPreviewObs(value: unknown): number {
  const n = typeof value === "number" && Number.isFinite(value) ? Math.floor(value) : NaN;
  if (Number.isNaN(n)) return DEFAULT_DATA_PREVIEW_OBS;
  return Math.min(MAX_DATA_PREVIEW_OBS, Math.max(MIN_DATA_PREVIEW_OBS, n));
}

/**
 * Stata code for the preview listing.
 *
 * Uses `if _n <= N` rather than `in 1/N`: an `in` range whose upper bound
 * exceeds `_N` is an error (r(198), "observation numbers out of range"), so
 * `in 1/N` fails on every dataset smaller than the preview window — which is
 * most teaching datasets. The `if` form degrades to "list what there is",
 * including an empty listing when nothing is loaded.
 *
 * linesize is widened around the listing and restored afterwards so the
 * preview is not wrapped while the user's own output formatting is left alone.
 */
export function buildDataPreviewCode(previewObs: number): string {
  const n = clampPreviewObs(previewObs);
  return [
    "local _sc_linesize = c(linesize)",
    `quietly set linesize ${PREVIEW_LINESIZE}`,
    `list if _n <= ${n}, clean noobs abbreviate(24)`,
    "quietly set linesize `_sc_linesize'",
  ].join("\n");
}

/**
 * Drop Stata's command echo (`. cmd` and its `> ` continuations) and collapse
 * runs of blank lines. The preview runs code the extension wrote, so the echo
 * is pure noise between the reader and the data.
 */
export function stripCommandEcho(text: string): string {
  const out: string[] = [];
  let echoing = false;
  for (const line of text.split("\n")) {
    if (/^\.(\s|$)/.test(line)) {
      echoing = true;
      continue;
    }
    if (echoing && /^>\s/.test(line)) continue;
    echoing = false;
    if (line.trim() === "" && (out.length === 0 || out[out.length - 1].trim() === "")) continue;
    out.push(line.trimEnd());
  }
  while (out.length > 0 && out[out.length - 1].trim() === "") out.pop();
  return out.join("\n");
}

/** Render the preview document shown in the editor. */
export function formatDataPreviewDocument(
  result: RunResult,
  text: string,
  previewObs: number,
): string {
  const ds = result.dataset;
  const shown = Math.min(ds.n_obs, clampPreviewObs(previewObs));
  const rows =
    ds.n_obs === 0
      ? "no observations"
      : shown < ds.n_obs
        ? `showing first ${shown} of ${ds.n_obs} (raise stataCode.dataPreviewObs for more)`
        : `showing all ${ds.n_obs}`;

  const summary = [
    `${ds.n_obs} obs × ${ds.n_vars} vars`,
    rows,
    ...(ds.frame !== result.session_id ? [`frame ${ds.frame}`] : []),
    ...(ds.changed ? ["unsaved changes"] : []),
  ].join(" · ");

  const body = stripCommandEcho(text);
  const lines = [
    `stata-code data preview · session ${result.session_id}`,
    summary,
    ...(ds.filename ? [ds.filename] : []),
  ];
  if (!result.ok) {
    lines.push(`preview failed: rc=${result.rc}${result.error ? ` ${result.error.message}` : ""}`);
  }
  lines.push("", body || placeholderBody(result, ds.n_vars), "", variableSection(result));
  return lines.join("\n");
}

function placeholderBody(result: RunResult, nVars: number): string {
  if (!result.ok) return "(no output)";
  return nVars === 0 ? "(no data in memory)" : "(no rows to show)";
}

function variableSection(result: RunResult): string {
  const variables = result.dataset.variables ?? [];
  if (variables.length === 0) return "variables: (none)";
  const nameWidth = Math.max(...variables.map((v) => v.name.length));
  const typeWidth = Math.max(...variables.map((v) => v.type.length));
  const rows = variables.map((v) => {
    const label = v.label?.trim();
    return `  ${v.name.padEnd(nameWidth)}  ${v.type.padEnd(typeWidth)}${label ? `  ${label}` : ""}`.trimEnd();
  });
  return [`variables (${variables.length})`, ...rows].join("\n");
}
