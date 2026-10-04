// Shapes the dta reader's output for the data-viewer webview: the one-time
// `init` payload (dataset + variable metadata) and formatted row blocks.
//
// All formatting happens here, on the extension-host side, so the webview
// script stays a dumb grid and this logic stays unit-testable. Kept free of
// any `vscode` import so it runs under `node --test`.

import { formatStataNumber, formatWidthHint, isDateFormat } from "./dtaFormat";
import type { ColumnSummary } from "./dtaQuery";
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
/** Cells one clipboard copy may hold. */
export const MAX_COPY_CELLS = 500_000;

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
  /** Whether "Load in Stata" applies (a real file on disk, not a snapshot). */
  canLoadInStata: boolean;
  /** Whether variable labels can be written back to the file. */
  canEditLabels: boolean;
}

export interface ViewerRows {
  type: "rows";
  id: number;
  start: number;
  firstColumn: number;
  rows: string[][];
  /**
   * 1-based observation number of each row, sent when the view is filtered
   * or sorted so the gutter can show where a row sits in the dataset.
   */
  obs?: number[];
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
  options: {
    title: string;
    subtitle?: string;
    warnings?: string[];
    canLoadInStata?: boolean;
    canEditLabels?: boolean;
  },
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
    canLoadInStata: options.canLoadInStata ?? false,
    canEditLabels: options.canEditLabels === true,
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
 * Formatted cells for view rows [start, start+count) and columns
 * [firstColumn, endColumn). `order` is the view's row order (null for the
 * dataset as stored). Arguments come from the webview, so every one is
 * clamped rather than trusted.
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
  order: Uint32Array | null = null,
): Promise<{ start: number; firstColumn: number; rows: string[][]; obs?: number[] }> {
  const { meta } = reader;
  const total = order ? order.length : meta.nObs;
  const start = clampInt(request.start, 0, Math.max(0, total));
  const count = Math.min(clampInt(request.count, 0, MAX_ROWS_PER_REQUEST), total - start);
  const firstColumn = clampInt(request.firstColumn, 0, meta.nVars);
  const endColumn = clampInt(request.endColumn, firstColumn, meta.nVars);
  const useLabels = request.useLabels !== false;

  const columns: number[] = [];
  for (let c = firstColumn; c < endColumn; c++) columns.push(c);
  if (columns.length === 0 || count <= 0) return { start, firstColumn, rows: [] };

  const vars = columns.map((c) => meta.variables[c]);
  const tables = vars.map((v) => (v.valueLabel ? meta.valueLabels.get(v.valueLabel) : undefined));
  const indices = order ? order.subarray(start, start + count) : undefined;
  const raw = indices
    ? await reader.readRowsAt(indices, columns)
    : await reader.readRows(start, count, columns);
  const rows = raw.map((row) =>
    row.map((cell, i) => formatCell(cell, vars[i], tables[i], useLabels)),
  );
  return indices
    ? { start, firstColumn, rows, obs: Array.from(indices, (i) => i + 1) }
    : { start, firstColumn, rows };
}

/**
 * The cells of a rectangular selection, as the grid shows them, for the
 * clipboard. Throws RangeError when the selection exceeds MAX_COPY_CELLS.
 */
export async function formatRange(
  reader: DtaReader,
  request: {
    firstRow: unknown;
    lastRow: unknown;
    firstColumn: unknown;
    lastColumn: unknown;
    useLabels: unknown;
    headers: unknown;
  },
  order: Uint32Array | null = null,
): Promise<string[][]> {
  const { meta } = reader;
  const total = order ? order.length : meta.nObs;
  if (total === 0 || meta.nVars === 0) return [];
  const r0 = clampInt(request.firstRow, 0, total - 1);
  const r1 = clampInt(request.lastRow, r0, total - 1);
  const c0 = clampInt(request.firstColumn, 0, meta.nVars - 1);
  const c1 = clampInt(request.lastColumn, c0, meta.nVars - 1);
  const cells = (r1 - r0 + 1) * (c1 - c0 + 1);
  if (cells > MAX_COPY_CELLS) {
    throw new RangeError(
      `Selection is ${cells.toLocaleString("en-US")} cells; copy at most ` +
        `${MAX_COPY_CELLS.toLocaleString("en-US")} at a time, or export to CSV.`,
    );
  }
  const out: string[][] = [];
  if (request.headers === true) {
    out.push(meta.variables.slice(c0, c1 + 1).map((v) => v.name));
  }
  for (let start = r0; start <= r1; start += MAX_ROWS_PER_REQUEST) {
    const block = await formatRows(
      reader,
      {
        start,
        count: Math.min(MAX_ROWS_PER_REQUEST, r1 - start + 1),
        firstColumn: c0,
        endColumn: c1 + 1,
        useLabels: request.useLabels,
      },
      order,
    );
    out.push(...block.rows);
  }
  return out;
}

export interface ViewerSummary {
  name: string;
  /** [statistic, value] pairs, ready to print. */
  stats: Array<[string, string]>;
  /** Most frequent values; `filter` is an `if` expression selecting that value. */
  frequencies: Array<{ value: string; label: string; count: string; share: string; filter: string }>;
  frequenciesOmitted: number;
}

function statText(x: number): string {
  if (Number.isInteger(x) && Math.abs(x) < 1e15) return x.toLocaleString("en-US");
  const rounded = Number(x.toPrecision(9));
  if (Math.abs(rounded) >= 1e15 || (rounded !== 0 && Math.abs(rounded) < 1e-4)) {
    return rounded.toExponential(4);
  }
  return rounded.toLocaleString("en-US", { maximumFractionDigits: 6 });
}

/**
 * Turn a column summary into display text. Order statistics of a date
 * variable print as dates (a minimum of 20103 says nothing; 14jan2015 does);
 * the mean and standard deviation stay numeric.
 */
export function formatSummary(summary: ColumnSummary, variable: DtaVariable): ViewerSummary {
  const count = (n: number): string => n.toLocaleString("en-US");
  const stats: Array<[string, string]> = [
    ["Obs", count(summary.n)],
    [summary.numeric ? "Missing" : "Empty", count(summary.missing)],
    ["Distinct", `${count(summary.distinct)}${summary.distinctCapped ? "+" : ""}`],
  ];
  if (summary.numeric && summary.mean !== undefined) {
    const dated = isDateFormat(variable.format);
    const point = (x: number | undefined): string =>
      x === undefined
        ? ""
        : dated && Number.isInteger(x)
          ? formatStataNumber(x, variable.format, variable.type)
          : statText(x);
    stats.push(["Mean", statText(summary.mean)]);
    if (summary.sd !== undefined) stats.push(["Std. dev.", statText(summary.sd)]);
    if (summary.percentilesOmitted) {
      // too many values to hold for a sort: the moments are still exact
      stats.push(
        ["Min", point(summary.min)],
        ["Median", "not computed (memory limit)"],
        ["Max", point(summary.max)],
      );
    } else {
      stats.push(
        ["Min", point(summary.min)],
        ["p25", point(summary.p25)],
        ["Median", point(summary.p50)],
        ["p75", point(summary.p75)],
        ["Max", point(summary.max)],
      );
    }
  }
  const frequencies = (summary.frequencies ?? []).map((f) => ({
    value: f.value,
    label: f.label,
    count: count(f.count),
    share: summary.rows > 0 ? `${((100 * f.count) / summary.rows).toFixed(1)}%` : "",
    filter: summary.numeric
      ? `${variable.name} == ${f.value}`
      : // A value with a double quote needs compound quotes to stay one literal.
        f.value.includes('"')
        ? `${variable.name} == \`"${f.value}"'`
        : `${variable.name} == "${f.value}"`,
  }));
  return {
    name: summary.name,
    stats,
    frequencies,
    frequenciesOmitted: summary.frequenciesOmitted ?? 0,
  };
}
