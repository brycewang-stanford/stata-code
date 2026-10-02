// VS Code glue for the Stata .dta viewer.
//
// Two entry points share one webview implementation:
//   - a read-only custom editor, so opening any *.dta file shows the data;
//   - `openDtaSnapshotPanel`, used by "View data preview" to show a snapshot
//     of the data a Stata session holds in memory.
//
// Parsing and formatting live in dtaReader / dtaFormat / dtaViewModel, which
// are unit-tested without VS Code; this file only moves messages.

import { promises as fs } from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";

import { openFileByteSource } from "./dtaFileSource";
import { BufferByteSource, DtaFormatError, DtaReader, type ByteSource } from "./dtaReader";
import { buildDtaViewerHtml } from "./dtaViewerHtml";
import { buildViewerInit, formatRows } from "./dtaViewModel";

export const DTA_VIEW_TYPE = "stataCode.dtaViewer";
const RELOAD_DEBOUNCE_MS = 300;

interface ViewerSource {
  uri: vscode.Uri;
  title: string;
  subtitle?: string;
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
  private source: ViewerSource;
  private ready = false;
  private loadSeq = 0;
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
      this.reader = reader;
      await this.webview.postMessage(
        buildViewerInit(reader.meta, { title: source.title, subtitle: source.subtitle }),
      );
    } catch (err) {
      if (seq !== this.loadSeq) return;
      this.reader = undefined;
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
      const block = await formatRows(reader, {
        start: message.start,
        count: message.count,
        firstColumn: message.firstColumn,
        endColumn: message.endColumn,
        useLabels: message.useLabels,
      });
      if (reader !== this.reader) return; // the file was reloaded meanwhile
      await this.webview.postMessage({ type: "rows", id: message.id, ...block });
    } catch (err) {
      const text = err instanceof Error ? err.message : String(err);
      this.log(`[stata-code] dta viewer: reading rows failed: ${text}`);
      // The file most likely changed under the reader; re-parse it.
      void this.load();
    }
  }

  dispose(): void {
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
      { uri: document.uri, title: path.basename(document.uri.path) },
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
