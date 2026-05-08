import * as vscode from "vscode";

import { computeRenameSkipMask, isCommentPrefix } from "./renameMask";
import type { RunResult } from "./types/runResult";

export const STATA_SECTION_RE = /^\s*\*{1,2}\s*(#{1,6})\s*(.*?)\s*$/;

const PROGRAM_DEFINE_RE =
  /^\s*(?:capture\s+)?program\s+(?:define\s+)?([A-Za-z_][A-Za-z0-9_]*)\b/i;
const PROGRAM_END_RE = /^\s*end\s*$/i;
const STATA_IDENTIFIER_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

const COMMON_STATA_COMMANDS = [
  "append",
  "areg",
  "assert",
  "browse",
  "bysort",
  "capture",
  "clear",
  "collapse",
  "compress",
  "correlate",
  "count",
  "describe",
  "destring",
  "display",
  "do",
  "drop",
  "duplicates",
  "egen",
  "encode",
  "estimates",
  "esttab",
  "export",
  "forvalues",
  "foreach",
  "generate",
  "graph",
  "gsort",
  "histogram",
  "import",
  "keep",
  "label",
  "levelsof",
  "list",
  "local",
  "log",
  "logit",
  "merge",
  "preserve",
  "probit",
  "putdocx",
  "putexcel",
  "regress",
  "rename",
  "replace",
  "reshape",
  "restore",
  "save",
  "scatter",
  "sort",
  "summarize",
  "sysuse",
  "tabstat",
  "tabulate",
  "test",
  "use",
  "version",
  "xtreg",
  "xtset",
];

const COMMON_STATA_FUNCTIONS = [
  "abs",
  "ceil",
  "cond",
  "cos",
  "date",
  "exp",
  "floor",
  "ln",
  "log",
  "max",
  "min",
  "missing",
  "mod",
  "normal",
  "real",
  "round",
  "runiform",
  "sin",
  "sqrt",
  "strtoname",
  "substr",
  "sum",
  "trim",
  "year",
];

export interface StataSectionRange {
  startLine: number;
  endLine: number;
  title: string;
  level: number;
}

interface ParsedHeading {
  title: string;
  level: number;
  line: number;
  endLine: number;
  range: vscode.Range;
  selectionRange: vscode.Range;
}

interface ParsedProgram {
  name: string;
  line: number;
  range: vscode.Range;
  selectionRange: vscode.Range;
}

interface HeadingSymbol {
  heading: ParsedHeading;
  symbol: vscode.DocumentSymbol;
}

export function headingAtLine(
  document: vscode.TextDocument,
  lineNumber: number,
): StataSectionRange | undefined {
  if (lineNumber < 0 || lineNumber >= document.lineCount) return undefined;
  const match = STATA_SECTION_RE.exec(document.lineAt(lineNumber).text);
  if (!match) return undefined;
  return {
    startLine: lineNumber,
    endLine: findSectionEnd(document, lineNumber, match[1].length),
    title: cleanSectionTitle(match[2], lineNumber),
    level: match[1].length,
  };
}

export function sectionRangeAtLine(
  document: vscode.TextDocument,
  lineNumber: number,
): StataSectionRange {
  const safeLine = Math.max(0, Math.min(lineNumber, document.lineCount - 1));
  for (let i = safeLine; i >= 0; i--) {
    const match = STATA_SECTION_RE.exec(document.lineAt(i).text);
    if (match) {
      return {
        startLine: i,
        endLine: findSectionEnd(document, i, match[1].length),
        title: cleanSectionTitle(match[2], i),
        level: match[1].length,
      };
    }
  }

  let endLine = document.lineCount - 1;
  for (let i = 0; i < document.lineCount; i++) {
    if (STATA_SECTION_RE.test(document.lineAt(i).text)) {
      endLine = Math.max(0, i - 1);
      break;
    }
  }
  return {
    startLine: 0,
    endLine,
    title: "preamble",
    level: 0,
  };
}

export class StataSectionSymbolProvider implements vscode.DocumentSymbolProvider {
  provideDocumentSymbols(document: vscode.TextDocument): vscode.DocumentSymbol[] {
    const headings = parseHeadings(document);
    const programs = parsePrograms(document);
    const roots: vscode.DocumentSymbol[] = [];
    const stack: HeadingSymbol[] = [];
    const indexed: HeadingSymbol[] = [];

    for (const heading of headings) {
      const symbol = new vscode.DocumentSymbol(
        heading.title,
        `section ${heading.level}`,
        vscode.SymbolKind.Namespace,
        heading.range,
        heading.selectionRange,
      );
      while (stack.length > 0 && stack[stack.length - 1].heading.level >= heading.level) {
        stack.pop();
      }
      if (stack.length === 0) {
        roots.push(symbol);
      } else {
        stack[stack.length - 1].symbol.children.push(symbol);
      }
      const entry = { heading, symbol };
      stack.push(entry);
      indexed.push(entry);
    }

    for (const program of programs) {
      const symbol = new vscode.DocumentSymbol(
        program.name,
        "program",
        vscode.SymbolKind.Function,
        program.range,
        program.selectionRange,
      );
      const parent = [...indexed]
        .reverse()
        .find(
          (entry) =>
            entry.heading.line < program.line && program.line <= entry.heading.endLine,
        );
      if (parent) {
        parent.symbol.children.push(symbol);
      } else {
        roots.push(symbol);
      }
    }

    return roots;
  }
}

export class StataSectionCodeLensProvider implements vscode.CodeLensProvider {
  private readonly changed = new vscode.EventEmitter<void>();
  readonly onDidChangeCodeLenses = this.changed.event;

  refresh(): void {
    this.changed.fire();
  }

  provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
    return parseHeadings(document).map((heading) => {
      const label =
        heading.title === `section ${heading.line + 1}`
          ? "Run Section"
          : `Run Section: ${heading.title}`;
      return new vscode.CodeLens(heading.selectionRange, {
        title: `▶ ${label}`,
        command: "stataCode.runSection",
        arguments: [document.uri, heading.line],
      });
    });
  }
}

export class StataRenameProvider implements vscode.RenameProvider {
  constructor(private readonly getLastResult: () => RunResult | undefined) {}

  prepareRename(
    document: vscode.TextDocument,
    position: vscode.Position,
  ): vscode.Range | undefined {
    if (isCommentPrefix(document.lineAt(position.line).text)) return undefined;
    const range = identifierRangeAt(document, position);
    if (!range) return undefined;
    const word = document.getText(range);
    if (!isLikelyVariable(document, this.getLastResult(), word)) return undefined;
    if (isCommandOrKeyword(word)) return undefined;
    if (isOptionNameAtPosition(document, range)) return undefined;
    return range;
  }

  provideRenameEdits(
    document: vscode.TextDocument,
    position: vscode.Position,
    newName: string,
  ): vscode.WorkspaceEdit | undefined {
    if (!STATA_IDENTIFIER_RE.test(newName) || isCommandOrKeyword(newName)) {
      vscode.window.showErrorMessage(
        "stata-code: variable names must start with a letter or underscore and avoid Stata command names",
      );
      return undefined;
    }

    const range = identifierRangeAt(document, position);
    if (!range) return undefined;
    const oldName = document.getText(range);
    if (oldName === newName) return undefined;

    const edit = new vscode.WorkspaceEdit();
    const pattern = new RegExp(`\\b${escapeRegExp(oldName)}\\b`, "g");
    let blockOpen = false;
    for (let i = 0; i < document.lineCount; i++) {
      const line = document.lineAt(i);
      const skip = computeRenameSkipMask(line.text, blockOpen);
      blockOpen = skip.blockOpen;
      if (skip.skipWholeLine) continue;
      for (const match of line.text.matchAll(pattern)) {
        if (match.index === undefined) continue;
        const matchStart = match.index;
        const matchEnd = matchStart + oldName.length;
        if (skip.ranges.some(([s, e]) => matchStart < e && matchEnd > s)) {
          continue;
        }
        edit.replace(
          document.uri,
          new vscode.Range(
            new vscode.Position(i, matchStart),
            new vscode.Position(i, matchEnd),
          ),
          newName,
        );
      }
    }
    return edit;
  }
}

export class StataCompletionProvider implements vscode.CompletionItemProvider {
  constructor(private readonly getLastResult: () => RunResult | undefined) {}

  provideCompletionItems(
    document: vscode.TextDocument,
    position: vscode.Position,
  ): vscode.CompletionItem[] {
    const prefix = document.getText(
      new vscode.Range(new vscode.Position(position.line, 0), position),
    );
    if (isCommentPrefix(prefix)) return [];

    const items: vscode.CompletionItem[] = [];
    for (const command of completionWords()) {
      const item = new vscode.CompletionItem(command, vscode.CompletionItemKind.Keyword);
      item.detail = "Stata command";
      items.push(item);
    }
    for (const fn of COMMON_STATA_FUNCTIONS) {
      const item = new vscode.CompletionItem(fn, vscode.CompletionItemKind.Function);
      item.detail = "Stata function";
      items.push(item);
    }
    for (const variable of variableCandidates(document, this.getLastResult())) {
      const item = new vscode.CompletionItem(variable, vscode.CompletionItemKind.Variable);
      item.detail = "Stata variable";
      items.push(item);
    }
    return items;
  }
}

export async function insertStataContinuation(): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showWarningMessage("stata-code: no active editor");
    return;
  }
  const position = editor.selection.active;
  const line = editor.document.lineAt(position.line);
  const before = line.text.slice(0, position.character).trimEnd();
  const after = line.text.slice(position.character).trimStart();
  const baseIndent = line.text.match(/^\s*/)?.[0] ?? "";
  const continuationIndent = previousLineContinues(editor.document, position.line)
    ? baseIndent
    : `${baseIndent}    `;
  const replacement = `${before} ///\n${continuationIndent}${after}`;

  const ok = await editor.edit((builder) => builder.replace(line.range, replacement));
  if (ok) {
    const next = new vscode.Position(position.line + 1, continuationIndent.length);
    editor.selection = new vscode.Selection(next, next);
  }
}

export async function openStataHelpForSelection(): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showWarningMessage("stata-code: no active editor");
    return;
  }
  const topic = selectedIdentifier(editor) ?? identifierAtCursor(editor);
  if (!topic) {
    vscode.window.showWarningMessage("stata-code: select a Stata command or place the cursor on one");
    return;
  }
  await vscode.env.openExternal(
    vscode.Uri.parse(`https://www.stata.com/help.cgi?${encodeURIComponent(topic)}`),
  );
}

function parseHeadings(document: vscode.TextDocument): ParsedHeading[] {
  const headings: ParsedHeading[] = [];
  for (let i = 0; i < document.lineCount; i++) {
    const line = document.lineAt(i);
    const match = STATA_SECTION_RE.exec(line.text);
    if (!match) continue;
    const level = match[1].length;
    const endLine = findSectionEnd(document, i, level);
    const range = new vscode.Range(
      i,
      0,
      endLine,
      document.lineAt(endLine).text.length,
    );
    headings.push({
      title: cleanSectionTitle(match[2], i),
      level,
      line: i,
      endLine,
      range,
      selectionRange: line.range,
    });
  }
  return headings;
}

function parsePrograms(document: vscode.TextDocument): ParsedProgram[] {
  const programs: ParsedProgram[] = [];
  for (let i = 0; i < document.lineCount; i++) {
    const line = document.lineAt(i);
    const match = PROGRAM_DEFINE_RE.exec(line.text);
    if (!match) continue;
    let endLine = i;
    for (let j = i + 1; j < document.lineCount; j++) {
      if (PROGRAM_END_RE.test(document.lineAt(j).text)) {
        endLine = j;
        break;
      }
    }
    programs.push({
      name: match[1],
      line: i,
      selectionRange: line.range,
      range: new vscode.Range(i, 0, endLine, document.lineAt(endLine).text.length),
    });
  }
  return programs;
}

function findSectionEnd(
  document: vscode.TextDocument,
  startLine: number,
  level: number,
): number {
  let endLine = document.lineCount - 1;
  for (let i = startLine + 1; i < document.lineCount; i++) {
    const match = STATA_SECTION_RE.exec(document.lineAt(i).text);
    if (match && match[1].length <= level) {
      endLine = i - 1;
      break;
    }
  }
  return Math.max(startLine, endLine);
}

function cleanSectionTitle(raw: string, line: number): string {
  // Strip decorative borders (e.g. ``=== Title ===``, ``--- Title ---``) but
  // keep numeric prefixes like ``1.2 Title`` — users often number sections
  // intentionally and the outline should preserve their hierarchy markers.
  const withoutDecor = raw
    .trim()
    .replace(/^[\s=*_#.-]+/, "")
    .replace(/[\s=*_#.-]+$/, "")
    .trim();
  return withoutDecor || `section ${line + 1}`;
}

function completionWords(): string[] {
  const cfg = vscode.workspace.getConfiguration("stataCode");
  const custom = cfg
    .get<string[]>("customCommands", ["reghdfe", "ivreghdfe", "gtools", "winsor2", "outreg2"])
    .filter((word) => STATA_IDENTIFIER_RE.test(word));
  return Array.from(new Set([...COMMON_STATA_COMMANDS, ...custom])).sort();
}

function variableCandidates(
  document: vscode.TextDocument,
  lastResult: RunResult | undefined,
): string[] {
  const variables = new Set<string>();
  for (const variable of lastResult?.dataset?.variables ?? []) {
    if (STATA_IDENTIFIER_RE.test(variable.name)) variables.add(variable.name);
  }
  for (let i = 0; i < document.lineCount; i++) {
    const text = document.lineAt(i).text;
    if (isCommentPrefix(text)) continue;
    for (const pattern of [
      /\b(?:gen|generate|egen|tempvar)\s+([A-Za-z_][A-Za-z0-9_]*)\b/gi,
      /\brename\s+([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z_][A-Za-z0-9_]*)\b/gi,
    ]) {
      for (const match of text.matchAll(pattern)) {
        for (const name of match.slice(1)) {
          if (STATA_IDENTIFIER_RE.test(name)) variables.add(name);
        }
      }
    }
  }
  return Array.from(variables).sort();
}

function isLikelyVariable(
  document: vscode.TextDocument,
  lastResult: RunResult | undefined,
  word: string,
): boolean {
  return variableCandidates(document, lastResult).includes(word);
}

function isCommandOrKeyword(word: string): boolean {
  const lower = word.toLowerCase();
  return (
    COMMON_STATA_COMMANDS.includes(lower) ||
    COMMON_STATA_FUNCTIONS.includes(lower) ||
    ["if", "in", "using", "by", "bysort", "foreach", "forvalues", "while"].includes(lower)
  );
}

function isOptionNameAtPosition(
  document: vscode.TextDocument,
  range: vscode.Range,
): boolean {
  const textBefore = document.lineAt(range.start.line).text.slice(0, range.start.character);
  const textAfter = document.lineAt(range.end.line).text.slice(range.end.character);
  const trimmedBefore = textBefore.trimEnd();
  const trimmedAfter = textAfter.trimStart();
  return trimmedBefore.endsWith(",") || trimmedBefore.endsWith("/") || trimmedAfter.startsWith("(");
}

function identifierRangeAt(
  document: vscode.TextDocument,
  position: vscode.Position,
): vscode.Range | undefined {
  return document.getWordRangeAtPosition(position, /[A-Za-z_][A-Za-z0-9_]*/);
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function previousLineContinues(document: vscode.TextDocument, line: number): boolean {
  for (let i = line - 1; i >= 0; i--) {
    const text = document.lineAt(i).text.trim();
    if (!text) continue;
    return text.endsWith("///");
  }
  return false;
}

function selectedIdentifier(editor: vscode.TextEditor): string | undefined {
  if (editor.selection.isEmpty) return undefined;
  const text = editor.document.getText(editor.selection).trim();
  return STATA_IDENTIFIER_RE.test(text) ? text : undefined;
}

function identifierAtCursor(editor: vscode.TextEditor): string | undefined {
  const range = editor.document.getWordRangeAtPosition(
    editor.selection.active,
    /[A-Za-z_][A-Za-z0-9_]*/,
  );
  if (!range) return undefined;
  const text = editor.document.getText(range);
  return STATA_IDENTIFIER_RE.test(text) ? text : undefined;
}

