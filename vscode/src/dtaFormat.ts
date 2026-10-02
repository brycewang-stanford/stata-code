// Render numbers the way a Stata display format would print them.
//
// Implements the documented behaviour of `%w.d{f,e,g}[c]` numeric formats and
// the `%t*` date/time formats (StataCorp `help format`, `help datetime display
// formats`). The goal is a faithful data-browser cell, not a bit-exact port:
// values are not padded to the format width, and business calendars (`%tb`)
// and leap-second-adjusted clocks (`%tC`) fall back to their closest
// equivalent.
//
// Kept free of any `vscode` import so it runs under `node --test`.

const MS_PER_DAY = 86400000;
const EPOCH_MS = Date.UTC(1960, 0, 1);
const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];
const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

type DateUnit = "c" | "d" | "w" | "m" | "q" | "h" | "y";

const DEFAULT_DATE_BODY: Record<DateUnit, string> = {
  c: "DDmonCCYY_HH:MM:SS",
  d: "DDmonCCYY",
  w: "CCYY!www",
  m: "CCYY!mnn",
  q: "CCYY!qq",
  h: "CCYY!hh",
  y: "CCYY",
};

interface ParsedDateFormat {
  unit: DateUnit;
  body: string;
}

function parseDateFormat(fmt: string): ParsedDateFormat | undefined {
  // %td…, %tc…, and the pre-Stata-10 spelling %d… (≡ %td…).
  const m = /^%-?(?:t([cCdwmqhy])|d)(.*)$/.exec(fmt);
  if (!m) return undefined;
  const unit = (m[1] ? m[1].toLowerCase() : "d") as DateUnit;
  return { unit, body: m[2] || DEFAULT_DATE_BODY[unit] };
}

/** True when `fmt` is one of Stata's date/time display formats. */
export function isDateFormat(fmt: string): boolean {
  return parseDateFormat(fmt) !== undefined;
}

interface DateParts {
  date: Date; // UTC
  /** Milliseconds within the day; 0 for anything coarser than %tc. */
  msOfDay: number;
}

function toDateParts(value: number, unit: DateUnit): DateParts | undefined {
  let ms: number;
  let msOfDay = 0;
  switch (unit) {
    case "c": {
      const day = Math.floor(value / MS_PER_DAY);
      msOfDay = value - day * MS_PER_DAY;
      ms = EPOCH_MS + day * MS_PER_DAY;
      break;
    }
    case "d":
      ms = EPOCH_MS + Math.floor(value) * MS_PER_DAY;
      break;
    case "w": {
      const v = Math.floor(value);
      const year = 1960 + Math.floor(v / 52);
      ms = Date.UTC(year, 0, 1) + (v - (year - 1960) * 52) * 7 * MS_PER_DAY;
      break;
    }
    case "m": {
      const v = Math.floor(value);
      ms = Date.UTC(1960 + Math.floor(v / 12), ((v % 12) + 12) % 12, 1);
      break;
    }
    case "q": {
      const v = Math.floor(value);
      ms = Date.UTC(1960 + Math.floor(v / 4), (((v % 4) + 4) % 4) * 3, 1);
      break;
    }
    case "h": {
      const v = Math.floor(value);
      ms = Date.UTC(1960 + Math.floor(v / 2), (((v % 2) + 2) % 2) * 6, 1);
      break;
    }
    default:
      ms = Date.UTC(Math.floor(value), 0, 1);
  }
  const date = new Date(ms);
  if (Number.isNaN(date.getTime())) return undefined;
  const year = date.getUTCFullYear();
  // Stata's own date functions are defined for years 0100-9999.
  if (year < 100 || year > 9999) return undefined;
  return { date, msOfDay };
}

function pad(n: number, width: number): string {
  return String(n).padStart(width, "0");
}

function dayOfYear(date: Date): number {
  return Math.floor((date.getTime() - Date.UTC(date.getUTCFullYear(), 0, 1)) / MS_PER_DAY) + 1;
}

// Longest codes first so "Month" is not read as "Mon" + "th".
const DATE_CODES: Array<[string, (p: DateParts) => string]> = [
  ["Dayname", (p) => DAYS[p.date.getUTCDay()]],
  ["Month", (p) => MONTHS[p.date.getUTCMonth()]],
  ["month", (p) => MONTHS[p.date.getUTCMonth()].toLowerCase()],
  ["A.M.", (p) => (p.msOfDay < MS_PER_DAY / 2 ? "A.M." : "P.M.")],
  ["a.m.", (p) => (p.msOfDay < MS_PER_DAY / 2 ? "a.m." : "p.m.")],
  [".sss", (p) => `.${pad(Math.floor(p.msOfDay % 1000), 3)}`],
  ["JJJ", (p) => pad(dayOfYear(p.date), 3)],
  ["jjj", (p) => String(dayOfYear(p.date))],
  ["Mon", (p) => MONTHS[p.date.getUTCMonth()].slice(0, 3)],
  ["mon", (p) => MONTHS[p.date.getUTCMonth()].slice(0, 3).toLowerCase()],
  ["Day", (p) => DAYS[p.date.getUTCDay()].slice(0, 3)],
  ["day", (p) => DAYS[p.date.getUTCDay()].slice(0, 3).toLowerCase()],
  [".ss", (p) => `.${pad(Math.floor((p.msOfDay % 1000) / 10), 2)}`],
  ["CC", (p) => pad(Math.floor(p.date.getUTCFullYear() / 100), 2)],
  ["cc", (p) => String(Math.floor(p.date.getUTCFullYear() / 100))],
  ["YY", (p) => pad(p.date.getUTCFullYear() % 100, 2)],
  ["yy", (p) => String(p.date.getUTCFullYear() % 100)],
  ["NN", (p) => pad(p.date.getUTCMonth() + 1, 2)],
  ["nn", (p) => String(p.date.getUTCMonth() + 1)],
  ["DD", (p) => pad(p.date.getUTCDate(), 2)],
  ["dd", (p) => String(p.date.getUTCDate())],
  ["Da", (p) => DAYS[p.date.getUTCDay()].slice(0, 2)],
  ["da", (p) => DAYS[p.date.getUTCDay()].slice(0, 2).toLowerCase()],
  ["WW", (p) => pad(Math.min(52, Math.floor((dayOfYear(p.date) - 1) / 7) + 1), 2)],
  ["ww", (p) => String(Math.min(52, Math.floor((dayOfYear(p.date) - 1) / 7) + 1))],
  ["HH", (p) => pad(Math.floor(p.msOfDay / 3600000), 2)],
  ["Hh", (p) => pad(((Math.floor(p.msOfDay / 3600000) + 11) % 12) + 1, 2)],
  ["hH", (p) => String(Math.floor(p.msOfDay / 3600000))],
  ["hh", (p) => String(((Math.floor(p.msOfDay / 3600000) + 11) % 12) + 1)],
  ["MM", (p) => pad(Math.floor(p.msOfDay / 60000) % 60, 2)],
  ["mm", (p) => String(Math.floor(p.msOfDay / 60000) % 60)],
  ["SS", (p) => pad(Math.floor(p.msOfDay / 1000) % 60, 2)],
  ["ss", (p) => String(Math.floor(p.msOfDay / 1000) % 60)],
  [".s", (p) => `.${Math.floor((p.msOfDay % 1000) / 100)}`],
  ["AM", (p) => (p.msOfDay < MS_PER_DAY / 2 ? "AM" : "PM")],
  ["am", (p) => (p.msOfDay < MS_PER_DAY / 2 ? "am" : "pm")],
  ["h", (p) => String(Math.floor(p.date.getUTCMonth() / 6) + 1)],
  ["q", (p) => String(Math.floor(p.date.getUTCMonth() / 3) + 1)],
];

function renderDate(parts: DateParts, body: string): string {
  let out = "";
  let i = 0;
  while (i < body.length) {
    const ch = body[i];
    if (ch === "!" && i + 1 < body.length) {
      out += body[i + 1];
      i += 2;
      continue;
    }
    if (ch === "_") {
      out += " ";
      i += 1;
      continue;
    }
    const code = DATE_CODES.find(([token]) => body.startsWith(token, i));
    if (code) {
      out += code[1](parts);
      i += code[0].length;
      continue;
    }
    // "+", ".", ",", ":", "-", "/", "\" and anything unrecognized print as-is,
    // except "+", which Stata treats as "no separator".
    if (ch !== "+") out += ch;
    i += 1;
  }
  return out;
}

function withCommas(text: string): string {
  const m = /^(-?)(\d+)(.*)$/.exec(text);
  if (!m) return text;
  return m[1] + m[2].replace(/\B(?=(\d{3})+(?!\d))/g, ",") + m[3];
}

/** Stata prints two-digit exponents ("1.2e+05"); JS prints "1.2e+5". */
function stataExponent(text: string): string {
  return text.replace(/e([+-])(\d)$/, "e$10$2");
}

function plainDecimal(x: number, significant: number): string | undefined {
  const rounded = Number(x.toPrecision(significant));
  const text = String(rounded);
  if (text.includes("e")) return undefined;
  return text;
}

/**
 * Stata's `%w.dg`: the most significant digits that fit in `w - 1` columns
 * (one column is held back for the sign), trailing zeros trimmed, never
 * rounding away integer digits, and exponent notation when even the integer
 * part does not fit. `d > 0` caps the significant digits.
 */
function formatGeneral(value: number, width: number, commas: boolean, maxDigits: number): string {
  if (value === 0) return "0";
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  const fit = Math.max(1, width - 1);
  const integerDigits = abs >= 1 ? Math.floor(Math.log10(abs)) + 1 : 1;
  for (let digits = Math.max(maxDigits, integerDigits); digits >= integerDigits; digits--) {
    if (digits > 17) continue;
    let text = plainDecimal(abs, digits);
    if (text === undefined) break;
    // %g drops the leading zero: 0.25 prints as .25, -0.5 as -.5.
    text = text.replace(/^0\./, ".");
    if (commas) {
      const grouped = withCommas(text);
      if (grouped.length <= fit) return sign + grouped;
    }
    if (text.length <= fit) return sign + text;
    if (abs < 1 && digits === integerDigits) break;
  }
  if (abs < 1) {
    // Small magnitudes: fewer significant digits may still fit as a decimal.
    for (let digits = Math.min(maxDigits, fit); digits >= 1; digits--) {
      const text = plainDecimal(abs, digits)?.replace(/^0\./, ".");
      if (text === undefined) break;
      if (text.length <= fit && Number(text) !== 0) return sign + text;
    }
  }
  const exponentChars = abs >= 1e100 || abs < 1e-99 ? 5 : 4;
  const mantissaDigits = Math.max(0, Math.min(fit - exponentChars - 2, 16));
  return sign + stataExponent(abs.toExponential(mantissaDigits));
}

interface NumericFormat {
  width: number;
  decimals: number;
  style: "f" | "e" | "g";
  commas: boolean;
  /** European convention (`%9,2f`): comma as the decimal mark. */
  decimalComma: boolean;
}

function parseNumericFormat(fmt: string): NumericFormat | undefined {
  const m = /^%-?0?(\d+)([.,])(\d+)([efg])(c?)$/.exec(fmt);
  if (!m) return undefined;
  return {
    width: Number(m[1]),
    decimals: Number(m[3]),
    style: m[4] as "f" | "e" | "g",
    commas: m[5] === "c",
    decimalComma: m[2] === ",",
  };
}

function swapDecimalMark(text: string): string {
  return text.replace(/[.,]/g, (ch) => (ch === "." ? "," : "."));
}

/**
 * Format a non-missing numeric value with a Stata display format.
 *
 * `storage` is the variable's storage type; it bounds how many significant
 * digits a `%g` format may show (a float carries about 7, a double about 16).
 */
export function formatStataNumber(value: number, fmt: string, storage = "double"): string {
  if (!Number.isFinite(value)) return String(value);

  const date = parseDateFormat(fmt);
  if (date) {
    const parts = toDateParts(value, date.unit);
    // Outside the range Stata's calendar covers, Stata prints the raw number.
    return parts ? renderDate(parts, date.body) : formatGeneral(value, 9, false, 16);
  }

  const numeric = parseNumericFormat(fmt);
  if (!numeric) {
    // %21x, a string format on a numeric variable, or something unrecognized.
    return formatGeneral(value, 18, false, storage === "float" ? 8 : 16);
  }

  let text: string;
  if (numeric.style === "f") {
    text = value.toFixed(Math.min(numeric.decimals, 20));
    if (numeric.commas) text = withCommas(text);
  } else if (numeric.style === "e") {
    text = stataExponent(value.toExponential(Math.min(numeric.decimals, 20)));
  } else {
    const maxDigits =
      numeric.decimals > 0 ? numeric.decimals : storage === "float" ? 8 : 16;
    text = formatGeneral(value, numeric.width, numeric.commas, maxDigits);
  }
  return numeric.decimalComma ? swapDecimalMark(text) : text;
}

/** Characters a column needs to show values in `fmt` (used to size the grid). */
export function formatWidthHint(fmt: string, type: string): number {
  const date = parseDateFormat(fmt);
  if (date) {
    const sample: Record<DateUnit, number> = {
      c: 1900000000000,
      d: 22222,
      w: 3000,
      m: 730,
      q: 243,
      h: 121,
      y: 2020,
    };
    return formatStataNumber(sample[date.unit], fmt).length;
  }
  const str = /^%-?(\d+)s$/.exec(fmt);
  if (str) return Number(str[1]);
  const numeric = parseNumericFormat(fmt);
  if (numeric) return numeric.width;
  const m = /^str(\d+)$/.exec(type);
  return m ? Number(m[1]) : 9;
}
