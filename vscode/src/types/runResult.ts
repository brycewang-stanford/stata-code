// Hand-rolled minimal TypeScript subset of the v1.0 RunResult envelope.
//
// The normative shape lives in ../../../SCHEMA.md and the machine-readable
// JSON Schema is at ../../../schema/run_result.schema.json. To regenerate
// the *full* set of types from that artifact, run `npm run gen-types`.
//
// This file ships hand-rolled, not generated, so the extension can compile
// without first running the codegen step. Keep field names identical to
// the schema; do NOT add fields here that aren't in SCHEMA.md.

export type ErrorKind =
  | "syntax"
  | "command_not_found"
  | "varname_not_found"
  | "invalid_name"
  | "type_mismatch"
  | "name_conflict"
  | "not_sorted"
  | "convergence"
  | "infeasible"
  | "estimation_sample_empty"
  | "estimation_failure"
  | "no_estimation_results"
  | "no_observations"
  | "data_in_memory"
  | "matrix_singular"
  | "matrix_conformability"
  | "matrix_missing"
  | "file_not_found"
  | "file_exists"
  | "file_corrupt"
  | "file_io"
  | "network"
  | "permission"
  | "encoding"
  | "stata_limit"
  | "out_of_memory"
  | "interrupt"
  | "cancelled"
  | "timeout"
  | "adapter_crash"
  | "unknown";

export type GraphFormat = "png" | "svg" | "pdf";
export type IncludeGraphs = "ref" | "inline" | "none";
export type Backend = "pystata" | "console";
export type StataEdition = "MP" | "SE" | "IC" | "BE" | "unknown";

export interface LogInfo {
  head: string;
  tail: string;
  lines_total: number;
  bytes_total: number;
  truncated: boolean;
  complete: boolean;
  error_window: string | null;
  ref: string | null;
  files?: LogFileInfo | null;
}

export interface LogFileInfo {
  directory: string;
  log_path: string;
  smcl_path: string;
  manifest_path: string;
  code_path: string | null;
  working_dir?: string | null;
  graphs_dir?: string | null;
  outputs_dir?: string | null;
  graph_paths?: string[];
  output_paths?: string[];
  policy: "per_run_directory";
  append: boolean;
}

export interface Matrix {
  rows: string[];
  cols: string[];
  values: (number | null)[][] | null;
  ref: string | null;
}

export interface StataReturns {
  scalars: Record<string, number | null>;
  macros: Record<string, string>;
  matrices: Record<string, Matrix>;
}

export interface ResultsInfo {
  r: StataReturns;
  e: StataReturns;
  last_estimation_cmd: string | null;
}

export interface VariableInfo {
  name: string;
  type: string;
  label: string;
}

export interface DatasetInfo {
  frame: string;
  n_obs: number;
  n_vars: number;
  changed: boolean;
  filename: string | null;
  variables: VariableInfo[] | null;
}

export interface GraphInfo {
  ref: string;
  name: string;
  format: GraphFormat;
  width: number | null;
  height: number | null;
  source_command: string | null;
  source_line: number | null;
  inline: string | null; // base64 when include_graphs="inline"
  file_path?: string | null;
}

export interface Suggestion {
  action: string;
  command: string | null;
}

export interface ErrorContext {
  before: string[];
  failing: string;
  after: string[];
}

export interface ErrorInfo {
  kind: ErrorKind;
  rc: number;
  rc_label: string;
  message: string;
  command: string | null;
  line: number | null;
  context: ErrorContext;
  commands_executed: number | null;
  path: string | null;
  varname: string | null;
  name: string | null;
  suggestions: Suggestion[];
}

export interface StataWarning {
  kind: string;
  message: string;
}

export interface StataInfo {
  version: string | null;
  edition: StataEdition;
  backend: Backend;
}

export interface OriginInfo {
  path: string | null;
  kind: string | null;
  label: string | null;
  cell_id: string | null;
}

export interface RunResult {
  ok: boolean;
  rc: number;
  session_id: string;
  request_id: string;
  started_at: string; // ISO 8601 UTC ms
  elapsed_ms: number;
  stata_elapsed_ms: number | null;
  stata: StataInfo;
  log: LogInfo;
  results: ResultsInfo;
  dataset: DatasetInfo;
  graphs: GraphInfo[];
  warnings: StataWarning[];
  error: ErrorInfo | null;
  origin?: OriginInfo | null;
  schema_version: string;
  capabilities: string[];
}
