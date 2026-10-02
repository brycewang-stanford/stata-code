import { strict as assert } from "node:assert";
import * as fs from "node:fs";
import * as path from "node:path";
import { describe, test } from "node:test";
import * as vm from "node:vm";

import { buildDtaViewerHtml } from "./dtaViewerHtml";

const MEDIA = path.join(__dirname, "..", "media");

const html = buildDtaViewerHtml({
  cspSource: "vscode-webview://abc",
  nonce: "NONCE123",
  scriptUri: "vscode-webview://abc/media/dtaViewer.js",
  styleUri: "vscode-webview://abc/media/dtaViewer.css",
});

describe("buildDtaViewerHtml", () => {
  test("locks the page down with a nonce-only script policy", () => {
    const csp = /Content-Security-Policy" content="([^"]+)"/.exec(html)?.[1] ?? "";
    assert.match(csp, /default-src 'none'/);
    assert.match(csp, /script-src 'nonce-NONCE123'/);
    assert.equal(/script-src[^;]*unsafe-inline/.test(csp), false);
    assert.equal(/script-src[^;]*unsafe-eval/.test(csp), false);
    assert.match(html, /<script nonce="NONCE123" src="vscode-webview:\/\/abc\/media\/dtaViewer\.js">/);
  });

  test("escapes attribute values", () => {
    const hostile = buildDtaViewerHtml({
      cspSource: "x",
      nonce: "n",
      scriptUri: 'a"><script>alert(1)</script>',
      styleUri: "s",
    });
    assert.equal(hostile.includes('<script>alert(1)'), false);
  });
});

describe("media/dtaViewer.js", () => {
  const script = fs.readFileSync(path.join(MEDIA, "dtaViewer.js"), "utf8");

  test("parses as JavaScript", () => {
    assert.doesNotThrow(() => new vm.Script(script, { filename: "dtaViewer.js" }));
  });

  test("every element it looks up exists in the HTML shell", () => {
    const ids = [...script.matchAll(/\$\("([\w-]+)"\)/g)].map((m) => m[1]);
    assert.ok(ids.length > 10);
    for (const id of ids) {
      assert.ok(html.includes(`id="${id}"`), `#${id} is missing from the HTML shell`);
    }
  });

  test("never builds markup from unescaped data", () => {
    // Guard against a regression to innerHTML with raw strings: every
    // interpolated value in a markup concatenation goes through esc()/group().
    assert.equal(/innerHTML\s*=\s*[a-zA-Z_.]+\.(label|name|text|title)\b/.test(script), false);
  });

  test("the stylesheet exists", () => {
    assert.ok(fs.statSync(path.join(MEDIA, "dtaViewer.css")).size > 0);
  });
});
