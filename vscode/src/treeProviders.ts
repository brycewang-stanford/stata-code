// Tree data providers for the stata_code activity bar sidebar.
//
// Three independent providers, one per view:
//   - SessionsProvider   : live sessions (id / n_obs), refreshable on demand
//   - LastResultProvider : ok/rc, error, scalars, warnings, dataset summary
//   - GraphsHistoryProvider : all graphs from successful runs this session
//
// All three are passive: they read from a shared `Store` interface that
// extension.ts updates after each run, and from the MCP client for live
// session data. They never own state of their own beyond the cached
// `_onDidChangeTreeData` emitter.

import * as vscode from "vscode";

import { buildDataNodes, type DataNode } from "./dataBrowser";
import { buildOutputNodes, type OutputNode } from "./outputs";
import type { StataMcpClient } from "./mcpClient";
import type { GraphInfo, Matrix, RunResult } from "./types/runResult";

export interface ResultStore {
  getLastResult(): RunResult | undefined;
  getRunHistory(): RunHistoryEntry[];
  getGraphsHistory(): GraphHistoryEntry[];
  getLogsHistory(): LogHistoryEntry[];
}

export interface SessionStore {
  getKnownSessionIds(): string[];
}

export interface GraphHistoryEntry {
  runId: string;
  ts: number;
  sessionId: string;
  graph: GraphInfo;
}

export interface LogHistoryEntry {
  runId: string;
  ts: number;
  result: RunResult;
}

export interface RunHistoryEntry {
  runId: string;
  ts: number;
  code: string;
  originUri: vscode.Uri;
  baseLine: number;
  originLabel: string;
  result: RunResult;
}

// ─────────────────────────────────────────────────────────────────────────
// Sessions
// ─────────────────────────────────────────────────────────────────────────

export class SessionsProvider implements vscode.TreeDataProvider<SessionItem> {
  private readonly _changed = new vscode.EventEmitter<SessionItem | undefined>();
  readonly onDidChangeTreeData = this._changed.event;

  private cache: SessionItem[] | undefined;

  constructor(
    private readonly client: () => StataMcpClient,
    private readonly store: SessionStore,
  ) {}

  refresh(): void {
    this.cache = undefined;
    this._changed.fire(undefined);
  }

  getTreeItem(item: SessionItem): vscode.TreeItem {
    return item;
  }

  async getChildren(parent?: SessionItem): Promise<SessionItem[]> {
    if (parent) return [];
    if (this.cache) return this.cache;
    try {
      const sessions = await this.client().listSessions();
      const current = vscode.workspace
        .getConfiguration("stataCode")
        .get<string>("sessionId", "main");
      const merged = new Map<string, SessionInfo>();
      for (const sid of this.store.getKnownSessionIds()) {
        merged.set(sid, {
          session_id: sid,
          frame: sid === "main" ? "default" : sid,
          n_obs: 0,
          isLive: false,
        });
      }
      for (const s of sessions) {
        merged.set(s.session_id, { ...s, isLive: true });
      }
      this.cache = Array.from(merged.values())
        .sort((a, b) => sessionSortKey(a.session_id, current).localeCompare(sessionSortKey(b.session_id, current)))
        .map((s) => new SessionItem(s, s.session_id === current));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      const current = vscode.workspace
        .getConfiguration("stataCode")
        .get<string>("sessionId", "main");
      const known = this.store.getKnownSessionIds().map(
        (sid) =>
          new SessionItem(
            {
              session_id: sid,
              frame: sid === "main" ? "default" : sid,
              n_obs: 0,
              isLive: false,
            },
            sid === current,
          ),
      );
      const failItem = new SessionItem(
        { session_id: `(error: ${msg})`, frame: "", n_obs: 0, isLive: false },
        false,
      );
      failItem.contextValue = "stataCode.sessionError";
      this.cache = [...known, failItem];
    }
    return this.cache;
  }
}

interface SessionInfo {
  session_id: string;
  frame: string;
  n_obs: number;
  isLive: boolean;
}

class SessionItem extends vscode.TreeItem {
  constructor(
    public readonly info: SessionInfo,
    public readonly isCurrent: boolean,
  ) {
    super(info.session_id, vscode.TreeItemCollapsibleState.None);
    this.description = [
      info.isLive ? `${info.n_obs} obs` : "not started",
      isCurrent ? "current" : "",
    ]
      .filter(Boolean)
      .join(" · ");
    this.iconPath = new vscode.ThemeIcon(isCurrent ? "circle-filled" : "circle-outline");
    this.tooltip = `frame=${info.frame || info.session_id}, n_obs=${info.n_obs}, ${info.isLive ? "live" : "not started"}`;
    this.contextValue = [
      "stataCode.session",
      isCurrent ? "current" : "other",
      info.session_id === "main" ? "main" : "closable",
      info.isLive ? "live" : "local",
    ].join(".");
    this.command = {
      command: "stataCode.switchSession",
      title: "Switch to Stata Session",
      arguments: [this],
    };
  }
}

function sessionSortKey(sessionId: string, current: string): string {
  if (sessionId === current) return `0-${sessionId}`;
  if (sessionId === "main") return "1-main";
  return `2-${sessionId.toLocaleLowerCase()}`;
}

// ─────────────────────────────────────────────────────────────────────────
// Last result
// ─────────────────────────────────────────────────────────────────────────

type LastNode =
  | { kind: "section"; label: string; description?: string; children: LastNode[]; icon?: string }
  | {
      kind: "leaf";
      label: string;
      description?: string;
      tooltip?: string;
      icon?: string;
      command?: vscode.Command;
      contextValue?: string;
    };

export class LastResultProvider implements vscode.TreeDataProvider<LastNode> {
  private readonly _changed = new vscode.EventEmitter<LastNode | undefined>();
  readonly onDidChangeTreeData = this._changed.event;

  constructor(private readonly store: ResultStore) {}

  refresh(): void {
    this._changed.fire(undefined);
  }

  getTreeItem(node: LastNode): vscode.TreeItem {
    const item = new vscode.TreeItem(
      node.label,
      node.kind === "section"
        ? vscode.TreeItemCollapsibleState.Collapsed
        : vscode.TreeItemCollapsibleState.None,
    );
    item.description = node.description;
    if (node.kind === "leaf") item.tooltip = node.tooltip;
    if (node.icon) item.iconPath = new vscode.ThemeIcon(node.icon);
    if (node.kind === "leaf") {
      item.command = node.command;
      item.contextValue = node.contextValue;
    }
    return item;
  }

  getChildren(parent?: LastNode): LastNode[] {
    const r = this.store.getLastResult();
    if (!r) return []; // welcome view shows
    if (!parent) return rootNodesFor(r);
    if (parent.kind === "section") return parent.children;
    return [];
  }
}

function rootNodesFor(r: RunResult): LastNode[] {
  const nodes: LastNode[] = [];

  if (r.ok) {
    nodes.push({
      kind: "leaf",
      label: `ok · rc=${r.rc}`,
      description: `${r.elapsed_ms} ms · session "${r.session_id}"`,
      icon: "pass",
    });
  } else {
    const err = r.error;
    nodes.push({
      kind: "section",
      label: `FAIL · ${err?.kind ?? "?"} · rc=${r.rc}`,
      description: err?.line !== null && err?.line !== undefined ? `line ${err.line}` : undefined,
      icon: "error",
      children: [
        ...(err?.message ? [leaf("message", err.message)] : []),
        ...(err?.context?.failing
          ? [leaf("failing", err.context.failing, err.context.failing)]
          : []),
        ...(err?.suggestions?.map((s) => leaf("hint", s.action, s.command ?? undefined)) ?? []),
      ],
    });
  }

  if (r.log.head || r.log.tail || r.log.ref) {
    nodes.push({
      kind: "leaf",
      label: "log",
      description: `${r.log.lines_total} lines${r.log.truncated ? " · truncated" : ""}${
        r.log.files ? " · saved" : ""
      }${
        r.log.files?.output_paths?.length ? ` · ${r.log.files.output_paths.length} outputs` : ""
      }`,
      tooltip: r.log.files?.directory ?? r.log.ref ?? "inline log",
      icon: "output",
      command: {
        command: "stataCode.openLog",
        title: "Open Log",
        arguments: [r],
      },
      contextValue: "stataCode.lastLog",
    });
  }

  if (r.graphs.length) {
    nodes.push({
      kind: "leaf",
      label: "graphs",
      description: String(r.graphs.length),
      icon: "graph",
      command: {
        command: "stataCode.showGraphs",
        title: "Show Graphs",
      },
      contextValue: "stataCode.lastGraphs",
    });
  }

  if (r.results) {
    const rScalars = Object.entries(r.results.r?.scalars ?? {});
    const rMacros = Object.entries(r.results.r?.macros ?? {});
    const rMatrices = Object.entries(r.results.r?.matrices ?? {});
    const eScalars = Object.entries(r.results.e?.scalars ?? {});
    const eMacros = Object.entries(r.results.e?.macros ?? {});
    const eMatrices = Object.entries(r.results.e?.matrices ?? {});

    if (rScalars.length || rMacros.length || rMatrices.length) {
      nodes.push({
        kind: "section",
        label: "r() returns",
        description: `${rScalars.length}s ${rMacros.length}m ${rMatrices.length}x`,
        icon: "symbol-namespace",
        children: [
          ...rScalars.map(([k, v]) => leaf(k, formatScalar(v))),
          ...rMacros.map(([k, v]) => leaf(k, String(v))),
          ...rMatrices.map(([k, v]) => matrixLeaf("r", k, v)),
        ],
      });
    }
    if (eScalars.length || eMacros.length || eMatrices.length) {
      nodes.push({
        kind: "section",
        label: "e() returns",
        description: `${eScalars.length}s ${eMacros.length}m ${eMatrices.length}x`,
        icon: "symbol-namespace",
        children: [
          ...eScalars.map(([k, v]) => leaf(k, formatScalar(v))),
          ...eMacros.map(([k, v]) => leaf(k, String(v))),
          ...eMatrices.map(([k, v]) => matrixLeaf("e", k, v)),
        ],
      });
    }
  }

  if (r.warnings?.length) {
    nodes.push({
      kind: "section",
      label: "warnings",
      description: String(r.warnings.length),
      icon: "warning",
      children: r.warnings.map((w) => leaf(w.kind, w.message)),
    });
  }

  if (r.dataset) {
    const ds = r.dataset;
    nodes.push({
      kind: "section",
      label: "dataset",
      description: `${ds.n_obs} obs · ${ds.n_vars} vars`,
      icon: "table",
      children: (ds.variables ?? []).map((v) =>
        leaf(v.name, v.type, v.label ?? undefined),
      ),
    });
  }

  return nodes;
}

function leaf(label: string, description?: string, tooltip?: string): LastNode {
  return { kind: "leaf", label, description, tooltip };
}

function matrixLeaf(scope: "r" | "e", name: string, matrix: Matrix): LastNode {
  const rows = matrix.rows?.length ?? 0;
  const cols = matrix.cols?.length ?? 0;
  return {
    kind: "leaf",
    label: `${name}`,
    description: `${rows}x${cols}`,
    tooltip: matrix.ref ?? `${scope}(${name}) inline matrix`,
    icon: "symbol-array",
    command: {
      command: "stataCode.openMatrix",
      title: "Open Matrix",
      arguments: [{ scope, name, matrix }],
    },
    contextValue: "stataCode.matrix",
  };
}

function formatScalar(v: unknown): string {
  if (typeof v === "number") {
    return Number.isInteger(v) ? String(v) : v.toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
  }
  return String(v);
}

// ─────────────────────────────────────────────────────────────────────────
// Data browser (current dataset / variables)
// ─────────────────────────────────────────────────────────────────────────

export class DataProvider implements vscode.TreeDataProvider<DataNode> {
  private readonly _changed = new vscode.EventEmitter<DataNode | undefined>();
  readonly onDidChangeTreeData = this._changed.event;

  constructor(private readonly store: ResultStore) {}

  refresh(): void {
    this._changed.fire(undefined);
  }

  getTreeItem(node: DataNode): vscode.TreeItem {
    const item = new vscode.TreeItem(node.label, vscode.TreeItemCollapsibleState.None);
    item.description = node.description;
    item.tooltip = node.tooltip;
    if (node.icon) item.iconPath = new vscode.ThemeIcon(node.icon);
    if (node.kind === "variable") {
      item.contextValue = "stataCode.variable";
      item.command = {
        command: "stataCode.copyVariableName",
        title: "Copy Variable Name",
        arguments: [node.varName],
      };
    }
    return item;
  }

  getChildren(parent?: DataNode): DataNode[] {
    if (parent) return [];
    return buildDataNodes(this.store.getLastResult());
  }
}

// ─────────────────────────────────────────────────────────────────────────
// Run history
// ─────────────────────────────────────────────────────────────────────────

export class RunHistoryProvider implements vscode.TreeDataProvider<RunHistoryItem> {
  private readonly _changed = new vscode.EventEmitter<RunHistoryItem | undefined>();
  readonly onDidChangeTreeData = this._changed.event;

  constructor(private readonly store: ResultStore) {}

  refresh(): void {
    this._changed.fire(undefined);
  }

  getTreeItem(item: RunHistoryItem): vscode.TreeItem {
    return item;
  }

  getChildren(): RunHistoryItem[] {
    return this.store
      .getRunHistory()
      .slice()
      .reverse()
      .map((entry) => new RunHistoryItem(entry));
  }
}

class RunHistoryItem extends vscode.TreeItem {
  constructor(public readonly entry: RunHistoryEntry) {
    const result = entry.result;
    const label = `${formatClock(entry.ts)} · ${result.ok ? "OK" : "FAIL"}`;
    super(label, vscode.TreeItemCollapsibleState.None);
    const lines = entry.code.split(/\r?\n/).length;
    const graphs = result.graphs.length ? ` · ${result.graphs.length} graph${result.graphs.length === 1 ? "" : "s"}` : "";
    this.description = `${result.session_id} · ${result.elapsed_ms} ms · ${lines} lines${graphs}`;
    this.tooltip = [
      entry.originLabel,
      `run=${entry.runId}`,
      result.error?.message ? `error=${result.error.message}` : "",
      "",
      entry.code.split(/\r?\n/).slice(0, 8).join("\n"),
    ]
      .filter((part) => part !== "")
      .join("\n");
    this.iconPath = new vscode.ThemeIcon(result.ok ? "history" : "error");
    this.contextValue = "stataCode.runHistoryItem";
    this.command = {
      command: "stataCode.openLog",
      title: "Open Run Log",
      arguments: [entry.result],
    };
  }
}

// ─────────────────────────────────────────────────────────────────────────
// Graphs history
// ─────────────────────────────────────────────────────────────────────────

export class GraphsHistoryProvider implements vscode.TreeDataProvider<GraphHistoryItem> {
  private readonly _changed = new vscode.EventEmitter<GraphHistoryItem | undefined>();
  readonly onDidChangeTreeData = this._changed.event;

  constructor(private readonly store: ResultStore) {}

  refresh(): void {
    this._changed.fire(undefined);
  }

  getTreeItem(item: GraphHistoryItem): vscode.TreeItem {
    return item;
  }

  getChildren(): GraphHistoryItem[] {
    return this.store
      .getGraphsHistory()
      .slice()
      .reverse()
      .map((entry) => new GraphHistoryItem(entry));
  }
}

class GraphHistoryItem extends vscode.TreeItem {
  constructor(public readonly entry: GraphHistoryEntry) {
    super(entry.graph.name || "graph", vscode.TreeItemCollapsibleState.None);
    const g = entry.graph;
    const dim = g.width && g.height ? `${g.width}×${g.height}` : "";
    this.description = [entry.sessionId, g.format, dim].filter(Boolean).join(" · ");
    this.tooltip = `${g.source_command ?? ""}\n${new Date(entry.ts).toLocaleString()}\nrun=${entry.runId}`;
    this.iconPath = new vscode.ThemeIcon("graph");
    this.contextValue = "stataCode.graphHistoryItem";
    this.command = {
      command: "stataCode.openGraph",
      title: "Open Graph",
      arguments: [entry],
    };
  }
}

// ─────────────────────────────────────────────────────────────────────────
// Logs history
// ─────────────────────────────────────────────────────────────────────────

export class LogsHistoryProvider implements vscode.TreeDataProvider<LogHistoryItem> {
  private readonly _changed = new vscode.EventEmitter<LogHistoryItem | undefined>();
  readonly onDidChangeTreeData = this._changed.event;

  constructor(private readonly store: ResultStore) {}

  refresh(): void {
    this._changed.fire(undefined);
  }

  getTreeItem(item: LogHistoryItem): vscode.TreeItem {
    return item;
  }

  getChildren(): LogHistoryItem[] {
    return this.store
      .getLogsHistory()
      .slice()
      .reverse()
      .map((entry) => new LogHistoryItem(entry));
  }
}

class LogHistoryItem extends vscode.TreeItem {
  constructor(public readonly entry: LogHistoryEntry) {
    const result = entry.result;
    const label = `${formatClock(entry.ts)} · ${result.ok ? "OK" : "FAIL"}`;
    super(label, vscode.TreeItemCollapsibleState.None);
    this.description = `${result.session_id} · ${result.log.lines_total} lines`;
    this.tooltip = [
      `session=${result.session_id}`,
      `run=${entry.runId}`,
      `started=${result.started_at}`,
      result.error?.message ?? "",
    ]
      .filter(Boolean)
      .join("\n");
    this.iconPath = new vscode.ThemeIcon(result.ok ? "output" : "error");
    this.contextValue = "stataCode.logHistoryItem";
    this.command = {
      command: "stataCode.openLog",
      title: "Open Log",
      arguments: [entry],
    };
  }
}

function formatClock(ts: number): string {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// ─────────────────────────────────────────────────────────────────────────
// Outputs (table / export artifacts written during runs)
// ─────────────────────────────────────────────────────────────────────────

export class OutputsHistoryProvider implements vscode.TreeDataProvider<OutputItem> {
  private readonly _changed = new vscode.EventEmitter<OutputItem | undefined>();
  readonly onDidChangeTreeData = this._changed.event;

  constructor(private readonly store: ResultStore) {}

  refresh(): void {
    this._changed.fire(undefined);
  }

  getTreeItem(item: OutputItem): vscode.TreeItem {
    return item;
  }

  getChildren(): OutputItem[] {
    const runs = this.store
      .getRunHistory()
      .map((e) => ({ runId: e.runId, ts: e.ts, result: e.result }));
    return buildOutputNodes(runs).map((node) => new OutputItem(node));
  }
}

class OutputItem extends vscode.TreeItem {
  constructor(public readonly node: OutputNode) {
    super(node.label, vscode.TreeItemCollapsibleState.None);
    this.description = node.description;
    this.tooltip = node.tooltip;
    this.resourceUri = vscode.Uri.file(node.path);
    this.iconPath = new vscode.ThemeIcon(node.icon);
    this.contextValue = "stataCode.outputItem";
    this.command = {
      command: "stataCode.openOutputFile",
      title: "Open Output",
      arguments: [node.path],
    };
  }
}
