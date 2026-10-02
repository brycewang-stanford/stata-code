// Shapes the dta reader's output for the data-viewer webview: the one-time
// `init` payload (dataset + variable metadata) and formatted row blocks.
//
// All formatting happens here, on the extension-host side, so the webview
// script stays a dumb grid and this logic stays unit-testable. Kept free of
// any `vscode` import so it runs under `node --test`.

import { formatStataNumber, formatWidthHint } from "./dtaFormat";
import {
  DtaCell,
  DtaMeta,
  DtaReader,
  DtaVariable,
  VALUE_LABEL_MISSING_BASE,
  missingCode,
  missingIndex,
} from "./dtaReader";

/** Value-label entries sent per label; a 65,536-entry label is not UI material. */
export const MAX_VALUE_LABEL_ENTRIES = 2000;
const MIN_COLUMN_CHARS = 5;
const MAX_COLUMN_CHARS = 32;
/** Rows a single request may ask for, whatever the webview sends. */
export const MAX_ROWS_PER_REQUEST = 1000;

export interface ViewerVariable {
  name: string;
  type: string;
  format: string;
  label: string;
  valueLabel: string;
  numeric: boolean;
  /** Suggested column width, in characters. */
  width: number;
  notes: string[];
}

export interface ViewerInit {
  type: "init";
  title: string;
  /** Extra context shown beside the title, e.g. "first 1,000 of 52,000 obs". */
  subtitle: string;
  release: number;
  nObs: number;
  nVars: number;
  dataLabel: string;
  timestamp: string;
  sortedBy: string[];
  notes: string[];
  warnings: string[];
  variables: ViewerVariable[];
  /** Label name → [value as Stata prints it, label text], in stored order. */
  valueLabels: Record<string, Array<[string, string]>>;
  /** Labels with more than MAX_VALUE_LABEL_ENTRIES entries (list was cut). */
  valueLabelsTruncated: string[];
}

export interface ViewerRows {
  type: "rows";
  id: number;
  start: number;
  firstColumn: number;
  rows: string[][];
}

/** How a value-label table's integer key prints: a number, or `.a` … `.z`. */
export function valueLabelKeyText(key: number): string {
  return key >= VALUE_LABEL_MISSING_BASE ? missingCode(key - VALUE_LABEL_MISSING_BASE) : String(key);
}

function isNumeric(v: DtaVariable): boolean {
  return v.kind !== "str" && v.kind !== "strL" && v.kind !== "alias";
}

function columnChars(v: DtaVariable, table: Map<number, string> | undefined): number {
  let chars = Math.max(v.name.length, formatWidthHint(v.format, v.type));
  if (table) {
    let longest = 0;
    let seen = 0;
    for (const text of table.values()) {
      longest = Math.max(longest, text.length);
      if (++seen >= 200) break;
    }
    chars = Math.max(chars, longest);
  }
  return Math.min(MAX_COLUMN_CHARS, Math.max(MIN_COLUMN_CHARS, chars));
}

export function buildViewerInit(
  meta: DtaMeta,
  options: { title: string; subtitle?: string; warnings?: string[] },
): ViewerInit {
  const valueLabels: Record<string, Array<[string, string]>> = {};
  const valueLabelsTruncated: string[] = [];
  const used = new Set(meta.variables.map((v) => v.valueLabel).filter(Boolean));
  for (const [name, table] of meta.valueLabels) {
    if (!used.has(name)) continue; // definitions no variable uses are not shown
    const entries: Array<[string, string]> = [];
    for (const [key, text] of table) {
      if (entries.length >= MAX_VALUE_LABEL_ENTRIES) {
        valueLabelsTruncated.push(name);
        break;
      }
      entries.push([valueLabelKeyText(key), text]);
    }
    valueLabels[name] = entries;
  }

  return {
    type: "init",
    title: options.title,
    subtitle: options.subtitle ?? "",
    release: meta.release,
    nObs: meta.nObs,
    nVars: meta.nVars,
    dataLabel: meta.dataLabel,
    timestamp: meta.timestamp,
    sortedBy: meta.sortedBy,
    notes: meta.notes,
    warnings: [...(options.warnings ?? []), ...meta.warnings],
    variables: meta.variables.map((v) => ({
      name: v.name,
      type: v.type,
      format: v.format,
      label: v.label,
      valueLabel: v.valueLabel,
      numeric: isNumeric(v),
      width: columnChars(v, v.valueLabel ? meta.valueLabels.get(v.valueLabel) : undefined),
      notes: v.notes,
    })),
    valueLabels,
    valueLabelsTruncated,
  };
}

/**
 * Text for one grid cell. With `useLabels`, a value that its variable's value
 * label maps is shown as the label text (as Stata's Data Browser does);
 * unmapped values, and everything when `useLabels` is off, print through the
 * variable's display format.
 */
export function formatCell(
  cell: DtaCell,
  variable: DtaVariable,
  table: Map<number, string> | undefined,
  useLabels: boolean,
): string {
  if (!isNumeric(variable)) return String(cell);
  if (typeof cell === "string") {
    // A Stata missing value: "." or ".a" … ".z".
    if (useLabels && table) {
      const index = missingIndex(cell);
      const text = index >= 0 ? table.get(VALUE_LABEL_MISSING_BASE + index) : undefined;
      if (text !== undefined) return text;
    }
    return cell;
  }
  if (useLabels && table && Number.isInteger(cell)) {
    const text = table.get(cell);
    if (text !== undefined) return text;
  }
  return formatStataNumber(cell, variable.format, variable.type);
}

function clampInt(value: unknown, low: number, high: number): number {
  const n = typeof value === "number" && Number.isFinite(value) ? Math.floor(value) : low;
  return Math.min(high, Math.max(low, n));
}

/**
 * Formatted cells for rows [start, start+count) and columns
 * [firstColumn, endColumn). Arguments come from the webview, so every one is
 * clamped to the dataset rather than trusted.
 */
export async function formatRows(
  reader: DtaReader,
  request: {
    start: unknown;
    count: unknown;
    firstColumn: unknown;
    endColumn: unknown;
    useLabels: unknown;
  },
): Promise<{ start: number; firstColumn: number; rows: string[][] }> {
  const { meta } = reader;
  const start = clampInt(request.start, 0, Math.max(0, meta.nObs));
  const count = clampInt(request.count, 0, MAX_ROWS_PER_REQUEST);
  const firstColumn = clampInt(request.firstColumn, 0, meta.nVars);
  const endColumn = clampInt(request.endColumn, firstColumn, meta.nVars);
  const useLabels = request.useLabels !== false;

  const columns: number[] = [];
  for (let c = firstColumn; c < endColumn; c++) columns.push(c);
  if (columns.length === 0 || count === 0) return { start, firstColumn, rows: [] };

  const vars = columns.map((c) => meta.variables[c]);
  const tables = vars.map((v) => (v.valueLabel ? meta.valueLabels.get(v.valueLabel) : undefined));
  const raw = await reader.readRows(start, count, columns);
  const rows = raw.map((row) =>
    row.map((cell, i) => formatCell(cell, vars[i], tables[i], useLabels)),
  );
  return { start, firstColumn, rows };
}
