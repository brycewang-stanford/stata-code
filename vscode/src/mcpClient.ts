// Thin wrapper around @modelcontextprotocol/sdk's stdio client.
//
// One MCP client instance per VSCode workspace, lazily started on the
// first call. The child process is `stata-code-mcp` (configurable). The
// extension calls high-level methods (`runStata`, `getLog`, `getGraph`,
// `getMatrix`); this module hides the JSON-RPC plumbing.

import * as vscode from "vscode";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

import type { RunResult } from "./types/runResult";

export interface StataServerLaunch {
  command: string;
  args: string[];
  cwd?: string;
  env?: Record<string, string>;
}

export interface RunStataOptions {
  sessionId?: string;
  includeFullLog?: boolean;
  includeGraphs?: "ref" | "inline" | "none";
  persistLogFiles?: boolean;
  persistGeneratedFiles?: boolean;
  originPath?: string;
  originKind?: "file" | "selection" | "line" | "cell" | "section" | "code" | "unknown";
  originLabel?: string;
  useOriginWorkdir?: boolean;
  workingDir?: string;
}

export class StataMcpClient implements vscode.Disposable {
  private client: Client | null = null;
  private transport: StdioClientTransport | null = null;
  private startPromise: Promise<void> | null = null;

  constructor(
    private readonly launchCandidates: StataServerLaunch[],
    private readonly output: vscode.OutputChannel,
  ) {}

  private async start(): Promise<void> {
    if (this.client) return;
    if (this.startPromise) return this.startPromise;

    this.startPromise = this.startWithFallbacks();

    try {
      await this.startPromise;
    } catch (err) {
      this.startPromise = null;
      this.client = null;
      this.transport = null;
      throw err;
    }
  }

  private async startWithFallbacks(): Promise<void> {
    const failures: string[] = [];

    for (const candidate of this.launchCandidates) {
      try {
        await this.connectCandidate(candidate);
        return;
      } catch (err) {
        const message = formatStartupFailure(candidate, err);
        failures.push(message);
        this.output.appendLine(`[stata-code] MCP startup failed: ${message}`);
      }
    }

    this.output.appendLine("[stata-code] all MCP startup attempts failed");
    throw new Error(
      [
        "MCP server failed to start.",
        `Tried: ${this.launchCandidates.map(formatLaunch).join("; ")}.`,
        failures.length > 0 ? `Last failure: ${failures[failures.length - 1]}.` : "",
        "Install with `python3 -m pip install \"stata-code[mcp]\"` or set `stataCode.serverCommand` and `stataCode.serverArgs`.",
        "See the stata-code output panel for startup details.",
      ]
        .filter(Boolean)
        .join(" "),
    );
  }

  private async connectCandidate(candidate: StataServerLaunch): Promise<void> {
    let stderr = "";
    const transport = new StdioClientTransport({
      command: candidate.command,
      args: candidate.args,
      cwd: candidate.cwd,
      env: candidate.env,
      stderr: "pipe",
    });
    transport.stderr?.on("data", (chunk: Buffer | string) => {
      const text = chunk.toString();
      stderr += text;
      for (const line of text.split(/\r?\n/)) {
        if (line.trim()) {
          this.output.appendLine(`[stata-code:mcp stderr] ${line}`);
        }
      }
    });

    const client = new Client(
      { name: "stata-code-vscode", version: "0.6.2" },
      { capabilities: {} },
    );
    this.output.appendLine(`[stata-code] launching MCP server: ${formatLaunch(candidate)}`);

    try {
      await client.connect(transport);
    } catch (err) {
      await transport.close().catch(() => undefined);
      throw addStderrToError(err, stderr);
    }

    this.client = client;
    this.transport = transport;
    this.output.appendLine("[stata-code] MCP server connected");
  }

  async runStata(code: string, opts: RunStataOptions = {}): Promise<RunResult> {
    await this.start();
    if (!this.client) throw new Error("MCP client not initialized");

    const args: Record<string, unknown> = { code };
    if (opts.sessionId !== undefined) args.session_id = opts.sessionId;
    if (opts.includeFullLog !== undefined) args.include_full_log = opts.includeFullLog;
    if (opts.includeGraphs !== undefined) args.include_graphs = opts.includeGraphs;
    if (opts.persistLogFiles !== undefined) args.persist_log_files = opts.persistLogFiles;
    if (opts.persistGeneratedFiles !== undefined) {
      args.persist_generated_files = opts.persistGeneratedFiles;
    }
    if (opts.originPath !== undefined) args.origin_path = opts.originPath;
    if (opts.originKind !== undefined) args.origin_kind = opts.originKind;
    if (opts.originLabel !== undefined) args.origin_label = opts.originLabel;
    if (opts.useOriginWorkdir !== undefined) args.use_origin_workdir = opts.useOriginWorkdir;
    if (opts.workingDir !== undefined) args.working_dir = opts.workingDir;

    const reply = await this.client.callTool({ name: "stata_run", arguments: args });
    return parseTextResult<RunResult>(reply, "stata_run");
  }

  async getLog(ref: string): Promise<{ text: string; lines_total: number; bytes_total: number }> {
    await this.start();
    if (!this.client) throw new Error("MCP client not initialized");
    const reply = await this.client.callTool({ name: "get_log", arguments: { ref } });
    return parseTextResult(reply, "get_log");
  }

  async getMatrix(
    ref: string,
  ): Promise<{ rows: string[]; cols: string[]; values: (number | null)[][] }> {
    await this.start();
    if (!this.client) throw new Error("MCP client not initialized");
    const reply = await this.client.callTool({ name: "get_matrix", arguments: { ref } });
    return parseTextResult(reply, "get_matrix");
  }

  async listSessions(): Promise<Array<{ session_id: string; frame: string; n_obs: number }>> {
    await this.start();
    if (!this.client) throw new Error("MCP client not initialized");
    const reply = await this.client.callTool({ name: "list_sessions", arguments: {} });
    return parseTextResult(reply, "list_sessions");
  }

  async cancelSession(sessionId: string): Promise<{
    session_id: string;
    was_pending: boolean;
    is_pending: boolean;
    killed_worker: boolean;
  }> {
    await this.start();
    if (!this.client) throw new Error("MCP client not initialized");
    const reply = await this.client.callTool({
      name: "cancel_session",
      arguments: { session_id: sessionId },
    });
    return parseTextResult(reply, "cancel_session");
  }

  async resetSession(sessionId: string): Promise<unknown> {
    await this.start();
    if (!this.client) throw new Error("MCP client not initialized");
    const reply = await this.client.callTool({
      name: "reset_session",
      arguments: { session_id: sessionId },
    });
    return parseTextResult(reply, "reset_session");
  }

  async getGraphBytes(ref: string): Promise<{ data: string; mimeType: string }> {
    await this.start();
    if (!this.client) throw new Error("MCP client not initialized");
    const reply = (await this.client.callTool({
      name: "get_graph",
      arguments: { ref },
    })) as { content: Array<{ type: string; data?: string; mimeType?: string }> };
    const image = reply.content.find((c) => c.type === "image");
    if (!image || !image.data) {
      throw new Error("get_graph did not return an image");
    }
    return { data: image.data, mimeType: image.mimeType ?? "image/png" };
  }

  dispose(): void {
    if (this.transport) {
      void this.transport.close();
    }
    this.transport = null;
    this.client = null;
    this.startPromise = null;
  }
}

function formatLaunch(candidate: StataServerLaunch): string {
  return [candidate.command, ...candidate.args].map(quoteCommandPart).join(" ");
}

function quoteCommandPart(value: string): string {
  return /^[\w@%+=:,./-]+$/.test(value) ? value : JSON.stringify(value);
}

function formatStartupFailure(candidate: StataServerLaunch, err: unknown): string {
  const message = err instanceof Error ? err.message : String(err);
  return `${formatLaunch(candidate)} -> ${message}`;
}

function addStderrToError(err: unknown, stderr: string): Error {
  const message = err instanceof Error ? err.message : String(err);
  const tail = stderr.trim().split(/\r?\n/).slice(-8).join("\n");
  if (!tail) return new Error(message);
  return new Error(`${message}\n${tail}`);
}

function parseTextResult<T>(
  reply: unknown,
  toolName: string,
): T {
  const r = reply as { content?: Array<{ type: string; text?: string }> };
  const text = r.content?.find((c) => c.type === "text")?.text;
  if (text === undefined) {
    throw new Error(`${toolName} returned no text content`);
  }
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new Error(`${toolName} returned non-JSON text: ${text.slice(0, 200)}`);
  }
}
