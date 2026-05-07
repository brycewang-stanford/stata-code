// Status bar item for stata_code.
//
// One left-aligned item shows the current session. While a run is in flight
// the icon swaps to a spinner and the tooltip mentions cancellation. Clicking
// the item opens a QuickPick with session/cancel/reset actions.

import * as vscode from "vscode";

export class StataStatusBar implements vscode.Disposable {
  private readonly item: vscode.StatusBarItem;
  private running = false;

  constructor(commandId: string) {
    this.item = vscode.window.createStatusBarItem(
      vscode.StatusBarAlignment.Left,
      100,
    );
    this.item.command = commandId;
    this.refresh();
    this.item.show();
  }

  setRunning(running: boolean): void {
    this.running = running;
    this.refresh();
  }

  refresh(): void {
    const sid = currentSessionId();
    if (this.running) {
      this.item.text = `$(sync~spin) Stata: ${sid}`;
      this.item.tooltip = `stata-code: running in session "${sid}". Click for actions (cancel / switch).`;
    } else {
      this.item.text = `$(database) Stata: ${sid}`;
      this.item.tooltip = `stata-code: session "${sid}". Click for actions (switch / cancel / reset).`;
    }
  }

  dispose(): void {
    this.item.dispose();
  }
}

export function currentSessionId(): string {
  return vscode.workspace
    .getConfiguration("stataCode")
    .get<string>("sessionId", "main");
}
