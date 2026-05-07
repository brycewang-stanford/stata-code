// Entry point for the stata_code VSCode extension.
//
// Activates on Stata files (`.do` / `.ado` / `.mata`) and registers three
// commands. The MCP client is lazily initialized on first use; it speaks
// to a `stata-code-mcp` child process over stdio. All Stata interaction
// goes through that channel — this extension is a thin transport.

import * as vscode from "vscode";

import { StataMcpClient } from "./mcpClient";
import type { RunResult } from "./types/runResult";

let client: StataMcpClient | undefined;
let output: vscode.OutputChannel | undefined;
let lastResult: RunResult | undefined;

export function activate(context: vscode.ExtensionContext): void {
  output = vscode.window.createOutputChannel("stata_code");
  context.subscriptions.push(output);

  context.subscriptions.push(
    vscode.commands.registerCommand("stataCode.runSelection", () =>
      runSelection(false),
    ),
    vscode.commands.registerCommand("stataCode.runFile", () => runSelection(true)),
    vscode.commands.registerCommand("stataCode.showLastResult", showLastResult),
  );
}

export function deactivate(): void {
  client?.dispose();
  client = undefined;
}

function getClient(): StataMcpClient {
  if (client) return client;
  if (!output) throw new Error("output channel not initialized");

  const cfg = vscode.workspace.getConfiguration("stataCode");
  const command = cfg.get<string>("serverCommand", "stata-code-mcp");
  const args = cfg.get<string[]>("serverArgs", []);
  client = new StataMcpClient(command, args, output);
  return client;
}

async function runSelection(wholeFile: boolean): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showWarningMessage("stata_code: no active editor");
    return;
  }
  const code = wholeFile
    ? editor.document.getText()
    : editor.selection.isEmpty
      ? editor.document.lineAt(editor.selection.active.line).text
      : editor.document.getText(editor.selection);

  if (!code.trim()) {
    vscode.window.showWarningMessage("stata_code: nothing to run");
    return;
  }

  const cfg = vscode.workspace.getConfiguration("stataCode");
  const sessionId = cfg.get<string>("sessionId", "main");
  const includeFullLog = cfg.get<boolean>("includeFullLog", false);

  const c = getClient();
  output!.show(true);
  output!.appendLine(
    `[stata_code] run (session=${sessionId}, lines=${code.split("\n").length})`,
  );

  await vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: "stata_code: running…",
      cancellable: false,
    },
    async () => {
      try {
        const result = await c.runStata(code, { sessionId, includeFullLog });
        lastResult = result;
        renderResult(result);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        output!.appendLine(`[stata_code] error: ${msg}`);
        vscode.window.showErrorMessage(`stata_code: ${msg}`);
      }
    },
  );
}

function renderResult(r: RunResult): void {
  if (!output) return;

  if (r.ok) {
    output.appendLine(
      `[stata_code] ok rc=${r.rc} elapsed=${r.elapsed_ms}ms session=${r.session_id}`,
    );
  } else {
    const err = r.error;
    output.appendLine(
      `[stata_code] FAIL rc=${r.rc} kind=${err?.kind ?? "?"} line=${err?.line ?? "?"}`,
    );
    if (err?.message) output.appendLine(`  message: ${err.message}`);
    if (err?.context?.failing) {
      output.appendLine(`  failing: ${err.context.failing}`);
    }
    for (const s of err?.suggestions ?? []) {
      output.appendLine(`  hint: ${s.action}`);
    }
  }

  if (r.log.head) {
    output.appendLine("--- log head ---");
    output.appendLine(r.log.head);
  }
  if (r.log.truncated && r.log.tail) {
    output.appendLine(`... (truncated; ${r.log.lines_total} lines total) ...`);
    output.appendLine(r.log.tail);
  }

  for (const w of r.warnings) {
    output.appendLine(`[warn:${w.kind}] ${w.message}`);
  }

  if (r.graphs.length > 0) {
    output.appendLine(
      `[stata_code] ${r.graphs.length} graph(s) captured. Use the Show Last Result command to render.`,
    );
  }
}

async function showLastResult(): Promise<void> {
  if (!lastResult) {
    vscode.window.showInformationMessage("stata_code: no result yet");
    return;
  }
  const doc = await vscode.workspace.openTextDocument({
    language: "json",
    content: JSON.stringify(lastResult, null, 2),
  });
  await vscode.window.showTextDocument(doc, { preview: true });
}
