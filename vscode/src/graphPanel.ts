// Webview panel that renders captured Stata graphs.
//
// Lifecycle:
// - One panel per workspace (singleton). show() creates it on first call,
//   reveals it on subsequent calls.
// - On each successful run with graphs, the extension calls
//   GraphPanel.show(client, runResult). The panel fetches graph bytes via
//   get_graph(ref) — never inlined in the original RunResult — and renders
//   them as <img> / <object> tags inside the webview HTML.
// - The panel survives across runs; new runs replace its content.
//
// Webview security:
// - Strict CSP with a per-render nonce on inline <script>.
// - default-src 'none'; img-src data:; object-src data:;
//   script-src 'nonce-…'; style-src 'unsafe-inline'.
// - Graph bytes come from the local pystata process via the MCP client,
//   not from the web. Save / open actions are postMessage'd back to the
//   extension host, which performs the FS / external-open via VSCode API.

import * as path from "node:path";
import * as vscode from "vscode";

import type { StataMcpClient } from "./mcpClient";
import type { GraphFormat, GraphInfo, RunResult } from "./types/runResult";

const MIME_BY_FORMAT: Record<GraphFormat, string> = {
  png: "image/png",
  svg: "image/svg+xml",
  pdf: "application/pdf",
};

const EXT_BY_FORMAT: Record<GraphFormat, string> = {
  png: "png",
  svg: "svg",
  pdf: "pdf",
};

interface SaveMessage {
  type: "save" | "openExternal";
  ref: string;
  name: string;
  format: GraphFormat;
}

interface RefreshMessage {
  type: "refresh";
}

type WebviewMessage = SaveMessage | RefreshMessage;

export class GraphPanel {
  private static instance: GraphPanel | undefined;

  static async show(
    client: StataMcpClient,
    result: RunResult,
    output: vscode.OutputChannel,
  ): Promise<void> {
    if (result.graphs.length === 0) return;

    if (!GraphPanel.instance) {
      const panel = vscode.window.createWebviewPanel(
        "stataCodeGraphs",
        "stata-code: Graphs",
        { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
        { enableScripts: true, retainContextWhenHidden: true },
      );
      GraphPanel.instance = new GraphPanel(panel, output);
      panel.onDidDispose(() => {
        GraphPanel.instance = undefined;
      });
    } else {
      GraphPanel.instance.panel.reveal(vscode.ViewColumn.Beside, true);
    }

    await GraphPanel.instance.render(client, result);
  }

  private currentClient: StataMcpClient | undefined;
  private currentResult: RunResult | undefined;

  private constructor(
    private readonly panel: vscode.WebviewPanel,
    private readonly output: vscode.OutputChannel,
  ) {
    this.panel.webview.onDidReceiveMessage((msg: WebviewMessage) => {
      void this.handleMessage(msg);
    });
  }

  private async render(client: StataMcpClient, result: RunResult): Promise<void> {
    this.currentClient = client;
    this.currentResult = result;

    const sections: string[] = [];
    for (const g of result.graphs) {
      try {
        const { data, mimeType } = g.inline
          ? { data: g.inline, mimeType: MIME_BY_FORMAT[g.format] }
          : await client.getGraphBytes(g.ref);
        sections.push(renderGraphSection(g, data, mimeType));
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.output.appendLine(`[stata-code] failed to fetch ${g.ref}: ${msg}`);
        sections.push(renderErrorSection(g, msg));
      }
    }

    this.panel.webview.html = renderHtml(result, sections);
  }

  private async handleMessage(msg: WebviewMessage): Promise<void> {
    if (msg.type === "refresh") {
      if (this.currentClient && this.currentResult) {
        await this.render(this.currentClient, this.currentResult);
      }
      return;
    }

    if (msg.type === "save" || msg.type === "openExternal") {
      const client = this.currentClient;
      if (!client) return;
      try {
        const { data } = await client.getGraphBytes(msg.ref);
        const bytes = Buffer.from(data, "base64");
        if (msg.type === "save") {
          await this.savePngLike(bytes, msg.name, msg.format);
        } else {
          await this.openExternal(bytes, msg.name, msg.format);
        }
      } catch (err) {
        const errMsg = err instanceof Error ? err.message : String(err);
        this.output.appendLine(`[stata-code] graph action failed: ${errMsg}`);
        vscode.window.showErrorMessage(`stata-code: ${errMsg}`);
      }
    }
  }

  private async savePngLike(
    bytes: Buffer,
    name: string,
    format: GraphFormat,
  ): Promise<void> {
    const ext = EXT_BY_FORMAT[format];
    const safeName = sanitizeFilename(name) || "graph";
    const defaultUri = vscode.workspace.workspaceFolders?.[0]
      ? vscode.Uri.joinPath(
          vscode.workspace.workspaceFolders[0].uri,
          `${safeName}.${ext}`,
        )
      : vscode.Uri.file(`${safeName}.${ext}`);
    const target = await vscode.window.showSaveDialog({
      defaultUri,
      filters: { [`${format.toUpperCase()} image`]: [ext] },
    });
    if (!target) return;
    await vscode.workspace.fs.writeFile(target, bytes);
    vscode.window.showInformationMessage(`stata-code: saved ${path.basename(target.fsPath)}`);
  }

  private async openExternal(
    bytes: Buffer,
    name: string,
    format: GraphFormat,
  ): Promise<void> {
    const ext = EXT_BY_FORMAT[format];
    const safeName = sanitizeFilename(name) || "graph";
    const tmpDir = vscode.Uri.file(require("node:os").tmpdir());
    const target = vscode.Uri.joinPath(tmpDir, `${safeName}-${Date.now()}.${ext}`);
    await vscode.workspace.fs.writeFile(target, bytes);
    await vscode.env.openExternal(target);
  }
}

function sanitizeFilename(name: string): string {
  return name.replace(/[^A-Za-z0-9._-]/g, "_").slice(0, 64);
}

function renderGraphSection(g: GraphInfo, b64: string, mime: string): string {
  const dataUri = `data:${mime};base64,${b64}`;
  const title = escapeHtml(g.name || "graph");
  const meta = [
    g.format,
    g.width && g.height ? `${g.width}×${g.height}` : null,
    g.source_command ? `cmd=<code>${escapeHtml(g.source_command)}</code>` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  // Use <object> for PDFs (some VSCode webview hosts won't render PDFs in
  // <img>); <img> for raster + svg.
  const body =
    g.format === "pdf"
      ? `<object class="graph-object" data="${dataUri}" type="${mime}"></object>`
      : `<img src="${dataUri}" alt="${title}" />`;

  const refAttr = escapeHtml(g.ref);
  const nameAttr = escapeHtml(g.name);
  const fmtAttr = g.format;
  const actions = `
        <div class="actions">
          <button class="btn" data-action="save"
                  data-ref="${refAttr}" data-name="${nameAttr}" data-fmt="${fmtAttr}">
            Save as...
          </button>
          <button class="btn" data-action="openExternal"
                  data-ref="${refAttr}" data-name="${nameAttr}" data-fmt="${fmtAttr}">
            Open externally
          </button>
        </div>`;

  return `
    <details class="graph-card" open>
      <summary>
        <span class="graph-title">${title}</span>
        <span class="meta">${meta}</span>
      </summary>
      ${actions}
      <div class="graph-canvas">${body}</div>
    </details>`;
}

function renderErrorSection(g: GraphInfo, msg: string): string {
  return `
    <details class="graph-card error" open>
      <summary>
        <span class="graph-title">${escapeHtml(g.name || "graph")} failed to fetch</span>
      </summary>
      <p>${escapeHtml(msg)}</p>
    </details>`;
}

function renderHtml(result: RunResult, sections: string[]): string {
  const nonce = makeNonce();
  const cspMeta = [
    "default-src 'none'",
    "img-src data:",
    "object-src data:",
    `script-src 'nonce-${nonce}'`,
    "style-src 'unsafe-inline'",
  ].join("; ");

  const heading = `${result.graphs.length} graph${result.graphs.length === 1 ? "" : "s"}`;
  const subheading = `session ${escapeHtml(result.session_id)} · run ${escapeHtml(result.request_id.slice(0, 10))}`;

  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta http-equiv="Content-Security-Policy" content="${cspMeta}" />
    <title>stata-code Graphs</title>
    <style>
      * { box-sizing: border-box; }
      body {
        margin: 0;
        background: var(--vscode-editor-background);
        color: var(--vscode-foreground);
        font-family: var(--vscode-font-family);
      }
      header {
        position: sticky;
        top: 0;
        z-index: 1;
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 1rem;
        padding: 0.85rem 1rem;
        border-bottom: 1px solid var(--vscode-panel-border);
        background: var(--vscode-sideBar-background, var(--vscode-editor-background));
      }
      h1 { font-size: 1rem; line-height: 1.25; margin: 0; font-weight: 600; }
      .subheading { margin-top: 0.15rem; font-size: 0.78rem; opacity: 0.7; }
      main { padding: 1rem; }
      .graphs {
        display: grid;
        gap: 0.85rem;
      }
      .graph-card {
        border: 1px solid var(--vscode-panel-border);
        border-radius: 6px;
        background: var(--vscode-editorWidget-background, var(--vscode-editor-background));
        overflow: hidden;
      }
      .graph-card[open] summary {
        border-bottom: 1px solid var(--vscode-panel-border);
      }
      .graph-card.error { color: var(--vscode-errorForeground); }
      summary {
        cursor: pointer;
        display: flex;
        align-items: baseline;
        gap: 0.75rem;
        padding: 0.65rem 0.75rem;
        user-select: none;
      }
      .graph-title { font-size: 0.92rem; font-weight: 600; }
      .meta { font-size: 0.76rem; opacity: 0.72; overflow-wrap: anywhere; }
      .actions {
        display: flex;
        gap: 0.5rem;
        flex-wrap: wrap;
        padding: 0.65rem 0.75rem;
      }
      .graph-canvas {
        overflow: auto;
        padding: 0.75rem;
        background: var(--vscode-editor-background);
      }
      img {
        display: block;
        max-width: 100%;
        height: auto;
        background: white;
      }
      .graph-object {
        display: block;
        width: 100%;
        height: 640px;
        border: 0;
        background: white;
      }
      button.btn {
        background: var(--vscode-button-secondaryBackground, var(--vscode-button-background));
        color: var(--vscode-button-secondaryForeground, var(--vscode-button-foreground));
        border: 1px solid var(--vscode-button-border, transparent);
        padding: 0.25rem 0.55rem;
        font-family: var(--vscode-font-family);
        font-size: 0.8rem;
        border-radius: 2px;
        cursor: pointer;
        white-space: nowrap;
      }
      button.btn:hover { background: var(--vscode-button-secondaryHoverBackground, var(--vscode-button-hoverBackground)); }
      code { font-family: var(--vscode-editor-font-family); }
    </style>
  </head>
  <body>
    <header>
      <div>
        <h1>${heading}</h1>
        <div class="subheading">${subheading}</div>
      </div>
      <button class="btn" id="refresh-all">Refresh</button>
    </header>
    <main class="graphs">
      ${sections.join("\n")}
    </main>
    <script nonce="${nonce}">
      (function () {
        const vscode = acquireVsCodeApi();
        document.addEventListener("click", (e) => {
          const t = e.target;
          if (!(t instanceof HTMLButtonElement)) return;
          if (t.id === "refresh-all") {
            vscode.postMessage({ type: "refresh" });
            return;
          }
          const action = t.dataset.action;
          const ref = t.dataset.ref;
          const name = t.dataset.name;
          const fmt = t.dataset.fmt;
          if (!action || !ref || name === undefined || !fmt) return;
          vscode.postMessage({ type: action, ref, name, format: fmt });
        });
      })();
    </script>
  </body>
</html>`;
}

function makeNonce(): string {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let s = "";
  for (let i = 0; i < 32; i++) s += chars[Math.floor(Math.random() * chars.length)];
  return s;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
