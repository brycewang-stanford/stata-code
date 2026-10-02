// Webview side of the Stata .dta viewer: a virtual grid plus a variables panel.
//
// The extension host owns the file and all value formatting. This script only
// asks for blocks of already-formatted cells ("rows" messages) and draws the
// part of the dataset that is on screen, so a file with millions of
// observations costs the same as a small one.
(function () {
  "use strict";

  const vscode = acquireVsCodeApi();

  const ROW_H = 24;
  /** Rows per fetched block. */
  const BLOCK = 100;
  /** Browsers cap element heights; past this the scrollbar maps proportionally. */
  const MAX_SCROLL_PX = 8000000;
  const MAX_CACHED_BLOCKS = 400;
  const MAX_LISTED_VARS = 2000;
  const CELL_PAD = 17; // horizontal padding + border of a cell, in px
  const MISSING = /^\.[a-z]?$/;

  const $ = (id) => document.getElementById(id);
  const el = {
    summary: $("summary"),
    labels: $("labels"),
    goto: $("goto"),
    toggleSide: $("toggle-side"),
    warnings: $("warnings"),
    main: $("main"),
    head: $("head"),
    gutter: $("gutter"),
    scroller: $("scroller"),
    sizer: $("sizer"),
    cells: $("cells"),
    empty: $("empty"),
    side: $("side"),
    filter: $("filter"),
    varlist: $("varlist"),
    detail: $("detail"),
    where: $("where"),
    value: $("value"),
    fatal: $("fatal"),
  };

  const saved = vscode.getState() || {};
  let init = null;
  let useLabels = saved.useLabels !== false;
  let colLeft = [];
  let colWidth = [];
  let totalWidth = 0;
  let colChunk = 1;
  /** Per value-label name: the set of label texts, to tell labels from numbers. */
  let labelTexts = {};
  let sel = { row: -1, col: -1 };
  /** Bumped on every (re)load so replies to an older file are dropped. */
  let generation = 0;
  let nextRequestId = 1;
  const cache = new Map();
  const pending = new Map(); // request id → cache key
  const inFlight = new Set(); // cache keys
  let renderQueued = false;

  el.labels.checked = useLabels;
  if (saved.sideHidden) el.side.hidden = true;
  el.toggleSide.setAttribute("aria-pressed", String(!el.side.hidden));

  function esc(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function group(n) {
    return Number(n).toLocaleString("en-US");
  }

  function saveState() {
    vscode.setState({ useLabels, sideHidden: el.side.hidden });
  }

  function measureCharWidth() {
    const probe = document.createElement("span");
    probe.style.cssText =
      "position:absolute;visibility:hidden;white-space:pre;font-family:var(--grid-font);" +
      "font-size:var(--vscode-editor-font-size,12px)";
    probe.textContent = "0".repeat(40);
    document.body.appendChild(probe);
    const width = probe.getBoundingClientRect().width / 40;
    probe.remove();
    return width > 0 ? width : 7.2;
  }

  // ── load ────────────────────────────────────────────────────────────────

  function load(message) {
    init = message;
    generation += 1;
    cache.clear();
    pending.clear();
    inFlight.clear();

    const charWidth = measureCharWidth();
    colLeft = [];
    colWidth = [];
    let x = 0;
    for (const v of init.variables) {
      const w = Math.ceil(v.width * charWidth) + CELL_PAD;
      colLeft.push(x);
      colWidth.push(w);
      x += w;
    }
    totalWidth = x;
    // Row numbers print with thousands separators; size the gutter to the last one.
    const gutter = Math.max(44, Math.ceil(group(init.nObs).length * charWidth) + 18);
    document.documentElement.style.setProperty("--gutter-w", gutter + "px");
    colChunk = init.nVars <= 64 ? Math.max(1, init.nVars) : 32;

    labelTexts = {};
    for (const name of Object.keys(init.valueLabels)) {
      labelTexts[name] = new Set(init.valueLabels[name].map((entry) => entry[1]));
    }

    if (sel.row >= init.nObs) sel.row = -1;
    if (sel.col >= init.nVars) sel.col = -1;

    el.fatal.hidden = true;
    el.main.hidden = false;
    el.goto.max = String(init.nObs);
    el.goto.disabled = init.nObs === 0;
    el.labels.disabled = Object.keys(init.valueLabels).length === 0;

    renderSummary();
    renderWarnings();
    renderVarList();
    renderDetail();
    layout();
    render();
    updateStatus();
  }

  function renderSummary() {
    const parts = [
      '<span class="name">' + esc(init.title) + "</span>",
      group(init.nObs) + " obs × " + group(init.nVars) + " vars",
    ];
    if (init.subtitle) parts.push(esc(init.subtitle));
    if (init.dataLabel) parts.push("“" + esc(init.dataLabel) + "”");
    parts.push('<span class="dim">format ' + esc(init.release) + "</span>");
    el.summary.innerHTML = parts.join(' <span class="dim">·</span> ');
    el.summary.title = el.summary.textContent;
  }

  function renderWarnings() {
    el.warnings.innerHTML = init.warnings.map((w) => "<div>" + esc(w) + "</div>").join("");
  }

  // ── grid ────────────────────────────────────────────────────────────────

  function totalRowsPx() {
    return init.nObs * ROW_H;
  }

  function layout() {
    const height = Math.min(totalRowsPx(), MAX_SCROLL_PX);
    el.sizer.style.width = Math.max(1, totalWidth) + "px";
    el.sizer.style.height = Math.max(1, height) + "px";
    const blank = init.nObs === 0 || init.nVars === 0;
    el.empty.style.display = blank ? "flex" : "none";
    el.empty.textContent =
      init.nVars === 0 ? "This dataset has no variables." : "This dataset has no observations.";
  }

  /** First visible row and the pixel offset of its top edge above the viewport. */
  function rowWindow() {
    const viewH = el.scroller.clientHeight;
    const total = totalRowsPx();
    const scrollTop = el.scroller.scrollTop;
    if (total <= MAX_SCROLL_PX) {
      const first = Math.floor(scrollTop / ROW_H);
      return { first, offset: scrollTop - first * ROW_H, viewH };
    }
    // Proportional mapping: the scrollbar spans the whole dataset even though
    // the scrollable element is far shorter than nObs * ROW_H. Rows snap to
    // whole-row positions; at the very end the last row is bottom-aligned.
    const maxScroll = Math.max(1, MAX_SCROLL_PX - viewH);
    const fraction = Math.min(1, Math.max(0, scrollTop / maxScroll));
    const maxFirst = Math.max(0, init.nObs - Math.floor(viewH / ROW_H));
    const first = Math.round(fraction * maxFirst);
    const offset = first === maxFirst ? Math.max(0, (init.nObs - first) * ROW_H - viewH) : 0;
    return { first, offset, viewH };
  }

  function firstVisibleColumn(scrollLeft) {
    let lo = 0;
    let hi = colLeft.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (colLeft[mid] + colWidth[mid] > scrollLeft) hi = mid;
      else lo = mid + 1;
    }
    return lo;
  }

  function cacheKey(block, chunk) {
    return (useLabels ? "L" : "C") + ":" + block + ":" + chunk;
  }

  function cellText(row, col) {
    const block = Math.floor(row / BLOCK);
    const chunk = Math.floor(col / colChunk);
    const key = cacheKey(block, chunk);
    const entry = cache.get(key);
    if (!entry) {
      request(key, block, chunk);
      return undefined;
    }
    const r = entry[row - block * BLOCK];
    return r ? r[col - chunk * colChunk] : undefined;
  }

  function request(key, block, chunk) {
    if (inFlight.has(key)) return;
    inFlight.add(key);
    const id = nextRequestId++;
    pending.set(id, { key, generation });
    vscode.postMessage({
      type: "rows",
      id,
      start: block * BLOCK,
      count: BLOCK,
      firstColumn: chunk * colChunk,
      endColumn: Math.min(init.nVars, (chunk + 1) * colChunk),
      useLabels,
    });
  }

  function receive(message) {
    const req = pending.get(message.id);
    pending.delete(message.id);
    if (!req || req.generation !== generation) return;
    inFlight.delete(req.key);
    cache.set(req.key, message.rows);
    if (cache.size > MAX_CACHED_BLOCKS) {
      // Map iterates in insertion order: drop the oldest blocks.
      const excess = cache.size - MAX_CACHED_BLOCKS;
      let dropped = 0;
      for (const key of cache.keys()) {
        if (dropped++ >= excess) break;
        cache.delete(key);
      }
    }
    queueRender();
    updateStatus();
  }

  function queueRender() {
    if (renderQueued) return;
    renderQueued = true;
    requestAnimationFrame(() => {
      renderQueued = false;
      render();
    });
  }

  function render() {
    if (!init) return;
    const viewW = el.scroller.clientWidth;
    const { first, offset, viewH } = rowWindow();
    const scrollLeft = el.scroller.scrollLeft;
    el.cells.style.width = viewW + "px";
    el.cells.style.height = viewH + "px";

    if (init.nVars === 0) {
      el.head.innerHTML = "";
      el.gutter.innerHTML = "";
      el.cells.innerHTML = "";
      return;
    }

    const c0 = firstVisibleColumn(scrollLeft);
    let c1 = c0;
    while (c1 < init.nVars && colLeft[c1] < scrollLeft + viewW) c1 += 1;
    const r1 = Math.min(init.nObs, first + Math.ceil((viewH + offset) / ROW_H));

    let head = "";
    for (let c = c0; c < c1; c++) {
      const v = init.variables[c];
      const tip = [v.name, v.type + " " + v.format, v.label].filter(Boolean).join("\n");
      head +=
        '<div class="h' +
        (v.numeric ? " num" : "") +
        (c === sel.col ? " sel" : "") +
        '" data-c="' +
        c +
        '" style="left:' +
        (colLeft[c] - scrollLeft) +
        "px;width:" +
        colWidth[c] +
        'px" title="' +
        esc(tip) +
        '"><span class="n">' +
        esc(v.name) +
        '</span><span class="l">' +
        (v.label ? esc(v.label) : "&nbsp;") +
        "</span></div>";
    }
    el.head.innerHTML = head;

    let gutter = "";
    let cells = "";
    for (let r = first; r < r1; r++) {
      const top = (r - first) * ROW_H - offset;
      gutter += '<div class="g" style="top:' + top + 'px">' + group(r + 1) + "</div>";
      for (let c = c0; c < c1; c++) {
        const v = init.variables[c];
        const text = cellText(r, c);
        let cls = "c";
        if (v.numeric) cls += " num";
        if (text === undefined) cls += " wait";
        else if (v.numeric && MISSING.test(text)) cls += " miss";
        else if (useLabels && v.valueLabel && labelTexts[v.valueLabel]?.has(text)) cls += " lab";
        if (r === sel.row && c === sel.col) cls += " sel";
        else if (c === sel.col) cls += " colsel";
        cells +=
          '<div class="' +
          cls +
          '" data-r="' +
          r +
          '" data-c="' +
          c +
          '" style="top:' +
          top +
          "px;left:" +
          (colLeft[c] - scrollLeft) +
          "px;width:" +
          colWidth[c] +
          'px">' +
          (text === undefined ? "" : esc(text)) +
          "</div>";
      }
    }
    el.gutter.innerHTML = gutter;
    el.cells.innerHTML = cells;
  }

  function ensureVisible(row, col) {
    const s = el.scroller;
    if (col >= 0 && col < colLeft.length) {
      const left = colLeft[col];
      const right = left + colWidth[col];
      if (left < s.scrollLeft) s.scrollLeft = left;
      else if (right > s.scrollLeft + s.clientWidth) s.scrollLeft = right - s.clientWidth;
    }
    if (row >= 0) {
      const total = totalRowsPx();
      if (total <= MAX_SCROLL_PX) {
        const top = row * ROW_H;
        if (top < s.scrollTop) s.scrollTop = top;
        else if (top + ROW_H > s.scrollTop + s.clientHeight) {
          s.scrollTop = top + ROW_H - s.clientHeight;
        }
      } else {
        const { first, viewH } = rowWindow();
        const visible = Math.max(1, Math.floor(viewH / ROW_H));
        if (row < first || row >= first + visible) scrollRowToTop(row);
      }
    }
  }

  function scrollRowToTop(row) {
    const s = el.scroller;
    const total = totalRowsPx();
    if (total <= MAX_SCROLL_PX) {
      s.scrollTop = row * ROW_H;
      return;
    }
    const viewH = s.clientHeight;
    const maxScroll = Math.max(1, MAX_SCROLL_PX - viewH);
    const maxFirst = Math.max(1, init.nObs - Math.floor(viewH / ROW_H));
    s.scrollTop = Math.ceil((Math.min(row, maxFirst) / maxFirst) * maxScroll);
  }

  function select(row, col, reveal) {
    const colChanged = col !== sel.col;
    sel = { row, col };
    if (reveal) ensureVisible(row, col);
    if (colChanged) {
      markSelectedVariable();
      renderDetail();
    }
    render();
    updateStatus();
  }

  function updateStatus() {
    if (!init || sel.col < 0) {
      el.where.textContent = "";
      el.value.textContent = "";
      return;
    }
    const v = init.variables[sel.col];
    if (sel.row < 0) {
      el.where.textContent = v.name;
      el.value.textContent = v.label;
      return;
    }
    el.where.textContent = v.name + "[" + group(sel.row + 1) + "]";
    const text = cellText(sel.row, sel.col);
    el.value.textContent = text === undefined ? "" : text;
    el.value.title = text === undefined ? "" : text;
  }

  // ── variables panel ─────────────────────────────────────────────────────

  function renderVarList() {
    const needle = el.filter.value.trim().toLowerCase();
    let html = "";
    let shown = 0;
    let matched = 0;
    for (let c = 0; c < init.variables.length; c++) {
      const v = init.variables[c];
      if (
        needle &&
        !v.name.toLowerCase().includes(needle) &&
        !v.label.toLowerCase().includes(needle)
      ) {
        continue;
      }
      matched += 1;
      if (shown >= MAX_LISTED_VARS) continue;
      shown += 1;
      html +=
        '<div class="v' +
        (c === sel.col ? " sel" : "") +
        '" data-c="' +
        c +
        '"><span class="n">' +
        esc(v.name) +
        '</span><span class="t">' +
        esc(v.type) +
        (v.valueLabel ? " · " + esc(v.valueLabel) : "") +
        "</span>" +
        (v.label ? '<span class="l">' + esc(v.label) + "</span>" : "") +
        "</div>";
    }
    if (matched > shown) {
      html +=
        '<div class="v"><span class="l">' +
        group(matched - shown) +
        " more — type in the filter to narrow the list</span></div>";
    }
    if (matched === 0) html = '<div class="v"><span class="l">No matching variables</span></div>';
    el.varlist.innerHTML = html;
  }

  function markSelectedVariable() {
    for (const node of el.varlist.querySelectorAll(".v.sel")) node.classList.remove("sel");
    if (sel.col < 0) return;
    const node = el.varlist.querySelector('.v[data-c="' + sel.col + '"]');
    if (node) {
      node.classList.add("sel");
      node.scrollIntoView({ block: "nearest" });
    }
  }

  function row(term, text) {
    return text ? "<dt>" + esc(term) + "</dt><dd>" + esc(text) + "</dd>" : "";
  }

  function renderDetail() {
    if (sel.col < 0) {
      let html = "<h3>" + esc(init.title) + "</h3><dl>";
      html += row("Label", init.dataLabel);
      html += row("Saved", init.timestamp);
      html += row("Sorted by", init.sortedBy.join(" "));
      html += row("Format", ".dta " + init.release);
      html += "</dl>";
      if (init.notes.length) {
        html += "<h4>Notes</h4>" + init.notes.map((n) => "<p>" + esc(n) + "</p>").join("");
      }
      el.detail.innerHTML = html;
      return;
    }
    const v = init.variables[sel.col];
    let html = "<h3>" + esc(v.name) + "</h3><dl>";
    html += row("Label", v.label);
    html += row("Type", v.type);
    html += row("Format", v.format);
    html += row("Value label", v.valueLabel);
    html += "</dl>";
    if (v.notes.length) {
      html += "<h4>Notes</h4>" + v.notes.map((n) => "<p>" + esc(n) + "</p>").join("");
    }
    const entries = v.valueLabel ? init.valueLabels[v.valueLabel] : undefined;
    if (entries) {
      html += "<h4>Values</h4><table>";
      for (const entry of entries) {
        html += "<tr><td>" + esc(entry[0]) + "</td><td>" + esc(entry[1]) + "</td></tr>";
      }
      html += "</table>";
      if (init.valueLabelsTruncated.includes(v.valueLabel)) {
        html += "<p>(list cut at " + group(entries.length) + " entries)</p>";
      }
    } else if (v.valueLabel) {
      html += "<h4>Values</h4><p>The file does not define this value label.</p>";
    }
    el.detail.innerHTML = html;
  }

  // ── events ──────────────────────────────────────────────────────────────

  el.scroller.addEventListener("scroll", queueRender, { passive: true });
  new ResizeObserver(queueRender).observe(el.scroller);

  el.cells.addEventListener("mousedown", (event) => {
    const cell = event.target.closest(".c");
    if (!cell) return;
    select(Number(cell.dataset.r), Number(cell.dataset.c), false);
  });

  el.head.addEventListener("click", (event) => {
    const header = event.target.closest(".h");
    if (!header) return;
    select(-1, Number(header.dataset.c), false);
  });

  // The header strip is not itself scrollable; pass horizontal wheel through.
  el.head.addEventListener(
    "wheel",
    (event) => {
      el.scroller.scrollLeft += event.deltaX || event.deltaY;
    },
    { passive: true },
  );
  el.gutter.addEventListener(
    "wheel",
    (event) => {
      el.scroller.scrollTop += event.deltaY;
    },
    { passive: true },
  );

  el.varlist.addEventListener("click", (event) => {
    const item = event.target.closest(".v[data-c]");
    if (!item) return;
    const col = Number(item.dataset.c);
    select(sel.row, col, false);
    ensureVisible(-1, col);
  });

  el.filter.addEventListener("input", renderVarList);

  el.labels.addEventListener("change", () => {
    useLabels = el.labels.checked;
    saveState();
    render();
    updateStatus();
  });

  el.toggleSide.addEventListener("click", () => {
    el.side.hidden = !el.side.hidden;
    el.toggleSide.setAttribute("aria-pressed", String(!el.side.hidden));
    saveState();
    queueRender();
  });

  function goToRow() {
    if (!init || init.nObs === 0) return;
    const wanted = Math.floor(Number(el.goto.value));
    if (!Number.isFinite(wanted) || wanted < 1) return;
    const target = Math.min(init.nObs, wanted) - 1;
    scrollRowToTop(target);
    select(target, sel.col >= 0 ? sel.col : 0, false);
    el.scroller.focus();
  }
  el.goto.addEventListener("change", goToRow);
  el.goto.addEventListener("keydown", (event) => {
    if (event.key === "Enter") goToRow();
  });

  el.scroller.addEventListener("keydown", (event) => {
    if (!init || init.nObs === 0 || init.nVars === 0) return;
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "c") {
      if (sel.row >= 0 && sel.col >= 0) {
        const text = cellText(sel.row, sel.col);
        if (text !== undefined) vscode.postMessage({ type: "copy", text });
        event.preventDefault();
      }
      return;
    }
    const page = Math.max(1, Math.floor(el.scroller.clientHeight / ROW_H) - 1);
    let r = Math.max(0, sel.row);
    let c = Math.max(0, sel.col);
    switch (event.key) {
      case "ArrowDown":
        r += 1;
        break;
      case "ArrowUp":
        r -= 1;
        break;
      case "ArrowRight":
        c += 1;
        break;
      case "ArrowLeft":
        c -= 1;
        break;
      case "PageDown":
        r += page;
        break;
      case "PageUp":
        r -= page;
        break;
      case "Home":
        if (event.metaKey || event.ctrlKey) r = 0;
        c = 0;
        break;
      case "End":
        if (event.metaKey || event.ctrlKey) r = init.nObs - 1;
        c = init.nVars - 1;
        break;
      default:
        return;
    }
    event.preventDefault();
    select(
      Math.min(init.nObs - 1, Math.max(0, r)),
      Math.min(init.nVars - 1, Math.max(0, c)),
      true,
    );
  });

  window.addEventListener("message", (event) => {
    const message = event.data;
    if (!message || typeof message.type !== "string") return;
    if (message.type === "init") load(message);
    else if (message.type === "rows") receive(message);
    else if (message.type === "error") {
      el.main.hidden = true;
      el.fatal.hidden = false;
      el.fatal.innerHTML =
        "<h2>" + esc(message.title || "Cannot open this file") + "</h2><p>" + esc(message.message) + "</p>";
    }
  });

  vscode.postMessage({ type: "ready" });
})();
