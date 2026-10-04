import { strict as assert } from "node:assert";
import * as fs from "node:fs";
import { after, before, describe, test } from "node:test";

import { BufferByteSource, DtaReader } from "./dtaReader";
import { Browser, findChrome, openViewer, type LiveViewer } from "./webviewHarness";

// The viewer as a user meets it: the real page in a real browser, talking to
// the real session. Each expectation that is a number is Stata 18's own, the
// same ones dtaFilter.test.ts and dtaQuery.test.ts pin the engine to; here
// they have to survive the trip through the page.
//
// Run with `npm run test:webview`. Needs Chrome or Chromium (set CHROME_PATH
// to point at one); without it every test is skipped.

const chrome = findChrome();
const skip = chrome ? false : "no Chrome or Chromium found (set CHROME_PATH)";

/** Page helpers, written as source text that runs in the page. */
const typeFilter = (expression: string): string => `(() => {
  const box = document.getElementById("filter-expr");
  box.value = ${JSON.stringify(expression)};
  box.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  return true;
})()`;
const cell = (row: number, column: number): string =>
  `(document.querySelector('.c[data-r="${row}"][data-c="${column}"]') || {}).textContent`;
const text = (id: string): string => `document.getElementById(${JSON.stringify(id)}).textContent`;
const click = (selector: string): string => `(() => {
  const target = document.querySelector(${JSON.stringify(selector)});
  if (!target) throw new Error("nothing matches " + ${JSON.stringify(selector)});
  target.click();
  return true;
})()`;

async function columnOf(viewer: LiveViewer, name: string): Promise<number> {
  const init = [...viewer.posted].reverse().find((m) => m.type === "init") as unknown as {
    variables: Array<{ name: string }>;
  };
  const index = init.variables.findIndex((v) => v.name === name);
  assert.ok(index >= 0, name);
  return index;
}

async function rendered(viewer: LiveViewer): Promise<void> {
  await viewer.waitFor(`document.querySelectorAll(".c:not(.wait)").length > 0`, "cells with data");
}

describe("the .dta viewer in a browser", { skip }, () => {
  let browser: Browser;
  const open: LiveViewer[] = [];
  const view = async (fixture: string): Promise<LiveViewer> => {
    const viewer = await openViewer(browser, fixture);
    open.push(viewer);
    await rendered(viewer);
    return viewer;
  };

  before(async () => {
    browser = await Browser.launch(chrome as string);
  });
  after(async () => {
    for (const viewer of open) await viewer.close();
    await browser?.close();
  });

  test("opens a file: header line, variables, first rows", async () => {
    const viewer = await view("survey118.dta");
    const summary = await viewer.evaluate<string>(text("summary"));
    assert.match(summary, /survey118\.dta/);
    assert.match(summary, /500 obs × 9 vars/);
    assert.match(summary, /Synthetic survey/);
    assert.equal(await viewer.evaluate<number>(`document.querySelectorAll("#varlist .v[data-c]").length`), 9);
    assert.equal(await viewer.evaluate<string>(cell(0, await columnOf(viewer, "id"))), "1");
    // no script error reached the page's fatal banner
    assert.equal(await viewer.evaluate<boolean>(`document.getElementById("fatal").hidden`), true);
  });

  test("a filter shows Stata's count, and a bad one shows Stata's message", async () => {
    const viewer = await view("survey118.dta");
    await viewer.evaluate(typeFilter("age > 60"));
    await viewer.waitFor(`${text("query-status")} === "158 of 500 obs"`, "158 of 500 obs");
    await viewer.evaluate(typeFilter('region == "South":regionlbl'));
    await viewer.waitFor(`${text("query-status")} === "142 of 500 obs"`, "142 of 500 obs");

    await viewer.evaluate(typeFilter("city > 5"));
    await viewer.waitFor(`${text("query-status")} === "type mismatch"`, "type mismatch");
    // the view that was showing stays in place
    assert.equal(await viewer.evaluate<string>(`document.getElementById("query-status").className`), "bad");

    await viewer.evaluate(click("#filter-clear"));
    await viewer.waitFor(`${text("query-status")} === ""`, "the filter cleared");
  });

  test("a time-series filter and an abbreviation work from the filter box", async () => {
    const viewer = await view("panel118.dta");
    await viewer.evaluate(typeFilter("L.sales > 100"));
    await viewer.waitFor(`${text("query-status")}.startsWith("123 of ")`, "123 rows for L.sales > 100");
    await viewer.evaluate(typeFilter("D.sales < 0 & gro == 1"));
    await viewer.waitFor(`/^\\d+ of /.test(${text("query-status")})`, "a count for D.sales with gro");
    await viewer.evaluate(typeFilter("sal > 100"));
    await viewer.waitFor(
      `${text("query-status")} === "sal ambiguous abbreviation"`,
      "the ambiguous abbreviation message",
    );
  });

  test("clicking a column's sort button sorts as Stata does, both ways", async () => {
    const viewer = await view("survey118.dta");
    const age = await columnOf(viewer, "age");
    const id = await columnOf(viewer, "id");

    await viewer.evaluate(click(`#head [data-sort="${age}"]`));
    await viewer.waitFor(`${text("sort-chips")}.includes("age ↑")`, "the ascending chip");
    // sort age (stable): the youngest first
    await viewer.waitFor(`${cell(0, age)} === "18"`, "age 18 in the first row");

    await viewer.evaluate(click(`#head [data-sort="${age}"]`));
    await viewer.waitFor(`${text("sort-chips")}.includes("age ↓")`, "the descending chip");
    // gsort -age: 77 first (id 68), missing values still at the end
    await viewer.waitFor(`${cell(0, age)} === "77"`, "age 77 in the first row");
    assert.equal(await viewer.evaluate<string>(cell(0, id)), "68");

    await viewer.evaluate(click("#sort-clear"));
    await viewer.waitFor(`${text("sort-chips")} === ""`, "the sort cleared");
    await viewer.waitFor(`${cell(0, id)} === "1"`, "dataset order again");
  });

  test("value labels switch between text and codes", async () => {
    const viewer = await view("survey118.dta");
    const female = await columnOf(viewer, "female");
    const shown = await viewer.evaluate<string>(cell(0, female));
    assert.ok(shown === "Yes" || shown === "No", shown);
    await viewer.evaluate(click("#labels"));
    await viewer.waitFor(`/^[01]$/.test(${cell(0, female)})`, "the numeric code");
  });

  test("selecting a variable shows its summary, as summarize does", async () => {
    const viewer = await view("survey118.dta");
    const age = await columnOf(viewer, "age");
    await viewer.evaluate(click(`#varlist .v[data-c="${age}"]`));
    const detail = await viewer.waitFor<string>(
      `document.getElementById("detail").innerText.includes("Median") && document.getElementById("detail").innerText`,
      "the summary in the details panel",
    );
    // summarize age: 483 nonmissing of 500, range 18 to 77
    assert.match(detail, /Obs\s+483/);
    assert.match(detail, /Missing\s+17/);
    assert.match(detail, /Min\s+18/);
    assert.match(detail, /Max\s+77/);
  });

  test("editing a variable label in the details panel changes the file", async () => {
    const viewer = await view("survey118.dta");
    const age = await columnOf(viewer, "age");
    await viewer.evaluate(click(`#varlist .v[data-c="${age}"]`));
    await viewer.waitFor(`!!document.getElementById("label-edit")`, "the Edit button");
    await viewer.evaluate(click("#label-edit"));
    await viewer.waitFor(`!!document.getElementById("label-input")`, "the label input");
    await viewer.evaluate(`(() => {
      const input = document.getElementById("label-input");
      input.value = "Age at interview";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      return true;
    })()`);
    await viewer.waitFor(
      `document.getElementById("detail").innerText.includes("Age at interview") && !document.getElementById("label-input")`,
      "the new label in the details panel",
    );
    const reader = await DtaReader.open(new BufferByteSource(new Uint8Array(fs.readFileSync(viewer.file))));
    assert.equal(reader.meta.variables[age].label, "Age at interview");
    // and the notice offers to undo it
    await viewer.waitFor(`!!document.getElementById("undo-edit")`, "the Undo button");
    await viewer.evaluate(click("#undo-edit"));
    await viewer.waitFor(
      `document.getElementById("detail").innerText.includes("Age in years")`,
      "the original label after Undo",
    );
    const undone = await DtaReader.open(new BufferByteSource(new Uint8Array(fs.readFileSync(viewer.file))));
    assert.equal(undone.meta.variables[age].label, "Age in years");
  });

  test("scrolling far down renders the rows that are there", async () => {
    const viewer = await view("survey118.dta");
    const id = await columnOf(viewer, "id");
    await viewer.evaluate(`(() => {
      const scroller = document.getElementById("scroller");
      scroller.scrollTop = scroller.scrollHeight;
      return true;
    })()`);
    await viewer.waitFor(`${cell(499, id)} === "500"`, "the last observation");
  });
});
