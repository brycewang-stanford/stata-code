// Stata-free reader for .dta files.
//
// Written against StataCorp's published format documentation (`help dta`,
// plus the dta_113 / dta_114 / dta_115 / dta_117 / dta_119 / dta_120 / dta_121
// pages), which LICENSE-POLICY.md §2.1 lists as an allowed reference. No
// third-party reader was consulted.
//
// Covers the layouts Stata has written since Stata 8: the fixed-header
// formats 113-115 and the tagged formats 117-121. Metadata is parsed once;
// observations are fixed-width records, so any block of rows is one
// positioned read and a multi-gigabyte file never has to be loaded whole.
//
// Kept free of any `vscode` import so it runs under `node --test` (same
// convention as formatters.ts / dataBrowser.ts).

import { TextDecoder } from "node:util";

/** Random-access bytes. Implemented over a file handle or an in-memory buffer. */
export interface ByteSource {
  readonly size: number;
  /** Returns up to `length` bytes; shorter only when the read crosses EOF. */
  read(offset: number, length: number): Promise<Uint8Array>;
  close?(): Promise<void>;
}

export class BufferByteSource implements ByteSource {
  readonly size: number;
  constructor(private readonly bytes: Uint8Array) {
    this.size = bytes.byteLength;
  }
  async read(offset: number, length: number): Promise<Uint8Array> {
    const start = Math.min(Math.max(0, offset), this.size);
    const end = Math.min(start + Math.max(0, length), this.size);
    return this.bytes.subarray(start, end);
  }
}

export type DtaVarKind = "byte" | "int" | "long" | "float" | "double" | "str" | "strL" | "alias";

export interface DtaVariable {
  index: number;
  name: string;
  /** Stata storage type as `describe` prints it: byte, int, …, str18, strL, alias. */
  type: string;
  kind: DtaVarKind;
  /** Bytes this variable occupies in one observation record. */
  width: number;
  /** Byte offset of this variable inside one observation record. */
  offset: number;
  format: string;
  /** Name of the attached value label, or "" when none. */
  valueLabel: string;
  label: string;
  notes: string[];
}

export interface DtaMeta {
  /** .dta format number: 113-115 or 117-121. */
  release: number;
  byteOrder: "LSF" | "MSF";
  nVars: number;
  nObs: number;
  dataLabel: string;
  timestamp: string;
  variables: DtaVariable[];
  sortedBy: string[];
  /** Value-label definitions by label name: raw integer code → text. */
  valueLabels: Map<string, Map<number, string>>;
  /** Dataset-level notes (`notes _dta`). */
  notes: string[];
  rowWidth: number;
  /** Non-fatal problems found while parsing (e.g. a truncated data section). */
  warnings: string[];
}

/**
 * One cell. String variables yield a string. Numeric variables yield a number,
 * or — for Stata missing values — the string Stata itself prints: "." or
 * ".a" … ".z". Alias variables hold no data in the file and yield "".
 */
export type DtaCell = number | string;

export interface DtaReaderOptions {
  /**
   * Encoding for formats older than 118, which predate Stata's switch to
   * UTF-8. "auto" tries UTF-8, then GB18030, then Windows-1252.
   */
  legacyEncoding?: string;
  /** Longest strL value returned in full; longer ones are cut and marked "…". */
  maxStrLBytes?: number;
}

export class DtaFormatError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DtaFormatError";
  }
}

const DEFAULT_MAX_STRL_BYTES = 32768;
/** Guard against a corrupt header asking for an absurd metadata read. */
const MAX_METADATA_BYTES = 1 << 30;
const STRL_SCAN_CHUNK = 1 << 20;
/** Rows of slack within which scattered row reads are merged into one read. */
const READ_RUN_ROWS = 256;
const SCAN_CHUNK_ROWS = 20000;
const SCAN_CHUNK_BYTES = 8 << 20;

/** Raw integer code that a value-label table uses for missing value `.`, `.a`, …. */
export const VALUE_LABEL_MISSING_BASE = 2147483621;

/** "." for index 0, ".a" … ".z" for 1 … 26. */
export function missingCode(index: number): string {
  return index >= 1 && index <= 26 ? `.${String.fromCharCode(96 + index)}` : ".";
}

/** Inverse of {@link missingCode}; -1 when `text` is not a missing-value code. */
export function missingIndex(text: string): number {
  if (text === ".") return 0;
  if (text.length === 2 && text[0] === "." && text[1] >= "a" && text[1] <= "z") {
    return text.charCodeAt(1) - 96;
  }
  return -1;
}

/** The format Stata assigns a new variable of this storage type. */
export function defaultFormat(type: string): string {
  switch (type) {
    case "byte":
    case "int":
      return "%8.0g";
    case "long":
      return "%12.0g";
    case "float":
      return "%9.0g";
    case "double":
      return "%10.0g";
    case "strL":
      return "%9s";
    default: {
      const m = /^str(\d+)$/.exec(type);
      return m ? `%${Math.max(9, Number(m[1]))}s` : "";
    }
  }
}

interface Layout {
  release: number;
  littleEndian: boolean;
  dataOffset: number;
  /** [start, end) of the GSO records, or null when the format has no strLs. */
  strls: [number, number] | null;
  /** File offset of the first variable-label field, and the width of each. */
  labelsOffset: number;
  labelWidth: number;
  edit: DtaEditLayout;
}

/** One value-label set as it sits in the file, tags included. */
export interface DtaValueLabelRecord {
  name: string;
  start: number;
  end: number;
}

/**
 * Where the label metadata that dtaWriter can change sits in the file. Kept
 * apart from the rest of the layout because only the writer needs it.
 */
export interface DtaEditLayout {
  release: number;
  littleEndian: boolean;
  fileSize: number;
  /** File offset of the first value-label-name field, and the width of each. */
  setNamesOffset: number;
  nameWidth: number;
  /** Offset and length of everything a new dataset label replaces. */
  dataLabelAt: [number, number];
  /** File offset of the 14 map entries and their values; null before format 117. */
  mapAt: number | null;
  map: number[] | null;
  /** [start, end) of the value-label records. */
  valueLabels: [number, number];
  records: DtaValueLabelRecord[];
  /** False when the value-label section could not be read to its end. */
  valueLabelsIntact: boolean;
}

interface StrLEntry {
  offset: number;
  length: number;
  binary: boolean;
}

class Cursor {
  private readonly view: DataView;
  pos = 0;
  constructor(
    readonly bytes: Uint8Array,
    private readonly littleEndian: () => boolean,
  ) {
    this.view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  }
  private need(n: number): void {
    if (this.pos + n > this.bytes.byteLength) {
      throw new DtaFormatError("unexpected end of file while reading the .dta header");
    }
  }
  expect(tag: string): void {
    this.need(tag.length);
    for (let i = 0; i < tag.length; i++) {
      if (this.bytes[this.pos + i] !== tag.charCodeAt(i)) {
        throw new DtaFormatError(`malformed .dta file: expected ${tag} at byte ${this.pos}`);
      }
    }
    this.pos += tag.length;
  }
  take(n: number): Uint8Array {
    this.need(n);
    const out = this.bytes.subarray(this.pos, this.pos + n);
    this.pos += n;
    return out;
  }
  u8(): number {
    this.need(1);
    return this.view.getUint8(this.pos++);
  }
  u16(): number {
    this.need(2);
    const v = this.view.getUint16(this.pos, this.littleEndian());
    this.pos += 2;
    return v;
  }
  u32(): number {
    this.need(4);
    const v = this.view.getUint32(this.pos, this.littleEndian());
    this.pos += 4;
    return v;
  }
  i32(): number {
    this.need(4);
    const v = this.view.getInt32(this.pos, this.littleEndian());
    this.pos += 4;
    return v;
  }
  u64(): number {
    this.need(8);
    const v = this.view.getBigUint64(this.pos, this.littleEndian());
    this.pos += 8;
    if (v > BigInt(Number.MAX_SAFE_INTEGER)) {
      throw new DtaFormatError("malformed .dta file: 64-bit field out of range");
    }
    return Number(v);
  }
}

function zeroTerminated(bytes: Uint8Array): Uint8Array {
  const end = bytes.indexOf(0);
  return end < 0 ? bytes : bytes.subarray(0, end);
}

type Decoder = (bytes: Uint8Array) => string;

function makeDecoder(release: number, legacyEncoding: string): Decoder {
  const utf8 = new TextDecoder("utf-8");
  if (release >= 118) return (b) => utf8.decode(b);

  const strict: TextDecoder[] = [];
  let lenient: TextDecoder | undefined;
  const names = legacyEncoding === "auto" ? ["utf-8", "gb18030"] : [legacyEncoding];
  for (const name of names) {
    try {
      strict.push(new TextDecoder(name, { fatal: true }));
    } catch {
      // Unknown label, or a runtime built without full ICU: fall through.
    }
  }
  try {
    lenient = new TextDecoder(legacyEncoding === "auto" ? "windows-1252" : legacyEncoding);
  } catch {
    lenient = new TextDecoder("windows-1252");
  }
  return (b) => {
    for (const dec of strict) {
      try {
        return dec.decode(b);
      } catch {
        // Not valid in this encoding; try the next one.
      }
    }
    return (lenient as TextDecoder).decode(b);
  };
}

/** Shortest decimal that round-trips to the same 4-byte float (0.1f → 0.1). */
function float32ToNumber(x: number): number {
  if (!Number.isFinite(x) || Number.isInteger(x)) return x;
  for (let p = 1; p <= 9; p++) {
    const candidate = Number(x.toPrecision(p));
    if (Math.fround(candidate) === x) return candidate;
  }
  return x;
}

function typeFromModernCode(code: number): { type: string; kind: DtaVarKind; width: number } {
  if (code >= 1 && code <= 2045) return { type: `str${code}`, kind: "str", width: code };
  switch (code) {
    case 32768:
      return { type: "strL", kind: "strL", width: 8 };
    case 65525:
      return { type: "alias", kind: "alias", width: 0 };
    case 65526:
      return { type: "double", kind: "double", width: 8 };
    case 65527:
      return { type: "float", kind: "float", width: 4 };
    case 65528:
      return { type: "long", kind: "long", width: 4 };
    case 65529:
      return { type: "int", kind: "int", width: 2 };
    case 65530:
      return { type: "byte", kind: "byte", width: 1 };
    default:
      throw new DtaFormatError(`malformed .dta file: unknown variable type code ${code}`);
  }
}

function typeFromLegacyCode(code: number): { type: string; kind: DtaVarKind; width: number } {
  if (code >= 1 && code <= 244) return { type: `str${code}`, kind: "str", width: code };
  switch (code) {
    case 251:
      return { type: "byte", kind: "byte", width: 1 };
    case 252:
      return { type: "int", kind: "int", width: 2 };
    case 253:
      return { type: "long", kind: "long", width: 4 };
    case 254:
      return { type: "float", kind: "float", width: 4 };
    case 255:
      return { type: "double", kind: "double", width: 8 };
    default:
      throw new DtaFormatError(`malformed .dta file: unknown variable type code ${code}`);
  }
}

interface RawCharacteristic {
  varname: string;
  name: string;
  value: string;
}

/** Turn `note1`, `note2`, … characteristics into ordered note lists per variable. */
function collectNotes(chars: RawCharacteristic[]): Map<string, string[]> {
  const numbered = new Map<string, Array<[number, string]>>();
  for (const ch of chars) {
    const m = /^note(\d+)$/.exec(ch.name);
    if (!m || m[1] === "0") continue; // note0 holds the count, not a note
    const list = numbered.get(ch.varname) ?? [];
    list.push([Number(m[1]), ch.value]);
    numbered.set(ch.varname, list);
  }
  const out = new Map<string, string[]>();
  for (const [varname, list] of numbered) {
    out.set(
      varname,
      list.sort((a, b) => a[0] - b[0]).map(([, text]) => text),
    );
  }
  return out;
}

function parseValueLabelTable(
  cur: Cursor,
  tableLength: number,
  decode: Decoder,
): Map<number, string> {
  const end = cur.pos + tableLength;
  const n = cur.i32();
  const textLength = cur.i32();
  const table = new Map<number, string>();
  if (n < 0 || textLength < 0 || 8 + 8 * n + textLength > tableLength) {
    cur.pos = end;
    return table;
  }
  const offsets: number[] = [];
  for (let i = 0; i < n; i++) offsets.push(cur.i32());
  const values: number[] = [];
  for (let i = 0; i < n; i++) values.push(cur.i32());
  const text = cur.take(textLength);
  for (let i = 0; i < n; i++) {
    const off = offsets[i];
    if (off < 0 || off >= textLength) continue;
    table.set(values[i], decode(zeroTerminated(text.subarray(off))));
  }
  cur.pos = end;
  return table;
}

export class DtaReader {
  private strlIndex: Map<string, StrLEntry> | undefined;

  private constructor(
    private readonly source: ByteSource,
    readonly meta: DtaMeta,
    private readonly layout: Layout,
    private readonly decode: Decoder,
    private readonly maxStrLBytes: number,
  ) {}

  static async open(source: ByteSource, options: DtaReaderOptions = {}): Promise<DtaReader> {
    const head = await source.read(0, 4096);
    if (head.byteLength < 4) throw new DtaFormatError("not a Stata .dta file (file is too short)");
    const legacyEncoding = options.legacyEncoding?.trim() || "auto";
    const maxStrLBytes = options.maxStrLBytes ?? DEFAULT_MAX_STRL_BYTES;

    const isTagged = head[0] === 0x3c; // "<stata_dta>"
    if (isTagged) return DtaReader.openTagged(source, head, legacyEncoding, maxStrLBytes);
    if (head[0] >= 113 && head[0] <= 115) {
      return DtaReader.openLegacy(source, head, legacyEncoding, maxStrLBytes);
    }
    if (head[0] >= 102 && head[0] <= 112) {
      throw new DtaFormatError(
        `.dta format ${head[0]} (Stata 7 or older) is not supported; ` +
          "open it in Stata and save it again to convert it",
      );
    }
    throw new DtaFormatError("not a Stata .dta file (unrecognized header)");
  }

  // ── formats 117-121 ───────────────────────────────────────────────────────

  private static async openTagged(
    source: ByteSource,
    head: Uint8Array,
    legacyEncoding: string,
    maxStrLBytes: number,
  ): Promise<DtaReader> {
    let littleEndian = true;
    const cur = new Cursor(head, () => littleEndian);
    cur.expect("<stata_dta><header><release>");
    const release = Number(new TextDecoder("ascii").decode(cur.take(3)));
    if (!(release >= 117 && release <= 121) || release === 116) {
      throw new DtaFormatError(`.dta format ${release} is not supported`);
    }
    cur.expect("</release><byteorder>");
    const order = new TextDecoder("ascii").decode(cur.take(3));
    if (order !== "LSF" && order !== "MSF") {
      throw new DtaFormatError(`malformed .dta file: byteorder ${JSON.stringify(order)}`);
    }
    littleEndian = order === "LSF";
    const wide = release === 119 || release === 121; // > 32,767 variables
    const decode = makeDecoder(release, legacyEncoding);

    cur.expect("</byteorder><K>");
    const nVars = wide ? cur.u32() : cur.u16();
    cur.expect("</K><N>");
    const nObsHeader = release === 117 ? cur.u32() : cur.u64();
    cur.expect("</N><label>");
    const dataLabelStart = cur.pos;
    const labelLength = release === 117 ? cur.u8() : cur.u16();
    const dataLabel = decode(cur.take(labelLength));
    const dataLabelAt: [number, number] = [dataLabelStart, cur.pos - dataLabelStart];
    cur.expect("</label><timestamp>");
    const timestamp = decode(cur.take(cur.u8())).trim();
    cur.expect("</timestamp></header><map>");
    const mapAt = cur.pos;
    const map: number[] = [];
    for (let i = 0; i < 14; i++) map.push(cur.u64());
    cur.expect("</map>");

    for (let i = 2; i <= 12; i++) {
      if (map[i] < map[i - 1] || map[i] > source.size) {
        throw new DtaFormatError("malformed or truncated .dta file: section map is out of range");
      }
    }

    const nameWidth = release === 117 ? 33 : 129;
    const formatWidth = release === 117 ? 49 : 57;
    const labelWidth = release === 117 ? 81 : 321;

    // Everything between <variable_types> and <data> is descriptors.
    const descLength = map[9] - map[2];
    if (descLength > MAX_METADATA_BYTES) {
      throw new DtaFormatError("malformed .dta file: descriptor section is implausibly large");
    }
    const desc = new Cursor(await source.read(map[2], descLength), () => littleEndian);
    const at = (mapIndex: number): void => {
      desc.pos = map[mapIndex] - map[2];
    };

    at(2);
    desc.expect("<variable_types>");
    const types = [];
    for (let i = 0; i < nVars; i++) types.push(typeFromModernCode(desc.u16()));
    desc.expect("</variable_types>");

    at(3);
    desc.expect("<varnames>");
    const names: string[] = [];
    for (let i = 0; i < nVars; i++) names.push(decode(zeroTerminated(desc.take(nameWidth))));
    desc.expect("</varnames>");

    at(4);
    desc.expect("<sortlist>");
    const sortedBy: string[] = [];
    for (let i = 0; i < nVars + 1; i++) {
      const v = wide ? desc.u32() : desc.u16();
      if (v === 0 || v > nVars) break;
      sortedBy.push(names[v - 1]);
    }

    at(5);
    desc.expect("<formats>");
    const formats: string[] = [];
    for (let i = 0; i < nVars; i++) formats.push(decode(zeroTerminated(desc.take(formatWidth))));
    desc.expect("</formats>");

    at(6);
    desc.expect("<value_label_names>");
    const labelNames: string[] = [];
    for (let i = 0; i < nVars; i++) labelNames.push(decode(zeroTerminated(desc.take(nameWidth))));
    desc.expect("</value_label_names>");

    at(7);
    desc.expect("<variable_labels>");
    const labels: string[] = [];
    for (let i = 0; i < nVars; i++) labels.push(decode(zeroTerminated(desc.take(labelWidth))));
    desc.expect("</variable_labels>");

    at(8);
    desc.expect("<characteristics>");
    const chars: RawCharacteristic[] = [];
    while (desc.bytes[desc.pos + 1] === 0x63) {
      // "<ch>" (as opposed to "</characteristics>")
      desc.expect("<ch>");
      const length = desc.u32();
      const body = desc.take(length);
      desc.expect("</ch>");
      if (length < 2 * nameWidth) continue;
      const name = decode(zeroTerminated(body.subarray(nameWidth, 2 * nameWidth)));
      // Only notes are text worth decoding; other characteristics may be binary.
      if (!/^note\d+$/.test(name)) continue;
      chars.push({
        varname: decode(zeroTerminated(body.subarray(0, nameWidth))),
        name,
        value: decode(zeroTerminated(body.subarray(2 * nameWidth))),
      });
    }

    // Value labels.
    const valueLabels = new Map<string, Map<number, string>>();
    const vlLength = map[12] - map[11];
    if (vlLength > MAX_METADATA_BYTES) {
      throw new DtaFormatError("malformed .dta file: value-label section is implausibly large");
    }
    const vl = new Cursor(await source.read(map[11], vlLength), () => littleEndian);
    vl.expect("<value_labels>");
    const recordsStart = map[11] + vl.pos;
    const records: DtaValueLabelRecord[] = [];
    while (vl.bytes[vl.pos + 1] === 0x6c) {
      // "<lbl>" (as opposed to "</value_labels>")
      const start = map[11] + vl.pos;
      vl.expect("<lbl>");
      const length = vl.u32();
      const name = decode(zeroTerminated(vl.take(nameWidth)));
      vl.take(3);
      valueLabels.set(name, parseValueLabelTable(vl, length, decode));
      vl.expect("</lbl>");
      records.push({ name, start, end: map[11] + vl.pos });
    }
    const recordsEnd = map[11] + vl.pos;
    let valueLabelsIntact = true;
    try {
      vl.expect("</value_labels>");
    } catch {
      valueLabelsIntact = false;
    }

    const dataOffset = map[9] + "<data>".length;
    const dataEnd = map[10] - "</data>".length;
    const layout: Layout = {
      release,
      littleEndian,
      dataOffset,
      strls: [map[10] + "<strls>".length, map[11] - "</strls>".length],
      labelsOffset: map[7] + "<variable_labels>".length,
      labelWidth,
      edit: {
        release,
        littleEndian,
        fileSize: source.size,
        setNamesOffset: map[6] + "<value_label_names>".length,
        nameWidth,
        dataLabelAt,
        mapAt,
        map,
        valueLabels: [recordsStart, recordsEnd],
        records,
        valueLabelsIntact,
      },
    };
    const meta = buildMeta({
      release,
      littleEndian,
      nObsHeader,
      dataLabel,
      timestamp,
      types,
      names,
      formats,
      labelNames,
      labels,
      sortedBy,
      valueLabels,
      chars,
      dataBytes: Math.max(0, dataEnd - dataOffset),
    });
    return new DtaReader(source, meta, layout, decode, maxStrLBytes);
  }

  // ── formats 113-115 ───────────────────────────────────────────────────────

  private static async openLegacy(
    source: ByteSource,
    head: Uint8Array,
    legacyEncoding: string,
    maxStrLBytes: number,
  ): Promise<DtaReader> {
    const release = head[0];
    if (head[1] !== 1 && head[1] !== 2) {
      throw new DtaFormatError("malformed .dta file: unknown byte order");
    }
    const littleEndian = head[1] === 2;
    const decode = makeDecoder(release, legacyEncoding);
    const formatWidth = release === 113 ? 12 : 49;

    const fixed = new Cursor(head, () => littleEndian);
    fixed.pos = 4;
    const nVars = fixed.u16();
    const nObsHeader = fixed.u32();
    const dataLabel = decode(zeroTerminated(fixed.take(81)));
    const timestamp = decode(zeroTerminated(fixed.take(18))).trim();

    const descLength = nVars * (1 + 33 + formatWidth + 33 + 81) + 2 * (nVars + 1);
    const desc = new Cursor(await source.read(109, descLength), () => littleEndian);
    const types = [];
    for (let i = 0; i < nVars; i++) types.push(typeFromLegacyCode(desc.u8()));
    const names: string[] = [];
    for (let i = 0; i < nVars; i++) names.push(decode(zeroTerminated(desc.take(33))));
    const sortedBy: string[] = [];
    const sortStart = desc.pos;
    for (let i = 0; i < nVars + 1; i++) {
      const v = desc.u16();
      if (v === 0 || v > nVars) break;
      sortedBy.push(names[v - 1]);
    }
    desc.pos = sortStart + 2 * (nVars + 1);
    const formats: string[] = [];
    for (let i = 0; i < nVars; i++) formats.push(decode(zeroTerminated(desc.take(formatWidth))));
    const setNamesOffset = 109 + desc.pos;
    const labelNames: string[] = [];
    for (let i = 0; i < nVars; i++) labelNames.push(decode(zeroTerminated(desc.take(33))));
    const labelsOffset = 109 + desc.pos;
    const labels: string[] = [];
    for (let i = 0; i < nVars; i++) labels.push(decode(zeroTerminated(desc.take(81))));

    // Expansion fields: (type, len, contents) records ending in a 0/0 record.
    const chars: RawCharacteristic[] = [];
    let pos = 109 + descLength;
    for (;;) {
      const rec = new Cursor(await source.read(pos, 5), () => littleEndian);
      const type = rec.u8();
      const length = rec.u32();
      pos += 5;
      if (type === 0 && length === 0) break;
      if (pos + length > source.size) {
        throw new DtaFormatError("malformed or truncated .dta file: expansion field overruns EOF");
      }
      if (type === 1 && length >= 66) {
        const nameBytes = await source.read(pos + 33, 33);
        const name = decode(zeroTerminated(nameBytes));
        if (/^note\d+$/.test(name)) {
          const body = await source.read(pos, length);
          chars.push({
            varname: decode(zeroTerminated(body.subarray(0, 33))),
            name,
            value: decode(zeroTerminated(body.subarray(66))),
          });
        }
      }
      pos += length;
    }

    const dataOffset = pos;
    const rowWidth = types.reduce((sum, t) => sum + t.width, 0);
    const dataEnd = Math.min(source.size, dataOffset + nObsHeader * rowWidth);

    // Value labels run from the end of the data to EOF.
    const valueLabels = new Map<string, Map<number, string>>();
    const vlLength = source.size - dataEnd;
    const records: DtaValueLabelRecord[] = [];
    // The writer needs every record accounted for; the viewer does not.
    let valueLabelsIntact =
      dataOffset + nObsHeader * rowWidth <= source.size && vlLength <= MAX_METADATA_BYTES;
    if (vlLength > 0 && vlLength <= MAX_METADATA_BYTES) {
      const vl = new Cursor(await source.read(dataEnd, vlLength), () => littleEndian);
      try {
        while (vl.pos + 40 <= vl.bytes.byteLength) {
          const start = dataEnd + vl.pos;
          const length = vl.i32();
          const name = decode(zeroTerminated(vl.take(33)));
          vl.take(3);
          if (length < 8 || vl.pos + length > vl.bytes.byteLength) {
            valueLabelsIntact = false;
            break;
          }
          valueLabels.set(name, parseValueLabelTable(vl, length, decode));
          records.push({ name, start, end: dataEnd + vl.pos });
        }
        if (vl.pos !== vl.bytes.byteLength) valueLabelsIntact = false;
      } catch (err) {
        if (!(err instanceof DtaFormatError)) throw err;
        // A cut-off trailing table is not worth refusing the whole file for.
        valueLabelsIntact = false;
      }
    }

    const layout: Layout = {
      release,
      littleEndian,
      dataOffset,
      strls: null,
      labelsOffset,
      labelWidth: 81,
      edit: {
        release,
        littleEndian,
        fileSize: source.size,
        setNamesOffset,
        nameWidth: 33,
        dataLabelAt: [10, 81],
        mapAt: null,
        map: null,
        valueLabels: [dataEnd, source.size],
        records,
        valueLabelsIntact,
      },
    };
    const meta = buildMeta({
      release,
      littleEndian,
      nObsHeader,
      dataLabel,
      timestamp,
      types,
      names,
      formats,
      labelNames,
      labels,
      sortedBy,
      valueLabels,
      chars,
      dataBytes: Math.max(0, source.size - dataOffset),
    });
    return new DtaReader(source, meta, layout, decode, maxStrLBytes);
  }

  /** Where the label metadata dtaWriter can change sits in the file. */
  get editLayout(): DtaEditLayout {
    return this.layout.edit;
  }

  /**
   * Where variable `index`'s label sits in the file. Labels are fixed-width,
   * zero-terminated fields, which is what lets dtaWriter replace one in place.
   */
  variableLabelField(index: number): { offset: number; width: number } {
    if (!Number.isInteger(index) || index < 0 || index >= this.meta.nVars) {
      throw new RangeError(`variable index ${index} is out of range`);
    }
    const { labelsOffset, labelWidth } = this.layout;
    return { offset: labelsOffset + index * labelWidth, width: labelWidth };
  }

  // ── observations ──────────────────────────────────────────────────────────

  /**
   * Read `count` observations starting at zero-based row `start`. `columns`
   * selects variables by index (default: all); cells come back in that order.
   * The range is clamped to the dataset, so the result may be shorter.
   */
  async readRows(start: number, count: number, columns?: number[]): Promise<DtaCell[][]> {
    const { nObs, rowWidth, variables } = this.meta;
    const first = Math.max(0, Math.floor(start));
    const n = Math.max(0, Math.min(Math.floor(count), nObs - first));
    const vars = (columns ?? variables.map((v) => v.index)).map((i) => {
      const v = variables[i];
      if (!v) throw new RangeError(`variable index ${i} is out of range`);
      return v;
    });
    if (n === 0) return [];
    if (rowWidth === 0) return Array.from({ length: n }, () => vars.map(() => ""));

    const bytes = await this.source.read(this.layout.dataOffset + first * rowWidth, n * rowWidth);
    if (bytes.byteLength < n * rowWidth) {
      throw new DtaFormatError("truncated .dta file: data section ends early");
    }
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const le = this.layout.littleEndian;

    const rows: DtaCell[][] = [];
    const pending: Array<{ row: number; col: number; key: string }> = [];
    for (let r = 0; r < n; r++) {
      const base = r * rowWidth;
      const row: DtaCell[] = new Array(vars.length);
      for (let c = 0; c < vars.length; c++) {
        const v = vars[c];
        const at = base + v.offset;
        switch (v.kind) {
          case "byte": {
            const x = view.getInt8(at);
            row[c] = x > 100 ? missingCode(x - 101) : x;
            break;
          }
          case "int": {
            const x = view.getInt16(at, le);
            row[c] = x > 32740 ? missingCode(x - 32741) : x;
            break;
          }
          case "long": {
            const x = view.getInt32(at, le);
            row[c] = x > 2147483620 ? missingCode(x - VALUE_LABEL_MISSING_BASE) : x;
            break;
          }
          case "float": {
            const bits = view.getUint32(at, le);
            if (bits >= 0x7f000000 && bits < 0x80000000) {
              const step = bits - 0x7f000000;
              row[c] = step % 0x800 === 0 ? missingCode(step / 0x800) : ".";
            } else {
              row[c] = float32ToNumber(view.getFloat32(at, le));
            }
            break;
          }
          case "double": {
            const hi = view.getUint32(le ? at + 4 : at, le);
            const lo = view.getUint32(le ? at : at + 4, le);
            if (hi >= 0x7fe00000 && hi < 0x80000000) {
              const step = hi - 0x7fe00000;
              row[c] = lo === 0 && step % 0x100 === 0 ? missingCode(step / 0x100) : ".";
            } else {
              row[c] = view.getFloat64(at, le);
            }
            break;
          }
          case "str":
            row[c] = this.decode(zeroTerminated(bytes.subarray(at, at + v.width)));
            break;
          case "strL": {
            const key = this.strLKey(view, at);
            row[c] = "";
            if (key) pending.push({ row: r, col: c, key });
            break;
          }
          default:
            row[c] = "";
        }
      }
      rows.push(row);
    }

    if (pending.length > 0) {
      const index = await this.loadStrLIndex();
      const cache = new Map<string, string>();
      for (const p of pending) {
        let text = cache.get(p.key);
        if (text === undefined) {
          text = await this.readStrL(index.get(p.key));
          cache.set(p.key, text);
        }
        rows[p.row][p.col] = text;
      }
    }
    return rows;
  }

  /**
   * Read specific observations, in the order given (used for sorted and
   * filtered views, where the rows on screen are not contiguous in the file).
   * Indices that sit close together in the file are fetched in one read.
   */
  async readRowsAt(indices: ArrayLike<number>, columns?: number[]): Promise<DtaCell[][]> {
    const n = indices.length;
    const out: DtaCell[][] = new Array(n);
    if (n === 0) return out;
    const order = Array.from({ length: n }, (_, i) => i).sort((a, b) => indices[a] - indices[b]);
    let i = 0;
    while (i < n) {
      const first = indices[order[i]];
      let j = i;
      // Extend the run while the next wanted row is at most a few rows away.
      while (j + 1 < n && indices[order[j + 1]] - first < READ_RUN_ROWS) j += 1;
      const last = indices[order[j]];
      const rows = await this.readRows(first, last - first + 1, columns);
      for (let k = i; k <= j; k++) {
        const row = rows[indices[order[k]] - first];
        if (!row) throw new RangeError(`observation ${indices[order[k]]} is out of range`);
        out[order[k]] = row;
      }
      i = j + 1;
    }
    return out;
  }

  /**
   * Visit every observation in file order, `chunkRows` at a time, decoding
   * only `columns`. `visit` gets each chunk and its first row index; return
   * `false` from it to stop early.
   */
  async scan(
    columns: number[],
    visit: (rows: DtaCell[][], start: number) => boolean | void,
    chunkRows?: number,
  ): Promise<void> {
    const { nObs, rowWidth } = this.meta;
    // Bound a chunk by bytes as well as rows: observations can be very wide.
    chunkRows ??= Math.max(1, Math.min(SCAN_CHUNK_ROWS, Math.floor(SCAN_CHUNK_BYTES / Math.max(1, rowWidth))));
    for (let start = 0; start < nObs; start += chunkRows) {
      const rows = await this.readRows(start, Math.min(chunkRows, nObs - start), columns);
      if (visit(rows, start) === false) return;
    }
  }

  /** The (v,o) reference stored in a strL data cell, or "" for an empty string. */
  private strLKey(view: DataView, at: number): string {
    const le = this.layout.littleEndian;
    const release = this.layout.release;
    let v: number;
    let o: number;
    if (release === 117) {
      v = view.getUint32(at, le);
      o = view.getUint32(at + 4, le);
    } else {
      // 118/120 pack (v,o) as 2+6 bytes; 119/121 as 3+5 bytes.
      const vBytes = release === 119 || release === 121 ? 3 : 2;
      const raw: number[] = [];
      for (let i = 0; i < 8; i++) raw.push(view.getUint8(at + i));
      const field = (from: number, length: number): number => {
        let acc = 0;
        for (let i = 0; i < length; i++) {
          const byte = le ? raw[from + length - 1 - i] : raw[from + i];
          acc = acc * 256 + byte;
        }
        return acc;
      };
      v = field(0, vBytes);
      o = field(vBytes, 8 - vBytes);
    }
    return v === 0 && o === 0 ? "" : `${v}:${o}`;
  }

  /** Scan the <strls> section once, recording where each GSO's contents live. */
  private async loadStrLIndex(): Promise<Map<string, StrLEntry>> {
    if (this.strlIndex) return this.strlIndex;
    const index = new Map<string, StrLEntry>();
    const section = this.layout.strls;
    if (section) {
      const [start, end] = section;
      const le = this.layout.littleEndian;
      const oBytes = this.layout.release === 117 ? 4 : 8;
      const headerSize = 3 + 4 + oBytes + 1 + 4;
      let pos = start;
      let chunkStart = 0;
      let chunk: Uint8Array = new Uint8Array(0);
      while (pos + headerSize <= end) {
        if (pos < chunkStart || pos + headerSize > chunkStart + chunk.byteLength) {
          chunkStart = pos;
          chunk = await this.source.read(pos, Math.min(STRL_SCAN_CHUNK, end - pos));
          if (chunk.byteLength < headerSize) break;
        }
        const at = pos - chunkStart;
        if (chunk[at] !== 0x47 || chunk[at + 1] !== 0x53 || chunk[at + 2] !== 0x4f) break; // "GSO"
        const view = new DataView(chunk.buffer, chunk.byteOffset + at, headerSize);
        const v = view.getUint32(3, le);
        const o = oBytes === 4 ? view.getUint32(7, le) : Number(view.getBigUint64(7, le));
        const type = view.getUint8(7 + oBytes);
        const length = view.getUint32(8 + oBytes, le);
        index.set(`${v}:${o}`, { offset: pos + headerSize, length, binary: type === 129 });
        pos += headerSize + length;
      }
    }
    this.strlIndex = index;
    return index;
  }

  private async readStrL(entry: StrLEntry | undefined): Promise<string> {
    if (!entry) return "";
    // ASCII-typed GSOs include a trailing \0 in their length; binary ones do not.
    const length = entry.binary ? entry.length : Math.max(0, entry.length - 1);
    const shown = Math.min(length, this.maxStrLBytes);
    const bytes = await this.source.read(entry.offset, shown);
    const text = this.decode(entry.binary ? bytes : zeroTerminated(bytes));
    return shown < length ? `${text}…` : text;
  }

  async close(): Promise<void> {
    await this.source.close?.();
  }
}

interface MetaParts {
  release: number;
  littleEndian: boolean;
  nObsHeader: number;
  dataLabel: string;
  timestamp: string;
  types: Array<{ type: string; kind: DtaVarKind; width: number }>;
  names: string[];
  formats: string[];
  labelNames: string[];
  labels: string[];
  sortedBy: string[];
  valueLabels: Map<string, Map<number, string>>;
  chars: RawCharacteristic[];
  /** Bytes actually present in the data section. */
  dataBytes: number;
}

function buildMeta(p: MetaParts): DtaMeta {
  const notes = collectNotes(p.chars);
  const variables: DtaVariable[] = [];
  let offset = 0;
  for (let i = 0; i < p.types.length; i++) {
    const t = p.types[i];
    variables.push({
      index: i,
      name: p.names[i],
      type: t.type,
      kind: t.kind,
      width: t.width,
      offset,
      format: p.formats[i],
      valueLabel: p.labelNames[i],
      label: p.labels[i],
      notes: notes.get(p.names[i]) ?? [],
    });
    offset += t.width;
  }
  const rowWidth = offset;
  const warnings: string[] = [];
  let nObs = p.nObsHeader;
  if (rowWidth > 0) {
    const available = Math.floor(p.dataBytes / rowWidth);
    if (available < nObs) {
      warnings.push(
        `The file is truncated: its header declares ${nObs} observations ` +
          `but only ${available} are present.`,
      );
      nObs = available;
    }
  }
  return {
    release: p.release,
    byteOrder: p.littleEndian ? "LSF" : "MSF",
    nVars: variables.length,
    nObs,
    dataLabel: p.dataLabel,
    timestamp: p.timestamp,
    variables,
    sortedBy: p.sortedBy,
    valueLabels: p.valueLabels,
    notes: notes.get("_dta") ?? [],
    rowWidth,
    warnings,
  };
}
