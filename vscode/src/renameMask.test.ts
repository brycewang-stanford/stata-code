import { strict as assert } from "node:assert";
import { describe, test } from "node:test";

import { computeRenameSkipMask, isCommentPrefix } from "./renameMask";

/** Convenience: assert that ``[s, e)`` is in the mask's ranges. */
function assertSkipped(
  ranges: Array<[number, number]>,
  start: number,
  end: number,
): void {
  const found = ranges.find(([s, e]) => s === start && e === end);
  assert.ok(
    found,
    `expected range [${start}, ${end}) in ${JSON.stringify(ranges)}`,
  );
}

/** Convenience: assert that the half-open ``[start, end)`` overlaps no skip range. */
function assertNotSkipped(
  ranges: Array<[number, number]>,
  start: number,
  end: number,
): void {
  const overlap = ranges.find(([s, e]) => start < e && end > s);
  assert.equal(
    overlap,
    undefined,
    `range [${start}, ${end}) should NOT overlap any skip; got ${JSON.stringify(overlap)}`,
  );
}

describe("isCommentPrefix", () => {
  test("recognizes Stata line comments and slash-slash", () => {
    assert.equal(isCommentPrefix("* a comment"), true);
    assert.equal(isCommentPrefix("  *! header"), true);
    assert.equal(isCommentPrefix("// inline-style comment"), true);
    assert.equal(isCommentPrefix("\t// indented"), true);
  });

  test("does not match commands that begin with similar chars", () => {
    assert.equal(isCommentPrefix("display 1"), false);
    assert.equal(isCommentPrefix("gen x = 1"), false);
    assert.equal(isCommentPrefix(""), false);
  });
});

describe("computeRenameSkipMask — basic states", () => {
  test("plain command leaves identifiers untouched", () => {
    const r = computeRenameSkipMask("regress mpg weight", false);
    assert.deepEqual(r.ranges, []);
    assert.equal(r.skipWholeLine, false);
    assert.equal(r.blockOpen, false);
  });

  test("``*``-prefixed line marks the whole line as a comment", () => {
    const text = "* this references mpg but should not be renamed";
    const r = computeRenameSkipMask(text, false);
    assert.equal(r.skipWholeLine, true);
    assert.equal(r.blockOpen, false);
    assertSkipped(r.ranges, 0, text.length);
  });

  test("``//`` mid-line skips from there to end of line", () => {
    //                       0123456789012345678901234
    const text = "list mpg // also mentions mpg";
    const r = computeRenameSkipMask(text, false);
    // The "mpg" at index 5 is reachable; the trailing "mpg" lives in the
    // comment span [9, len).
    assertNotSkipped(r.ranges, 5, 8);
    assertSkipped(r.ranges, 9, text.length);
  });
});

describe("computeRenameSkipMask — strings", () => {
  test("``\"…\"`` skip range covers content and quotes", () => {
    const text = 'display "the value of mpg is unknown"';
    const r = computeRenameSkipMask(text, false);
    const stringStart = text.indexOf('"');
    const stringEnd = text.lastIndexOf('"') + 1;
    assertSkipped(r.ranges, stringStart, stringEnd);
    // The "mpg" inside the literal must be inside the skip span.
    const mpgIndex = text.indexOf("mpg");
    assertNotSkipped(r.ranges, 0, stringStart); // pre-string is open
    assert.ok(r.ranges.some(([s, e]) => mpgIndex >= s && mpgIndex < e));
  });

  test("unterminated string skips to end of line, blockOpen stays false", () => {
    const text = 'display "broken here mpg';
    const r = computeRenameSkipMask(text, false);
    const start = text.indexOf('"');
    assertSkipped(r.ranges, start, text.length);
    assert.equal(r.blockOpen, false);
  });
});

describe("computeRenameSkipMask — block comments across lines", () => {
  test("single-line ``/* … */`` skips the marked span only", () => {
    //                       0123456789012345678901234567890
    const text = "regress y x /* hide mpg */ // tail";
    const out = computeRenameSkipMask(text, false);
    const blockStart = text.indexOf("/*");
    const blockEnd = text.indexOf("*/") + 2;
    const slashSlash = text.indexOf("//");
    assertSkipped(out.ranges, blockStart, blockEnd);
    assertSkipped(out.ranges, slashSlash, text.length);
    assert.equal(out.blockOpen, false);
  });

  test("``/*`` at end of line opens block, threaded across lines", () => {
    const line1 = "regress mpg weight /* explanation";
    const r1 = computeRenameSkipMask(line1, false);
    assert.equal(r1.blockOpen, true);
    assertSkipped(r1.ranges, line1.indexOf("/*"), line1.length);

    const line2 = "still inside the block";
    const r2 = computeRenameSkipMask(line2, r1.blockOpen);
    assert.equal(r2.blockOpen, true);
    assertSkipped(r2.ranges, 0, line2.length);

    const line3 = "still inside */ now after mpg";
    const r3 = computeRenameSkipMask(line3, r2.blockOpen);
    assert.equal(r3.blockOpen, false);
    const closeAt = line3.indexOf("*/") + 2;
    assertSkipped(r3.ranges, 0, closeAt);
    assertNotSkipped(r3.ranges, line3.indexOf("mpg"), line3.indexOf("mpg") + 3);
  });
});

describe("computeRenameSkipMask — Stata macro references", () => {
  test("simple ``\\`name'`` skips the whole token", () => {
    const text = "display `mpg' done";
    const r = computeRenameSkipMask(text, false);
    const start = text.indexOf("`");
    const end = text.indexOf("'") + 1;
    assertSkipped(r.ranges, start, end);
  });

  test("nested ``\\`outer \\`inner' '`` skips outer span as a whole", () => {
    const text = "display `outer `inner' ' tail";
    const r = computeRenameSkipMask(text, false);
    const start = text.indexOf("`");
    // Closing apostrophe is the SECOND ' in the line, after "inner' ".
    const firstClose = text.indexOf("'");
    const secondClose = text.indexOf("'", firstClose + 1);
    assert.ok(secondClose > firstClose, "expected two ' chars");
    // The skip range must span the whole nested macro.
    assertSkipped(r.ranges, start, secondClose + 1);
    // "tail" after the macro is reachable.
    assertNotSkipped(r.ranges, text.indexOf("tail"), text.length);
  });

  test("unterminated backtick skips to end of line", () => {
    const text = "display `forever_open foo";
    const r = computeRenameSkipMask(text, false);
    const start = text.indexOf("`");
    assertSkipped(r.ranges, start, text.length);
  });

  test("apostrophe inside a string inside a macro doesn't close the macro early", () => {
    // `` `= "it's"' `` — without string-tracking the inner `'` would be
    // treated as the macro close, leaving the trailing `'` and the rest of
    // the line dangling. With proper tracking the whole macro span is one
    // skip range and the trailing identifier is reachable.
    const text = "display `= \"it's\"' tail";
    const r = computeRenameSkipMask(text, false);
    const start = text.indexOf("`");
    const end = text.lastIndexOf("'") + 1;
    assertSkipped(r.ranges, start, end);
    const tailStart = text.indexOf("tail");
    assertNotSkipped(r.ranges, tailStart, tailStart + 4);
  });
});

describe("computeRenameSkipMask — heading and block-close edge cases", () => {
  test("``**#`` section heading is a Stata line comment", () => {
    // Section headings are still ``*``-prefixed lines from Stata's POV, so
    // identifiers inside the heading must not be renamed.
    const text = "**# Build mpg model";
    const r = computeRenameSkipMask(text, false);
    assert.equal(r.skipWholeLine, true);
    assertSkipped(r.ranges, 0, text.length);
  });

  test("``*/`` at the start of a line closes a previous block cleanly", () => {
    // After `/*` on the previous line, this line begins inside the block.
    // The `*/` at column 0 closes it; the rest of the line is reachable.
    const text = "*/ regress mpg";
    const r = computeRenameSkipMask(text, true);
    assert.equal(r.blockOpen, false);
    assertSkipped(r.ranges, 0, 2);
    const mpgIdx = text.indexOf("mpg");
    assertNotSkipped(r.ranges, mpgIdx, mpgIdx + 3);
  });

  test("``*`` at first column when a block is open is NOT a line comment", () => {
    // Inside an open `/* … */` block, a leading `*` is just block content
    // (or part of a `*/` close), NOT a Stata `*`-line comment.
    const line = "* still inside the block";
    const r = computeRenameSkipMask(line, true);
    assert.equal(r.skipWholeLine, false);
    assert.equal(r.blockOpen, true);
    assertSkipped(r.ranges, 0, line.length);
  });
});

describe("computeRenameSkipMask — interactions", () => {
  test("string opens before macro: macro inside string is just text", () => {
    // A ``"`name'"`` would normally trigger the string scan, which closes at
    // the next ``"``. The macro markers inside are simply part of the string.
    const text = 'display "hello `mpg\' world" mpg';
    const r = computeRenameSkipMask(text, false);
    const stringStart = text.indexOf('"');
    const stringEnd = text.lastIndexOf('"') + 1;
    assertSkipped(r.ranges, stringStart, stringEnd);
    // The trailing bare "mpg" outside the string is reachable.
    const tailMpg = text.lastIndexOf("mpg");
    assertNotSkipped(r.ranges, tailMpg, tailMpg + 3);
  });

  test("``//`` inside string is part of the string, not a comment", () => {
    const text = 'display "see // mpg here" mpg';
    const r = computeRenameSkipMask(text, false);
    const stringStart = text.indexOf('"');
    const stringEnd = text.lastIndexOf('"') + 1;
    assertSkipped(r.ranges, stringStart, stringEnd);
    // Trailing mpg is NOT inside the comment that // would otherwise create.
    const tailMpg = text.lastIndexOf("mpg");
    assertNotSkipped(r.ranges, tailMpg, tailMpg + 3);
  });
});
