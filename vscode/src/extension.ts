// Entry point for the stata_code VSCode extension.
//
// The extension is a thin VS Code UI layer over the stata_code MCP server:
// it submits code, tracks lightweight local history, and fetches heavy logs
// or graphs lazily when the user asks for them.

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";

import { CellCodeLensProvider, cellRangeAtMarker } from "./cellLens";
import { StataDiagnostics, type SubmitOrigin } from "./diagnostics";
import { GraphPanel } from "./graphPanel";
import { StataMcpClient, type StataServerLaunch } from "./mcpClient";
import { StataStatusBar, currentSessionId } from "./statusBar";
import {
  GraphsHistoryProvider,
  LastResultProvider,
  LogsHistoryProvider,
  RunHistoryProvider,
  type GraphHistoryEntry,
  type LogHistoryEntry,
  type ResultStore,
  type RunHistoryEntry,
  type SessionStore,
  SessionsProvider,
} from "./treeProviders";
import type { GraphFormat, GraphInfo, Matrix, RunResult } from "./types/runResult";

const HISTORY_CAP = 64;
const DATA_PREVIEW_OBS = 100;
const SESSION_IDS_KEY = "stataCode.sessionIds";
const SESSION_ID_RE = /^[A-Za-z_][A-Za-z0-9_]{0,31}$/;
const DEFAULT_SERVER_COMMAND = "stata-code-mcp";

const EXT_BY_FORMAT: Record<GraphFormat, string> = {
  png: "png",
  svg: "svg",
  pdf: "pdf",
};

let extensionContext: vscode.ExtensionContext | undefined;
let client: StataMcpClient | undefined;
let output: vscode.OutputChannel | undefined;
let lastResult: RunResult | undefined;
let statusBar: StataStatusBar | undefined;
let knownSessionIds = new Set<string>(["main"]);

const graphsHistory: GraphHistoryEntry[] = [];
const logsHistory: LogHistoryEntry[] = [];
const runHistory: RunHistoryEntry[] = [];

let sessionsProvider: SessionsProvider | undefined;
let lastResultProvider: LastResultProvider | undefined;
let graphsHistoryProvider: GraphsHistoryProvider | undefined;
let logsHistoryProvider: LogsHistoryProvider | undefined;
let runHistoryProvider: RunHistoryProvider | undefined;
let diagnostics: StataDiagnostics | undefined;

export function activate(context: vscode.ExtensionContext): void {
  extensionContext = context;
  hydrateKnownSessions(context);

  output = vscode.window.createOutputChannel("stata-code");
  context.subscriptions.push(output);

  statusBar = new StataStatusBar("stataCode.statusBarMenu");
  context.subscriptions.push(statusBar);

  diagnostics = new StataDiagnostics();
  context.subscriptions.push(diagnostics);

  const cellLens = new CellCodeLensProvider();
  context.subscriptions.push(
    vscode.languages.registerCodeLensProvider({ language: "stata" }, cellLens),
  );

  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("stataCode.sessionId")) {
        rememberSessionId(currentSessionId());
        statusBar?.refresh();
        sessionsProvider?.refresh();
      }
      if (
        e.affectsConfiguration("stataCode.serverCommand") ||
        e.affectsConfiguration("stataCode.serverArgs")
      ) {
        client?.dispose();
        client = undefined;
        output?.appendLine("[stata-code] MCP client reset after server launch settings changed");
        sessionsProvider?.refresh();
      }
    }),
  );

  const resultStore: ResultStore = {
    getLastResult: () => lastResult,
    getRunHistory: () => runHistory,
    getGraphsHistory: () => graphsHistory,
    getLogsHistory: () => logsHistory,
  };
  const sessionStore: SessionStore = {
    getKnownSessionIds: () => Array.from(knownSessionIds),
  };

  sessionsProvider = new SessionsProvider(getClient, sessionStore);
  lastResultProvider = new LastResultProvider(resultStore);
  runHistoryProvider = new RunHistoryProvider(resultStore);
  graphsHistoryProvider = new GraphsHistoryProvider(resultStore);
  logsHistoryProvider = new LogsHistoryProvider(resultStore);
  context.subscriptions.push(
    vscode.window.registerTreeDataProvider("stataCode.sessions", sessionsProvider),
    vscode.window.registerTreeDataProvider("stataCode.lastResult", lastResultProvider),
    vscode.window.registerTreeDataProvider("stataCode.runHistory", runHistoryProvider),
    vscode.window.registerTreeDataProvider("stataCode.logsHistory", logsHistoryProvider),
    vscode.window.registerTreeDataProvider("stataCode.graphsHistory", graphsHistoryProvider),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("stataCode.runSelection", () =>
      runSelection(false),
    ),
    vscode.commands.registerCommand("stataCode.runFile", () => runSelection(true)),
    vscode.commands.registerCommand("stataCode.runCell", runCell),
    vscode.commands.registerCommand("stataCode.showLastResult", showLastResult),
    vscode.commands.registerCommand("stataCode.openRunResult", openRunResult),
    vscode.commands.registerCommand("stataCode.openMatrix", openMatrix),
    vscode.commands.registerCommand("stataCode.viewDataPreview", viewDataPreview),
    vscode.commands.registerCommand("stataCode.rerunHistory", rerunHistory),
    vscode.commands.registerCommand("stataCode.copyRunCode", copyRunCode),
    vscode.commands.registerCommand("stataCode.exportRunBundle", exportRunBundle),
    vscode.commands.registerCommand("stataCode.clearRunHistory", clearRunHistory),
    vscode.commands.registerCommand("stataCode.showGraphs", showGraphs),
    vscode.commands.registerCommand("stataCode.showOutput", showOutput),
    vscode.commands.registerCommand("stataCode.openLog", openLog),
    vscode.commands.registerCommand("stataCode.saveLog", saveLog),
    vscode.commands.registerCommand("stataCode.clearLogs", clearLogs),
    vscode.commands.registerCommand("stataCode.openGraph", openSingleGraph),
    vscode.commands.registerCommand("stataCode.saveGraph", saveGraph),
    vscode.commands.registerCommand("stataCode.clearGraphs", clearGraphs),
    vscode.commands.registerCommand("stataCode.statusBarMenu", statusBarMenu),
    vscode.commands.registerCommand("stataCode.switchSession", switchSession),
    vscode.commands.registerCommand("stataCode.newSession", newSession),
    vscode.commands.registerCommand("stataCode.closeSession", closeSession),
    vscode.commands.registerCommand("stataCode.cancelSession", cancelSession),
    vscode.commands.registerCommand("stataCode.resetSession", resetSession),
    vscode.commands.registerCommand("stataCode.workingDirectoryMenu", workingDirectoryMenu),
    vscode.commands.registerCommand("stataCode.showStataPwd", showStataPwd),
    vscode.commands.registerCommand("stataCode.cdToWorkspace", cdToWorkspace),
    vscode.commands.registerCommand("stataCode.cdToCurrentFile", cdToCurrentFile),
    vscode.commands.registerCommand("stataCode.chooseWorkingDirectory", chooseWorkingDirectory),
    vscode.commands.registerCommand("stataCode.refreshSessions", () =>
      sessionsProvider?.refresh(),
    ),
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
  const command = cfg.get<string>("serverCommand", DEFAULT_SERVER_COMMAND);
  const args = cfg.get<string[]>("serverArgs", []);
  client = new StataMcpClient(
    buildServerLaunchCandidates(command, args, extensionContext?.extensionUri.fsPath),
    output,
  );
  return client;
}

function buildServerLaunchCandidates(
  configuredCommand: string,
  configuredArgs: string[],
  extensionRoot: string | undefined,
): StataServerLaunch[] {
  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  const cwd = workspaceRoot;
  const sourceRoots = localSourceRoots(workspaceRoot, extensionRoot);
  const venvDirs = localVenvDirs(workspaceRoot, sourceRoots);
  const env = serverEnvironment(sourceRoots);
  const [command, inlineArgs] = normalizeConfiguredCommand(configuredCommand, configuredArgs);
  const args = inlineArgs;
  const usesDefaultCommand = command === DEFAULT_SERVER_COMMAND && args.length === 0;

  if (!usesDefaultCommand) {
    return [{ command, args, cwd, env }];
  }

  const candidates: StataServerLaunch[] = [];
  const add = (candidate: StataServerLaunch): void => {
    const key = `${candidate.command}\0${candidate.args.join("\0")}`;
    if (!candidates.some((existing) => `${existing.command}\0${existing.args.join("\0")}` === key)) {
      candidates.push(candidate);
    }
  };

  for (const venvDir of venvDirs) {
    const venvScript = venvExecutable(venvDir, DEFAULT_SERVER_COMMAND);
    if (fs.existsSync(venvScript)) {
      add({ command: venvScript, args: [], cwd, env });
    }
  }

  add({ command: DEFAULT_SERVER_COMMAND, args: [], cwd, env });

  for (const venvDir of venvDirs) {
    const venvPython = venvExecutable(venvDir, "python");
    if (fs.existsSync(venvPython)) {
      add({ command: venvPython, args: ["-m", "stata_code.mcp"], cwd, env });
    }
  }

  for (const pythonCommand of configuredPythonCommands()) {
    add({ command: pythonCommand, args: ["-m", "stata_code.mcp"], cwd, env });
  }

  for (const pythonCommand of commonPythonCommands()) {
    if (fs.existsSync(pythonCommand)) {
      add({ command: pythonCommand, args: ["-m", "stata_code.mcp"], cwd, env });
    }
  }

  if (process.platform === "win32") {
    add({ command: "py", args: ["-3", "-m", "stata_code.mcp"], cwd, env });
    add({ command: "python", args: ["-m", "stata_code.mcp"], cwd, env });
  } else {
    add({ command: "python3", args: ["-m", "stata_code.mcp"], cwd, env });
    add({ command: "python", args: ["-m", "stata_code.mcp"], cwd, env });
  }

  return candidates;
}

function normalizeConfiguredCommand(
  command: string,
  configuredArgs: string[],
): [string, string[]] {
  const raw = command.trim();
  if (!raw) {
    return [DEFAULT_SERVER_COMMAND, configuredArgs];
  }
  const expanded = expandHome(raw);
  if (!/\s/.test(expanded) || fs.existsSync(expanded)) {
    return [expanded, configuredArgs];
  }
  const parts = parseCommandLine(raw);
  if (parts.length === 0) return [expanded, configuredArgs];
  if (parts.length === 1) return [expandHome(parts[0]), configuredArgs];
  return [expandHome(parts[0]), [...parts.slice(1), ...configuredArgs]];
}

function parseCommandLine(value: string): string[] {
  const parts: string[] = [];
  let current = "";
  let quote: "'" | "\"" | undefined;
  let escaping = false;

  for (const ch of value) {
    if (escaping) {
      current += ch;
      escaping = false;
      continue;
    }
    if (ch === "\\") {
      escaping = true;
      continue;
    }
    if (quote) {
      if (ch === quote) {
        quote = undefined;
      } else {
        current += ch;
      }
      continue;
    }
    if (ch === "'" || ch === "\"") {
      quote = ch;
      continue;
    }
    if (/\s/.test(ch)) {
      if (current) {
        parts.push(current);
        current = "";
      }
      continue;
    }
    current += ch;
  }

  if (escaping) current += "\\";
  if (current) parts.push(current);
  return parts;
}

function serverEnvironment(sourceRoots: string[]): Record<string, string> {
  const env: Record<string, string> = {
    PATH: expandedPath(),
  };
  const pythonPath = uniqueStrings([...sourceRoots, ...splitPathList(process.env.PYTHONPATH)]);
  if (pythonPath.length > 0) {
    env.PYTHONPATH = pythonPath.join(path.delimiter);
  }
  return env;
}

function expandedPath(): string {
  const entries = [
    ...splitPathList(process.env.PATH),
    path.join(os.homedir(), ".local", "bin"),
    ...pythonUserScriptDirs(),
    "/opt/homebrew/bin",
    "/usr/local/bin",
  ].filter((entry): entry is string => Boolean(entry));
  return uniqueStrings(entries).join(path.delimiter);
}

function localSourceRoots(
  workspaceRoot: string | undefined,
  extensionRoot: string | undefined,
): string[] {
  const candidates = [
    workspaceRoot,
    workspaceRoot ? path.dirname(workspaceRoot) : undefined,
    extensionRoot,
    extensionRoot ? path.dirname(extensionRoot) : undefined,
  ].filter((entry): entry is string => Boolean(entry));
  return uniqueStrings(candidates).filter((root) =>
    fs.existsSync(path.join(root, "stata_code", "mcp", "__main__.py")),
  );
}

function localVenvDirs(
  workspaceRoot: string | undefined,
  sourceRoots: string[],
): string[] {
  const roots = [workspaceRoot, ...sourceRoots].filter((entry): entry is string =>
    Boolean(entry),
  );
  return uniqueStrings(roots.map((root) => path.join(root, ".venv")));
}

function splitPathList(value: string | undefined): string[] {
  return value?.split(path.delimiter).filter(Boolean) ?? [];
}

function uniqueStrings(values: string[]): string[] {
  return Array.from(new Set(values));
}

function pythonUserScriptDirs(): string[] {
  if (process.platform === "win32") return [];
  const versions = ["3.13", "3.12", "3.11", "3.10"];
  return versions.map((version) =>
    path.join(os.homedir(), "Library", "Python", version, "bin"),
  );
}

function configuredPythonCommands(): string[] {
  const cfg = vscode.workspace.getConfiguration("python");
  return [
    cfg.get<string>("defaultInterpreterPath"),
    cfg.get<string>("pythonPath"),
  ]
    .filter((value): value is string => Boolean(value?.trim()))
    .map((value) => expandHome(value.trim()));
}

function commonPythonCommands(): string[] {
  if (process.platform === "win32") return [];
  return [
    "/opt/homebrew/bin/python3",
    "/usr/local/bin/python3",
    "/opt/homebrew/bin/python3.13",
    "/usr/local/bin/python3.13",
    "/opt/homebrew/bin/python3.12",
    "/usr/local/bin/python3.12",
    "/opt/homebrew/bin/python3.11",
    "/usr/local/bin/python3.11",
    "/opt/homebrew/bin/python3.10",
    "/usr/local/bin/python3.10",
  ];
}

function venvExecutable(venvDir: string, name: string): string {
  if (process.platform === "win32") {
    const executable = name === "python" ? "python.exe" : `${name}.exe`;
    return path.join(venvDir, "Scripts", executable);
  }
  return path.join(venvDir, "bin", name);
}

function expandHome(value: string): string {
  if (value === "~") return os.homedir();
  if (value.startsWith(`~${path.sep}`)) {
    return path.join(os.homedir(), value.slice(2));
  }
  return value;
}

async function runSelection(wholeFile: boolean): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showWarningMessage("stata-code: no active editor");
    return;
  }

  let code: string;
  let baseLine: number;
  if (wholeFile) {
    code = editor.document.getText();
    baseLine = 0;
  } else if (editor.selection.isEmpty) {
    code = editor.document.lineAt(editor.selection.active.line).text;
    baseLine = editor.selection.active.line;
  } else {
    code = editor.document.getText(editor.selection);
    baseLine = editor.selection.start.line;
  }

  await submitCode(code, {
    uri: editor.document.uri,
    baseLine,
    kind: wholeFile ? "file" : editor.selection.isEmpty ? "line" : "selection",
  });
}

async function runCell(uri: vscode.Uri, markerLine: number): Promise<void> {
  const doc = await vscode.workspace.openTextDocument(uri);
  const rangeInfo = cellRangeAtMarker(doc, markerLine);
  if (!rangeInfo || rangeInfo.startLine > rangeInfo.endLine) {
    vscode.window.showWarningMessage("stata-code: empty cell");
    return;
  }

  const range = new vscode.Range(
    new vscode.Position(rangeInfo.startLine, 0),
    doc.lineAt(rangeInfo.endLine).range.end,
  );
  await submitCode(doc.getText(range), {
    uri,
    baseLine: rangeInfo.startLine,
    kind: "cell",
  });
}

async function submitCode(code: string, origin: SubmitOrigin): Promise<void> {
  if (!code.trim()) {
    vscode.window.showWarningMessage("stata-code: nothing to run");
    return;
  }

  diagnostics?.clear(origin.uri);

  const cfg = vscode.workspace.getConfiguration("stataCode");
  const sessionId = cfg.get<string>("sessionId", "main");
  const includeFullLog = cfg.get<boolean>("includeFullLog", false);
  const persistLogFiles = cfg.get<boolean>("persistLogFiles", true);
  const persistGeneratedFiles = cfg.get<boolean>("persistGeneratedFiles", true);
  const useOriginWorkdir = cfg.get<boolean>("useDoFileDirectory", true);
  const originLabel = formatOriginLabel(origin);
  const originPath = origin.uri.scheme === "file" ? origin.uri.fsPath : undefined;

  const c = getClient();
  output?.show(true);
  output?.appendLine(
    `[stata-code] run (session=${sessionId}, lines=${code.split("\n").length}, origin=${originLabel})`,
  );

  statusBar?.setRunning(true);
  await vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: "stata-code: running...",
      cancellable: true,
    },
    async (_progress, token) => {
      const cancelOnRequest = token.onCancellationRequested(() => {
        void c.cancelSession(sessionId).catch((err) => {
          const msg = err instanceof Error ? err.message : String(err);
          output?.appendLine(`[stata-code] cancel failed: ${msg}`);
        });
      });
      try {
        const result = await c.runStata(code, {
          sessionId,
          includeFullLog,
          persistLogFiles: persistLogFiles && originPath !== undefined,
          persistGeneratedFiles,
          originPath,
          originKind: origin.kind ?? "unknown",
          originLabel,
          useOriginWorkdir,
        });
        lastResult = result;
        rememberSessionId(result.session_id);
        recordRun(result, code, origin);
        recordLog(result);
        recordGraphs(result);
        diagnostics?.publish(origin, result);
        lastResultProvider?.refresh();
        runHistoryProvider?.refresh();
        logsHistoryProvider?.refresh();
        graphsHistoryProvider?.refresh();
        sessionsProvider?.refresh();
        renderResult(result);
        if (result.ok && result.graphs.length > 0) {
          await GraphPanel.show(c, result, output!);
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        output?.appendLine(`[stata-code] error: ${msg}`);
        vscode.window.showErrorMessage(`stata-code: ${msg}`);
      } finally {
        cancelOnRequest.dispose();
        statusBar?.setRunning(false);
      }
    },
  );
}

function recordRun(result: RunResult, code: string, origin: SubmitOrigin): void {
  runHistory.push({
    runId: result.request_id,
    ts: Date.now(),
    code,
    originUri: origin.uri,
    baseLine: origin.baseLine,
    originLabel: formatOriginLabel(origin),
    result,
  });
  trimHistory(runHistory);
}

function recordLog(result: RunResult): void {
  if (!result.log.head && !result.log.tail && !result.log.ref) return;
  logsHistory.push({ runId: result.request_id, ts: Date.now(), result });
  trimHistory(logsHistory);
}

function recordGraphs(result: RunResult): void {
  if (result.graphs.length === 0) return;
  const ts = Date.now();
  for (const graph of result.graphs) {
    graphsHistory.push({
      runId: result.request_id,
      ts,
      sessionId: result.session_id,
      graph,
    });
  }
  trimHistory(graphsHistory);
}

function trimHistory<T>(items: T[]): void {
  if (items.length > HISTORY_CAP) {
    items.splice(0, items.length - HISTORY_CAP);
  }
}

function renderResult(r: RunResult): void {
  if (!output) return;

  if (r.ok) {
    output.appendLine(
      `[stata-code] ok rc=${r.rc} elapsed=${r.elapsed_ms}ms session=${r.session_id}`,
    );
  } else {
    const err = r.error;
    output.appendLine(
      `[stata-code] FAIL rc=${r.rc} kind=${err?.kind ?? "?"} line=${err?.line ?? "?"}`,
    );
    if (err?.message) output.appendLine(`  message: ${err.message}`);
    if (err?.context?.failing) output.appendLine(`  failing: ${err.context.failing}`);
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
  if (r.log.ref) {
    output.appendLine("[stata-code] full log available from the Logs view.");
  }
  if (r.log.files) {
    output.appendLine(`[stata-code] log files saved: ${r.log.files.directory}`);
    if (r.log.files.working_dir) output.appendLine(`  pwd: ${r.log.files.working_dir}`);
    output.appendLine(`  log: ${r.log.files.log_path}`);
    output.appendLine(`  smcl: ${r.log.files.smcl_path}`);
    if (r.log.files.graph_paths?.length) {
      output.appendLine(`  graphs: ${r.log.files.graph_paths.length} saved`);
    }
    if (r.log.files.output_paths?.length) {
      output.appendLine(`  outputs: ${r.log.files.output_paths.length} copied`);
    }
  }

  for (const w of r.warnings) {
    output.appendLine(`[warn:${w.kind}] ${w.message}`);
  }

  if (r.graphs.length > 0) {
    output.appendLine(
      `[stata-code] ${r.graphs.length} graph(s) captured (see the Graphs view).`,
    );
  }
}

async function showGraphs(): Promise<void> {
  if (!lastResult) {
    vscode.window.showInformationMessage("stata-code: no result yet");
    return;
  }
  if (lastResult.graphs.length === 0) {
    vscode.window.showInformationMessage("stata-code: last run produced no graphs");
    return;
  }
  await GraphPanel.show(getClient(), lastResult, output!);
}

async function openSingleGraph(target?: unknown): Promise<void> {
  const entry = resolveGraphEntry(target);
  const graph = entry?.graph ?? (isGraphInfo(target) ? target : undefined);
  if (!graph) {
    vscode.window.showInformationMessage("stata-code: no graph selected");
    return;
  }
  const baseResult = entry?.runId
    ? findResultForRun(entry.runId) ?? lastResult
    : lastResult;
  if (!baseResult) {
    vscode.window.showInformationMessage("stata-code: graph history is empty");
    return;
  }

  const synthetic: RunResult = {
    ...baseResult,
    request_id: entry?.runId ?? baseResult.request_id,
    session_id: entry?.sessionId ?? baseResult.session_id,
    started_at: entry ? new Date(entry.ts).toISOString() : baseResult.started_at,
    graphs: [graph],
  };
  await GraphPanel.show(getClient(), synthetic, output!);
}

async function saveGraph(target?: unknown): Promise<void> {
  const entry = resolveGraphEntry(target);
  const graph = entry?.graph ?? (isGraphInfo(target) ? target : undefined);
  if (!graph) {
    vscode.window.showInformationMessage("stata-code: no graph selected");
    return;
  }

  try {
    const { data } = graph.inline
      ? { data: graph.inline }
      : await getClient().getGraphBytes(graph.ref);
    const bytes = Buffer.from(data, "base64");
    const ext = EXT_BY_FORMAT[graph.format];
    const defaultUri = defaultWorkspaceUri(`${sanitizeFilename(graph.name) || "graph"}.${ext}`);
    const targetUri = await vscode.window.showSaveDialog({
      defaultUri,
      filters: { [`${graph.format.toUpperCase()} image`]: [ext] },
    });
    if (!targetUri) return;
    await vscode.workspace.fs.writeFile(targetUri, bytes);
    vscode.window.showInformationMessage(
      `stata-code: saved ${path.basename(targetUri.fsPath)}`,
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    output?.appendLine(`[stata-code] save graph failed: ${msg}`);
    vscode.window.showErrorMessage(`stata-code: ${msg}`);
  }
}

function clearGraphs(): void {
  graphsHistory.splice(0);
  graphsHistoryProvider?.refresh();
}

async function showLastResult(): Promise<void> {
  if (!lastResult) {
    vscode.window.showInformationMessage("stata-code: no result yet");
    return;
  }
  await openRunResult(lastResult);
}

async function openRunResult(target?: unknown): Promise<void> {
  const result = resolveRunResult(target);
  if (!result) {
    vscode.window.showInformationMessage("stata-code: no result yet");
    return;
  }
  const doc = await vscode.workspace.openTextDocument({
    language: "json",
    content: JSON.stringify(result, null, 2),
  });
  await vscode.window.showTextDocument(doc, { preview: false });
}

async function openMatrix(target?: unknown): Promise<void> {
  const matrixTarget = resolveMatrixTarget(target);
  if (!matrixTarget?.matrix) {
    vscode.window.showInformationMessage("stata-code: no matrix selected");
    return;
  }

  try {
    const matrix = matrixTarget.matrix.ref
      ? { ...(await getClient().getMatrix(matrixTarget.matrix.ref)), ref: matrixTarget.matrix.ref }
      : matrixTarget.matrix;
    if (!matrix.values) {
      vscode.window.showInformationMessage("stata-code: matrix values are not available");
      return;
    }
    const doc = await vscode.workspace.openTextDocument({
      language: "plaintext",
      content: matrixToTsv(matrixTarget.name, matrix),
    });
    await vscode.window.showTextDocument(doc, {
      preview: false,
      viewColumn: vscode.ViewColumn.Beside,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    output?.appendLine(`[stata-code] open matrix failed: ${msg}`);
    vscode.window.showErrorMessage(`stata-code: ${msg}`);
  }
}

async function viewDataPreview(): Promise<void> {
  const result = await runUtilityCode(
    `list in 1/${DATA_PREVIEW_OBS}, clean noobs abbreviate(24)`,
    "view data preview",
  );
  if (!result) return;

  const doc = await vscode.workspace.openTextDocument({
    language: "plaintext",
    content: formatDataPreviewDocument(result, inlineLogText(result)),
  });
  await vscode.window.showTextDocument(doc, {
    preview: false,
    viewColumn: vscode.ViewColumn.Beside,
  });
}

async function rerunHistory(target?: unknown): Promise<void> {
  const entry = resolveRunHistoryEntry(target);
  if (!entry) {
    vscode.window.showInformationMessage("stata-code: no run selected");
    return;
  }
  rememberSessionId(entry.result.session_id);
  await setCurrentSession(entry.result.session_id);
  await submitCode(entry.code, { uri: entry.originUri, baseLine: entry.baseLine });
}

async function copyRunCode(target?: unknown): Promise<void> {
  const entry = resolveRunHistoryEntry(target);
  if (!entry) {
    vscode.window.showInformationMessage("stata-code: no run selected");
    return;
  }
  await vscode.env.clipboard.writeText(entry.code);
  vscode.window.showInformationMessage("stata-code: copied run code");
}

function clearRunHistory(): void {
  runHistory.splice(0);
  runHistoryProvider?.refresh();
}

async function exportRunBundle(target?: unknown): Promise<void> {
  const entry = resolveRunHistoryEntry(target) ?? runHistory[runHistory.length - 1];
  if (!entry) {
    vscode.window.showInformationMessage("stata-code: no run to export");
    return;
  }

  const picked = await vscode.window.showOpenDialog({
    canSelectFiles: false,
    canSelectFolders: true,
    canSelectMany: false,
    defaultUri: vscode.workspace.workspaceFolders?.[0]?.uri,
    openLabel: "Export Here",
    title: "Choose a folder for the Stata run bundle",
  });
  const parent = picked?.[0];
  if (!parent) return;

  const dir = vscode.Uri.joinPath(parent, bundleDirectoryName(entry));
  const graphFiles: Array<{ name: string; ref: string; ok: boolean; error?: string }> = [];
  const exportedGraphFiles: string[] = [];

  await vscode.workspace.fs.createDirectory(dir);
  await vscode.workspace.fs.writeFile(
    vscode.Uri.joinPath(dir, "code.do"),
    Buffer.from(entry.code, "utf8"),
  );
  await vscode.workspace.fs.writeFile(
    vscode.Uri.joinPath(dir, "result.json"),
    Buffer.from(JSON.stringify(entry.result, null, 2), "utf8"),
  );
  await vscode.workspace.fs.writeFile(
    vscode.Uri.joinPath(dir, "log.txt"),
    Buffer.from(formatLogDocument(entry.result, await getLogText(entry.result)), "utf8"),
  );

  if (entry.result.graphs.length) {
    const graphsDir = vscode.Uri.joinPath(dir, "graphs");
    await vscode.workspace.fs.createDirectory(graphsDir);
    for (const [index, graph] of entry.result.graphs.entries()) {
      const ext = EXT_BY_FORMAT[graph.format];
      const fileName = `${String(index + 1).padStart(2, "0")}-${sanitizeFilename(graph.name) || "graph"}.${ext}`;
      try {
        const { data } = graph.inline
          ? { data: graph.inline }
          : await getClient().getGraphBytes(graph.ref);
        await vscode.workspace.fs.writeFile(
          vscode.Uri.joinPath(graphsDir, fileName),
          Buffer.from(data, "base64"),
        );
        const manifestName = `graphs/${fileName}`;
        exportedGraphFiles.push(manifestName);
        graphFiles.push({ name: manifestName, ref: graph.ref, ok: true });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        graphFiles.push({
          name: `graphs/${fileName}`,
          ref: graph.ref,
          ok: false,
          error: message,
        });
      }
    }
  }

  const manifest = {
    exported_at: new Date().toISOString(),
    origin: entry.originLabel,
    run_id: entry.runId,
    session_id: entry.result.session_id,
    ok: entry.result.ok,
    rc: entry.result.rc,
    files: ["code.do", "log.txt", "result.json", ...exportedGraphFiles],
    graphs: graphFiles,
  };
  await vscode.workspace.fs.writeFile(
    vscode.Uri.joinPath(dir, "manifest.json"),
    Buffer.from(JSON.stringify(manifest, null, 2), "utf8"),
  );

  vscode.window.showInformationMessage(
    `stata-code: exported ${path.basename(dir.fsPath)}`,
  );
}

function showOutput(): void {
  output?.show(false);
}

async function openLog(target?: unknown): Promise<void> {
  const result = resolveLogResult(target);
  if (!result) {
    vscode.window.showInformationMessage("stata-code: no log yet");
    return;
  }

  const text = await getLogText(result);
  const doc = await vscode.workspace.openTextDocument({
    language: "plaintext",
    content: formatLogDocument(result, text),
  });
  await vscode.window.showTextDocument(doc, {
    preview: false,
    viewColumn: vscode.ViewColumn.Beside,
  });
}

async function saveLog(target?: unknown): Promise<void> {
  const result = resolveLogResult(target);
  if (!result) {
    vscode.window.showInformationMessage("stata-code: no log yet");
    return;
  }

  const text = formatLogDocument(result, await getLogText(result));
  const defaultUri = defaultWorkspaceUri(
    `stata-${sanitizeFilename(result.session_id)}-${shortRunId(result.request_id)}.log`,
  );
  const targetUri = await vscode.window.showSaveDialog({
    defaultUri,
    filters: { "Stata log": ["log", "txt"] },
  });
  if (!targetUri) return;

  await vscode.workspace.fs.writeFile(targetUri, Buffer.from(text, "utf8"));
  vscode.window.showInformationMessage(
    `stata-code: saved ${path.basename(targetUri.fsPath)}`,
  );
}

function clearLogs(): void {
  logsHistory.splice(0);
  logsHistoryProvider?.refresh();
}

async function getLogText(result: RunResult): Promise<string> {
  if (result.log.ref) {
    try {
      const full = await getClient().getLog(result.log.ref);
      return full.text;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      output?.appendLine(`[stata-code] get_log failed: ${msg}`);
      vscode.window.showWarningMessage(
        "stata-code: full log ref is unavailable; showing inline preview",
      );
    }
  }
  return inlineLogText(result);
}

function inlineLogText(result: RunResult): string {
  const chunks: string[] = [];
  if (result.log.head) chunks.push(result.log.head);
  if (result.log.truncated && result.log.tail && result.log.tail !== result.log.head) {
    chunks.push(`... (${result.log.lines_total} lines total; full log unavailable) ...`);
    chunks.push(result.log.tail);
  } else if (!result.log.head && result.log.tail) {
    chunks.push(result.log.tail);
  }
  return chunks.join("\n");
}

function formatLogDocument(result: RunResult, text: string): string {
  const status = result.ok ? "OK" : `FAIL rc=${result.rc}`;
  return [
    "stata-code log",
    `status: ${status}`,
    `session: ${result.session_id}`,
    `request: ${result.request_id}`,
    `started: ${result.started_at}`,
    `elapsed_ms: ${result.elapsed_ms}`,
    `lines: ${result.log.lines_total}`,
    ...(result.log.files
      ? [
          ...(result.log.files.working_dir ? [`working_dir: ${result.log.files.working_dir}`] : []),
          `log_file: ${result.log.files.log_path}`,
          `smcl_file: ${result.log.files.smcl_path}`,
          ...(result.log.files.graph_paths?.length
            ? [`graphs_dir: ${result.log.files.graphs_dir ?? ""}`]
            : []),
          ...(result.log.files.output_paths?.length
            ? [`outputs_dir: ${result.log.files.outputs_dir ?? ""}`]
            : []),
        ]
      : []),
    "",
    "-".repeat(72),
    text,
  ].join("\n");
}

function formatDataPreviewDocument(result: RunResult, text: string): string {
  const ds = result.dataset;
  const status = result.ok ? "OK" : `FAIL rc=${result.rc}`;
  const rowsShown = result.ok ? Math.min(ds.n_obs, DATA_PREVIEW_OBS) : 0;
  const body = text.trim() || (ds.n_vars === 0 ? "(no variables in memory)" : "(no preview output)");
  const variableLines = (ds.variables ?? []).map(
    (v) => `  ${v.name}\t${v.type}${v.label ? `\t${v.label}` : ""}`,
  );

  return [
    "stata-code data preview",
    `status: ${status}`,
    `session: ${result.session_id}`,
    `request: ${result.request_id}`,
    `dataset: ${ds.n_obs} obs · ${ds.n_vars} vars · frame ${ds.frame}`,
    ds.filename ? `file: ${ds.filename}` : "file: (memory)",
    `showing: ${rowsShown} of ${ds.n_obs} observations`,
    "",
    "-".repeat(72),
    body,
    "",
    "-".repeat(72),
    "variables",
    variableLines.length ? variableLines.join("\n") : "  (none)",
  ].join("\n");
}

async function statusBarMenu(): Promise<void> {
  const sid = currentSessionId();
  const items = [
    { label: "$(add) New Stata tab...", action: "new" as const },
    { label: "$(arrow-swap) Switch tab...", action: "switch" as const },
    { label: "$(table) View data preview", action: "data" as const },
    { label: "$(archive) Export latest run", action: "export" as const },
    { label: "$(output) Open latest log", action: "log" as const },
    { label: "$(graph) Show latest graphs", action: "graphs" as const },
    { label: "$(folder-opened) Working directory...", action: "cwd" as const },
    { label: "$(terminal) Show output", action: "output" as const },
    { label: `$(stop-circle) Cancel "${sid}"`, action: "cancel" as const },
    { label: `$(trash) Reset "${sid}"`, action: "reset" as const },
    { label: `$(close) Close tab "${sid}"`, action: "close" as const },
  ];

  const pick = await vscode.window.showQuickPick(items, {
    placeHolder: `stata-code: current tab "${sid}"`,
  });
  if (!pick) return;

  if (pick.action === "new") return newSession();
  if (pick.action === "switch") return switchSession();
  if (pick.action === "data") return viewDataPreview();
  if (pick.action === "export") return exportRunBundle();
  if (pick.action === "log") return openLog();
  if (pick.action === "graphs") return showGraphs();
  if (pick.action === "cwd") return workingDirectoryMenu();
  if (pick.action === "output") return showOutput();
  if (pick.action === "cancel") return cancelSession();
  if (pick.action === "reset") return resetSession();
  if (pick.action === "close") return closeSession();
}

async function workingDirectoryMenu(): Promise<void> {
  const items = [
    { label: "$(terminal) Show current Stata directory", action: "pwd" as const },
    { label: "$(root-folder) cd to workspace folder", action: "workspace" as const },
    { label: "$(file-directory) cd to current file folder", action: "file" as const },
    { label: "$(folder-opened) Choose folder...", action: "choose" as const },
  ];
  const pick = await vscode.window.showQuickPick(items, {
    placeHolder: `Stata working directory · session "${currentSessionId()}"`,
  });
  if (!pick) return;
  if (pick.action === "pwd") return showStataPwd();
  if (pick.action === "workspace") return cdToWorkspace();
  if (pick.action === "file") return cdToCurrentFile();
  if (pick.action === "choose") return chooseWorkingDirectory();
}

async function showStataPwd(): Promise<void> {
  const result = await runUtilityCode("pwd", "show working directory");
  if (!result) return;
  const log = inlineLogText(result).trim();
  vscode.window.showInformationMessage(log || "stata-code: pwd returned no output");
}

async function cdToWorkspace(): Promise<void> {
  const folders = vscode.workspace.workspaceFolders ?? [];
  if (folders.length === 0) {
    vscode.window.showInformationMessage("stata-code: no workspace folder is open");
    return;
  }

  let folder = folders[0];
  if (folders.length > 1) {
    const pick = await vscode.window.showQuickPick(
      folders.map((f) => ({ label: f.name, description: f.uri.fsPath, folder: f })),
      { placeHolder: "Choose workspace folder for Stata cd" },
    );
    if (!pick) return;
    folder = pick.folder;
  }
  await cdToDirectory(folder.uri);
}

async function cdToCurrentFile(): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor || editor.document.uri.scheme !== "file") {
    vscode.window.showInformationMessage("stata-code: no file-backed editor is active");
    return;
  }
  await cdToDirectory(vscode.Uri.file(path.dirname(editor.document.uri.fsPath)));
}

async function chooseWorkingDirectory(): Promise<void> {
  const picked = await vscode.window.showOpenDialog({
    canSelectFiles: false,
    canSelectFolders: true,
    canSelectMany: false,
    defaultUri: vscode.workspace.workspaceFolders?.[0]?.uri,
    openLabel: "cd",
    title: "Choose Stata working directory",
  });
  if (!picked?.[0]) return;
  await cdToDirectory(picked[0]);
}

async function cdToDirectory(uri: vscode.Uri): Promise<void> {
  const directory = uri.fsPath;
  if (!directory) {
    vscode.window.showErrorMessage("stata-code: selected folder has no local path");
    return;
  }
  const result = await runUtilityCode(`cd "${escapeStataPath(directory)}"`, "change working directory");
  if (!result) return;
  if (result.ok) {
    vscode.window.showInformationMessage(`stata-code: cd ${directory}`);
  } else {
    vscode.window.showErrorMessage(
      `stata-code: cd failed${result.error?.message ? `: ${result.error.message}` : ""}`,
    );
  }
}

async function runUtilityCode(code: string, label: string): Promise<RunResult | undefined> {
  const sessionId = currentSessionId();
  output?.show(true);
  output?.appendLine(`[stata-code] ${label} (session=${sessionId})`);
  statusBar?.setRunning(true);
  try {
    const result = await getClient().runStata(code, {
      sessionId,
      includeFullLog: true,
      includeGraphs: "none",
    });
    renderResult(result);
    sessionsProvider?.refresh();
    return result;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    output?.appendLine(`[stata-code] ${label} failed: ${msg}`);
    vscode.window.showErrorMessage(`stata-code: ${msg}`);
    return undefined;
  } finally {
    statusBar?.setRunning(false);
  }
}

async function newSession(): Promise<void> {
  const chosen = await vscode.window.showInputBox({
    prompt: "New Stata tab/session id",
    value: nextSessionName(),
    validateInput: validateSessionId,
  });
  if (!chosen) return;
  await setCurrentSession(chosen);
}

async function switchSession(target?: unknown): Promise<void> {
  const direct = sessionIdFromTarget(target);
  if (direct) {
    await setCurrentSession(direct);
    return;
  }

  const current = currentSessionId();
  const live = await listLiveSessionsForPick();
  const merged = new Map<string, { session_id: string; n_obs: number; isLive: boolean }>();
  for (const sid of knownSessionIds) {
    merged.set(sid, { session_id: sid, n_obs: 0, isLive: false });
  }
  for (const s of live) {
    merged.set(s.session_id, { ...s, isLive: true });
  }

  type SessionPick = vscode.QuickPickItem & {
    action?: "new";
    sessionId?: string;
  };
  const items: SessionPick[] = Array.from(merged.values())
    .sort((a, b) => sessionSortKey(a.session_id, current).localeCompare(sessionSortKey(b.session_id, current)))
    .map((s) => ({
      label: s.session_id,
      description: [s.session_id === current ? "current" : "", s.isLive ? "live" : "not started"]
        .filter(Boolean)
        .join(" · "),
      detail: s.isLive ? `${s.n_obs} obs` : "Will be created on first run.",
      sessionId: s.session_id,
    }));
  items.push({
    label: "$(add) New Stata tab...",
    description: "enter a new id",
    action: "new",
  });

  const pick = await vscode.window.showQuickPick(items, {
    placeHolder: "Switch Stata tab/session",
  });
  if (!pick) return;
  if (pick.action === "new") return newSession();
  if (pick.sessionId) await setCurrentSession(pick.sessionId);
}

async function listLiveSessionsForPick(): Promise<Array<{ session_id: string; n_obs: number }>> {
  try {
    return (await getClient().listSessions()).map((s) => ({
      session_id: s.session_id,
      n_obs: s.n_obs,
    }));
  } catch (err) {
    output?.appendLine(
      `[stata-code] list_sessions failed: ${err instanceof Error ? err.message : String(err)}`,
    );
    return [];
  }
}

async function setCurrentSession(sessionId: string): Promise<void> {
  const validation = validateSessionId(sessionId);
  if (validation) {
    vscode.window.showErrorMessage(`stata-code: ${validation}`);
    return;
  }

  rememberSessionId(sessionId);
  const target = vscode.workspace.workspaceFolders?.length
    ? vscode.ConfigurationTarget.Workspace
    : vscode.ConfigurationTarget.Global;
  await vscode.workspace
    .getConfiguration("stataCode")
    .update("sessionId", sessionId, target);
  statusBar?.refresh();
  sessionsProvider?.refresh();
  output?.appendLine(`[stata-code] current session: ${sessionId}`);
}

async function closeSession(target?: unknown): Promise<void> {
  const sid = resolveTargetSessionId(target);
  if (sid === "main") {
    vscode.window.showInformationMessage(
      'stata-code: the "main" tab cannot be closed; use reset to clear it.',
    );
    return;
  }

  const ok = await vscode.window.showWarningMessage(
    `Close Stata tab "${sid}"? This drops its data and removes its local history.`,
    { modal: true },
    "Close Tab",
  );
  if (ok !== "Close Tab") return;

  try {
    if (client) await client.resetSession(sid);
    forgetSessionId(sid);
    dropHistoryForSession(sid);
    if (currentSessionId() === sid) await setCurrentSession("main");
    refreshResultViews();
    vscode.window.showInformationMessage(`stata-code: closed tab "${sid}"`);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    vscode.window.showErrorMessage(`stata-code: close failed: ${msg}`);
  }
}

function dropHistoryForSession(sessionId: string): void {
  removeMatching(runHistory, (entry) => entry.result.session_id === sessionId);
  removeMatching(logsHistory, (entry) => entry.result.session_id === sessionId);
  removeMatching(graphsHistory, (entry) => entry.sessionId === sessionId);
  if (lastResult?.session_id === sessionId) lastResult = undefined;
}

function removeMatching<T>(items: T[], predicate: (item: T) => boolean): void {
  for (let i = items.length - 1; i >= 0; i--) {
    if (predicate(items[i])) items.splice(i, 1);
  }
}

function refreshResultViews(): void {
  lastResultProvider?.refresh();
  runHistoryProvider?.refresh();
  logsHistoryProvider?.refresh();
  graphsHistoryProvider?.refresh();
  sessionsProvider?.refresh();
}

function resolveTargetSessionId(target?: unknown): string {
  return sessionIdFromTarget(target) ?? currentSessionId();
}

function sessionIdFromTarget(target?: unknown): string | undefined {
  if (target && typeof target === "object" && "info" in target) {
    const info = (target as { info?: { session_id?: string } }).info;
    if (info?.session_id) return info.session_id;
  }
  return undefined;
}

async function cancelSession(target?: unknown): Promise<void> {
  const sid = resolveTargetSessionId(target);
  try {
    const r = await getClient().cancelSession(sid);
    output?.appendLine(
      `[stata-code] cancel_session ${sid}: was_pending=${r.was_pending} is_pending=${r.is_pending} killed_worker=${r.killed_worker}`,
    );
    vscode.window.showInformationMessage(
      `stata-code: cancel requested for "${sid}"${r.killed_worker ? " (worker killed)" : ""}`,
    );
    sessionsProvider?.refresh();
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    vscode.window.showErrorMessage(`stata-code: cancel failed: ${msg}`);
  }
}

async function resetSession(target?: unknown): Promise<void> {
  const sid = resolveTargetSessionId(target);
  const ok = await vscode.window.showWarningMessage(
    `Reset session "${sid}"? This drops the session data but keeps the tab.`,
    { modal: true },
    "Reset",
  );
  if (ok !== "Reset") return;
  try {
    await getClient().resetSession(sid);
    rememberSessionId(sid);
    vscode.window.showInformationMessage(`stata-code: session "${sid}" reset`);
    sessionsProvider?.refresh();
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    vscode.window.showErrorMessage(`stata-code: reset failed: ${msg}`);
  }
}

function hydrateKnownSessions(context: vscode.ExtensionContext): void {
  const persisted = context.workspaceState.get<string[]>(SESSION_IDS_KEY, []);
  knownSessionIds = new Set(["main", ...persisted.filter((sid) => SESSION_ID_RE.test(sid))]);
  rememberSessionId(currentSessionId());
}

function rememberSessionId(sessionId: string): void {
  if (!SESSION_ID_RE.test(sessionId)) return;
  const before = knownSessionIds.size;
  knownSessionIds.add(sessionId);
  if (knownSessionIds.size !== before) void persistKnownSessionIds();
}

function forgetSessionId(sessionId: string): void {
  if (sessionId === "main") return;
  if (knownSessionIds.delete(sessionId)) void persistKnownSessionIds();
}

async function persistKnownSessionIds(): Promise<void> {
  const ids = Array.from(knownSessionIds).sort((a, b) =>
    sessionSortKey(a, currentSessionId()).localeCompare(sessionSortKey(b, currentSessionId())),
  );
  await extensionContext?.workspaceState.update(SESSION_IDS_KEY, ids);
}

function validateSessionId(value: string): string | null {
  return SESSION_ID_RE.test(value)
    ? null
    : "session id must match [A-Za-z_][A-Za-z0-9_]{0,31}";
}

function nextSessionName(): string {
  for (let i = 1; i < 1000; i++) {
    const candidate = `session${i}`;
    if (!knownSessionIds.has(candidate)) return candidate;
  }
  return "session";
}

function sessionSortKey(sessionId: string, current: string): string {
  if (sessionId === current) return `0-${sessionId}`;
  if (sessionId === "main") return "1-main";
  return `2-${sessionId.toLocaleLowerCase()}`;
}

function findResultForRun(runId: string): RunResult | undefined {
  return (
    runHistory.find((entry) => entry.runId === runId)?.result ??
    logsHistory.find((entry) => entry.runId === runId)?.result
  );
}

function resolveLogResult(target?: unknown): RunResult | undefined {
  if (!target) return lastResult;
  if (isRunResult(target)) return target;
  if (target && typeof target === "object") {
    if ("result" in target && isRunResult((target as { result?: unknown }).result)) {
      return (target as { result: RunResult }).result;
    }
    if ("entry" in target) {
      const entry = (target as { entry?: { result?: unknown } }).entry;
      if (isRunResult(entry?.result)) return entry.result;
    }
  }
  return lastResult;
}

function resolveRunResult(target?: unknown): RunResult | undefined {
  if (!target) return lastResult;
  if (isRunResult(target)) return target;
  const entry = resolveRunHistoryEntry(target);
  if (entry) return entry.result;
  if (target && typeof target === "object") {
    if ("result" in target && isRunResult((target as { result?: unknown }).result)) {
      return (target as { result: RunResult }).result;
    }
  }
  return lastResult;
}

function resolveRunHistoryEntry(target?: unknown): RunHistoryEntry | undefined {
  if (!target || typeof target !== "object") return undefined;
  if (isRunHistoryEntry(target)) return target;
  if ("entry" in target && isRunHistoryEntry((target as { entry?: unknown }).entry)) {
    return (target as { entry: RunHistoryEntry }).entry;
  }
  return undefined;
}

function resolveMatrixTarget(
  target?: unknown,
): { scope?: string; name: string; matrix: Matrix } | undefined {
  if (!target || typeof target !== "object") return undefined;
  if ("matrix" in target && isMatrix((target as { matrix?: unknown }).matrix)) {
    const maybeName = (target as { name?: unknown }).name;
    const maybeScope = (target as { scope?: unknown }).scope;
    return {
      name: typeof maybeName === "string" ? maybeName : "matrix",
      scope: typeof maybeScope === "string" ? maybeScope : undefined,
      matrix: (target as { matrix: Matrix }).matrix,
    };
  }
  return undefined;
}

function resolveGraphEntry(target?: unknown): GraphHistoryEntry | undefined {
  if (!target || typeof target !== "object") return undefined;
  if ("graph" in target && isGraphInfo((target as { graph?: unknown }).graph)) {
    return target as GraphHistoryEntry;
  }
  if ("entry" in target) {
    const entry = (target as { entry?: unknown }).entry;
    if (entry && typeof entry === "object" && "graph" in entry) {
      return entry as GraphHistoryEntry;
    }
  }
  return undefined;
}

function isRunResult(value: unknown): value is RunResult {
  return Boolean(
    value &&
      typeof value === "object" &&
      "request_id" in value &&
      "log" in value &&
      "session_id" in value,
  );
}

function isRunHistoryEntry(value: unknown): value is RunHistoryEntry {
  return Boolean(
    value &&
      typeof value === "object" &&
      "runId" in value &&
      "code" in value &&
      "result" in value &&
      isRunResult((value as { result?: unknown }).result),
  );
}

function isMatrix(value: unknown): value is Matrix {
  return Boolean(
    value &&
      typeof value === "object" &&
      "rows" in value &&
      "cols" in value &&
      "ref" in value,
  );
}

function isGraphInfo(value: unknown): value is GraphInfo {
  return Boolean(
    value &&
      typeof value === "object" &&
      "ref" in value &&
      "format" in value &&
      "name" in value,
  );
}

function defaultWorkspaceUri(filename: string): vscode.Uri {
  const folder = vscode.workspace.workspaceFolders?.[0]?.uri;
  return folder ? vscode.Uri.joinPath(folder, filename) : vscode.Uri.file(filename);
}

function sanitizeFilename(name: string): string {
  return name.replace(/[^A-Za-z0-9._-]/g, "_").slice(0, 64);
}

function shortRunId(requestId: string): string {
  return sanitizeFilename(requestId).slice(0, 10) || "run";
}

function formatOriginLabel(origin: SubmitOrigin): string {
  const label =
    origin.uri.scheme === "file"
      ? vscode.workspace.asRelativePath(origin.uri)
      : origin.uri.toString();
  return `${label}:${origin.baseLine + 1}`;
}

function bundleDirectoryName(entry: RunHistoryEntry): string {
  const stamp = new Date(entry.ts)
    .toISOString()
    .replace(/[-:]/g, "")
    .replace(/\.\d{3}Z$/, "Z");
  return `stata-run-${stamp}-${shortRunId(entry.runId)}`;
}

function escapeStataPath(fsPath: string): string {
  return fsPath.replace(/\\/g, "/").replace(/"/g, '""');
}

function matrixToTsv(name: string, matrix: Matrix): string {
  const rows = matrix.rows ?? [];
  const cols = matrix.cols ?? [];
  const values = matrix.values ?? [];
  const lines = [`# ${name}`, ["", ...cols].join("\t")];
  for (let i = 0; i < values.length; i++) {
    const rowLabel = rows[i] ?? String(i + 1);
    lines.push([rowLabel, ...values[i].map(formatMatrixValue)].join("\t"));
  }
  return `${lines.join("\n")}\n`;
}

function formatMatrixValue(value: number | null): string {
  if (value === null) return "";
  return Number.isInteger(value) ? String(value) : String(Number(value.toPrecision(12)));
}
