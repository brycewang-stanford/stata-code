// Map a failed RunResult back to a vscode.Diagnostic anchored at the
// failing line in the submitting document. Uses the cooperative
// `error.line` (1-based, relative to the submitted code) plus the base
// line offset captured at run time.

import * as vscode from "vscode";

import type { RunResult } from "./types/runResult";

export interface SubmitOrigin {
  uri: vscode.Uri;
  baseLine: number;
}

export class StataDiagnostics implements vscode.Disposable {
  private readonly collection: vscode.DiagnosticCollection;

  constructor() {
    this.collection = vscode.languages.createDiagnosticCollection("stata-code");
  }

  publish(origin: SubmitOrigin, result: RunResult): void {
    this.collection.delete(origin.uri);
    if (result.ok || !result.error) return;

    const err = result.error;
    const submittedLine = err.line ?? 1;
    const fileLine = origin.baseLine + (submittedLine - 1);

    const range = computeRange(origin.uri, fileLine);

    const parts = [
      err.message,
      err.context?.failing ? `failing: ${err.context.failing}` : "",
      ...err.suggestions.map((s) => `hint: ${s.action}`),
    ].filter((s) => s);
    const diagnostic = new vscode.Diagnostic(
      range,
      parts.join("\n"),
      vscode.DiagnosticSeverity.Error,
    );
    diagnostic.source = "stata-code";
    diagnostic.code = err.kind;
    this.collection.set(origin.uri, [diagnostic]);
  }

  clear(uri?: vscode.Uri): void {
    if (uri) this.collection.delete(uri);
    else this.collection.clear();
  }

  dispose(): void {
    this.collection.dispose();
  }
}

function computeRange(uri: vscode.Uri, fileLine: number): vscode.Range {
  const editor = vscode.window.visibleTextEditors.find(
    (e) => e.document.uri.toString() === uri.toString(),
  );
  if (editor) {
    const safeLine = Math.max(0, Math.min(fileLine, editor.document.lineCount - 1));
    const lineText = editor.document.lineAt(safeLine);
    return lineText.range;
  }
  return new vscode.Range(fileLine, 0, fileLine, 1024);
}
