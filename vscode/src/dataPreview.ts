// Pure logic behind the "View data preview" command: the Stata snippet that
// produces the listing, and the document rendered from its result.
//
// Kept free of any `vscode` runtime import so it can be unit-tested under
// `node --test` (same convention as formatters.ts / dataBrowser.ts).

import type { RunResult } from "./types/runResult";

/** Rows requested when the user has not configured `stataCode.dataPreviewObs`. */
export const DEFAULT_DATA_PREVIEW_OBS = 1000;

/** Bounds applied to the configured row count before it reaches Stata. */
export const MIN_DATA_PREVIEW_OBS = 1;
export const MAX_DATA_PREVIEW_OBS = 100000;

/**
 * Cap for the plain-text fallback listing. The grid viewer scrolls through as
 * many rows as the snapshot holds; a text document of `list` output does not.
 */
export const TEXT_PREVIEW_MAX_OBS = 200;

/** Name of the scratch frame the snapshot is copied through. */
const SNAPSHOT_FRAME = "_sc_snapshot";

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

/** Row count for the text fallback: the configured value, capped. */
export function textPreviewObs(value: unknown): number {
  return Math.min(clampPreviewObs(value), TEXT_PREVIEW_MAX_OBS);
}

/**
 * Stata code that saves the first `previewObs` observations of the data in
 * memory to `file` as a .dta, which the dta viewer then opens. Going through
 * a real .dta keeps everything `list` output loses: variable and value
 * labels, display formats, notes, strLs, and exact numeric values.
 *
 * The copy is made with `frame put`, so the user's own frame is never
 * touched — not its data, its sort order, `c(filename)`, nor `c(changed)`.
 * Frames need Stata 16; on older Stata the code fails and the caller falls
 * back to the text listing.
 *
 * Returns `undefined` when `file` cannot be written safely inside a Stata
 * string literal (a backtick or `$` would be macro-expanded).
 */
export function buildDataSnapshotCode(file: string, previewObs: number): string | undefined {
  if (/[`$"\r\n]/.test(file)) return undefined;
  const n = clampPreviewObs(previewObs);
  const target = file.replace(/\\/g, "/");
  return [
    `capture frame drop ${SNAPSHOT_FRAME}`,
    `frame put * if _n <= ${n}, into(${SNAPSHOT_FRAME})`,
    `capture noisily frame ${SNAPSHOT_FRAME}: quietly save "${target}", replace`,
    "local _sc_rc = _rc",
    `frame drop ${SNAPSHOT_FRAME}`,
    "if `_sc_rc' error `_sc_rc'",
  ].join("\n");
}

/**
 * `use "<file>", clear` for a path on disk, or `undefined` when the path
 * cannot sit safely inside a Stata string literal (same rule as the snapshot).
 */
export function buildUseCode(file: string): string | undefined {
  if (/[`$"\r\n]/.test(file)) return undefined;
  return `use "${file.replace(/\\/g, "/")}", clear`;
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
