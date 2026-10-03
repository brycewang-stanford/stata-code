// Webview side of the Stata .dta viewer: a virtual grid plus a variables panel.
//
// The extension host owns the file, the filter/sort row order, and all value
// formatting. This script asks for blocks of already-formatted cells ("rows"
// messages) for the part of the view that is on screen and draws them, so a
// file with millions of observations costs the same as a small one.
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
  const MAX_LABEL_CHARS = 80; // Stata's limit on a variable label
  const CELL_PAD = 17; // horizontal padding + border of a cell, in px
  const MIN_COL_W = 36;
  /** Above this many observations a summary is computed on request, not on select. */
  const AUTO_SUMMARY_ROWS = 500000;
  const MISSING = /^\.[a-z]?$/;

  const $ = (id) => document.getElementById(id);
  const el = {
    summary: $("summary"),
    labels: $("labels"),
    goto: $("goto"),
    toggleSide: $("toggle-side"),
    menuButton: $("menu-button"),
    menu: $("menu"),
    exportCsv: $("export-csv"),
    exportCodebook: $("export-codebook"),
    loadStata: $("load-stata"),
    filter: $("filter-expr"),
    filterClear: $("filter-clear"),
    queryStatus: $("query-status"),
    sortChips: $("sort-chips"),
    warnings: $("warnings"),
    main: $("main"),
    head: $("head"),
    gutter: $("gutter"),
    scroller: $("scroller"),
    sizer: $("sizer"),
    cells: $("cells"),
    empty: $("empty"),
    side: $("side"),
    varFilter: $("filter"),
    varlist: $("varlist"),
    detail: $("detail"),
    where: $("where"),
    value: $("value"),
    notice: $("notice"),
    fatal: $("fatal"),
  };

  const saved = vscode.getState() || {};
  let init = null;
  let useLabels = saved.useLabels !== false;
  let colLeft = [];
  let colWidth = [];
  let totalWidth = 0;
  let colChunk = 1;
  let charWidth = 7.2;
  /** Per value-label name: the set of label texts, to tell labels from numbers. */
  let labelTexts = {};

  /** Rows in the current view (after the filter); equals init.nObs when unfiltered. */
  let viewRows = 0;
  /** True when the view is filtered or sorted, so rows carry their own obs numbers. */
  let reordered = false;
  let sortKeys = [];
  /**
   * The sort most recently asked for. Differs from `sortKeys` only while a
   * query is in flight, so a second click builds on the first, not on the
   * stale applied state.
   */
  let wantedSort = [];
  let appliedFilter = "";
  let queryPending = false;

  /** Selection: `anchor` is where it started, `focus` where it ends. row -1 = whole column. */
  let anchor = { row: -1, col: -1 };
  let focus = { row: -1, col: -1 };
  let dragging = false;

  /** Bumped whenever cached cells stop being valid (reload, new view). */
  let generation = 0;
  let nextRequestId = 1;
  const cache = new Map(); // key → { rows, obs }
  const pending = new Map(); // request id → { key, generation }
  const inFlight = new Set();
  let renderQueued = false;
  /** Column summaries for the current view, by column index. */
  const summaries = new Map();
  let noticeTimer;
  /**
   * The label being edited in the detail panel: {name, draft, caret}. The
   * name is a variable's, or "_dta" for the dataset label.
   */
  let labelEdit = null;
  /**
   * The value label being edited: {name, isNew, forVar, text}. `text` holds
   * one "code label" pair per line; saving replaces the whole set.
   */
  let valueLabelEdit = null;

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

  function invalidateCells() {
    generation += 1;
    cache.clear();
    pending.clear();
    inFlight.clear();
  }

  function recomputeColumns() {
    colLeft = [];
    let x = 0;
    for (const w of colWidth) {
      colLeft.push(x);
      x += w;
    }
    totalWidth = x;
  }

  // ── load ────────────────────────────────────────────────────────────────

  function load(message) {
    const sameShape =
      init !== null &&
      init.nVars === message.nVars &&
      init.variables.every((v, i) => v.name === message.variables[i].name);
    init = message;
    invalidateCells();
    summaries.clear();

    charWidth = measureCharWidth();
    // Keep hand-resized column widths across a reload of the same dataset.
    if (!sameShape) {
      colWidth = init.variables.map((v) => Math.ceil(v.width * charWidth) + CELL_PAD);
    }
    recomputeColumns();
    colChunk = init.nVars <= 64 ? Math.max(1, init.nVars) : 32;

    labelTexts = {};
    for (const name of Object.keys(init.valueLabels)) {
      labelTexts[name] = new Set(init.valueLabels[name].map((entry) => entry[1]));
    }

    viewRows = init.nObs;
    reordered = false;
    if (!sameShape) {
      anchor = { row: -1, col: -1 };
      focus = { row: -1, col: -1 };
      sortKeys = [];
      wantedSort = [];
    }
    clampSelection();

    el.fatal.hidden = true;
    el.main.hidden = false;
    el.labels.disabled = Object.keys(init.valueLabels).length === 0;
    el.loadStata.hidden = !init.canLoadInStata;

    renderSummary();
    renderWarnings();
    renderVarList();
    renderDetail();
    layout();
    render();
    updateStatus();
  }

  /** The host finished a filter/sort (or a reload): `message.rows` rows are in view. */
  function applyView(message) {
    queryPending = false;
    invalidateCells();
    summaries.clear();
    viewRows = message.rows;
    appliedFilter = message.filter || "";
    sortKeys = Array.isArray(message.sort) ? message.sort : [];
    wantedSort = sortKeys;
    reordered = appliedFilter !== "" || sortKeys.length > 0;
    if (el.filter.value.trim() === "" || document.activeElement !== el.filter) {
      el.filter.value = appliedFilter;
    }
    el.filter.classList.remove("bad");
    clampSelection();
    renderQueryStatus();
    renderSortChips();
    layout();
    el.scroller.scrollTop = 0;
    render();
    renderDetail();
    updateStatus();
  }

  function clampSelection() {
    for (const p of [anchor, focus]) {
      if (p.row >= viewRows) p.row = viewRows - 1;
      if (init && p.col >= init.nVars) p.col = init.nVars - 1;
    }
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

  function renderQueryStatus(error) {
    el.goto.max = String(viewRows);
    el.goto.disabled = viewRows === 0;
    el.filterClear.hidden = appliedFilter === "" && el.filter.value.trim() === "";
    if (error) {
      el.queryStatus.textContent = error;
      el.queryStatus.className = "bad";
      return;
    }
    el.queryStatus.className = "";
    if (queryPending) {
      el.queryStatus.textContent = "Working…";
    } else if (appliedFilter !== "") {
      el.queryStatus.textContent = group(viewRows) + " of " + group(init.nObs) + " obs";
    } else {
      el.queryStatus.textContent = "";
    }
  }

  function renderSortChips() {
    if (sortKeys.length === 0) {
      el.sortChips.innerHTML = "";
      return;
    }
    const names = sortKeys
      .map((k) => esc(init.variables[k.column].name) + (k.descending ? " ↓" : " ↑"))
      .join(", ");
    el.sortChips.innerHTML =
      '<span class="chip">sorted by ' +
      names +
      ' <button type="button" id="sort-clear" title="Clear sort" aria-label="Clear sort">×</button></span>';
  }

  // ── queries ─────────────────────────────────────────────────────────────

  function sendQuery(filter, sort) {
    wantedSort = sort;
    queryPending = true;
    renderQueryStatus();
    vscode.postMessage({ type: "query", filter, sort });
  }

  function applyFilterInput() {
    sendQuery(el.filter.value.trim(), wantedSort);
  }

  function cycleSort(col, additive) {
    const current = wantedSort;
    const existing = current.find((k) => k.column === col);
    let next;
    if (!existing) {
      const key = { column: col, descending: false };
      next = additive ? current.concat([key]) : [key];
    } else if (!existing.descending) {
      const flipped = { column: col, descending: true };
      next = additive ? current.map((k) => (k.column === col ? flipped : k)) : [flipped];
    } else {
      next = additive ? current.filter((k) => k.column !== col) : [];
    }
    sendQuery(appliedFilter, next);
  }

  // ── grid ────────────────────────────────────────────────────────────────

  function totalRowsPx() {
    return viewRows * ROW_H;
  }

  function layout() {
    const height = Math.min(totalRowsPx(), MAX_SCROLL_PX);
    el.sizer.style.width = Math.max(1, totalWidth) + "px";
    el.sizer.style.height = Math.max(1, height) + "px";
    // Row numbers print with thousands separators; size the gutter to the largest.
    const gutter = Math.max(44, Math.ceil(group(init.nObs).length * charWidth) + 18);
    document.documentElement.style.setProperty("--gutter-w", gutter + "px");
    const blank = viewRows === 0 || init.nVars === 0;
    el.empty.style.display = blank ? "flex" : "none";
    el.empty.textContent =
      init.nVars === 0
        ? "This dataset has no variables."
        : init.nObs === 0
          ? "This dataset has no observations."
          : "No observations match the filter.";
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
    // the scrollable element is far shorter than viewRows * ROW_H. Rows snap to
    // whole-row positions; at the very end the last row is bottom-aligned.
    const maxScroll = Math.max(1, MAX_SCROLL_PX - viewH);
    const fraction = Math.min(1, Math.max(0, scrollTop / maxScroll));
    const maxFirst = Math.max(0, viewRows - Math.floor(viewH / ROW_H));
    const first = Math.round(fraction * maxFirst);
    const offset = first === maxFirst ? Math.max(0, (viewRows - first) * ROW_H - viewH) : 0;
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

  function blockFor(row, col) {
    const block = Math.floor(row / BLOCK);
    const chunk = Math.floor(col / colChunk);
    const key = cacheKey(block, chunk);
    const entry = cache.get(key);
    if (!entry) request(key, block, chunk);
    return { entry, block, chunk };
  }

  function cellText(row, col) {
    const { entry, block, chunk } = blockFor(row, col);
    if (!entry) return undefined;
    const r = entry.rows[row - block * BLOCK];
    return r ? r[col - chunk * colChunk] : undefined;
  }

  /** The observation number to show in the gutter for view row `row`. */
  function obsNumber(row) {
    if (!reordered) return row + 1;
    const { entry, block } = blockFor(row, 0);
    return entry && entry.obs ? entry.obs[row - block * BLOCK] : undefined;
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
    cache.set(req.key, { rows: message.rows, obs: message.obs });
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

  function selectionBounds() {
    if (focus.col < 0) return null;
    const wholeColumns = anchor.row < 0 || focus.row < 0;
    return {
      r0: wholeColumns ? 0 : Math.min(anchor.row, focus.row),
      r1: wholeColumns ? viewRows - 1 : Math.max(anchor.row, focus.row),
      c0: Math.min(anchor.col, focus.col),
      c1: Math.max(anchor.col, focus.col),
      wholeColumns,
    };
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
    const r1 = Math.min(viewRows, first + Math.ceil((viewH + offset) / ROW_H));
    const bounds = selectionBounds();

    let head = "";
    for (let c = c0; c < c1; c++) {
      const v = init.variables[c];
      const tip = [v.name, v.type + " " + v.format, v.label].filter(Boolean).join("\n");
      const sortAt = sortKeys.findIndex((k) => k.column === c);
      const arrow =
        sortAt < 0
          ? "⇅"
          : (sortKeys[sortAt].descending ? "↓" : "↑") + (sortKeys.length > 1 ? sortAt + 1 : "");
      head +=
        '<div class="h' +
        (v.numeric ? " num" : "") +
        (bounds && c >= bounds.c0 && c <= bounds.c1 ? " sel" : "") +
        (sortAt >= 0 ? " sorted" : "") +
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
        '</span><button type="button" class="s" data-sort="' +
        c +
        '" title="Sort (Shift-click to add a key)" aria-label="Sort by ' +
        esc(v.name) +
        '">' +
        arrow +
        '</button><span class="rz" data-resize="' +
        c +
        '"></span></div>';
    }
    el.head.innerHTML = head;

    let gutter = "";
    let cells = "";
    for (let r = first; r < r1; r++) {
      const top = (r - first) * ROW_H - offset;
      const obs = obsNumber(r);
      gutter +=
        '<div class="g' +
        (bounds && !bounds.wholeColumns && r >= bounds.r0 && r <= bounds.r1 ? " sel" : "") +
        '" style="top:' +
        top +
        'px">' +
        (obs === undefined ? "" : group(obs)) +
        "</div>";
      for (let c = c0; c < c1; c++) {
        const v = init.variables[c];
        const text = cellText(r, c);
        let cls = "c";
        if (v.numeric) cls += " num";
        if (text === undefined) cls += " wait";
        else if (v.numeric && MISSING.test(text)) cls += " miss";
        else if (useLabels && v.valueLabel && labelTexts[v.valueLabel]?.has(text)) cls += " lab";
        if (bounds && c >= bounds.c0 && c <= bounds.c1) {
          if (r === focus.row && c === focus.col) cls += " sel";
          else if (bounds.wholeColumns) cls += " colsel";
          else if (r >= bounds.r0 && r <= bounds.r1) cls += " range";
        }
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
    const maxFirst = Math.max(1, viewRows - Math.floor(viewH / ROW_H));
    s.scrollTop = Math.ceil((Math.min(row, maxFirst) / maxFirst) * maxScroll);
  }

  /** Move the selection. With `extend`, the anchor stays and a range forms. */
  function select(row, col, options) {
    const reveal = options && options.reveal;
    const extend = options && options.extend && anchor.col >= 0;
    const colChanged = col !== focus.col;
    focus = { row, col };
    if (!extend) anchor = { row, col };
    if (reveal) ensureVisible(row, col);
    if (colChanged) {
      markSelectedVariable();
      renderDetail();
    }
    render();
    updateStatus();
  }

  function updateStatus() {
    if (!init || focus.col < 0) {
      el.where.textContent = "";
      el.value.textContent = "";
      return;
    }
    const v = init.variables[focus.col];
    const bounds = selectionBounds();
    if (focus.row < 0) {
      el.where.textContent = v.name;
      el.value.textContent = v.label;
      return;
    }
    const obs = obsNumber(focus.row);
    el.where.textContent = v.name + "[" + (obs === undefined ? "…" : group(obs)) + "]";
    const many = bounds && (bounds.r1 > bounds.r0 || bounds.c1 > bounds.c0);
    if (many) {
      el.value.textContent =
        group(bounds.r1 - bounds.r0 + 1) + " × " + group(bounds.c1 - bounds.c0 + 1) + " cells selected";
      el.value.title = "";
      return;
    }
    const text = cellText(focus.row, focus.col);
    el.value.textContent = text === undefined ? "" : text;
    el.value.title = text === undefined ? "" : text;
  }

  function copySelection(headers) {
    const bounds = selectionBounds();
    if (!bounds || viewRows === 0) return;
    const single = !bounds.wholeColumns && bounds.r0 === bounds.r1 && bounds.c0 === bounds.c1;
    if (single && !headers) {
      const text = cellText(bounds.r0, bounds.c0);
      if (text !== undefined) {
        vscode.postMessage({ type: "copy", text });
        showNotice("Copied");
      }
      return;
    }
    vscode.postMessage({
      type: "copyRange",
      firstRow: bounds.r0,
      lastRow: bounds.r1,
      firstColumn: bounds.c0,
      lastColumn: bounds.c1,
      useLabels,
      headers,
    });
  }

  function showNotice(text, isError, canUndo) {
    el.notice.textContent = text;
    el.notice.className = isError ? "bad" : "";
    if (canUndo) {
      const undo = document.createElement("button");
      undo.type = "button";
      undo.id = "undo-edit";
      undo.textContent = "Undo";
      undo.title = "Put the file back as it was before this edit";
      el.notice.append(" ", undo);
    }
    if (noticeTimer) clearTimeout(noticeTimer);
    noticeTimer = setTimeout(
      () => {
        el.notice.textContent = "";
      },
      isError || canUndo ? 8000 : 3000,
    );
  }

  // ── variables panel ─────────────────────────────────────────────────────

  function renderVarList() {
    const needle = el.varFilter.value.trim().toLowerCase();
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
        (c === focus.col ? " sel" : "") +
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
    if (focus.col < 0) return;
    const node = el.varlist.querySelector('.v[data-c="' + focus.col + '"]');
    if (node) {
      node.classList.add("sel");
      node.scrollIntoView({ block: "nearest" });
    }
  }

  function row(term, text) {
    return text ? "<dt>" + esc(term) + "</dt><dd>" + esc(text) + "</dd>" : "";
  }

  function summaryHtml(col) {
    const state = summaries.get(col);
    const scope = appliedFilter !== "" ? " (filtered rows)" : "";
    if (!state) {
      if (init.nObs > AUTO_SUMMARY_ROWS) {
        return (
          "<h4>Summary</h4><p><button type=\"button\" id=\"summarize\">Summarize " +
          group(viewRows) +
          " rows</button></p>"
        );
      }
      requestSummary(col);
      return "<h4>Summary" + scope + "</h4><p class=\"dim\">Computing…</p>";
    }
    if (state === "pending") return "<h4>Summary" + scope + "</h4><p class=\"dim\">Computing…</p>";
    if (state.error) return "<h4>Summary</h4><p class=\"bad\">" + esc(state.error) + "</p>";
    let html = "<h4>Summary" + scope + "</h4><dl>";
    for (const pair of state.stats) html += row(pair[0], pair[1]);
    html += "</dl>";
    if (state.frequencies.length) {
      html += "<h4>Most frequent</h4><table class=\"freq\">";
      for (const f of state.frequencies) {
        const shown = f.label || (f.value === "" ? "(empty)" : f.value);
        html +=
          '<tr data-filter="' +
          esc(f.filter) +
          '" title="Filter to ' +
          esc(f.filter) +
          '"><td>' +
          esc(f.count) +
          "</td><td>" +
          esc(f.share) +
          "</td><td>" +
          esc(shown) +
          (f.label ? ' <span class="dim">' + esc(f.value) + "</span>" : "") +
          "</td></tr>";
      }
      html += "</table>";
      if (state.frequenciesOmitted > 0) {
        html += '<p class="dim">' + group(state.frequenciesOmitted) + " more values</p>";
      }
    }
    return html;
  }

  function requestSummary(col) {
    summaries.set(col, "pending");
    vscode.postMessage({ type: "summary", column: col });
  }

  function renderDetail() {
    if (focus.col < 0) {
      let html = "<h3>" + esc(init.title) + "</h3><dl>";
      if (labelEdit && labelEdit.name !== "_dta") labelEdit = null;
      if (labelEdit) {
        html +=
          '<dt>Label</dt><dd><input type="text" id="label-input" maxlength="' +
          MAX_LABEL_CHARS +
          '" spellcheck="false" aria-label="Dataset label" value="' +
          esc(labelEdit.draft) +
          '" /><span class="hint">Enter to save to the file · Esc to cancel</span></dd>';
      } else if (init.canEditLabels) {
        html +=
          "<dt>Label</dt><dd>" +
          (init.dataLabel ? esc(init.dataLabel) : '<span class="dim">(none)</span>') +
          ' <button type="button" id="label-edit" title="Edit the dataset label in the file">Edit</button></dd>';
      } else {
        html += row("Label", init.dataLabel);
      }
      html += row("Saved", init.timestamp);
      html += row("Sorted by", init.sortedBy.join(" "));
      html += row("Format", ".dta " + init.release);
      html += "</dl>";
      if (init.notes.length) {
        html += "<h4>Notes</h4>" + init.notes.map((n) => "<p>" + esc(n) + "</p>").join("");
      }
      el.detail.innerHTML = html;
      restoreLabelInput();
      return;
    }
    const v = init.variables[focus.col];
    if (valueLabelEdit && valueLabelEdit.forVar !== v.name) valueLabelEdit = null;
    let html = "<h3>" + esc(v.name) + "</h3><dl>";
    if (labelEdit && labelEdit.name !== v.name) labelEdit = null;
    if (labelEdit) {
      html +=
        '<dt>Label</dt><dd><input type="text" id="label-input" maxlength="' +
        MAX_LABEL_CHARS +
        '" spellcheck="false" aria-label="Variable label for ' +
        esc(v.name) +
        '" value="' +
        esc(labelEdit.draft) +
        '" /><span class="hint">Enter to save to the file · Esc to cancel</span></dd>';
    } else if (init.canEditLabels) {
      html +=
        "<dt>Label</dt><dd>" +
        (v.label ? esc(v.label) : '<span class="dim">(none)</span>') +
        ' <button type="button" id="label-edit" title="Edit this variable\'s label in the file">Edit</button></dd>';
    } else {
      html += row("Label", v.label);
    }
    html += row("Type", v.type);
    html += row("Format", v.format);
    if (init.canEditLabels && v.numeric && v.type !== "alias") {
      // attach one of the file's value labels, none, or a new one
      const names = Object.keys(init.valueLabels);
      if (v.valueLabel && !names.includes(v.valueLabel)) names.push(v.valueLabel);
      html +=
        '<dt>Value label</dt><dd><select id="vl-attach" aria-label="Value label attached to ' +
        esc(v.name) +
        '"><option value="">(none)</option>' +
        names
          .map(
            (n) =>
              '<option value="' +
              esc(n) +
              '"' +
              (n === v.valueLabel ? " selected" : "") +
              ">" +
              esc(n) +
              "</option>",
          )
          .join("") +
        '<option value="+new">New value label…</option></select></dd>';
    } else {
      html += row("Value label", v.valueLabel);
    }
    html += "</dl>";
    if (v.notes.length) {
      html += "<h4>Notes</h4>" + v.notes.map((n) => "<p>" + esc(n) + "</p>").join("");
    }
    if (v.type !== "alias" && viewRows > 0) html += summaryHtml(focus.col);
    const entries = v.valueLabel ? init.valueLabels[v.valueLabel] : undefined;
    if (valueLabelEdit) {
      html += valueLabelEditorHtml();
    } else if (entries) {
      const truncated = init.valueLabelsTruncated.includes(v.valueLabel);
      html += '<h4>Value label <span class="set-name">' + esc(v.valueLabel) + "</span>";
      if (init.canEditLabels && !truncated) {
        html +=
          ' <button type="button" id="vl-edit" title="Edit the codes and texts of this value label in the file">Edit</button>' +
          '<button type="button" id="vl-drop" title="Remove this value label from the file and from every variable that uses it">Drop</button>';
      }
      html += "</h4><table>";
      for (const entry of entries) {
        html += "<tr><td>" + esc(entry[0]) + "</td><td>" + esc(entry[1]) + "</td></tr>";
      }
      html += "</table>";
      if (truncated) {
        html += "<p>(list cut at " + group(entries.length) + " entries)</p>";
      }
    } else if (v.valueLabel) {
      html += "<h4>Value label</h4><p>The file does not define this value label.</p>";
    }
    el.detail.innerHTML = html;
    restoreLabelInput();
    const area = document.getElementById("vl-text");
    if (area && valueLabelEdit.caret !== undefined) {
      const focusName = valueLabelEdit.inName ? document.getElementById("vl-name") : area;
      focusName?.focus();
      if (!valueLabelEdit.inName) area.setSelectionRange(valueLabelEdit.caret, valueLabelEdit.caret);
    }
  }

  function restoreLabelInput() {
    const input = document.getElementById("label-input");
    if (!input || !labelEdit) return;
    // The panel re-renders when a summary arrives; keep the caret where it was.
    input.focus();
    const at = Math.min(labelEdit.caret ?? input.value.length, input.value.length);
    input.setSelectionRange(at, at);
  }

  function startLabelEdit() {
    if (!init || !init.canEditLabels) return;
    labelEdit =
      focus.col < 0
        ? { name: "_dta", draft: init.dataLabel }
        : { name: init.variables[focus.col].name, draft: init.variables[focus.col].label };
    renderDetail();
    document.getElementById("label-input")?.select();
  }

  function commitLabelEdit() {
    if (!labelEdit) return;
    const { name, draft } = labelEdit;
    labelEdit = null;
    if (name === "_dta") {
      if (draft.trim() !== init.dataLabel) {
        vscode.postMessage({ type: "editLabels", edit: { dataLabel: draft.trim() } });
      }
    } else {
      const v = init.variables.find((x) => x.name === name);
      if (v && draft !== v.label) vscode.postMessage({ type: "setLabel", name, label: draft });
    }
    renderDetail();
  }

  // ── value labels ────────────────────────────────────────────────────────

  function usersOf(setName) {
    return init.variables.filter((x) => x.valueLabel === setName).map((x) => x.name);
  }

  function valueLabelEditorHtml() {
    const edit = valueLabelEdit;
    const others = edit.isNew ? [] : usersOf(edit.name).filter((n) => n !== edit.forVar);
    let html =
      "<h4>" +
      (edit.isNew
        ? "New value label"
        : 'Value label <span class="set-name">' + esc(edit.name) + "</span>") +
      "</h4>";
    html += '<div id="vl-editor">';
    if (edit.isNew) {
      html +=
        '<input type="text" id="vl-name" maxlength="32" spellcheck="false" placeholder="Name, e.g. yesno" aria-label="Name of the new value label" value="' +
        esc(edit.name) +
        '" />';
    }
    html +=
      '<textarea id="vl-text" rows="' +
      Math.min(14, Math.max(4, edit.text.split("\n").length + 1)) +
      '" spellcheck="false" aria-label="Codes and labels, one pair per line">' +
      esc(edit.text) +
      "</textarea>" +
      '<span class="hint">One per line: the code, a space, the label. A code is an integer or .a to .z.' +
      (others.length ? " Also used by " + esc(others.join(", ")) + "." : "") +
      "</span>" +
      '<div class="actions"><button type="button" id="vl-save" title="Write to the file (Ctrl/Cmd+Enter)">Save to file</button>' +
      '<button type="button" id="vl-cancel">Cancel</button></div></div>';
    return html;
  }

  function startValueLabelEdit(isNew) {
    if (!init || !init.canEditLabels || focus.col < 0) return;
    const v = init.variables[focus.col];
    if (isNew) {
      valueLabelEdit = { name: "", isNew: true, forVar: v.name, text: "", caret: 0, inName: true };
    } else {
      const entries = init.valueLabels[v.valueLabel];
      if (!entries) return;
      const text = entries.map((e) => e[0] + " " + e[1]).join("\n");
      valueLabelEdit = { name: v.valueLabel, isNew: false, forVar: v.name, text, caret: text.length };
    }
    renderDetail();
  }

  /** Parse the editor's text into {code: label}, or return a message. */
  function parseValueLabelText(text) {
    const table = {};
    const lines = text.split("\n");
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].trim() === "") continue;
      const m = /^\s*(-?\d+|\.[a-z])\s+(.*\S)\s*$/.exec(lines[i]);
      if (!m) {
        return "Line " + (i + 1) + ": expected a code (an integer or .a to .z), a space, and the label";
      }
      if (Object.prototype.hasOwnProperty.call(table, m[1])) {
        return "Line " + (i + 1) + ": code " + m[1] + " is given twice";
      }
      table[m[1]] = m[2];
    }
    return Object.keys(table).length ? table : "Enter at least one code and label";
  }

  function commitValueLabelEdit() {
    if (!valueLabelEdit) return;
    const edit = valueLabelEdit;
    const name = edit.name.trim();
    if (edit.isNew && !/^[A-Za-z_][A-Za-z0-9_]{0,31}$/.test(name)) {
      showNotice("A value label name is 1-32 letters, digits or _, not starting with a digit", true);
      return;
    }
    if (edit.isNew && init.valueLabels[name]) {
      showNotice("The file already has a value label named " + name + "; pick it from the list", true);
      return;
    }
    const table = parseValueLabelText(edit.text);
    if (typeof table === "string") {
      showNotice(table, true);
      return;
    }
    valueLabelEdit = null;
    const message = { valueLabels: { [name]: table } };
    if (edit.isNew) message.attach = { [edit.forVar]: name };
    vscode.postMessage({ type: "editLabels", edit: message });
    renderDetail();
  }

  // ── events ──────────────────────────────────────────────────────────────

  el.scroller.addEventListener("scroll", queueRender, { passive: true });
  new ResizeObserver(queueRender).observe(el.scroller);

  el.cells.addEventListener("mousedown", (event) => {
    if (event.button !== 0) return;
    const cell = event.target.closest(".c");
    if (!cell) return;
    dragging = true;
    select(Number(cell.dataset.r), Number(cell.dataset.c), { extend: event.shiftKey });
  });
  el.cells.addEventListener("mouseover", (event) => {
    if (!dragging) return;
    const cell = event.target.closest(".c");
    if (!cell) return;
    const r = Number(cell.dataset.r);
    const c = Number(cell.dataset.c);
    if (r !== focus.row || c !== focus.col) select(r, c, { extend: true });
  });
  window.addEventListener("mouseup", () => {
    dragging = false;
  });

  // Column resize: drag the right edge of a header.
  let resizing = null;
  el.head.addEventListener("mousedown", (event) => {
    const handle = event.target.closest("[data-resize]");
    if (!handle) return;
    const col = Number(handle.dataset.resize);
    resizing = { col, startX: event.clientX, startW: colWidth[col] };
    document.body.classList.add("resizing");
    event.preventDefault();
  });
  window.addEventListener("mousemove", (event) => {
    if (!resizing) return;
    colWidth[resizing.col] = Math.max(MIN_COL_W, resizing.startW + event.clientX - resizing.startX);
    recomputeColumns();
    el.sizer.style.width = Math.max(1, totalWidth) + "px";
    queueRender();
  });
  window.addEventListener("mouseup", () => {
    if (!resizing) return;
    resizing = null;
    document.body.classList.remove("resizing");
  });
  // Double-click the edge to fit the column to the cells on screen.
  el.head.addEventListener("dblclick", (event) => {
    const handle = event.target.closest("[data-resize]");
    if (!handle) return;
    const col = Number(handle.dataset.resize);
    let chars = init.variables[col].name.length;
    for (const cell of el.cells.querySelectorAll('.c[data-c="' + col + '"]')) {
      chars = Math.max(chars, cell.textContent.length);
    }
    colWidth[col] = Math.max(MIN_COL_W, Math.ceil(Math.min(chars, 80) * charWidth) + CELL_PAD + 14);
    recomputeColumns();
    el.sizer.style.width = Math.max(1, totalWidth) + "px";
    queueRender();
  });

  el.head.addEventListener("click", (event) => {
    if (event.target.closest("[data-resize]")) return;
    const sort = event.target.closest("[data-sort]");
    if (sort) {
      cycleSort(Number(sort.dataset.sort), event.shiftKey);
      return;
    }
    const header = event.target.closest(".h");
    if (!header) return;
    select(-1, Number(header.dataset.c), { extend: event.shiftKey });
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
    select(focus.row, col);
    ensureVisible(-1, col);
  });

  el.varFilter.addEventListener("input", renderVarList);

  el.varlist.addEventListener("dblclick", (event) => {
    if (event.target.closest(".v[data-c]")) startLabelEdit();
  });

  el.detail.addEventListener("input", (event) => {
    if (valueLabelEdit && event.target.id === "vl-text") {
      valueLabelEdit.text = event.target.value;
      valueLabelEdit.caret = event.target.selectionStart;
      valueLabelEdit.inName = false;
      return;
    }
    if (valueLabelEdit && event.target.id === "vl-name") {
      valueLabelEdit.name = event.target.value;
      valueLabelEdit.inName = true;
      return;
    }
    if (event.target.id !== "label-input" || !labelEdit) return;
    labelEdit.draft = event.target.value;
    labelEdit.caret = event.target.selectionStart;
  });
  el.detail.addEventListener("change", (event) => {
    if (event.target.id !== "vl-attach" || focus.col < 0) return;
    const v = init.variables[focus.col];
    const choice = event.target.value;
    if (choice === "+new") {
      startValueLabelEdit(true);
      return;
    }
    if (choice !== v.valueLabel) {
      vscode.postMessage({ type: "editLabels", edit: { attach: { [v.name]: choice } } });
    }
  });
  el.detail.addEventListener("keydown", (event) => {
    if (event.target.id === "vl-text" || event.target.id === "vl-name") {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !event.isComposing) {
        event.preventDefault();
        commitValueLabelEdit();
      } else if (event.key === "Escape") {
        event.preventDefault();
        valueLabelEdit = null;
        renderDetail();
      }
      event.stopPropagation();
      return;
    }
    if (event.target.id !== "label-input") return;
    if (event.key === "Enter" && !event.isComposing) {
      event.preventDefault();
      commitLabelEdit();
    } else if (event.key === "Escape") {
      event.preventDefault();
      labelEdit = null;
      renderDetail();
    }
    event.stopPropagation();
  });

  el.notice.addEventListener("click", (event) => {
    if (event.target.id !== "undo-edit") return;
    el.notice.textContent = "";
    vscode.postMessage({ type: "undoEdit" });
  });

  el.detail.addEventListener("click", (event) => {
    if (event.target.id === "label-edit") {
      startLabelEdit();
      return;
    }
    if (event.target.id === "vl-edit") {
      startValueLabelEdit(false);
      return;
    }
    if (event.target.id === "vl-save") {
      commitValueLabelEdit();
      return;
    }
    if (event.target.id === "vl-cancel") {
      valueLabelEdit = null;
      renderDetail();
      return;
    }
    if (event.target.id === "vl-drop" && focus.col >= 0) {
      // the extension asks for confirmation; a webview cannot
      vscode.postMessage({ type: "dropValueLabel", name: init.variables[focus.col].valueLabel });
      return;
    }
    if (event.target.id === "summarize" && focus.col >= 0) {
      requestSummary(focus.col);
      renderDetail();
      return;
    }
    const freq = event.target.closest("tr[data-filter]");
    if (freq) {
      el.filter.value = freq.dataset.filter;
      applyFilterInput();
    }
  });

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

  // Filter bar.
  el.filter.addEventListener("keydown", (event) => {
    if (event.key === "Enter") applyFilterInput();
    else if (event.key === "Escape") {
      el.filter.value = appliedFilter;
      el.filter.classList.remove("bad");
      renderQueryStatus();
    }
  });
  el.filter.addEventListener("input", () => {
    el.filterClear.hidden = appliedFilter === "" && el.filter.value.trim() === "";
  });
  el.filterClear.addEventListener("click", () => {
    el.filter.value = "";
    sendQuery("", wantedSort);
  });
  el.sortChips.addEventListener("click", (event) => {
    if (event.target.id === "sort-clear") sendQuery(appliedFilter, []);
  });

  // "More" menu.
  function closeMenu() {
    el.menu.hidden = true;
    el.menuButton.setAttribute("aria-expanded", "false");
  }
  el.menuButton.addEventListener("click", (event) => {
    event.stopPropagation();
    el.menu.hidden = !el.menu.hidden;
    el.menuButton.setAttribute("aria-expanded", String(!el.menu.hidden));
  });
  document.addEventListener("click", closeMenu);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMenu();
  });
  el.exportCsv.addEventListener("click", () => {
    vscode.postMessage({ type: "export", kind: "csv", useLabels });
  });
  el.exportCodebook.addEventListener("click", () => {
    vscode.postMessage({ type: "export", kind: "codebook" });
  });
  el.loadStata.addEventListener("click", () => {
    vscode.postMessage({ type: "loadInStata" });
  });

  function goToRow() {
    if (!init || viewRows === 0) return;
    const wanted = Math.floor(Number(el.goto.value));
    if (!Number.isFinite(wanted) || wanted < 1) return;
    const target = Math.min(viewRows, wanted) - 1;
    scrollRowToTop(target);
    select(target, focus.col >= 0 ? focus.col : 0);
    el.scroller.focus();
  }
  el.goto.addEventListener("change", goToRow);
  el.goto.addEventListener("keydown", (event) => {
    if (event.key === "Enter") goToRow();
  });

  el.scroller.addEventListener("keydown", (event) => {
    if (!init || viewRows === 0 || init.nVars === 0) return;
    const mod = event.metaKey || event.ctrlKey;
    if (mod && event.key.toLowerCase() === "c") {
      copySelection(event.shiftKey);
      event.preventDefault();
      return;
    }
    if (mod && event.key.toLowerCase() === "a") {
      anchor = { row: 0, col: 0 };
      focus = { row: viewRows - 1, col: init.nVars - 1 };
      render();
      updateStatus();
      event.preventDefault();
      return;
    }
    const page = Math.max(1, Math.floor(el.scroller.clientHeight / ROW_H) - 1);
    let r = Math.max(0, focus.row);
    let c = Math.max(0, focus.col);
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
        if (mod) r = 0;
        c = 0;
        break;
      case "End":
        if (mod) r = viewRows - 1;
        c = init.nVars - 1;
        break;
      default:
        return;
    }
    event.preventDefault();
    select(Math.min(viewRows - 1, Math.max(0, r)), Math.min(init.nVars - 1, Math.max(0, c)), {
      reveal: true,
      extend: event.shiftKey,
    });
  });

  window.addEventListener("message", (event) => {
    const message = event.data;
    if (!message || typeof message.type !== "string") return;
    switch (message.type) {
      case "init":
        load(message);
        break;
      case "view":
        if (init) applyView(message);
        break;
      case "queryError":
        queryPending = false;
        wantedSort = sortKeys;
        el.filter.classList.add("bad");
        renderQueryStatus(message.message);
        break;
      case "rows":
        receive(message);
        break;
      case "summary":
        summaries.set(message.column, message.error ? { error: message.error } : message.summary);
        if (message.column === focus.col) renderDetail();
        break;
      case "notice":
        showNotice(message.text, message.isError, message.canUndo);
        break;
      case "error":
        el.main.hidden = true;
        el.fatal.hidden = false;
        el.fatal.innerHTML =
          "<h2>" +
          esc(message.title || "Cannot open this file") +
          "</h2><p>" +
          esc(message.message) +
          "</p>";
        break;
      default:
        break;
    }
  });

  vscode.postMessage({ type: "ready" });
})();
