// In-place editing of variable labels in a .dta file, without Stata.
//
// A variable label is a fixed-width, zero-terminated field in every format the
// reader supports (81 bytes through format 117, 321 bytes from 118 on; see
// `help dta`). Replacing one therefore overwrites that field and nothing else:
// no offset moves, and the data, value labels, notes, characteristics and
// strLs keep their bytes. That is why this is a patch and not a re-save.
//
// The pure half (planLabelEdits) is free of `vscode` and of the file system so
// it runs under `node --test`; setVariableLabels adds the file I/O.

import { promises as fs } from "node:fs";
import * as nodePath from "node:path";

import { openFileByteSource } from "./dtaFileSource";
import { DtaReader, type DtaReaderOptions } from "./dtaReader";

/** Stata's limit on a variable label, in characters. */
export const MAX_VARIABLE_LABEL_CHARS = 80;

export class DtaEditError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DtaEditError";
  }
}

export interface LabelChange {
  name: string;
  before: string;
  after: string;
}

export interface LabelPatch extends LabelChange {
  /** Absolute file offset of the label field. */
  offset: number;
  /** The whole field: the encoded label, zero-padded to the field width. */
  bytes: Uint8Array;
}

/**
 * Encode `label` as the contents of a label field `width` bytes wide in a
 * format-`release` file. Throws DtaEditError when Stata could not hold it.
 */
export function encodeVariableLabel(label: string, release: number, width: number): Uint8Array {
  const chars = Array.from(label);
  if (chars.length > MAX_VARIABLE_LABEL_CHARS) {
    throw new DtaEditError(
      `label is ${chars.length} characters; Stata allows at most ${MAX_VARIABLE_LABEL_CHARS}`,
    );
  }
  for (const ch of chars) {
    const code = ch.codePointAt(0) as number;
    if (code < 0x20 || code === 0x7f) {
      throw new DtaEditError("label contains a control character (a line break or a tab?)");
    }
    if (code >= 0xd800 && code <= 0xdfff) {
      throw new DtaEditError("label contains an unpaired surrogate and is not valid Unicode");
    }
    if (release < 118 && code > 0x7e) {
      // These formats predate UTF-8 and do not record which encoding they use,
      // so there is no way to write a non-ASCII label that every reader agrees on.
      throw new DtaEditError(
        `this is a format-${release} file (Stata 13 or older), which can only take ` +
          "plain-ASCII labels here; save it again in Stata 14 or newer to use other characters",
      );
    }
  }
  const encoded = new TextEncoder().encode(label);
  if (encoded.byteLength > width - 1) {
    throw new DtaEditError(
      `label takes ${encoded.byteLength} bytes; this file format has room for ${width - 1}`,
    );
  }
  const field = new Uint8Array(width);
  field.set(encoded);
  return field;
}

/**
 * Work out the bytes to write for a set of label edits, validating all of them
 * before any is applied. Variables whose label already equals the requested
 * text produce no patch.
 */
export function planLabelEdits(reader: DtaReader, edits: Record<string, string>): LabelPatch[] {
  const { release, variables } = reader.meta;
  const byName = new Map(variables.map((v) => [v.name, v]));
  const patches: LabelPatch[] = [];
  const problems: string[] = [];
  for (const [name, label] of Object.entries(edits)) {
    const variable = byName.get(name);
    if (!variable) {
      problems.push(`${name}: no such variable`);
      continue;
    }
    if (typeof label !== "string") {
      problems.push(`${name}: label must be a string`);
      continue;
    }
    if (label === variable.label) continue;
    const field = reader.variableLabelField(variable.index);
    try {
      patches.push({
        name,
        before: variable.label,
        after: label,
        offset: field.offset,
        bytes: encodeVariableLabel(label, release, field.width),
      });
    } catch (err) {
      if (!(err instanceof DtaEditError)) throw err;
      problems.push(`${name}: ${err.message}`);
    }
  }
  if (problems.length > 0) throw new DtaEditError(problems.join("; "));
  return patches;
}

/**
 * Set variable labels in the .dta file at `path`. `edits` maps variable name to
 * the new label; "" removes a label. The file is re-parsed here, so the write
 * is planned against what is on disk now, not what a viewer read earlier. If
 * any edit is invalid, nothing is written.
 */
export async function setVariableLabels(
  path: string,
  edits: Record<string, string>,
  options: DtaReaderOptions = {},
): Promise<LabelChange[]> {
  const reader = await DtaReader.open(await openFileByteSource(path), options);
  const patches = planLabelEdits(reader, edits);
  if (patches.length === 0) return [];
  const handle = await fs.open(path, "r+");
  try {
    for (const patch of patches) {
      await handle.write(patch.bytes, 0, patch.bytes.byteLength, patch.offset);
    }
    await handle.sync();
  } finally {
    await handle.close();
  }
  return patches.map(({ name, before, after }) => ({ name, before, after }));
}

// ── value labels, attachments and the dataset label ─────────────────────────
//
// The name of the value label attached to a variable is a fixed-width field
// like a variable label, and so is the dataset label of a format 113-115
// file: both are patched in place. The contents of a value-label set and the
// dataset label of a format 117+ file are variable-length. For those the file
// is written again to a temporary file beside the original (every byte
// outside the changed fields is copied, never re-encoded), the offsets in
// <map> are corrected, and the temporary file replaces the original in one
// rename, so a crash leaves either the old file or the new one.
//
// This is the TypeScript counterpart of stata_code/core/dta_edit.py. Both must
// refuse the same edits and write the same bytes; test-fixtures/dta/
// edit_cases.json holds them to it.

/** Stata's limit on the text of one value label, in bytes. */
export const MAX_VALUE_LABEL_BYTES = 32000;
const MIN_CODE = -2147483647;
const MAX_CODE = 2147483620;
/** In a value-label table `.` is this integer and `.a` … `.z` follow. */
const MISSING_BASE = 2147483621;
const NEW_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,31}$/;

export interface DtaLabelEdit {
  /** variable → label; "" removes a label. */
  variableLabels?: Record<string, string>;
  /**
   * name → {code: text} defines that value label, replacing the whole set if
   * it exists; null drops it and detaches it from the variables that used it.
   * A code is an integer (as a string, as JSON keys are) or ".a" … ".z".
   */
  valueLabels?: Record<string, Record<string, string> | null>;
  /** variable → value-label name; "" detaches. */
  attach?: Record<string, string>;
  /** The dataset label; "" removes it. */
  dataLabel?: string;
}

export interface DtaEditResult {
  variableLabels: LabelChange[];
  /** name → "defined" | "modified" | "dropped" */
  valueLabels: Record<string, string>;
  /** before / after are value-label names, "" for none. */
  attached: LabelChange[];
  dataLabel: LabelChange | null;
  /** True when the file was written again in full rather than patched. */
  rewritten: boolean;
}

/** Replace `remove` bytes at `offset` with `data`. */
export interface Splice {
  offset: number;
  remove: number;
  data: Uint8Array;
}

/** `.a` for the raw table integer of an extended missing value, else the number. */
export function valueLabelCode(raw: number): string {
  if (raw >= MISSING_BASE) {
    return raw === MISSING_BASE ? "." : "." + String.fromCharCode(96 + raw - MISSING_BASE);
  }
  return String(raw);
}

function checkText(text: string, release: number, what: string): Uint8Array {
  for (const ch of Array.from(text)) {
    const code = ch.codePointAt(0) as number;
    if (code < 0x20 || code === 0x7f) {
      throw new DtaEditError(`${what} contains a control character (a line break or a tab?)`);
    }
    if (code >= 0xd800 && code <= 0xdfff) {
      throw new DtaEditError(`${what} contains an unpaired surrogate and is not valid Unicode`);
    }
    if (release < 118 && code > 0x7e) {
      throw new DtaEditError(
        `this is a format-${release} file (Stata 13 or older), which can only take ` +
          "plain-ASCII labels here; save it again in Stata 14 or newer to use other characters",
      );
    }
  }
  return new TextEncoder().encode(text);
}

/** The raw table integer for a code written as "12", "-3" or ".a". */
function normalizeCode(raw: string): number {
  if (/^\.[a-z]$/.test(raw)) return MISSING_BASE + (raw.charCodeAt(1) - 96);
  if (!/^-?\d+$/.test(raw)) {
    throw new DtaEditError(`'${raw}' is not a value-label code (an integer, or .a to .z)`);
  }
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < MIN_CODE || value > MAX_CODE) {
    throw new DtaEditError(
      `code ${raw} is outside the range a value label can hold (${MIN_CODE} to ${MAX_CODE})`,
    );
  }
  return value;
}

/** A requested set as [raw code, text] pairs in the order of the codes. */
function normalizeTable(mapping: unknown, release: number): Array<[number, string]> {
  if (typeof mapping !== "object" || mapping === null || Array.isArray(mapping)) {
    throw new DtaEditError("must be an object mapping codes to label texts, or null");
  }
  const entries = Object.entries(mapping as Record<string, unknown>);
  if (entries.length === 0) {
    throw new DtaEditError("has no entries; pass null to drop the value label");
  }
  const table = new Map<number, string>();
  for (const [rawCode, text] of entries) {
    const code = normalizeCode(rawCode);
    const shown = valueLabelCode(code);
    if (table.has(code)) throw new DtaEditError(`code ${shown} is given twice`);
    if (typeof text !== "string" || text === "") {
      throw new DtaEditError(`the label of ${shown} must be a non-empty string`);
    }
    const encoded = checkText(text, release, `the label of ${shown}`);
    if (encoded.byteLength > MAX_VALUE_LABEL_BYTES) {
      throw new DtaEditError(
        `the label of ${shown} takes ${encoded.byteLength} bytes; Stata allows ` +
          `at most ${MAX_VALUE_LABEL_BYTES}`,
      );
    }
    table.set(code, text);
  }
  return [...table.entries()].sort((a, b) => a[0] - b[0]);
}

function sameTable(current: Map<number, string>, wanted: Array<[number, string]>): boolean {
  if (current.size !== wanted.length) return false;
  return wanted.every(([code, text]) => current.get(code) === text);
}

function concat(parts: Uint8Array[]): Uint8Array {
  const out = new Uint8Array(parts.reduce((sum, p) => sum + p.byteLength, 0));
  let at = 0;
  for (const part of parts) {
    out.set(part, at);
    at += part.byteLength;
  }
  return out;
}

function ascii(text: string): Uint8Array {
  return new TextEncoder().encode(text);
}

function encodeName(name: string, width: number): Uint8Array {
  const encoded = new TextEncoder().encode(name);
  if (encoded.byteLength > width - 1) {
    throw new DtaEditError(
      `value-label name takes ${encoded.byteLength} bytes; this format has room for ${width - 1}`,
    );
  }
  const field = new Uint8Array(width);
  field.set(encoded);
  return field;
}

/** One value-label set as the bytes of its record, tags included. */
function encodeRecord(
  name: string,
  table: Array<[number, string]>,
  layout: { littleEndian: boolean; nameWidth: number; tagged: boolean },
): Uint8Array {
  const texts = table.map(([, text]) => new TextEncoder().encode(text));
  const textLength = texts.reduce((sum, t) => sum + t.byteLength + 1, 0);
  const n = table.length;
  const body = new Uint8Array(8 + 8 * n + textLength);
  const view = new DataView(body.buffer);
  const le = layout.littleEndian;
  view.setInt32(0, n, le);
  view.setInt32(4, textLength, le);
  let at = 0;
  texts.forEach((text, i) => {
    view.setInt32(8 + 4 * i, at, le);
    view.setInt32(8 + 4 * n + 4 * i, table[i][0], le);
    body.set(text, 8 + 8 * n + at);
    at += text.byteLength + 1;
  });
  const nameField = new Uint8Array(layout.nameWidth + 3);
  nameField.set(new TextEncoder().encode(name));
  const length = new Uint8Array(4);
  const lengthView = new DataView(length.buffer);
  if (layout.tagged) {
    lengthView.setUint32(0, body.byteLength, le);
    return concat([ascii("<lbl>"), length, nameField, body, ascii("</lbl>")]);
  }
  lengthView.setInt32(0, body.byteLength, le);
  return concat([length, nameField, body]);
}

function encodeDataLabel(
  label: string,
  release: number,
  tagged: boolean,
  littleEndian: boolean,
): Uint8Array {
  // same rules as a variable label: 80 characters, no control characters
  const padded = encodeVariableLabel(label, release, tagged ? 321 : 81);
  if (!tagged) return padded;
  const encoded = new TextEncoder().encode(label);
  if (release === 117) {
    if (encoded.byteLength > 80) {
      throw new DtaEditError(
        `label takes ${encoded.byteLength} bytes; this file format has room for 80`,
      );
    }
    return concat([Uint8Array.of(encoded.byteLength), encoded]);
  }
  const prefix = new Uint8Array(2);
  new DataView(prefix.buffer).setUint16(0, encoded.byteLength, littleEndian);
  return concat([prefix, encoded]);
}

/**
 * Work out the bytes to write for an edit, validating all of it before
 * anything is applied. The splices are in file order and do not overlap.
 */
export function planEdits(
  reader: DtaReader,
  edit: DtaLabelEdit,
): { splices: Splice[]; result: DtaEditResult } {
  const { release, variables, valueLabels: currentSets } = reader.meta;
  const layout = reader.editLayout;
  const tagged = layout.map !== null;
  const byName = new Map(variables.map((v) => [v.name, v]));
  const problems: string[] = [];
  const splices: Splice[] = [];
  const result: DtaEditResult = {
    variableLabels: [],
    valueLabels: {},
    attached: [],
    dataLabel: null,
    rewritten: false,
  };

  for (const [name, label] of Object.entries(edit.variableLabels ?? {})) {
    const variable = byName.get(name);
    if (!variable) {
      problems.push(`${name}: no such variable`);
    } else if (typeof label !== "string") {
      problems.push(`${name}: label must be a string`);
    } else if (label !== variable.label) {
      const field = reader.variableLabelField(variable.index);
      try {
        splices.push({
          offset: field.offset,
          remove: field.width,
          data: encodeVariableLabel(label, release, field.width),
        });
        result.variableLabels.push({ name, before: variable.label, after: label });
      } catch (err) {
        if (!(err instanceof DtaEditError)) throw err;
        problems.push(`${name}: ${err.message}`);
      }
    }
  }

  // value-label sets: the mapping given for a name replaces that set
  const existing = new Map(layout.records.map((r) => [r.name, r]));
  const replaced = new Map<string, Array<[number, string]> | null>();
  const requestedSets = Object.entries(edit.valueLabels ?? {});
  if (requestedSets.length > 0 && !layout.valueLabelsIntact) {
    problems.push("the value-label section of this file is damaged and cannot be rewritten");
  }
  for (const [name, mapping] of requestedSets) {
    if (!name) {
      problems.push("value label names must be non-empty strings");
      continue;
    }
    if (mapping === null) {
      // dropping a set that is not there is already done, not an error
      if (existing.has(name)) {
        replaced.set(name, null);
        result.valueLabels[name] = "dropped";
      }
      continue;
    }
    if (!existing.has(name) && !NEW_NAME.test(name)) {
      problems.push(
        `value label ${name}: a name is 1-32 letters, digits or _, not starting with a digit`,
      );
      continue;
    }
    let table: Array<[number, string]>;
    try {
      table = normalizeTable(mapping, release);
    } catch (err) {
      if (!(err instanceof DtaEditError)) throw err;
      problems.push(`value label ${name}: ${err.message}`);
      continue;
    }
    const current = currentSets.get(name);
    if (existing.has(name) && current && sameTable(current, table)) continue;
    replaced.set(name, table);
    result.valueLabels[name] = existing.has(name) ? "modified" : "defined";
  }
  const afterNames = new Set<string>();
  for (const name of existing.keys()) if (replaced.get(name) !== null) afterNames.add(name);
  for (const [name, table] of replaced) if (table !== null) afterNames.add(name);

  // attachments; a dropped set is detached from the variables that used it
  const wanted = new Map<string, string>();
  for (const v of variables) {
    if (v.valueLabel && replaced.get(v.valueLabel) === null) wanted.set(v.name, "");
  }
  for (const [name, setName] of Object.entries(edit.attach ?? {})) {
    const variable = byName.get(name);
    if (!variable) {
      problems.push(`${name}: no such variable`);
    } else if (typeof setName !== "string") {
      problems.push(`${name}: value label name must be a string ("" detaches)`);
    } else if (setName && (variable.kind === "str" || variable.kind === "strL")) {
      problems.push(`${name}: is a string variable; value labels attach to numbers`);
    } else if (setName && !afterNames.has(setName)) {
      problems.push(`${name}: no value label named ${setName}; define it in the same call`);
    } else {
      wanted.set(name, setName);
    }
  }
  for (const [name, setName] of wanted) {
    const variable = byName.get(name) as (typeof variables)[number];
    if (setName === variable.valueLabel) continue;
    try {
      splices.push({
        offset: layout.setNamesOffset + variable.index * layout.nameWidth,
        remove: layout.nameWidth,
        data: encodeName(setName, layout.nameWidth),
      });
      result.attached.push({ name, before: variable.valueLabel, after: setName });
    } catch (err) {
      if (!(err instanceof DtaEditError)) throw err;
      problems.push(`${name}: ${err.message}`);
    }
  }

  if (edit.dataLabel !== undefined) {
    if (typeof edit.dataLabel !== "string") {
      problems.push("data_label must be a string");
    } else if (edit.dataLabel !== reader.meta.dataLabel) {
      try {
        splices.push({
          offset: layout.dataLabelAt[0],
          remove: layout.dataLabelAt[1],
          data: encodeDataLabel(edit.dataLabel, release, tagged, layout.littleEndian),
        });
        result.dataLabel = { name: "_dta", before: reader.meta.dataLabel, after: edit.dataLabel };
      } catch (err) {
        if (!(err instanceof DtaEditError)) throw err;
        problems.push(`data label: ${err.message}`);
      }
    }
  }

  if (problems.length > 0) throw new DtaEditError(problems.join("; "));

  if (replaced.size > 0) {
    // untouched sets keep their bytes; changed ones are encoded afresh and
    // stay where they were; new ones go to the end
    const shape = { littleEndian: layout.littleEndian, nameWidth: layout.nameWidth, tagged };
    for (const record of layout.records) {
      if (!replaced.has(record.name)) continue;
      const table = replaced.get(record.name) as Array<[number, string]> | null;
      splices.push({
        offset: record.start,
        remove: record.end - record.start,
        data: table === null ? new Uint8Array(0) : encodeRecord(record.name, table, shape),
      });
    }
    const fresh: Uint8Array[] = [];
    for (const [name, table] of replaced) {
      if (table !== null && !existing.has(name)) fresh.push(encodeRecord(name, table, shape));
    }
    if (fresh.length > 0) {
      splices.push({ offset: layout.valueLabels[1], remove: 0, data: concat(fresh) });
    }
  }

  const inFileOrder = (a: Splice, b: Splice): number => a.offset - b.offset || a.remove - b.remove;
  splices.sort(inFileOrder);
  const moves = splices.some((s) => s.remove !== s.data.byteLength);
  if (moves && layout.map !== null && layout.mapAt !== null) {
    const packed = new Uint8Array(14 * 8);
    const view = new DataView(packed.buffer);
    layout.map.forEach((entry, i) => {
      let shift = 0;
      for (const s of splices) if (s.offset < entry) shift += s.data.byteLength - s.remove;
      view.setBigUint64(8 * i, BigInt(entry + shift), layout.littleEndian);
    });
    splices.push({ offset: layout.mapAt, remove: 14 * 8, data: packed });
    splices.sort(inFileOrder);
  }
  result.rewritten = moves;
  return { splices, result };
}

/** The bytes of `original` with `splices` applied; for tests and small files. */
export function applySplices(original: Uint8Array, splices: Splice[]): Uint8Array {
  const parts: Uint8Array[] = [];
  let pos = 0;
  for (const splice of splices) {
    parts.push(original.subarray(pos, splice.offset), splice.data);
    pos = splice.offset + splice.remove;
  }
  parts.push(original.subarray(pos));
  return concat(parts);
}

async function copyRange(
  source: fs.FileHandle,
  out: fs.FileHandle,
  offset: number,
  length: number,
): Promise<void> {
  if (length < 0) throw new DtaEditError("internal error: overlapping edits");
  const buffer = Buffer.allocUnsafe(Math.min(Math.max(length, 1), 1 << 20));
  let done = 0;
  while (done < length) {
    const want = Math.min(buffer.byteLength, length - done);
    const { bytesRead } = await source.read(buffer, 0, want, offset + done);
    if (bytesRead === 0) throw new DtaEditError("malformed or truncated .dta file");
    await out.write(buffer, 0, bytesRead);
    done += bytesRead;
  }
}

/** Write the spliced file beside `path`, then put it in `path`'s place. */
async function rewrite(path: string, splices: Splice[], size: number): Promise<void> {
  const dir = nodePath.dirname(path);
  const temp = nodePath.join(
    dir,
    `.${nodePath.basename(path)}.${process.pid}.${Date.now().toString(36)}.tmp`,
  );
  const { mode } = await fs.stat(path);
  const source = await fs.open(path, "r");
  try {
    const out = await fs.open(temp, "wx", mode & 0o777);
    try {
      let pos = 0;
      for (const splice of splices) {
        await copyRange(source, out, pos, splice.offset - pos);
        await out.write(splice.data, 0, splice.data.byteLength);
        pos = splice.offset + splice.remove;
      }
      await copyRange(source, out, pos, size - pos);
      await out.sync();
    } finally {
      await out.close();
    }
    await fs.chmod(temp, mode & 0o777);
    await fs.rename(temp, path);
  } catch (err) {
    await fs.rm(temp, { force: true });
    throw err;
  } finally {
    await source.close();
  }
}

/**
 * Edit the label metadata of the .dta file at `path`: variable labels, value
 * labels, the value label attached to each variable, and the dataset label.
 * The file is re-parsed here, so the write is planned against what is on disk
 * now. If any part of the edit is invalid, nothing is written.
 */
export async function editLabels(
  path: string,
  edit: DtaLabelEdit,
  options: DtaReaderOptions & { dryRun?: boolean } = {},
): Promise<DtaEditResult> {
  const reader = await DtaReader.open(await openFileByteSource(path), options);
  const { splices, result } = planEdits(reader, edit);
  if (options.dryRun || splices.length === 0) return result;
  if (result.rewritten) {
    await rewrite(path, splices, reader.editLayout.fileSize);
    return result;
  }
  const handle = await fs.open(path, "r+");
  try {
    for (const splice of splices) {
      await handle.write(splice.data, 0, splice.data.byteLength, splice.offset);
    }
    await handle.sync();
  } finally {
    await handle.close();
  }
  return result;
}

/**
 * The edit that puts back what `result` changed, given the file's metadata
 * from before the edit. A set that was defined is dropped; one that was
 * modified or dropped gets its old codes and texts back; every attachment,
 * including those a drop removed, returns to the name it had.
 */
export function inverseEdit(
  before: DtaReader["meta"],
  result: DtaEditResult,
): DtaLabelEdit {
  const inverse: DtaLabelEdit = {};
  if (result.variableLabels.length > 0) {
    inverse.variableLabels = Object.fromEntries(
      result.variableLabels.map((c) => [c.name, c.before]),
    );
  }
  const sets = Object.entries(result.valueLabels);
  if (sets.length > 0) {
    inverse.valueLabels = {};
    for (const [name, what] of sets) {
      const old = before.valueLabels.get(name);
      inverse.valueLabels[name] =
        what === "defined" || !old
          ? null
          : Object.fromEntries([...old].map(([code, text]) => [valueLabelCode(code), text]));
    }
  }
  if (result.attached.length > 0) {
    inverse.attach = Object.fromEntries(result.attached.map((c) => [c.name, c.before]));
  }
  if (result.dataLabel) inverse.dataLabel = result.dataLabel.before;
  return inverse;
}

/** One line saying what an edit did, for the viewer's status bar. */
export function describeEdit(result: DtaEditResult): string {
  const parts: string[] = [];
  for (const c of result.variableLabels) {
    parts.push(
      c.after === ""
        ? `removed the label of ${c.name}`
        : c.before === ""
          ? `labeled ${c.name}`
          : `relabeled ${c.name}`,
    );
  }
  for (const [name, what] of Object.entries(result.valueLabels)) {
    parts.push(`${what} value label ${name}`);
  }
  for (const c of result.attached) {
    parts.push(c.after === "" ? `detached ${c.before} from ${c.name}` : `attached ${c.after} to ${c.name}`);
  }
  if (result.dataLabel) {
    parts.push(result.dataLabel.after === "" ? "removed the dataset label" : "set the dataset label");
  }
  if (parts.length === 0) return "";
  const text = parts.join("; ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Whether an edit result changed anything. */
export function editChanged(result: DtaEditResult): boolean {
  return (
    result.variableLabels.length > 0 ||
    Object.keys(result.valueLabels).length > 0 ||
    result.attached.length > 0 ||
    result.dataLabel !== null
  );
}
