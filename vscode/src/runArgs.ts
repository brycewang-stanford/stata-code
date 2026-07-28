// Builds the `stata_run` MCP argument object.
//
// Kept separate from `mcpClient.ts` — and free of any `vscode` import — so the
// wire arguments can be unit-tested without an Extension Host.

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

export function buildRunArguments(
  code: string,
  opts: RunStataOptions = {},
): Record<string, unknown> {
  const args: Record<string, unknown> = {
    code,
    // The editor is a human surface with no token budget to defend, and the
    // sidebar renders `matrix.values` directly. The server's default
    // (`include_results: "scalars"`) returns every matrix as a `matrix://`
    // stub, which would leave the "last result" matrix view empty. Ask for the
    // full values, matching what the Jupyter kernel does for the same reason.
    include_results: "full",
  };
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
  return args;
}
