// Whole-column work for the dta viewer: which rows a filter keeps, in what
// order a sort puts them, and summary statistics for one variable.
//
// Each operation is one sequential pass over the file that decodes only the
// columns it needs. Results follow Stata's conventions so the viewer agrees
// with `count if`, `sort`, `summarize, detail` and `tabulate` (the tests pin
// them to Stata 18's output on test-fixtures/dta/survey118.dta).
//
// Kept free of any `vscode` import so it runs under `node --test`.

import { compileFilter, isMissingKey, missingKeyIndex, numericKey } from "./dtaFilter";
import {
  DtaCell,
  DtaReader,
  DtaVariable,
  VALUE_LABEL_MISSING_BASE,
  missingCode,
} from "./dtaReader";

/**
 * Row positions are kept as 32-bit integers, so this is the most
 * observations a filter, sort or summary can address.
 */
export const MAX_QUERY_ROWS = 0xffffffff;
/**
 * What the whole-column operations may hold in memory at once. A filter
 * keeps 4 bytes per matching row and a sort 12 more plus 8 per numeric key,
 * so the default covers a sort of about 80 million rows on one key and a
 * filter of far more. A summary past the limit drops its percentiles
 * instead of failing; see {@link summarizeColumn}.
 */
export const DEFAULT_QUERY_MEMORY_BYTES = 2 * 1024 ** 3;

export interface QueryLimits {
  memoryBytes: number;
}
/** Distinct values tracked exactly; past this the count is reported as a floor. */
export const MAX_DISTINCT_TRACKED = 100_000;
const FREQUENCY_ROWS = 12;

export interface SortKey {
  column: number;
  descending: boolean;
}

export interface RowQuery {
  /** A Stata `if` expression, or "" for no filter. */
  filter: string;
  sort: SortKey[];
}

export class DtaQueryError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DtaQueryError";
  }
}

/** Thrown by a pass whose `isCancelled` callback reported true. */
export class DtaQueryCancelled extends Error {
  constructor() {
    super("cancelled");
    this.name = "DtaQueryCancelled";
  }
}

function isString(v: DtaVariable): boolean {
  return v.kind === "str" || v.kind === "strL";
}

function assertQueryable(reader: DtaReader): void {
  if (reader.meta.nObs > MAX_QUERY_ROWS) {
    throw new DtaQueryError(
      `Filtering, sorting and summaries are limited to ${MAX_QUERY_ROWS.toLocaleString("en-US")} ` +
        `observations; this file has ${reader.meta.nObs.toLocaleString("en-US")}.`,
    );
  }
}

function megabytes(bytes: number): string {
  return `${Math.ceil(bytes / 1024 ** 2).toLocaleString("en-US")} MB`;
}

function overBudget(what: string, needed: number, limits: QueryLimits): DtaQueryError {
  return new DtaQueryError(
    `${what} needs about ${megabytes(needed)} of memory and the viewer's limit is ` +
      `${megabytes(limits.memoryBytes)}. Narrow the filter, or raise the setting ` +
      `stataCode.dtaViewerMemoryMb.`,
  );
}

/**
 * Row order for a view: the zero-based observation indices that pass
 * `query.filter`, arranged by `query.sort`. Returns `null` for the identity
 * (no filter, no sort), so the common case costs nothing.
 *
 * Sorting is by the underlying values, not by value-label text, with missing
 * values after every number in both directions, the same as Stata's `sort`
 * and `gsort`. Ties keep their original order.
 */
export async function buildRowOrder(
  reader: DtaReader,
  query: RowQuery,
  isCancelled: () => boolean = () => false,
  limits: QueryLimits = { memoryBytes: DEFAULT_QUERY_MEMORY_BYTES },
): Promise<Uint32Array | null> {
  const { meta } = reader;
  const expression = query.filter.trim();
  if (expression === "" && query.sort.length === 0) return null;
  assertQueryable(reader);

  const filter = expression === "" ? undefined : compileFilter(expression, meta);
  // Time-series operators read other observations: one pass to fetch them.
  if (filter?.prepare) {
    const needed = (filter.prepareBytesPerRow ?? 0) * meta.nObs;
    if (needed > limits.memoryBytes) throw overBudget("This time-series filter", needed, limits);
    await filter.prepare(reader, () => {
      if (isCancelled()) throw new DtaQueryCancelled();
    });
  }
  for (const key of query.sort) {
    const v = meta.variables[key.column];
    if (!v) throw new DtaQueryError(`sort: variable index ${key.column} is out of range`);
    if (v.kind === "alias") throw new DtaQueryError(`${v.name} is an alias variable and cannot be sorted`);
  }

  // One scan reads the filter's columns followed by the sort keys' columns.
  const filterColumns = filter ? filter.columns : [];
  const columns = [...filterColumns, ...query.sort.map((k) => k.column)];
  const sortStrings = query.sort.map((k) => isString(meta.variables[k.column]));

  // Per kept row: its position, each numeric key, and for a sort the two
  // index arrays built below. String keys are counted as they arrive.
  const numericSortKeys = sortStrings.filter((isText) => !isText).length;
  const bytesPerRow = 4 + 8 * numericSortKeys + (query.sort.length > 0 ? 8 : 0);
  const affordable = Math.max(16, Math.floor(limits.memoryBytes / (2 * bytesPerRow)));
  let kept = new Uint32Array(Math.min(meta.nObs, 1 << 16, affordable));
  let count = 0;
  const numericKeys: Array<Float64Array | undefined> = query.sort.map((_, i) =>
    sortStrings[i] ? undefined : new Float64Array(kept.length),
  );
  const stringKeys: Array<string[] | undefined> = query.sort.map((_, i) =>
    sortStrings[i] ? [] : undefined,
  );

  let stringBytes = 0;
  const what = query.sort.length > 0 ? "Sorting these rows" : "This filter";
  const grow = (): void => {
    const size = Math.min(meta.nObs, kept.length * 2);
    // while growing, the old arrays and the new ones exist side by side
    const needed = (size + kept.length) * bytesPerRow + stringBytes;
    if (needed > limits.memoryBytes) throw overBudget(what, needed, limits);
    const next = new Uint32Array(size);
    next.set(kept);
    kept = next;
    for (let i = 0; i < numericKeys.length; i++) {
      const old = numericKeys[i];
      if (!old) continue;
      const bigger = new Float64Array(size);
      bigger.set(old);
      numericKeys[i] = bigger;
    }
  };

  const filterWidth = filterColumns.length;
  if (columns.length === 0) {
    // A filter that reads no variables (`_n <= 10`): no file access needed.
    const none: DtaCell[] = [];
    for (let obs = 0; obs < meta.nObs; obs++) {
      if (filter && !filter.test(none, obs + 1)) continue;
      if (count === kept.length) grow();
      kept[count++] = obs;
    }
  } else {
    await reader.scan(columns, (rows, start) => {
      if (isCancelled()) throw new DtaQueryCancelled();
      for (let r = 0; r < rows.length; r++) {
        const row = rows[r];
        if (filter && !filter.test(row, start + r + 1)) continue;
        if (count === kept.length) grow();
        kept[count] = start + r;
        for (let k = 0; k < query.sort.length; k++) {
          const cell = row[filterWidth + k];
          const numeric = numericKeys[k];
          if (numeric) numeric[count] = numericKey(cell);
          else {
            (stringKeys[k] as string[]).push(cell as string);
            // two bytes a character plus the engine's per-string overhead
            stringBytes += 2 * (cell as string).length + 24;
          }
        }
        count += 1;
      }
      if (stringBytes > limits.memoryBytes) {
        throw overBudget(what, kept.length * bytesPerRow + stringBytes, limits);
      }
    });
  }

  if (query.sort.length === 0) return kept.slice(0, count);

  const positions = new Uint32Array(count);
  for (let i = 0; i < count; i++) positions[i] = i;
  const comparators = query.sort.map((key, k) => {
    const sign = key.descending ? -1 : 1;
    const numeric = numericKeys[k];
    if (numeric && key.descending) {
      // gsort -x: largest first, but missing values still after every
      // number (Stata's default; `mfirst` is the exception), .z before '.'.
      return (a: number, b: number): number => {
        const x = numeric[a];
        const y = numeric[b];
        const xMissing = isMissingKey(x);
        if (xMissing !== isMissingKey(y)) return xMissing ? 1 : -1;
        return y - x;
      };
    }
    if (numeric) return (a: number, b: number): number => numeric[a] - numeric[b];
    const strings = stringKeys[k] as string[];
    // Code-unit order, as Stata sorts strings: uppercase before lowercase.
    return (a: number, b: number): number =>
      strings[a] === strings[b] ? 0 : sign * (strings[a] < strings[b] ? -1 : 1);
  });
  positions.sort((a, b) => {
    for (const compare of comparators) {
      const c = compare(a, b);
      if (c !== 0) return c;
    }
    return a - b; // stable: ties stay in dataset order
  });
  const order = new Uint32Array(count);
  for (let i = 0; i < count; i++) order[i] = kept[positions[i]];
  return order;
}

export interface FrequencyRow {
  /** The value as the viewer prints it with value labels off. */
  value: string;
  /** Value-label text for `value`, when the variable has one. */
  label: string;
  count: number;
}

export interface ColumnSummary {
  name: string;
  numeric: boolean;
  /** Observations summarized (after the view's filter). */
  rows: number;
  /** Nonmissing observations (for strings: non-empty). */
  n: number;
  missing: number;
  /** Distinct nonmissing values; a lower bound when `distinctCapped`. */
  distinct: number;
  distinctCapped: boolean;
  mean?: number;
  sd?: number;
  min?: number;
  max?: number;
  p25?: number;
  p50?: number;
  p75?: number;
  /**
   * True when the nonmissing values did not fit in the memory limit: the
   * percentiles are then left out, and the rest is still exact.
   */
  percentilesOmitted?: boolean;
  /** Most frequent values, missing included; absent for high-cardinality variables. */
  frequencies?: FrequencyRow[];
  /** Number of distinct values beyond those listed in `frequencies`. */
  frequenciesOmitted?: number;
}

/** Stata's `summarize, detail` percentile on an ascending array. */
function percentile(sorted: Float64Array, p: number): number {
  const n = sorted.length;
  const position = (n * p) / 100;
  if (Number.isInteger(position)) {
    const lower = sorted[Math.max(0, position - 1)];
    const upper = sorted[Math.min(n - 1, position)];
    return (lower + upper) / 2;
  }
  return sorted[Math.min(n - 1, Math.ceil(position) - 1)];
}

/**
 * Summary statistics for one variable over the rows in `order` (or the whole
 * dataset when `order` is null).
 */
export async function summarizeColumn(
  reader: DtaReader,
  column: number,
  order: Uint32Array | null,
  isCancelled: () => boolean = () => false,
  limits: QueryLimits = { memoryBytes: DEFAULT_QUERY_MEMORY_BYTES },
): Promise<ColumnSummary> {
  const { meta } = reader;
  const variable = meta.variables[column];
  if (!variable) throw new DtaQueryError(`variable index ${column} is out of range`);
  assertQueryable(reader);

  let include: Uint8Array | undefined;
  if (order) {
    include = new Uint8Array(meta.nObs);
    for (let i = 0; i < order.length; i++) include[order[i]] = 1;
  }
  const rows = order ? order.length : meta.nObs;
  const numeric = !isString(variable) && variable.kind !== "alias";
  const table = variable.valueLabel ? meta.valueLabels.get(variable.valueLabel) : undefined;

  const counts = new Map<number | string, number>();
  let countsCapped = false;
  let missing = 0;
  let n = 0;
  const affordable = Math.max(16, Math.floor(limits.memoryBytes / 16));
  let values = new Float64Array(numeric ? Math.min(rows, 1 << 16, affordable) : 0);
  // Past the memory limit the values are no longer kept; the moments below
  // are accumulated as the rows go by, so only the percentiles are lost.
  let keepValues = true;
  let mean = 0;
  let spread = 0; // sum of squared deviations from the running mean
  let lowest = Infinity;
  let highest = -Infinity;

  await reader.scan([column], (chunk, start) => {
    if (isCancelled()) throw new DtaQueryCancelled();
    for (let r = 0; r < chunk.length; r++) {
      if (include && include[start + r] === 0) continue;
      const cell = chunk[r][0];
      let key: number | string;
      if (numeric) {
        key = numericKey(cell);
        if (isMissingKey(key)) missing += 1;
        else {
          if (keepValues && n === values.length) {
            const size = Math.max(16, Math.min(rows, values.length * 2));
            if (8 * (size + values.length) > limits.memoryBytes) {
              keepValues = false;
              values = new Float64Array(0);
            } else {
              const bigger = new Float64Array(size);
              bigger.set(values);
              values = bigger;
            }
          }
          if (keepValues) values[n] = key;
          n += 1;
          const delta = key - mean;
          mean += delta / n;
          spread += delta * (key - mean);
          if (key < lowest) lowest = key;
          if (key > highest) highest = key;
        }
      } else {
        key = String(cell);
        if (key === "") missing += 1;
        else n += 1;
      }
      if (!countsCapped) {
        counts.set(key, (counts.get(key) ?? 0) + 1);
        if (counts.size > MAX_DISTINCT_TRACKED) countsCapped = true;
      }
    }
  });

  let distinct = 0;
  for (const key of counts.keys()) {
    const isMissing = typeof key === "number" ? isMissingKey(key) : key === "";
    if (!isMissing) distinct += 1;
  }

  const summary: ColumnSummary = {
    name: variable.name,
    numeric,
    rows,
    n,
    missing,
    distinct,
    distinctCapped: countsCapped,
  };

  if (numeric && n > 0 && keepValues) {
    const sorted = values.subarray(0, n).sort();
    let sum = 0;
    for (let i = 0; i < n; i++) sum += sorted[i];
    const exactMean = sum / n;
    let squares = 0;
    for (let i = 0; i < n; i++) squares += (sorted[i] - exactMean) ** 2;
    summary.mean = exactMean;
    summary.sd = n > 1 ? Math.sqrt(squares / (n - 1)) : undefined;
    summary.min = sorted[0];
    summary.max = sorted[n - 1];
    summary.p25 = percentile(sorted, 25);
    summary.p50 = percentile(sorted, 50);
    summary.p75 = percentile(sorted, 75);
  } else if (numeric && n > 0) {
    summary.mean = mean;
    summary.sd = n > 1 ? Math.sqrt(spread / (n - 1)) : undefined;
    summary.min = lowest;
    summary.max = highest;
    summary.percentilesOmitted = true;
  }

  // A frequency table is worth showing for categorical-looking variables:
  // strings, value-labelled variables, and numerics with few distinct values.
  const categorical = !numeric || table !== undefined || counts.size <= 20;
  if (!countsCapped && categorical && counts.size > 0) {
    const entries = [...counts.entries()].sort((a, b) => b[1] - a[1] || compareKeys(a[0], b[0]));
    summary.frequencies = entries.slice(0, FREQUENCY_ROWS).map(([key, count]) => {
      if (typeof key === "string") return { value: key, label: "", count };
      const missingAt = isMissingKey(key) ? missingKeyIndex(key) : -1;
      const value = missingAt >= 0 ? missingCode(missingAt) : String(key);
      const labelKey = missingAt >= 0 ? VALUE_LABEL_MISSING_BASE + missingAt : key;
      return { value, label: table?.get(labelKey) ?? "", count };
    });
    summary.frequenciesOmitted = Math.max(0, entries.length - FREQUENCY_ROWS);
  }
  return summary;
}

function compareKeys(a: number | string, b: number | string): number {
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a) < String(b) ? -1 : String(a) > String(b) ? 1 : 0;
}
