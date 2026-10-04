// VS Code glue for the Stata .dta viewer.
//
// Two entry points share one webview implementation:
//   - a read-only custom editor, so opening any *.dta file shows the data;
//   - `openDtaSnapshotPanel`, used by "View data preview" to show a snapshot
//     of the data a Stata session holds in memory.
//
// Parsing and formatting live in dtaReader / dtaFormat / dtaViewModel, which
// are unit-tested without VS Code; this file only moves messages.

import { createWriteStream, promises as fs } from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";

import { codebookCsv, csvChunks, rangeToTsv, valueLabelsCsv } from "./dtaExport";
import { openFileByteSource } from "./dtaFileSource";
import { DtaFilterError } from "./dtaFilter";
import {
  DEFAULT_QUERY_MEMORY_BYTES,
  buildRowOrder,
  DtaQueryCancelled,
  DtaQueryError,
  summarizeColumn,
  type QueryLimits,
  type RowQuery,
  type SortKey,
} from "./dtaQuery";
import { BufferByteSource, DtaFormatError, DtaReader, type ByteSource } from "./dtaReader";
import { buildDtaViewerHtml } from "./dtaViewerHtml";
import { CodebookError, editFromCodebook } from "./dtaImport";
import {
  describeEdit,
  type DtaLabelEdit,
  DtaEditError,
  editChanged,
  editLabels,
  inverseEdit,
} from "./dtaWriter";
import { buildViewerInit, formatRange, formatRows, formatSummary } from "./dtaViewModel";

export const DTA_VIEW_TYPE = "stataCode.dtaViewer";
const RELOAD_DEBOUNCE_MS = 300;

interface ViewerSource {
  uri: vscode.Uri;
  title: string;
  subtitle?: string;
  /** False for a snapshot of in-memory data: there is no file to `use`. */
  canLoadInStata: boolean;
  /** True for a dataset file on disk, whose variable labels can be rewritten. */
  canEditLabels: boolean;
}

interface WebviewRequest {
  type?: unknown;
  id?: unknown;
  start?: unknown;
  count?: unknown;
  firstColumn?: unknown;
  endColumn?: unknown;
  useLabels?: unknown;
  text?: unknown;
  filter?: unknown;
  sort?: unknown;
  column?: unknown;
  firstRow?: unknown;
  lastRow?: unknown;
  lastColumn?: unknown;
  headers?: unknown;
  kind?: unknown;
  name?: unknown;
  label?: unknown;
  edit?: unknown;
}

/** How many label edits a viewer can take back. */
const MAX_UNDO = 50;

/** Keep only the well-formed parts of an edit a webview sent. */
function parseEdit(value: unknown): DtaLabelEdit | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  const strings = (v: unknown): Record<string, string> | undefined => {
    if (typeof v !== "object" || v === null || Array.isArray(v)) return undefined;
    const entries = Object.entries(v as Record<string, unknown>);
    return entries.every(([, text]) => typeof text === "string")
      ? (Object.fromEntries(entries) as Record<string, string>)
      : undefined;
  };
  const edit: DtaLabelEdit = {};
  const variableLabels = strings(raw.variableLabels);
  if (variableLabels) edit.variableLabels = variableLabels;
  const attach = strings(raw.attach);
  if (attach) edit.attach = attach;
  if (typeof raw.dataLabel === "string") edit.dataLabel = raw.dataLabel;
  if (typeof raw.valueLabels === "object" && raw.valueLabels !== null) {
    const sets: Record<string, Record<string, string> | null> = {};
    for (const [name, table] of Object.entries(raw.valueLabels as Record<string, unknown>)) {
      if (table === null) sets[name] = null;
      else {
        const parsed = strings(table);
        if (!parsed) return null;
        sets[name] = parsed;
      }
    }
    edit.valueLabels = sets;
  }
  return edit;
}

const NO_QUERY: RowQuery = { filter: "", sort: [] };

/** The memory filters, sorts and summaries may take, from the user's setting. */
function queryLimits(): QueryLimits {
  const megabytes = vscode.workspace
    .getConfiguration("stataCode")
    .get<number>("dtaViewerMemoryMb", DEFAULT_QUERY_MEMORY_BYTES / 1024 ** 2);
  const safe = Number.isFinite(megabytes) && megabytes >= 16 ? megabytes : 16;
  return { memoryBytes: safe * 1024 ** 2 };
}

/** Validate the sort keys a webview sent; anything malformed is dropped. */
function parseSort(value: unknown, nVars: number): SortKey[] {
  if (!Array.isArray(value)) return [];
  const keys: SortKey[] = [];
  for (const item of value.slice(0, 8)) {
    const column = (item as { column?: unknown } | null)?.column;
    if (typeof column !== "number" || !Number.isInteger(column) || column < 0 || column >= nVars) {
      continue;
    }
    if (keys.some((k) => k.column === column)) continue;
    keys.push({ column, descending: (item as { descending?: unknown }).descending === true });
  }
  return keys;
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function makeNonce(): string {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let s = "";
  for (let i = 0; i < 32; i++) s += chars[Math.floor(Math.random() * chars.length)];
  return s;
}

async function openSource(uri: vscode.Uri): Promise<ByteSource> {
  if (uri.scheme === "file") return openFileByteSource(uri.fsPath);
  // Virtual file systems have no random access; read the file once.
  return new BufferByteSource(await vscode.workspace.fs.readFile(uri));
}

/** Drives one viewer webview: loads a .dta source and answers row requests. */
class DtaViewerSession implements vscode.Disposable {
  private reader: DtaReader | undefined;
  /** Row order of the current view; null is the dataset as stored. */
  private order: Uint32Array | null = null;
  private query: RowQuery = NO_QUERY;
  private querySeq = 0;
  private source: ViewerSource;
  private ready = false;
  private loadSeq = 0;
  /** Edits that take back the label changes made in this viewer, oldest first. */
  private undoStack: DtaLabelEdit[] = [];
  private reloadTimer: NodeJS.Timeout | undefined;
  private watcher: vscode.FileSystemWatcher | undefined;
  private readonly disposables: vscode.Disposable[] = [];

  constructor(
    private readonly webview: vscode.Webview,
    extensionUri: vscode.Uri,
    source: ViewerSource,
    private readonly log: (line: string) => void,
  ) {
    this.source = source;
    const media = vscode.Uri.joinPath(extensionUri, "media");
    webview.options = { enableScripts: true, localResourceRoots: [media] };
    webview.html = buildDtaViewerHtml({
      cspSource: webview.cspSource,
      nonce: makeNonce(),
      scriptUri: webview.asWebviewUri(vscode.Uri.joinPath(media, "dtaViewer.js")).toString(),
      styleUri: webview.asWebviewUri(vscode.Uri.joinPath(media, "dtaViewer.css")).toString(),
    });
    this.disposables.push(
      webview.onDidReceiveMessage((message: WebviewRequest) => void this.onMessage(message)),
      vscode.workspace.onDidChangeConfiguration((e) => {
        if (e.affectsConfiguration("stataCode.dtaLegacyEncoding")) void this.load();
      }),
    );
    this.watch();
  }

  /** Point the viewer at a different file (a fresh snapshot of the session). */
  setSource(source: ViewerSource): void {
    this.source = source;
    // Keep the filter and sort: re-previewing the same session after another
    // command is the common case, and load() drops them if they stop applying.
    this.watch();
    if (this.ready) void this.load();
  }

  private watch(): void {
    this.watcher?.dispose();
    this.watcher = undefined;
    const { uri } = this.source;
    if (uri.scheme !== "file") return;
    const pattern = new vscode.RelativePattern(
      vscode.Uri.file(path.dirname(uri.fsPath)),
      path.basename(uri.fsPath),
    );
    this.watcher = vscode.workspace.createFileSystemWatcher(pattern);
    const reload = (): void => {
      // Stata writes a dataset in several steps; wait for the dust to settle.
      if (this.reloadTimer) clearTimeout(this.reloadTimer);
      this.reloadTimer = setTimeout(() => void this.load(), RELOAD_DEBOUNCE_MS);
    };
    this.watcher.onDidChange(reload);
    this.watcher.onDidCreate(reload);
  }

  private async onMessage(message: WebviewRequest): Promise<void> {
    switch (message.type) {
      case "ready":
        this.ready = true;
        await this.load();
        return;
      case "rows":
        await this.sendRows(message);
        return;
      case "copy":
        if (typeof message.text === "string") {
          await vscode.env.clipboard.writeText(message.text);
        }
        return;
      case "query":
        await this.applyQuery(message);
        return;
      case "summary":
        await this.sendSummary(message);
        return;
      case "copyRange":
        await this.copyRange(message);
        return;
      case "export":
        await this.export(message);
        return;
      case "setLabel":
        if (typeof message.name === "string" && typeof message.label === "string") {
          await this.applyEdit({ variableLabels: { [message.name]: message.label.trim() } });
        }
        return;
      case "editLabels": {
        const edit = parseEdit(message.edit);
        if (edit) await this.applyEdit(edit);
        return;
      }
      case "dropValueLabel":
        await this.dropValueLabel(message);
        return;
      case "undoEdit":
        await this.undoEdit();
        return;
      case "importCodebook":
        await this.importCodebook();
        return;
      case "loadInStata":
        if (this.source.canLoadInStata) {
          await vscode.commands.executeCommand("stataCode.useDtaFile", this.source.uri);
        }
        return;
      default:
        return;
    }
  }

  private async load(): Promise<void> {
    const seq = ++this.loadSeq;
    const source = this.source;
    try {
      const legacyEncoding = vscode.workspace
        .getConfiguration("stataCode")
        .get<string>("dtaLegacyEncoding", "auto");
      const reader = await DtaReader.open(await openSource(source.uri), { legacyEncoding });
      if (seq !== this.loadSeq) return; // a newer load superseded this one
      // Carry the filter and sort across a reload (Stata re-saved the file);
      // if they no longer apply to the new contents, fall back to plain order.
      let order: Uint32Array | null = null;
      let query = this.query;
      const querySeq = ++this.querySeq;
      try {
        order = await buildRowOrder(
          reader,
          { filter: query.filter, sort: parseSort(query.sort, reader.meta.nVars) },
          () => querySeq !== this.querySeq,
          queryLimits(),
        );
      } catch {
        query = NO_QUERY;
      }
      if (seq !== this.loadSeq) return;
      this.reader = reader;
      this.order = order;
      this.query = query;
      await this.webview.postMessage(
        buildViewerInit(reader.meta, {
          title: source.title,
          subtitle: source.subtitle,
          canLoadInStata: source.canLoadInStata,
          // formats 102-111 (Stata 7 and older) are shown read-only
          canEditLabels:
            source.canEditLabels && source.uri.scheme === "file" && reader.meta.release >= 113,
        }),
      );
      await this.postView();
    } catch (err) {
      if (seq !== this.loadSeq) return;
      this.reader = undefined;
      this.order = null;
      const message = err instanceof Error ? err.message : String(err);
      this.log(`[stata-code] dta viewer: ${source.uri.toString()}: ${message}`);
      await this.webview.postMessage({
        type: "error",
        title:
          err instanceof DtaFormatError ? "Cannot read this .dta file" : "Cannot open this file",
        message,
      });
    }
  }

  private async sendRows(message: WebviewRequest): Promise<void> {
    const reader = this.reader;
    if (!reader || typeof message.id !== "number") return;
    try {
      const order = this.order;
      const block = await formatRows(
        reader,
        {
          start: message.start,
          count: message.count,
          firstColumn: message.firstColumn,
          endColumn: message.endColumn,
          useLabels: message.useLabels,
        },
        order,
      );
      // Drop the reply if the file was reloaded or the view re-queried meanwhile.
      if (reader !== this.reader || order !== this.order) return;
      await this.webview.postMessage({ type: "rows", id: message.id, ...block });
    } catch (err) {
      const text = err instanceof Error ? err.message : String(err);
      this.log(`[stata-code] dta viewer: reading rows failed: ${text}`);
      // The file most likely changed under the reader; re-parse it.
      void this.load();
    }
  }

  /** Tell the webview which rows the view now holds. */
  private async postView(): Promise<void> {
    const reader = this.reader;
    if (!reader) return;
    await this.webview.postMessage({
      type: "view",
      rows: this.order ? this.order.length : reader.meta.nObs,
      total: reader.meta.nObs,
      filter: this.query.filter,
      sort: this.query.sort,
    });
  }

  private async applyQuery(message: WebviewRequest): Promise<void> {
    const reader = this.reader;
    if (!reader) return;
    const query: RowQuery = {
      filter: typeof message.filter === "string" ? message.filter.slice(0, 4000) : "",
      sort: parseSort(message.sort, reader.meta.nVars),
    };
    const seq = ++this.querySeq;
    try {
      const order = await buildRowOrder(reader, query, () => seq !== this.querySeq, queryLimits());
      if (seq !== this.querySeq || reader !== this.reader) return;
      this.order = order;
      this.query = query;
      await this.postView();
    } catch (err) {
      if (err instanceof DtaQueryCancelled || seq !== this.querySeq) return;
      if (!(err instanceof DtaFilterError) && !(err instanceof DtaQueryError)) {
        this.log(`[stata-code] dta viewer: query failed: ${errorText(err)}`);
      }
      // The previous view stays in place; the webview shows the message.
      await this.webview.postMessage({ type: "queryError", message: errorText(err) });
    }
  }

  private async sendSummary(message: WebviewRequest): Promise<void> {
    const reader = this.reader;
    const column = message.column;
    if (!reader || typeof column !== "number") return;
    const order = this.order;
    try {
      const summary = await summarizeColumn(
        reader,
        column,
        order,
        () => reader !== this.reader || order !== this.order,
        queryLimits(),
      );
      if (reader !== this.reader || order !== this.order) return;
      await this.webview.postMessage({
        type: "summary",
        column,
        summary: formatSummary(summary, reader.meta.variables[column]),
      });
    } catch (err) {
      if (err instanceof DtaQueryCancelled) return;
      await this.webview.postMessage({ type: "summary", column, error: errorText(err) });
    }
  }

  private async notice(text: string, isError = false): Promise<void> {
    await this.webview.postMessage({ type: "notice", text, isError });
  }

  /**
   * Write a label edit into the file, then show the file as it now is. The
   * edit that takes it back is kept, so the notice can offer Undo.
   */
  private async applyEdit(edit: DtaLabelEdit, undoing = false): Promise<void> {
    const { uri, canEditLabels } = this.source;
    if (!canEditLabels || uri.scheme !== "file") return;
    const before = this.reader?.meta;
    try {
      const legacyEncoding = vscode.workspace
        .getConfiguration("stataCode")
        .get<string>("dtaLegacyEncoding", "auto");
      const result = await editLabels(uri.fsPath, edit, { legacyEncoding });
      await this.load();
      if (!editChanged(result)) return;
      if (!undoing && before) {
        this.undoStack.push(inverseEdit(before, result));
        if (this.undoStack.length > MAX_UNDO) this.undoStack.shift();
      }
      await this.webview.postMessage({
        type: "notice",
        text: undoing ? `Undone: ${describeEdit(result).toLowerCase()}` : describeEdit(result),
        isError: false,
        canUndo: !undoing && before !== undefined,
      });
    } catch (err) {
      if (!(err instanceof DtaEditError)) {
        this.log(`[stata-code] dta viewer: writing labels failed: ${errorText(err)}`);
      }
      await this.notice(`Not saved: ${errorText(err)}`, true);
    }
  }

  /** Drop a value label after asking; it is detached from every variable using it. */
  private async dropValueLabel(message: WebviewRequest): Promise<void> {
    const name = message.name;
    const meta = this.reader?.meta;
    if (typeof name !== "string" || !name || !meta) return;
    const users = meta.variables.filter((v) => v.valueLabel === name).map((v) => v.name);
    const detail =
      users.length > 0
        ? `It will be detached from ${users.join(", ")}. The codes in the data are not changed.`
        : "No variable uses it.";
    const choice = await vscode.window.showWarningMessage(
      `Drop the value label "${name}" from ${path.basename(this.source.uri.fsPath)}?`,
      { modal: true, detail },
      "Drop",
    );
    if (choice === "Drop") await this.applyEdit({ valueLabels: { [name]: null } });
  }

  /**
   * Apply the labels in a codebook CSV (the file *Export codebook* writes,
   * edited in a spreadsheet) as one edit, after showing what it would change.
   */
  private async importCodebook(): Promise<void> {
    const { uri, canEditLabels } = this.source;
    if (!canEditLabels || uri.scheme !== "file") return;
    const picked = await vscode.window.showOpenDialog({
      defaultUri: vscode.Uri.file(path.dirname(uri.fsPath)),
      canSelectMany: false,
      filters: { CSV: ["csv"] },
      title: "Import labels from a codebook CSV",
      openLabel: "Import labels",
    });
    if (!picked || picked.length === 0) return;
    try {
      const codebookPath = picked[0].fsPath;
      const codebook = await fs.readFile(codebookPath, "utf8");
      // the value-label mappings are exported next to the codebook
      const labelsPath = codebookPath.replace(/(\.csv)?$/i, "_value_labels.csv");
      let valueLabels: string | undefined;
      try {
        valueLabels = await fs.readFile(labelsPath, "utf8");
      } catch {
        valueLabels = undefined;
      }
      const edit = editFromCodebook(codebook, valueLabels);
      const legacyEncoding = vscode.workspace
        .getConfiguration("stataCode")
        .get<string>("dtaLegacyEncoding", "auto");
      const planned = await editLabels(uri.fsPath, edit, { legacyEncoding, dryRun: true });
      if (!editChanged(planned)) {
        await this.notice("The codebook matches the file; nothing to change");
        return;
      }
      const count =
        planned.variableLabels.length +
        Object.keys(planned.valueLabels).length +
        planned.attached.length +
        (planned.dataLabel ? 1 : 0);
      const summary = describeEdit(planned);
      const choice = await vscode.window.showWarningMessage(
        `Apply ${count} label change${count === 1 ? "" : "s"} to ${path.basename(uri.fsPath)}?`,
        {
          modal: true,
          detail:
            (summary.length > 600 ? `${summary.slice(0, 600)}…` : summary) +
            (valueLabels === undefined
              ? ""
              : `\n\nValue labels were read from ${path.basename(labelsPath)}.`),
        },
        "Apply",
      );
      if (choice === "Apply") await this.applyEdit(edit);
    } catch (err) {
      if (!(err instanceof DtaEditError) && !(err instanceof CodebookError)) {
        this.log(`[stata-code] dta viewer: importing a codebook failed: ${errorText(err)}`);
      }
      await this.notice(`Not imported: ${errorText(err)}`, true);
    }
  }

  private async undoEdit(): Promise<void> {
    const edit = this.undoStack.pop();
    if (!edit) {
      await this.notice("Nothing to undo");
      return;
    }
    await this.applyEdit(edit, true);
  }

  private async copyRange(message: WebviewRequest): Promise<void> {
    const reader = this.reader;
    if (!reader) return;
    try {
      const rows = await formatRange(
        reader,
        {
          firstRow: message.firstRow,
          lastRow: message.lastRow,
          firstColumn: message.firstColumn,
          lastColumn: message.lastColumn,
          useLabels: message.useLabels,
          headers: message.headers,
        },
        this.order,
      );
      await vscode.env.clipboard.writeText(rangeToTsv(rows));
      const body = message.headers === true ? rows.length - 1 : rows.length;
      const columns = rows[0]?.length ?? 0;
      await this.notice(
        `Copied ${body.toLocaleString("en-US")} × ${columns.toLocaleString("en-US")} cells`,
      );
    } catch (err) {
      await this.notice(errorText(err), true);
    }
  }

  private async export(message: WebviewRequest): Promise<void> {
    const reader = this.reader;
    if (!reader) return;
    // A snapshot's temp-file name means nothing to the user.
    const stem = this.source.canLoadInStata
      ? path.basename(this.source.uri.path).replace(/\.dta$/i, "") || "data"
      : "data";
    const folder =
      this.source.canLoadInStata && this.source.uri.scheme === "file"
        ? path.dirname(this.source.uri.fsPath)
        : (vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "");
    const suggest = (name: string): vscode.Uri | undefined =>
      folder ? vscode.Uri.file(path.join(folder, name)) : undefined;

    try {
      if (message.kind === "codebook") {
        const target = await vscode.window.showSaveDialog({
          defaultUri: suggest(`${stem}_codebook.csv`),
          filters: { CSV: ["csv"] },
          title: "Export codebook",
        });
        if (!target) return;
        await fs.writeFile(target.fsPath, codebookCsv(reader.meta), "utf8");
        const labels = valueLabelsCsv(reader.meta);
        let extra = "";
        if (labels !== undefined) {
          const labelsPath = target.fsPath.replace(/(\.csv)?$/i, "_value_labels.csv");
          await fs.writeFile(labelsPath, labels, "utf8");
          extra = ` and ${path.basename(labelsPath)}`;
        }
        await this.notice(`Wrote ${path.basename(target.fsPath)}${extra}`);
        return;
      }

      const target = await vscode.window.showSaveDialog({
        defaultUri: suggest(`${stem}.csv`),
        filters: { CSV: ["csv"] },
        title: "Export data as CSV",
      });
      if (!target) return;
      const order = this.order;
      const total = order ? order.length : reader.meta.nObs;
      const cancelled = await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: `Exporting ${total.toLocaleString("en-US")} rows to ${path.basename(target.fsPath)}`,
          cancellable: true,
        },
        async (_progress, token) => {
          const stream = createWriteStream(target.fsPath, { encoding: "utf8" });
          const finished = new Promise<void>((resolve, reject) => {
            stream.on("finish", resolve);
            stream.on("error", reject);
          });
          for await (const chunk of csvChunks(reader, order, {
            useLabels: message.useLabels !== false,
            isCancelled: () => token.isCancellationRequested,
          })) {
            if (!stream.write(chunk)) {
              await new Promise<void>((resolve) => stream.once("drain", resolve));
            }
          }
          stream.end();
          await finished;
          return token.isCancellationRequested;
        },
      );
      if (cancelled) {
        await fs.unlink(target.fsPath).catch(() => undefined);
        await this.notice("Export cancelled");
        return;
      }
      await this.notice(
        `Wrote ${total.toLocaleString("en-US")} rows to ${path.basename(target.fsPath)}`,
      );
    } catch (err) {
      this.log(`[stata-code] dta viewer: export failed: ${errorText(err)}`);
      await this.notice(`Export failed: ${errorText(err)}`, true);
    }
  }

  dispose(): void {
    this.querySeq += 1; // cancel any pass still running
    if (this.reloadTimer) clearTimeout(this.reloadTimer);
    this.watcher?.dispose();
    for (const d of this.disposables) d.dispose();
    this.reader = undefined;
  }
}

/** Read-only custom editor: opening a *.dta file shows it in the viewer. */
export class DtaViewerProvider implements vscode.CustomReadonlyEditorProvider {
  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly log: (line: string) => void,
  ) {}

  openCustomDocument(uri: vscode.Uri): vscode.CustomDocument {
    return { uri, dispose: () => undefined };
  }

  resolveCustomEditor(document: vscode.CustomDocument, panel: vscode.WebviewPanel): void {
    const session = new DtaViewerSession(
      panel.webview,
      this.extensionUri,
      {
        uri: document.uri,
        title: path.basename(document.uri.path),
        canLoadInStata: true,
        canEditLabels: true,
      },
      this.log,
    );
    panel.onDidDispose(() => session.dispose());
  }
}

interface SnapshotPanel {
  panel: vscode.WebviewPanel;
  session: DtaViewerSession;
  file: string;
}

const snapshotPanels = new Map<string, SnapshotPanel>();

async function removeQuietly(file: string): Promise<void> {
  try {
    await fs.unlink(file);
  } catch {
    // Already gone, or still locked; the OS temp directory is cleaned eventually.
  }
}

/**
 * Show a snapshot .dta of a session's in-memory data. One panel per session:
 * a second preview of the same session refreshes the panel that is already
 * open. The snapshot file is deleted when its panel closes or is refreshed.
 */
export function openDtaSnapshotPanel(
  extensionUri: vscode.Uri,
  log: (line: string) => void,
  options: { sessionId: string; file: string; subtitle: string },
): void {
  const source: ViewerSource = {
    uri: vscode.Uri.file(options.file),
    title: `Data — ${options.sessionId}`,
    subtitle: options.subtitle,
    canLoadInStata: false,
    canEditLabels: false,
  };
  const existing = snapshotPanels.get(options.sessionId);
  if (existing) {
    const previous = existing.file;
    existing.file = options.file;
    existing.session.setSource(source);
    existing.panel.reveal(undefined, true);
    void removeQuietly(previous);
    return;
  }

  const panel = vscode.window.createWebviewPanel(
    "stataCode.dataSnapshot",
    source.title,
    { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
    { enableScripts: true, retainContextWhenHidden: true },
  );
  const session = new DtaViewerSession(panel.webview, extensionUri, source, log);
  const entry: SnapshotPanel = { panel, session, file: options.file };
  snapshotPanels.set(options.sessionId, entry);
  panel.onDidDispose(() => {
    session.dispose();
    snapshotPanels.delete(options.sessionId);
    void removeQuietly(entry.file);
  });
}

/** Close every snapshot panel (and so delete its temp file) on deactivate. */
export function disposeDtaSnapshotPanels(): void {
  for (const entry of [...snapshotPanels.values()]) entry.panel.dispose();
}
