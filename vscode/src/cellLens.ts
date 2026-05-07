// Code lens provider that recognizes `* %%` cell delimiters in .do files.
//
// Convention: a line whose trimmed text starts with `* %%` (the Stata
// line-comment prefix `*` followed by ` %%`) marks the start of a cell.
// Optional title text follows: `* %% setup` → cell label "setup".
// Each marker gets a "▶ Run Cell [: title]" code lens above it. Clicking
// invokes `stataCode.runCell` with the document URI and the marker line.

import * as vscode from "vscode";

export const CELL_MARKER_RE = /^\s*\*\s*%%\s*(.*?)\s*$/;

export class CellCodeLensProvider implements vscode.CodeLensProvider {
  private readonly _changed = new vscode.EventEmitter<void>();
  readonly onDidChangeCodeLenses = this._changed.event;

  refresh(): void {
    this._changed.fire();
  }

  provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
    const lenses: vscode.CodeLens[] = [];
    for (let i = 0; i < document.lineCount; i++) {
      const line = document.lineAt(i);
      const m = CELL_MARKER_RE.exec(line.text);
      if (!m) continue;
      const title = m[1] ? `▶ Run Cell: ${m[1]}` : "▶ Run Cell";
      const lens = new vscode.CodeLens(line.range, {
        title,
        command: "stataCode.runCell",
        arguments: [document.uri, i],
      });
      lenses.push(lens);
    }
    return lenses;
  }
}

// Returns the [startLine, endLine] (inclusive, 0-based) range of the cell
// that *contains* the given marker line. The cell's code starts on the
// line AFTER the marker and ends at the line BEFORE the next marker (or
// at EOF). Returns null if the marker is the last line of the document
// (no cell content).
export function cellRangeAtMarker(
  document: vscode.TextDocument,
  markerLine: number,
): { startLine: number; endLine: number } | null {
  const start = markerLine + 1;
  if (start >= document.lineCount) return null;
  let end = document.lineCount - 1;
  for (let i = start; i < document.lineCount; i++) {
    if (CELL_MARKER_RE.test(document.lineAt(i).text)) {
      end = i - 1;
      break;
    }
  }
  return { startLine: start, endLine: end };
}
