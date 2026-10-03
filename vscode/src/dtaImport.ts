// Label edits read back from the codebook CSV the viewer exports.
//
// The codebook export is the half of a dataset a CSV cannot carry: one row
// per variable (name, value label, label) and a second file with every
// value-label mapping. Reading the same two files back turns a spreadsheet
// into a bulk label editor: export, edit a hundred labels in a sheet, import.
//
// Free of `vscode` and of the file system so it runs under `node --test`.

import type { DtaLabelEdit } from "./dtaWriter";

export class CodebookError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CodebookError";
  }
}

/** Parse CSV text (RFC 4180: quoted fields, doubled quotes, line breaks in quotes). */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  let started = false; // the current row holds at least one character or field
  const src = text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
  for (let i = 0; i < src.length; i++) {
    const ch = src[i];
    if (quoted) {
      if (ch === '"') {
        if (src[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          quoted = false;
        }
      } else {
        field += ch;
      }
      continue;
    }
    if (ch === '"' && field === "") {
      quoted = true;
      started = true;
    } else if (ch === ",") {
      row.push(field);
      field = "";
      started = true;
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && src[i + 1] === "\n") i++;
      if (started || field !== "") {
        row.push(field);
        rows.push(row);
      }
      row = [];
      field = "";
      started = false;
    } else {
      field += ch;
      started = true;
    }
  }
  if (quoted) throw new CodebookError("the CSV ends inside a quoted field");
  if (started || field !== "") {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

function columns(header: string[], file: string): Map<string, number> {
  const at = new Map<string, number>();
  header.forEach((name, i) => at.set(name.trim().toLowerCase(), i));
  if (at.size !== header.length) throw new CodebookError(`${file}: a column name is repeated`);
  return at;
}

/**
 * The label edit a codebook asks for. `codebook` has a `name` column and
 * either or both of `label` (variable labels) and `value_label` (the value
 * label attached to each variable); a column that is absent is left alone,
 * and so is a variable that has no row. `valueLabels`, when given, has
 * `value_label`, `value`, `text` and replaces each set it lists; sets it does
 * not list stay as they are.
 */
export function editFromCodebook(codebook: string, valueLabels?: string): DtaLabelEdit {
  const rows = parseCsv(codebook);
  if (rows.length === 0) throw new CodebookError("the codebook is empty");
  const at = columns(rows[0], "codebook");
  const nameAt = at.get("name");
  if (nameAt === undefined) {
    throw new CodebookError('the codebook has no "name" column; export one from this viewer first');
  }
  const labelAt = at.get("label");
  const setAt = at.get("value_label");
  if (labelAt === undefined && setAt === undefined && valueLabels === undefined) {
    throw new CodebookError('the codebook has neither a "label" nor a "value_label" column');
  }
  const edit: DtaLabelEdit = {};
  const seen = new Set<string>();
  for (let i = 1; i < rows.length; i++) {
    const name = (rows[i][nameAt] ?? "").trim();
    if (name === "") continue;
    if (seen.has(name)) throw new CodebookError(`codebook row ${i + 1}: ${name} appears twice`);
    seen.add(name);
    if (labelAt !== undefined) {
      (edit.variableLabels ??= {})[name] = (rows[i][labelAt] ?? "").trim();
    }
    if (setAt !== undefined) (edit.attach ??= {})[name] = (rows[i][setAt] ?? "").trim();
  }

  if (valueLabels !== undefined) {
    const vl = parseCsv(valueLabels);
    if (vl.length > 0) {
      const cols = columns(vl[0], "value labels");
      const setCol = cols.get("value_label");
      const valueCol = cols.get("value");
      const textCol = cols.get("text");
      if (setCol === undefined || valueCol === undefined || textCol === undefined) {
        throw new CodebookError(
          'the value-labels file needs the columns "value_label", "value" and "text"',
        );
      }
      const sets: Record<string, Record<string, string>> = {};
      for (let i = 1; i < vl.length; i++) {
        const set = (vl[i][setCol] ?? "").trim();
        const value = (vl[i][valueCol] ?? "").trim();
        if (set === "" && value === "") continue;
        if (set === "") throw new CodebookError(`value labels row ${i + 1}: no value_label name`);
        const table = (sets[set] ??= {});
        if (Object.prototype.hasOwnProperty.call(table, value)) {
          throw new CodebookError(`value labels row ${i + 1}: ${set} lists ${value} twice`);
        }
        table[value] = vl[i][textCol] ?? "";
      }
      if (Object.keys(sets).length > 0) edit.valueLabels = sets;
    }
  }
  return edit;
}
