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
// Security note: webview HTML uses a strict CSP that only allows data: URIs
// for images. No script execution, no remote loads. The base64 graph bytes
// come from the local pystata process via the MCP client, not from the web.

import * as vscode from "vscode";

import type { StataMcpClient } from "./mcpClient";
import type { GraphFormat, GraphInfo, RunResult } from "./types/runResult";

const MIME_BY_FORMAT: Record<GraphFormat, string> = {
  png: "image/png",
  svg: "image/svg+xml",
  pdf: "application/pdf",
};

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
        "stata_code: Graphs",
        { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
        { enableScripts: false, retainContextWhenHidden: true },
      );
      GraphPanel.instance = new GraphPanel(panel);
      panel.onDidDispose(() => {
        GraphPanel.instance = undefined;
      });
    } else {
      GraphPanel.instance.panel.reveal(vscode.ViewColumn.Beside, true);
    }

    await GraphPanel.instance.render(client, result, output);
  }

  private constructor(private readonly panel: vscode.WebviewPanel) {}

  private async render(
    client: StataMcpClient,
    result: RunResult,
    output: vscode.OutputChannel,
  ): Promise<void> {
    // Fetch each graph's bytes. If a graph already came back inline (because
    // the run requested include_graphs="inline"), skip the round-trip.
    const sections: string[] = [];
    for (const g of result.graphs) {
      try {
        const { data, mimeType } = g.inline
          ? { data: g.inline, mimeType: MIME_BY_FORMAT[g.format] }
          : await client.getGraphBytes(g.ref);
        sections.push(renderGraphSection(g, data, mimeType));
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        output.appendLine(`[stata_code] failed to fetch ${g.ref}: ${msg}`);
        sections.push(renderErrorSection(g, msg));
      }
    }

    this.panel.webview.html = renderHtml(result, sections);
  }
}

function renderGraphSection(g: GraphInfo, b64: string, mime: string): string {
  const dataUri = `data:${mime};base64,${b64}`;
  const meta = [
    g.name && `name=${escapeHtml(g.name)}`,
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
      ? `<object data="${dataUri}" type="${mime}" width="100%" height="600"></object>`
      : `<img src="${dataUri}" alt="${escapeHtml(g.name)}" />`;

  return `<section><h2>${escapeHtml(g.name)}</h2><p class="meta">${meta}</p>${body}</section>`;
}

function renderErrorSection(g: GraphInfo, msg: string): string {
  return `<section class="error"><h2>${escapeHtml(g.name)} (failed to fetch)</h2><p>${escapeHtml(msg)}</p></section>`;
}

function renderHtml(result: RunResult, sections: string[]): string {
  const cspMeta = [
    "default-src 'none'",
    "img-src data:",
    "object-src data:",
    "style-src 'unsafe-inline'",
  ].join("; ");

  const heading = `${result.graphs.length} graph${result.graphs.length === 1 ? "" : "s"} from session "${escapeHtml(result.session_id)}"`;

  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta http-equiv="Content-Security-Policy" content="${cspMeta}" />
    <title>stata_code Graphs</title>
    <style>
      body { font-family: var(--vscode-font-family); padding: 1rem; color: var(--vscode-foreground); }
      h1 { font-size: 1.1rem; margin: 0 0 1rem; opacity: 0.7; }
      h2 { font-size: 0.95rem; margin: 0 0 0.25rem; }
      section { margin-bottom: 2rem; padding-bottom: 1rem; border-bottom: 1px solid var(--vscode-panel-border); }
      section.error { color: var(--vscode-errorForeground); }
      img { max-width: 100%; height: auto; background: white; }
      .meta { font-size: 0.8rem; opacity: 0.7; margin: 0 0 0.5rem; }
      code { font-family: var(--vscode-editor-font-family); }
    </style>
  </head>
  <body>
    <h1>${heading}</h1>
    ${sections.join("\n")}
  </body>
</html>`;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
