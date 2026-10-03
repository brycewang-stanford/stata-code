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
