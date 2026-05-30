import type { Matrix, RunResult } from "./types/runResult";

export function inlineLogText(result: RunResult): string {
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

export function formatLogDocument(result: RunResult, text: string): string {
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

export function formatDataPreviewDocument(
  result: RunResult,
  text: string,
  previewObs: number,
): string {
  const ds = result.dataset;
  const status = result.ok ? "OK" : `FAIL rc=${result.rc}`;
  const rowsShown = result.ok ? Math.min(ds.n_obs, previewObs) : 0;
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

export function matrixToTsv(name: string, matrix: Matrix): string {
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
