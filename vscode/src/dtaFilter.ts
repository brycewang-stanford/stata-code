// A small evaluator for Stata `if` expressions, used to filter rows in the
// dta viewer: `age > 60 & !missing(income)`, `region == "South":regionlbl`,
// `inlist(city, "Boston", "北京")`.
//
// It follows Stata's semantics rather than JavaScript's, because a filter
// that silently disagrees with `count if …` would be worse than no filter:
//   - missing values are larger than every number, and . < .a < … < .z, so
//     `age > 60` is true for a missing age, exactly as in Stata;
//   - arithmetic on a missing value yields `.`;
//   - any nonzero result, including missing, is "true";
//   - comparing a string with a number is a "type mismatch" error.
// The test suite checks 48 expressions against Stata 18's own `count if`.
//
// Supported: numeric and string literals, `.` / `.a`–`.z`, variable names
// and their unambiguous abbreviations, `_n` / `_N`, `"text":labelname`, the
// operators ! ~ ^ - * / + == != ~= < <= > >= & |, a working set of functions
// (see FUNCTIONS), and the time-series operators L. F. D. S. (`L2.x`,
// `D.x`, `L2D.x`) on a dataset that was `tsset` or `xtset`. Not supported:
// macros, `in` ranges, operator lists such as `L(1/3).x`.
//
// A time-series operator reads another observation, so an expression that
// uses one must be prepared (one pass over the time, panel and operand
// columns) before it is tested; see CompiledFilter.prepare.
//
// Kept free of any `vscode` import so it runs under `node --test`.

import { TextEncoder } from "node:util";

import { DtaCell, DtaMeta, DtaReader, missingIndex } from "./dtaReader";

/** Numbers at or above this are Stata missing values in this module's encoding. */
const MISSING_FLOOR = 8.99e307;
const MISSING_BASE = 9e307;
const MISSING_STEP = 1e306;
/** Stata's system missing value `.`. */
export const SYSMISS = MISSING_BASE;

/**
 * Map a numeric cell onto one number line: ordinary values as themselves,
 * `.` and `.a`–`.z` as 27 distinct values above every ordinary one, in
 * Stata's order. Comparisons and sorting then need no special cases.
 */
export function numericKey(cell: DtaCell): number {
  if (typeof cell === "number") return cell;
  const index = missingIndex(cell);
  return MISSING_BASE + Math.max(0, index) * MISSING_STEP;
}

export function isMissingKey(x: number): boolean {
  return !(x < MISSING_FLOOR);
}

/** 0 for `.`, 1–26 for `.a`–`.z`; only meaningful when {@link isMissingKey}. */
export function missingKeyIndex(x: number): number {
  return Math.min(26, Math.max(0, Math.round((x - MISSING_BASE) / MISSING_STEP)));
}

export class DtaFilterError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DtaFilterError";
  }
}

export interface CompiledFilter {
  /** Variable indices the expression reads, in slot order. */
  columns: number[];
  /**
   * `cells` are the values of `columns` for one observation; `obs` is its
   * 1-based position in the dataset (`_n`).
   */
  test(cells: DtaCell[], obs: number): boolean;
  /**
   * Present when the expression uses time-series operators. Reads the lagged
   * values it needs in one pass; `test` may only be called once it resolves.
   * `onChunk` is called between chunks and may throw to cancel.
   */
  prepare?(reader: DtaReader, onChunk?: () => void): Promise<void>;
  /** Bytes per observation that `prepare` holds in memory. */
  prepareBytesPerRow?: number;
}

// ── tokens ──────────────────────────────────────────────────────────────────

type Token =
  | { kind: "num"; value: number }
  | { kind: "str"; value: string }
  | { kind: "name"; value: string }
  | { kind: "tsop"; value: string }
  | { kind: "op"; value: string }
  | { kind: "end" };

const OPERATORS = ["==", "!=", "~=", ">=", "<=", "&", "|", "!", "~", "^", "*", "/", "+", "-", ">", "<", "(", ")", ",", ":", "="];
const NAME_START = /[\p{L}_]/u;
const NAME_CHAR = /[\p{L}\p{N}_]/u;
/** `L`, `l2`, `F`, `D2`, `S12`, and runs of them such as `L2D`. */
const TS_OPERATOR = /^(?:[LFDSlfds]\d*)+$/;

function tokenize(source: string): Token[] {
  const tokens: Token[] = [];
  let i = 0;
  while (i < source.length) {
    const ch = source[i];
    if (/\s/.test(ch)) {
      i += 1;
      continue;
    }
    // Compound double quotes: `"…"'
    if (ch === "`" && source[i + 1] === '"') {
      const end = source.indexOf("\"'", i + 2);
      if (end < 0) throw new DtaFilterError("unterminated string");
      tokens.push({ kind: "str", value: source.slice(i + 2, end) });
      i = end + 2;
      continue;
    }
    if (ch === '"') {
      const end = source.indexOf('"', i + 1);
      if (end < 0) throw new DtaFilterError("unterminated string");
      tokens.push({ kind: "str", value: source.slice(i + 1, end) });
      i = end + 1;
      continue;
    }
    const number = /^(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/.exec(source.slice(i));
    if (number) {
      tokens.push({ kind: "num", value: Number(number[0]) });
      i += number[0].length;
      continue;
    }
    if (ch === ".") {
      // `.` or an extended missing value `.a` … `.z`
      const next = source[i + 1] ?? "";
      const after = source[i + 2] ?? "";
      if (next >= "a" && next <= "z" && !NAME_CHAR.test(after)) {
        tokens.push({ kind: "num", value: MISSING_BASE + (next.charCodeAt(0) - 96) * MISSING_STEP });
        i += 2;
      } else {
        tokens.push({ kind: "num", value: SYSMISS });
        i += 1;
      }
      continue;
    }
    if (NAME_START.test(ch)) {
      let j = i + 1;
      while (j < source.length && NAME_CHAR.test(source[j])) j += 1;
      const name = source.slice(i, j);
      // td(01jan2020): the argument is not an expression, so lex it whole.
      if (name === "td" && /^\s*\(/.test(source.slice(j))) {
        const close = source.indexOf(")", j);
        if (close < 0) throw new DtaFilterError("invalid syntax");
        const open = source.indexOf("(", j);
        tokens.push({ kind: "num", value: parseDateLiteral(source.slice(open + 1, close)) });
        i = close + 1;
        continue;
      }
      // `L.sales`: a time-series operator, its dot, and the variable.
      if (TS_OPERATOR.test(name) && source[j] === "." && NAME_START.test(source[j + 1] ?? "")) {
        tokens.push({ kind: "tsop", value: name });
        i = j + 1;
        continue;
      }
      tokens.push({ kind: "name", value: name });
      i = j;
      continue;
    }
    const op = OPERATORS.find((candidate) => source.startsWith(candidate, i));
    if (!op) throw new DtaFilterError(`invalid syntax near ${JSON.stringify(ch)}`);
    tokens.push({ kind: "op", value: op });
    i += op.length;
  }
  tokens.push({ kind: "end" });
  return tokens;
}

const MONTH_NAMES = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
const MS_PER_DAY = 86400000;
const EPOCH = Date.UTC(1960, 0, 1);

function daysFromCivil(year: number, month: number, day: number): number {
  const date = new Date(Date.UTC(2000, month - 1, day));
  date.setUTCFullYear(year);
  // Reject day/month overflow (31 feb), which Date would silently roll over.
  if (date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return SYSMISS;
  return Math.round((date.getTime() - EPOCH) / MS_PER_DAY);
}

function parseDateLiteral(text: string): number {
  const m = /^\s*(\d{1,2})\s*([A-Za-z]{3})[A-Za-z]*\s*(\d{4})\s*$/.exec(text);
  const month = m ? MONTH_NAMES.indexOf(m[2].toLowerCase()) : -1;
  if (!m || month < 0) throw new DtaFilterError(`td(): ${text.trim()} is not a date like 01jan2020`);
  return daysFromCivil(Number(m[3]), month + 1, Number(m[1]));
}

// ── compiled nodes ──────────────────────────────────────────────────────────

type Row = DtaCell[];
type NumFn = (row: Row, obs: number) => number;
type StrFn = (row: Row, obs: number) => string;
type Node = { type: "num"; fn: NumFn } | { type: "str"; fn: StrFn };

function num(fn: NumFn): Node {
  return { type: "num", fn };
}
function str(fn: StrFn): Node {
  return { type: "str", fn };
}

function wantNum(node: Node): NumFn {
  if (node.type !== "num") throw new DtaFilterError("type mismatch");
  return node.fn;
}
function wantStr(node: Node): StrFn {
  if (node.type !== "str") throw new DtaFilterError("type mismatch");
  return node.fn;
}

/** Arithmetic result → Stata value: non-finite or out of range becomes `.`. */
function arith(x: number): number {
  return Number.isFinite(x) && x < MISSING_FLOOR ? x : SYSMISS;
}

function bool(x: boolean): number {
  return x ? 1 : 0;
}

const utf8 = new TextEncoder();

function globToRegExp(pattern: string): RegExp {
  let out = "^";
  for (const ch of pattern) {
    if (ch === "*") out += "[\\s\\S]*";
    else if (ch === "?") out += "[\\s\\S]";
    else out += ch.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }
  return new RegExp(`${out}$`, "u");
}

type FunctionBuilder = (args: Node[]) => Node;

function arity(name: string, args: Node[], min: number, max = min): void {
  if (args.length < min || args.length > max) {
    throw new DtaFilterError(`${name}(): wrong number of arguments`);
  }
}

function numeric1(name: string, f: (x: number) => number): FunctionBuilder {
  return (args) => {
    arity(name, args, 1);
    const a = wantNum(args[0]);
    return num((r, n) => {
      const x = a(r, n);
      return isMissingKey(x) ? SYSMISS : arith(f(x));
    });
  };
}

function string1(name: string, f: (s: string) => string): FunctionBuilder {
  return (args) => {
    arity(name, args, 1);
    const a = wantStr(args[0]);
    return str((r, n) => f(a(r, n)));
  };
}

function isMissingNode(node: Node): NumFn {
  if (node.type === "num") {
    const f = node.fn;
    return (r, n) => bool(isMissingKey(f(r, n)));
  }
  const f = node.fn;
  return (r, n) => bool(f(r, n) === "");
}

function missingBuilder(name: string): FunctionBuilder {
  return (args) => {
    if (args.length === 0) throw new DtaFilterError(`${name}(): wrong number of arguments`);
    const tests = args.map(isMissingNode);
    return num((r, n) => bool(tests.some((t) => t(r, n) === 1)));
  };
}

function extremum(name: string, pick: (a: number, b: number) => number): FunctionBuilder {
  return (args) => {
    if (args.length === 0) throw new DtaFilterError(`${name}(): wrong number of arguments`);
    const fns = args.map(wantNum);
    // Stata's min()/max() skip missing arguments; all missing gives missing.
    return num((r, n) => {
      let acc = SYSMISS;
      for (const f of fns) {
        const x = f(r, n);
        if (isMissingKey(x)) continue;
        acc = isMissingKey(acc) ? x : pick(acc, x);
      }
      return acc;
    });
  };
}

const FUNCTIONS: Record<string, FunctionBuilder> = {
  missing: missingBuilder("missing"),
  mi: missingBuilder("mi"),
  inlist: (args) => {
    if (args.length < 2) throw new DtaFilterError("inlist(): wrong number of arguments");
    if (args[0].type === "num") {
      const fns = args.map(wantNum);
      return num((r, n) => {
        const x = fns[0](r, n);
        for (let i = 1; i < fns.length; i++) if (fns[i](r, n) === x) return 1;
        return 0;
      });
    }
    const fns = args.map(wantStr);
    return num((r, n) => {
      const x = fns[0](r, n);
      for (let i = 1; i < fns.length; i++) if (fns[i](r, n) === x) return 1;
      return 0;
    });
  },
  inrange: (args) => {
    arity("inrange", args, 3);
    if (args[0].type === "str") {
      const [z, a, b] = args.map(wantStr);
      return num((r, n) => bool(a(r, n) <= z(r, n) && z(r, n) <= b(r, n)));
    }
    const [z, a, b] = args.map(wantNum);
    // Stata: a missing bound is open on that side; a missing z is never in range.
    return num((r, n) => {
      const x = z(r, n);
      if (isMissingKey(x)) return 0;
      const lo = a(r, n);
      const hi = b(r, n);
      return bool((isMissingKey(lo) || lo <= x) && (isMissingKey(hi) || x <= hi));
    });
  },
  strpos: (args) => {
    arity("strpos", args, 2);
    const [s, sub] = args.map(wantStr);
    return num((r, n) => {
      const needle = sub(r, n);
      return needle === "" ? 0 : s(r, n).indexOf(needle) + 1;
    });
  },
  regexm: (args) => {
    arity("regexm", args, 2);
    const [s, re] = args.map(wantStr);
    const cache = new Map<string, RegExp>();
    return num((r, n) => {
      const pattern = re(r, n);
      let compiled = cache.get(pattern);
      if (!compiled) {
        try {
          compiled = new RegExp(pattern, "u");
        } catch {
          throw new DtaFilterError(`regexm(): invalid regular expression ${JSON.stringify(pattern)}`);
        }
        cache.set(pattern, compiled);
      }
      return bool(compiled.test(s(r, n)));
    });
  },
  strmatch: (args) => {
    arity("strmatch", args, 2);
    const [s, pattern] = args.map(wantStr);
    const cache = new Map<string, RegExp>();
    return num((r, n) => {
      const p = pattern(r, n);
      let compiled = cache.get(p);
      if (!compiled) {
        compiled = globToRegExp(p);
        cache.set(p, compiled);
      }
      return bool(compiled.test(s(r, n)));
    });
  },
  lower: string1("lower", (s) => s.toLowerCase()),
  upper: string1("upper", (s) => s.toUpperCase()),
  strlower: string1("strlower", (s) => s.toLowerCase()),
  strupper: string1("strupper", (s) => s.toUpperCase()),
  trim: string1("trim", (s) => s.replace(/^ +| +$/g, "")),
  strtrim: string1("strtrim", (s) => s.replace(/^ +| +$/g, "")),
  // Stata's strlen() counts bytes; ustrlen() counts characters.
  strlen: (args) => {
    arity("strlen", args, 1);
    const s = wantStr(args[0]);
    return num((r, n) => utf8.encode(s(r, n)).length);
  },
  length: (args) => FUNCTIONS.strlen(args),
  ustrlen: (args) => {
    arity("ustrlen", args, 1);
    const s = wantStr(args[0]);
    return num((r, n) => [...s(r, n)].length);
  },
  substr: (args) => {
    arity("substr", args, 3);
    const s = wantStr(args[0]);
    const from = wantNum(args[1]);
    const count = wantNum(args[2]);
    return str((r, n) => {
      const chars = [...s(r, n)];
      let start = from(r, n);
      const len = count(r, n);
      if (isMissingKey(start) || start === 0) return "";
      if (start < 0) start = chars.length + start + 1;
      if (start < 1) return "";
      const end = isMissingKey(len) ? chars.length : start - 1 + Math.max(0, len);
      return chars.slice(start - 1, end).join("");
    });
  },
  usubstr: (args) => FUNCTIONS.substr(args),
  abs: numeric1("abs", Math.abs),
  floor: numeric1("floor", Math.floor),
  ceil: numeric1("ceil", Math.ceil),
  int: numeric1("int", Math.trunc),
  sqrt: numeric1("sqrt", Math.sqrt),
  exp: numeric1("exp", Math.exp),
  ln: numeric1("ln", Math.log),
  log: numeric1("log", Math.log),
  log10: numeric1("log10", Math.log10),
  round: (args) => {
    arity("round", args, 1, 2);
    const x = wantNum(args[0]);
    const unit = args[1] ? wantNum(args[1]) : undefined;
    // Stata rounds halves away from zero.
    const nearest = (v: number): number => Math.sign(v) * Math.floor(Math.abs(v) + 0.5);
    return num((r, n) => {
      const v = x(r, n);
      if (isMissingKey(v)) return SYSMISS;
      const u = unit ? unit(r, n) : 1;
      if (isMissingKey(u)) return SYSMISS;
      return u === 0 ? v : arith(nearest(v / u) * u);
    });
  },
  mod: (args) => {
    arity("mod", args, 2);
    const [x, y] = args.map(wantNum);
    return num((r, n) => {
      const a = x(r, n);
      const b = y(r, n);
      if (isMissingKey(a) || isMissingKey(b) || b === 0) return SYSMISS;
      return arith(a - b * Math.floor(a / b));
    });
  },
  min: extremum("min", Math.min),
  max: extremum("max", Math.max),
  mdy: (args) => {
    arity("mdy", args, 3);
    const [m, d, y] = args.map(wantNum);
    return num((r, n) => {
      const mm = m(r, n);
      const dd = d(r, n);
      const yy = y(r, n);
      if (![mm, dd, yy].every((v) => Number.isInteger(v))) return SYSMISS;
      if (mm < 1 || mm > 12 || dd < 1 || dd > 31 || yy < 100 || yy > 9999) return SYSMISS;
      return daysFromCivil(yy, mm, dd);
    });
  },
};

// ── time-series operators ───────────────────────────────────────────────────

/**
 * An operator such as `L2D` as a polynomial in the lag operator: lag → weight.
 * `L2` is {2: 1}, `F` is {-1: 1}, `D` is {0: 1, 1: -1}, `S12` is
 * {0: 1, 12: -1}; a run of operators is the product of its parts.
 */
function lagPolynomial(operator: string): Map<number, number> {
  let poly = new Map<number, number>([[0, 1]]);
  const times = (factor: Map<number, number>): void => {
    const next = new Map<number, number>();
    for (const [a, wa] of poly) {
      for (const [b, wb] of factor) next.set(a + b, (next.get(a + b) ?? 0) + wa * wb);
    }
    poly = next;
  };
  for (const m of operator.matchAll(/([LFDSlfds])(\d*)/g)) {
    const letter = m[1].toUpperCase();
    const k = m[2] === "" ? 1 : Number(m[2]);
    if (letter === "L") times(new Map([[k, 1]]));
    else if (letter === "F") times(new Map([[-k, 1]]));
    else if (letter === "S") times(k === 0 ? new Map([[0, 1]]) : new Map([[0, 1], [k, -1]]));
    else for (let i = 0; i < k; i++) times(new Map([[0, 1], [1, -1]]));
  }
  for (const [lag, weight] of poly) if (weight === 0) poly.delete(lag);
  return poly;
}

/** One `L2.x`-style operand: filled by {@link prepareSeries}. */
interface Series {
  column: number;
  terms: Array<[number, number]>;
  /** Value per observation, in dataset order. */
  values?: Float64Array;
}

async function prepareSeries(
  reader: DtaReader,
  series: Series[],
  onChunk?: () => void,
): Promise<void> {
  const { meta } = reader;
  const ts = meta.tsset;
  if (!ts) throw new DtaFilterError("time variable not set");
  const index = new Map(meta.variables.map((v) => [v.name, v.index]));
  const timeColumn = index.get(ts.timeVar) as number;
  const panelColumn = ts.panelVar === "" ? undefined : (index.get(ts.panelVar) as number);
  const operands = [...new Set(series.map((s) => s.column))];
  const columns = [timeColumn, ...(panelColumn === undefined ? [] : [panelColumn]), ...operands];
  const first = panelColumn === undefined ? 1 : 2;

  const n = meta.nObs;
  const time = new Float64Array(n);
  const panel = new Float64Array(panelColumn === undefined ? 0 : n);
  const data = operands.map(() => new Float64Array(n));
  await reader.scan(columns, (rows, start) => {
    onChunk?.();
    for (let r = 0; r < rows.length; r++) {
      const row = rows[r];
      time[start + r] = numericKey(row[0]);
      if (panelColumn !== undefined) panel[start + r] = numericKey(row[1]);
      for (let k = 0; k < data.length; k++) data[k][start + r] = numericKey(row[first + k]);
    }
  });

  // Observations in (panel, time) order; a lag is then found by bisection.
  const byTime = new Uint32Array(n);
  for (let i = 0; i < n; i++) byTime[i] = i;
  const inPanels = panel.length > 0;
  byTime.sort((a, b) => (inPanels && panel[a] !== panel[b] ? panel[a] - panel[b] : time[a] - time[b]));
  const find = (group: number, when: number): number => {
    let lo = 0;
    let hi = n;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      const obs = byTime[mid];
      const before = inPanels && panel[obs] !== group ? panel[obs] < group : time[obs] < when;
      if (before) lo = mid + 1;
      else hi = mid;
    }
    if (lo === n) return -1;
    const obs = byTime[lo];
    return time[obs] === when && (!inPanels || panel[obs] === group) ? obs : -1;
  };

  for (const s of series) {
    const x = data[operands.indexOf(s.column)];
    const out = new Float64Array(n);
    // `L.x` alone hands back the lagged value itself, so `L.x == .a` is true
    // where the lag held .a; anything that computes yields `.` on a missing.
    const plain = s.terms.length === 1 && s.terms[0][1] === 1;
    for (let obs = 0; obs < n; obs++) {
      const when = time[obs];
      let value = 0;
      if (isMissingKey(when)) value = SYSMISS;
      else {
        for (const [lag, weight] of s.terms) {
          const at = lag === 0 ? obs : find(inPanels ? panel[obs] : 0, when - lag * ts.delta);
          if (at < 0) {
            value = SYSMISS;
            break;
          }
          if (plain) {
            value = x[at];
            break;
          }
          if (isMissingKey(x[at])) {
            value = SYSMISS;
            break;
          }
          value += weight * x[at];
        }
      }
      out[obs] = plain ? value : arith(value);
    }
    s.values = out;
  }
}

// ── parser ──────────────────────────────────────────────────────────────────

class Parser {
  private pos = 0;
  readonly columns: number[] = [];
  readonly series: Series[] = [];
  private readonly slots = new Map<number, number>();
  private readonly byName: Map<string, number>;

  constructor(
    private readonly tokens: Token[],
    private readonly meta: DtaMeta,
  ) {
    this.byName = new Map(meta.variables.map((v) => [v.name, v.index]));
  }

  private peek(): Token {
    return this.tokens[this.pos];
  }

  private isOp(...values: string[]): boolean {
    const t = this.peek();
    return t.kind === "op" && values.includes(t.value);
  }

  private takeOp(...values: string[]): string | undefined {
    const t = this.peek();
    if (t.kind === "op" && values.includes(t.value)) {
      this.pos += 1;
      return t.value;
    }
    return undefined;
  }

  private expectOp(value: string): void {
    if (!this.takeOp(value)) throw new DtaFilterError("invalid syntax");
  }

  parse(): Node {
    const node = this.or();
    if (this.peek().kind !== "end") throw new DtaFilterError("invalid syntax");
    return node;
  }

  private or(): Node {
    let left = this.and();
    while (this.takeOp("|")) {
      const a = wantNum(left);
      const b = wantNum(this.and());
      left = num((r, n) => bool(a(r, n) !== 0 || b(r, n) !== 0));
    }
    return left;
  }

  private and(): Node {
    let left = this.relational();
    while (this.takeOp("&")) {
      const a = wantNum(left);
      const b = wantNum(this.relational());
      left = num((r, n) => bool(a(r, n) !== 0 && b(r, n) !== 0));
    }
    return left;
  }

  private relational(): Node {
    let left = this.additive();
    for (;;) {
      const op = this.takeOp("==", "!=", "~=", ">=", "<=", ">", "<", "=");
      if (!op) return left;
      const right = this.additive();
      if (left.type !== right.type) throw new DtaFilterError("type mismatch");
      left = compare(op, left, right);
    }
  }

  private additive(): Node {
    let left = this.multiplicative();
    for (;;) {
      const op = this.takeOp("+", "-");
      if (!op) return left;
      const right = this.multiplicative();
      if (op === "+" && left.type === "str") {
        const a = left.fn;
        const b = wantStr(right);
        left = str((r, n) => a(r, n) + b(r, n));
        continue;
      }
      const a = wantNum(left);
      const b = wantNum(right);
      left = binaryArith(a, b, op === "+" ? (x, y) => x + y : (x, y) => x - y);
    }
  }

  private multiplicative(): Node {
    let left = this.negation();
    for (;;) {
      const op = this.takeOp("*", "/");
      if (!op) return left;
      const a = wantNum(left);
      const b = wantNum(this.negation());
      left = binaryArith(a, b, op === "*" ? (x, y) => x * y : (x, y) => x / y);
    }
  }

  private negation(): Node {
    if (this.takeOp("-")) {
      const a = wantNum(this.negation());
      return num((r, n) => {
        const x = a(r, n);
        return isMissingKey(x) ? SYSMISS : -x;
      });
    }
    if (this.takeOp("+")) return this.negation();
    return this.power();
  }

  private power(): Node {
    let left = this.not();
    while (this.takeOp("^")) {
      const a = wantNum(left);
      const b = wantNum(this.isOp("-", "+") ? this.negation() : this.not());
      left = binaryArith(a, b, (x, y) => x ** y);
    }
    return left;
  }

  private not(): Node {
    if (this.takeOp("!", "~")) {
      const a = wantNum(this.not());
      return num((r, n) => bool(a(r, n) === 0));
    }
    return this.primary();
  }

  private primary(): Node {
    const t = this.peek();
    if (t.kind === "num") {
      this.pos += 1;
      const value = t.value;
      return num(() => value);
    }
    if (t.kind === "str") {
      this.pos += 1;
      const text = t.value;
      if (this.takeOp(":")) return this.labelledValue(text);
      return str(() => text);
    }
    if (t.kind === "op" && t.value === "(") {
      this.pos += 1;
      const inner = this.or();
      this.expectOp(")");
      return inner;
    }
    if (t.kind === "name") {
      this.pos += 1;
      if (this.takeOp("(")) return this.call(t.value);
      return this.variable(t.value);
    }
    if (t.kind === "tsop") {
      this.pos += 1;
      const operand = this.peek();
      if (operand.kind !== "name") throw new DtaFilterError("invalid syntax");
      this.pos += 1;
      return this.lagged(t.value, operand.value);
    }
    throw new DtaFilterError("invalid syntax");
  }

  /** `L2.sales`: the operand's value at another time, read once prepared. */
  private lagged(operator: string, name: string): Node {
    if (!this.meta.tsset) throw new DtaFilterError("time variable not set");
    const index = this.resolve(name);
    const kind = this.meta.variables[index].kind;
    if (kind === "str" || kind === "strL") throw new DtaFilterError("type mismatch");
    if (kind === "alias") throw new DtaFilterError(`${name} is an alias variable and holds no data`);
    const entry: Series = { column: index, terms: [...lagPolynomial(operator)] };
    this.series.push(entry);
    return num((_r, n) => (entry.values as Float64Array)[n - 1]);
  }

  /**
   * A variable by name or, as Stata allows, by an abbreviation that only
   * one variable starts with. An exact name always wins.
   */
  private resolve(name: string): number {
    const exact = this.byName.get(name);
    if (exact !== undefined) return exact;
    const matches = this.meta.variables.filter((v) => v.name.startsWith(name));
    if (matches.length === 1) return matches[0].index;
    if (matches.length > 1) throw new DtaFilterError(`${name} ambiguous abbreviation`);
    throw new DtaFilterError(`${name} not found`);
  }

  /** `"South":regionlbl` — the number that value label maps to that text. */
  private labelledValue(text: string): Node {
    const t = this.peek();
    if (t.kind !== "name") throw new DtaFilterError("invalid syntax");
    this.pos += 1;
    const table = this.meta.valueLabels.get(t.value);
    if (!table) throw new DtaFilterError(`value label ${t.value} not found`);
    let value = SYSMISS;
    for (const [key, label] of table) {
      if (label === text) {
        // Extended missing values are stored above the largest long.
        value = key >= 2147483621 ? MISSING_BASE + (key - 2147483621) * MISSING_STEP : key;
        break;
      }
    }
    return num(() => value);
  }

  private call(name: string): Node {
    const args: Node[] = [];
    if (!this.takeOp(")")) {
      do {
        args.push(this.or());
      } while (this.takeOp(","));
      this.expectOp(")");
    }
    const builder = Object.prototype.hasOwnProperty.call(FUNCTIONS, name)
      ? FUNCTIONS[name]
      : undefined;
    if (!builder) throw new DtaFilterError(`unknown function ${name}()`);
    return builder(args);
  }

  private variable(name: string): Node {
    if (name === "_n") return num((_r, n) => n);
    if (name === "_N") {
      const total = this.meta.nObs;
      return num(() => total);
    }
    const index = this.resolve(name);
    let slot = this.slots.get(index);
    if (slot === undefined) {
      slot = this.columns.length;
      this.columns.push(index);
      this.slots.set(index, slot);
    }
    const at = slot;
    const kind = this.meta.variables[index].kind;
    if (kind === "str" || kind === "strL") return str((r) => r[at] as string);
    if (kind === "alias") throw new DtaFilterError(`${name} is an alias variable and holds no data`);
    return num((r) => numericKey(r[at]));
  }
}

function binaryArith(a: NumFn, b: NumFn, f: (x: number, y: number) => number): Node {
  return num((r, n) => {
    const x = a(r, n);
    const y = b(r, n);
    return isMissingKey(x) || isMissingKey(y) ? SYSMISS : arith(f(x, y));
  });
}

function compare(op: string, left: Node, right: Node): Node {
  // Both sides have the same type; JS relational operators give Stata's
  // ordering for the missing-value encoding and for strings alike.
  const a = left.fn as (r: Row, n: number) => number | string;
  const b = right.fn as (r: Row, n: number) => number | string;
  switch (op) {
    case "==":
    case "=":
      return num((r, n) => bool(a(r, n) === b(r, n)));
    case "!=":
    case "~=":
      return num((r, n) => bool(a(r, n) !== b(r, n)));
    case ">":
      return num((r, n) => bool(a(r, n) > b(r, n)));
    case ">=":
      return num((r, n) => bool(a(r, n) >= b(r, n)));
    case "<":
      return num((r, n) => bool(a(r, n) < b(r, n)));
    default:
      return num((r, n) => bool(a(r, n) <= b(r, n)));
  }
}

/**
 * Compile a Stata `if` expression against a dataset's variables. Throws
 * {@link DtaFilterError} with a Stata-style message when the expression is
 * malformed, names an unknown variable, or mixes strings and numbers.
 */
export function compileFilter(expression: string, meta: DtaMeta): CompiledFilter {
  // Accept a pasted `if age > 60` as well as the bare expression.
  const source = expression.replace(/^\s*if\b/, "");
  if (source.trim() === "") throw new DtaFilterError("empty expression");
  const parser = new Parser(tokenize(source), meta);
  const root = parser.parse();
  const fn = wantNum(root);
  const { series } = parser;
  return {
    columns: parser.columns,
    test: (cells, obs) => fn(cells, obs) !== 0,
    ...(series.length > 0
      ? {
          prepare: (reader: DtaReader, onChunk?: () => void) => prepareSeries(reader, series, onChunk),
          // time, panel, one array per operand and per result, and the index
          prepareBytesPerRow: 8 * (2 + new Set(series.map((x) => x.column)).size + series.length) + 4,
        }
      : {}),
  };
}
