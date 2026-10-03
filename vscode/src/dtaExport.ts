// Text exports from the dta viewer: the current view as CSV, the dataset's
// codebook (what a CSV cannot carry), and a cell range as TSV for the
// clipboard.
//
// Kept free of any `vscode` import so it runs under `node --test`.

import { formatStataNumber, isDateFormat } from "./dtaFormat";
import {
  DtaCell,
  DtaMeta,
  DtaReader,
  DtaVariable,
  VALUE_LABEL_MISSING_BASE,
  missingIndex,
} from "./dtaReader";
import { valueLabelKeyText } from "./dtaViewModel";

const EXPORT_BLOCK_ROWS = 2000;

/** Quote a CSV field when it contains a delimiter, quote, line break, or edge space. */
export function csvField(text: string): string {
  return /[",\r\n]|^\s|\s$/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function csvLine(fields: string[]): string {
  return `${fields.map(csvField).join(",")}\n`;
}

/**
 * One cell for a data export. Unlike a grid cell this keeps full numeric
 * precision — a `%9.0g` display format would round 1234567.891 to 1234568 —
 * while still writing dates as dates and, when asked, value labels as text.
 * System missing is an empty field and extended missing values are `.a`–`.z`,
 * as `export delimited` writes them.
 */
export function exportCell(
  cell: DtaCell,
  variable: DtaVariable,
  table: Map<number, string> | undefined,
  useLabels: boolean,
): string {
  if (variable.kind === "str" || variable.kind === "strL" || variable.kind === "alias") {
    return String(cell);
  }
  if (typeof cell === "string") {
    const index = missingIndex(cell);
    if (useLabels && table && index >= 0) {
      const text = table.get(VALUE_LABEL_MISSING_BASE + index);
      if (text !== undefined) return text;
    }
    return cell === "." ? "" : cell;
  }
  if (useLabels && table && Number.isInteger(cell)) {
    const text = table.get(cell);
    if (text !== undefined) return text;
  }
  if (isDateFormat(variable.format)) return formatStataNumber(cell, variable.format, variable.type);
  return String(cell);
}

/**
 * The view as CSV text, in chunks: a header of variable names, then the rows
 * of `order` (or the whole dataset) in view order.
 */
export async function* csvChunks(
  reader: DtaReader,
  order: Uint32Array | null,
  options: { useLabels: boolean; isCancelled?: () => boolean },
): AsyncGenerator<string> {
  const { meta } = reader;
  const tables = meta.variables.map((v) =>
    v.valueLabel ? meta.valueLabels.get(v.valueLabel) : undefined,
  );
  yield csvLine(meta.variables.map((v) => v.name));
  const total = order ? order.length : meta.nObs;
  for (let start = 0; start < total; start += EXPORT_BLOCK_ROWS) {
    if (options.isCancelled?.()) return;
    const count = Math.min(EXPORT_BLOCK_ROWS, total - start);
    const rows = order
      ? await reader.readRowsAt(order.subarray(start, start + count))
      : await reader.readRows(start, count);
    let text = "";
    for (const row of rows) {
      text += csvLine(
        row.map((cell, c) => exportCell(cell, meta.variables[c], tables[c], options.useLabels)),
      );
    }
    yield text;
  }
}

/** One row per variable: everything `describe` and `notes` report about it. */
export function codebookCsv(meta: DtaMeta): string {
  let text = csvLine(["position", "name", "type", "format", "value_label", "label", "notes"]);
  for (const v of meta.variables) {
    text += csvLine([
      String(v.index + 1),
      v.name,
      v.type,
      v.format,
      v.valueLabel,
      v.label,
      v.notes.join(" | "),
    ]);
  }
  return text;
}

/**
 * Every value-label mapping in use, one row per (label, value). `undefined`
 * when the dataset attaches no value labels.
 */
export function valueLabelsCsv(meta: DtaMeta): string | undefined {
  const used = new Set(meta.variables.map((v) => v.valueLabel).filter(Boolean));
  let text = csvLine(["value_label", "value", "text"]);
  let rows = 0;
  for (const [name, table] of meta.valueLabels) {
    if (!used.has(name)) continue;
    for (const [key, label] of table) {
      text += csvLine([name, valueLabelKeyText(key), label]);
      rows += 1;
    }
  }
  return rows > 0 ? text : undefined;
}

/** Cells as tab-separated text for the clipboard (tabs and newlines flattened). */
export function rangeToTsv(rows: string[][]): string {
  return rows
    .map((row) => row.map((cell) => cell.replace(/[\t\r\n]+/g, " ")).join("\t"))
    .join("\n");
}
