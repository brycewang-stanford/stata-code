/**
 * Pure-string utilities for the rename provider — no ``vscode`` imports so
 * the module is unit-testable from plain Node.
 */

export interface RenameSkipMask {
  /** Char ranges (half-open ``[start, end)``) inside which a match must be ignored. */
  ranges: Array<[number, number]>;
  /** ``true`` when the entire line is a Stata line comment (``*`` / ``//`` prefix). */
  skipWholeLine: boolean;
  /** ``true`` when a ``/* … *\/`` block was opened on this line and not yet closed. */
  blockOpen: boolean;
}

export function isCommentPrefix(text: string): boolean {
  const trimmed = text.trimStart();
  return trimmed.startsWith("*") || trimmed.startsWith("//");
}

/**
 * Identify the spans on ``text`` that a rename must leave alone — string
 * literals, line/block comments, and macro references like `` `name' `` (which
 * are local-macro lookups, not variable usages).
 *
 * ``blockOpenIn`` is the running "inside ``/* … *\/``" state from the previous
 * line. The returned ``blockOpen`` should be threaded into the next call.
 */
export function computeRenameSkipMask(
  text: string,
  blockOpenIn: boolean,
): RenameSkipMask {
  const ranges: Array<[number, number]> = [];

  // Stata's ``*`` / ``//`` line comments only count when they begin the line
  // (after leading whitespace) AND we aren't already inside a /* … */ block.
  if (!blockOpenIn && isCommentPrefix(text)) {
    return { ranges: [[0, text.length]], skipWholeLine: true, blockOpen: false };
  }

  let inBlock = blockOpenIn;
  let inString = false;
  let segmentStart = inBlock ? 0 : -1;
  let i = 0;

  while (i < text.length) {
    const ch = text[i];
    const next = text[i + 1];

    if (inBlock) {
      if (ch === "*" && next === "/") {
        ranges.push([segmentStart, i + 2]);
        inBlock = false;
        segmentStart = -1;
        i += 2;
      } else {
        i += 1;
      }
      continue;
    }

    if (inString) {
      // Stata strings have no backslash escapes — a literal ``"`` inside a
      // string requires compound double-quotes (`` `"…"' ``) instead, which
      // we handle through the backtick branch below. So the next ``"`` we
      // see always closes the string.
      if (ch === '"') {
        ranges.push([segmentStart, i + 1]);
        inString = false;
        segmentStart = -1;
      }
      i += 1;
      continue;
    }

    if (ch === "/" && next === "*") {
      segmentStart = i;
      inBlock = true;
      i += 2;
      continue;
    }
    if (ch === "/" && next === "/") {
      ranges.push([i, text.length]);
      return { ranges, skipWholeLine: false, blockOpen: false };
    }
    if (ch === '"') {
      segmentStart = i;
      inString = true;
      i += 1;
      continue;
    }
    if (ch === "`") {
      // Stata macro reference: `` `name' ``, `` `=expr' ``, or nested
      // `` `outer `inner' ' ``. Treat ``` ` ``` and ``'`` like balanced
      // brackets and skip the whole span. If the brackets stay open to
      // end-of-line, skip to EOL conservatively (Stata macros rarely span
      // lines, but we'd rather over-skip than corrupt code).
      let depth = 1;
      let j = i + 1;
      while (j < text.length && depth > 0) {
        const c = text[j];
        if (c === "`") depth += 1;
        else if (c === "'") depth -= 1;
        j += 1;
      }
      if (depth !== 0) {
        ranges.push([i, text.length]);
        return { ranges, skipWholeLine: false, blockOpen: inBlock };
      }
      ranges.push([i, j]);
      i = j;
      continue;
    }

    i += 1;
  }

  if (inBlock && segmentStart !== -1) {
    ranges.push([segmentStart, text.length]);
  } else if (inString && segmentStart !== -1) {
    // Unterminated string — Stata wouldn't accept this anyway, but fail safe
    // by treating the rest of the line as untouchable.
    ranges.push([segmentStart, text.length]);
  }

  return { ranges, skipWholeLine: false, blockOpen: inBlock };
}
