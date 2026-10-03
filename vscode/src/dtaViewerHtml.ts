// HTML shell for the Stata .dta viewer webview.
//
// The grid logic and styles live in media/dtaViewer.{js,css} and are loaded as
// webview resources; this only wires them up under a strict CSP. Kept free of
// any `vscode` import so it can be unit-tested under `node --test`.

export interface DtaViewerHtmlOptions {
  /** `webview.cspSource`. */
  cspSource: string;
  /** Per-render nonce for the one script tag. */
  nonce: string;
  scriptUri: string;
  styleUri: string;
}

function attr(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

export function buildDtaViewerHtml(options: DtaViewerHtmlOptions): string {
  const csp = [
    "default-src 'none'",
    // Cells are absolutely positioned through inline style attributes.
    `style-src ${options.cspSource} 'unsafe-inline'`,
    `script-src 'nonce-${options.nonce}'`,
    `font-src ${options.cspSource}`,
  ].join("; ");

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="${attr(csp)}" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <link rel="stylesheet" href="${attr(options.styleUri)}" />
  <title>Stata data</title>
</head>
<body>
  <div id="bar">
    <div id="summary">Loading…</div>
    <div id="tools">
      <label title="Show value labels in place of the numeric codes they stand for">
        <input type="checkbox" id="labels" checked /> Value labels
      </label>
      <input type="number" id="goto" min="1" placeholder="Go to row" aria-label="Go to row" />
      <div id="menu-wrap">
        <button id="menu-button" type="button" aria-haspopup="true" aria-expanded="false">Export ▾</button>
        <div id="menu" role="menu" hidden>
          <button id="export-csv" type="button" role="menuitem">Export view as CSV…</button>
          <button id="export-codebook" type="button" role="menuitem">Export codebook (labels, formats, notes)…</button>
          <button id="load-stata" type="button" role="menuitem">Load in Stata (use, clear)</button>
        </div>
      </div>
      <button id="toggle-side" type="button" aria-pressed="true">Variables</button>
    </div>
  </div>
  <div id="querybar">
    <span class="kw">if</span>
    <input type="text" id="filter-expr" spellcheck="false" autocomplete="off"
      placeholder='Filter rows with a Stata expression, e.g.  age &gt; 60 &amp; !missing(income)   —   Enter to apply'
      aria-label="Filter rows with a Stata if expression" />
    <button id="filter-clear" type="button" title="Clear filter" aria-label="Clear filter" hidden>×</button>
    <span id="query-status" role="status"></span>
    <span id="sort-chips"></span>
  </div>
  <div id="warnings"></div>
  <div id="fatal" hidden></div>
  <div id="main">
    <div id="grid">
      <div id="corner"></div>
      <div id="head"></div>
      <div id="gutter"></div>
      <div id="scroller" tabindex="0" aria-label="Data">
        <div id="sizer"><div id="cells"></div></div>
        <div id="empty"></div>
      </div>
    </div>
    <aside id="side">
      <div id="side-head">
        <input type="search" id="filter" placeholder="Filter variables" aria-label="Filter variables" />
      </div>
      <div id="varlist"></div>
      <div id="detail"></div>
    </aside>
  </div>
  <div id="status">
    <span id="where" class="where"></span><span id="value" class="value"></span>
    <span id="notice" role="status"></span>
  </div>
  <script nonce="${attr(options.nonce)}" src="${attr(options.scriptUri)}"></script>
</body>
</html>`;
}
