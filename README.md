# stata-code

> Agent-native Stata bridge — **one Python core, multiple frontends**.

`stata-code` lets you drive Stata from modern environments: an LLM agent (Claude Code, Cursor, Claude Desktop), a Jupyter notebook, or a VS Code editor session. All frontends share one Python core and return a stable, structured, **agent-friendly** result schema.

```text
                    ┌────────────────────────────────────────┐
                    │     stata-code core (Python)           │
                    │                                        │
                    │   • pystata adapter (Stata 17+)        │
                    │   • v1.0 unified result schema         │
                    │   • token-economy defaults             │
                    │   • multi-session via Stata frames     │
                    │   • typed errors + suggestions         │
                    └────────────────────────────────────────┘
                       ↑              ↑              ↑
              ┌────────┴────┐  ┌──────┴─────┐  ┌────┴────────────┐
              │  Jupyter    │  │  MCP       │  │  VS Code        │
              │  kernel     │  │  server    │  │  extension      │
              └─────────────┘  └────────────┘  └─────────────────┘
```

**Status: v0.2 (May 2026)** — the core, MCP server, and Jupyter kernel work end-to-end against Stata 18 MP. Current test suite: 144 passing tests (88 no-Stata unit tests + 56 real-Stata integration tests). License: **MIT**.

---

## Why this exists

The Stata AI / agent tooling landscape is fragmented; see [References-tools.md](References-tools.md):

- Existing MCP servers ([SepineTam/stata-mcp](https://github.com/sepinetam/stata-mcp), [tmonk/mcp-stata](https://github.com/tmonk/mcp-stata)) are **AGPL-3.0**, which is not a fit for closed-source or commercial integration.
- The popular VS Code AI extension ([hanlulong/stata-mcp](https://github.com/hanlulong/stata-mcp)) is MIT, but it bundles the MCP server inside the extension, making standalone reuse awkward.
- Each tool wraps `pystata` with its own result shape, so agents have to special-case each integration.
- Many existing tools were designed for humans first and then bolted onto MCP; they often dump long logs and base64 graph blobs into every reply, burning tokens by default.

`stata-code` is designed to fill that gap:

1. **MIT-licensed**, with no copyleft contagion.
2. One shared result schema for every frontend: [SCHEMA.md](SCHEMA.md).
3. Agent-native by default: typed errors, structured `r()` / `e()`, log refs, graph refs, and suggestion seeds.
4. One core, multiple frontends: Jupyter kernel, MCP server, and VS Code extension.

For the project's clean-room policy around AGPL/GPL Stata projects, see [LICENSE-POLICY.md](LICENSE-POLICY.md).

---

## Install

Requirements: **Stata 17+** (with `pystata` shipped by Stata) and **Python 3.10+**.

```bash
# from PyPI
pip install stata-code

# with the MCP server and Jupyter kernel extras
pip install "stata-code[mcp,kernel]"

# or from source (editable install for development)
git clone https://github.com/brycewang-stanford/stata-code.git
cd stata-code
pip install -e ".[mcp,kernel]"
```

> **Naming note.** The PyPI distribution is `stata-code` (hyphen), but
> the Python import is `stata_code` (underscore — Python identifiers
> can't contain hyphens). Same convention as `scikit-learn` →
> `import sklearn`. So: `pip install stata-code`,
> `from stata_code import run`.

Note: `pystata` is **not** on PyPI; it ships with Stata. `stata-code` auto-discovers it on macOS at `/Applications/Stata/utilities/pystata` and at equivalent Linux / Windows paths. If your install is elsewhere, add it to `PYTHONPATH` before importing.

---

## Quick Start

See [`examples/`](examples/) for end-to-end cookbook entries: basic regression, DiD, graphs, multi-session, and large matrices.

### As a Python Library

```python
from stata_code import run

r = run("sysuse auto, clear")
r = run("regress mpg weight")

if r.ok:
    print(r.results.e.scalars["r2"])           # 0.6515 (native float)
    print(r.results.e.macros["cmd"])           # "regress"
    b = r.results.e.matrices["b"]
    print(dict(zip(b.cols, b.values[0])))      # {"weight": -0.006, "_cons": 39.44}
else:
    print(r.error.kind, r.error.message)       # ErrorKind.VARNAME_NOT_FOUND, "..."
    for s in r.error.suggestions:
        print("hint:", s.action)               # "Did you mean `mpg`?"
```

### As an MCP Server

After `pip install stata-code`, the `stata-code-mcp` binary is on your `PATH`. You can wire it into Claude Code, Cursor, Claude Desktop, or any other MCP-compatible client.

#### Claude Code via `claude mcp add` (recommended)

If you have not installed Claude Code yet, see [anthropics/claude-code](https://github.com/anthropics/claude-code).

The fastest way is the `claude mcp add` CLI. Pick a scope based on how widely you want `stata-code` available:

```bash
# user scope — install once, available in every Claude Code workspace on this machine
claude mcp add stata-code --scope user -- stata-code-mcp

# local scope — only for the current workspace (your local Claude config, not committed)
claude mcp add stata-code --scope local -- stata-code-mcp

# project scope — written into ./.mcp.json so collaborators on this repo share it
claude mcp add stata-code --scope project -- stata-code-mcp
```

Then launch `claude` and type `/mcp` to confirm `stata-code` shows up with its 8 tools (`stata_run`, `stata_info`, `get_log`, `get_graph`, `get_matrix`, `list_sessions`, `cancel_session`, `reset_session`).

#### `uvx` (no global pip install)

If you prefer not to `pip install stata-code` globally, run it ephemerally through [`uv`](https://github.com/astral-sh/uv):

```bash
claude mcp add stata-code --scope user -- uvx --from stata-code stata-code-mcp
```

`uvx` will resolve and cache `stata-code` on first launch. Note: `pystata` is **not** on PyPI, so it still has to be locatable on the host. The runner adds the standard Stata install path (e.g. `/Applications/Stata/utilities/pystata` on macOS) to `sys.path` automatically; if your Stata lives elsewhere, set `PYTHONPATH` in the env block.

#### Manual JSON config (Cursor / Claude Desktop / fallback)

For clients without a `mcp add` CLI, edit the config file directly (`~/.claude/mcp.json`, Cursor settings, Claude Desktop `claude_desktop_config.json`, etc.):

```json
{
  "mcpServers": {
    "stata-code": {
      "command": "stata-code-mcp"
    }
  }
}
```

Or run it as a module if the binary is not on `PATH`:

```bash
python -m stata_code.mcp
```

The MCP server registers 8 tools:

| Tool | Purpose |
| --- | --- |
| `stata_run` | Execute Stata code and return a v1.0 RunResult JSON |
| `stata_info` | Report Stata edition, version, and capabilities |
| `get_log` | Fetch the full log behind a `log://` ref |
| `get_graph` | Fetch graph bytes behind a `graph://` ref (`ImageContent`) |
| `get_matrix` | Fetch matrix payloads behind a `matrix://` ref |
| `list_sessions` | Enumerate live sessions |
| `cancel_session` | Cooperatively cancel the next `stata_run` for a session |
| `reset_session` | Drop a session's data |

### As a Jupyter Kernel

```bash
stata-code-kernel install --user
```

Or install it as a module:

```bash
python -m stata_code.kernel install --user
```

Then open a notebook and select the **Stata** kernel. Stata commands run in cells; logs, graphs, and warnings render inline.

### As a VS Code Extension

The companion extension is on the Marketplace as [`brycewang-stanford.stata-code-vscode`](https://marketplace.visualstudio.com/items?itemName=brycewang-stanford.stata-code-vscode). It spawns `stata-code-mcp` as a child process and adds a sidebar (sessions / last result / run history / logs / graphs), code-lens "Run cell" actions on `.do` files, status-bar indicators, and inline diagnostics from the v1.0 typed errors.

```bash
# from the VS Code CLI
code --install-extension brycewang-stanford.stata-code-vscode
```

Or open the **Extensions** sidebar in VS Code and search `stata-code`.

The extension still requires `stata-code` itself to be importable on your system Python (`pip install stata-code`), so that `stata-code-mcp` resolves on `PATH`. Stata 17+ and a valid Stata license are required as for any other frontend.

---

## Token-Economy Defaults

A typical `stata_run` response is about **10x smaller** than servers that dump logs and images directly. Three design choices drive this:

1. **Logs return `head` + `tail` + `ref`** by default. Full logs are fetched on demand via `get_log(ref)`. A Stata regression log can be about 6,000 tokens; `stata-code` returns about 600 by default.
2. **Graphs return refs, not inline base64**. A 30 KB PNG can become about 50,000 base64 tokens; returning a ref avoids that unless the agent actually needs the bytes.
3. **Errors are typed**. Agents can check `err.kind == "varname_not_found"` instead of regex-parsing English logs.

For example, a misspelled variable returns a structured error:

```json
{
  "ok": false,
  "rc": 111,
  "error": {
    "kind": "varname_not_found",
    "varname": "mpgg",
    "line": 3,
    "context": {
      "before": ["use auto"],
      "failing": "summarize mpgg",
      "after": []
    },
    "suggestions": [
      {"action": "Did you mean `mpg`?", "command": "describe"}
    ]
  }
}
```

The full schema is in [SCHEMA.md](SCHEMA.md).

---

## Architecture

```text
stata_code/
├── core/
│   ├── _runtime.py    # process-singleton pystata wrapper
│   ├── _refs.py       # LRU ref store for log/graph/matrix payloads
│   ├── schema.py      # Pydantic v2 models for the v1.0 result schema
│   ├── errors.py      # rc → ErrorKind mapping + suggestion seeds
│   └── runner.py      # the one execute(); collects everything via sfi
├── mcp/
│   └── server.py      # MCP server (8 tools)
└── kernel/
    └── kernel.py      # Jupyter kernel
```

`runner.py` is the only place that touches Stata. The Jupyter kernel and MCP server both import from it and only translate results into their own transports.

---

## Comparison

| | stata-code | SepineTam/stata-mcp | hanlulong/stata-mcp | nbstata |
| --- | --- | --- | --- | --- |
| License | **MIT** | AGPL-3.0 | MIT | GPL-3.0 |
| Standalone MCP | ✓ | ✓ | bundled with VS Code | — |
| Jupyter kernel | ✓ | — | — | ✓ |
| Unified result schema | ✓ ([SCHEMA.md](SCHEMA.md)) | per-tool | per-tool | per-tool |
| Token-economy defaults | ✓ (log refs, graph refs) | — | — | — |
| Typed errors + suggestions | ✓ (32 kinds) | — | — | — |
| Multi-session | ✓ (Stata frames) | partial | — | — |
| Mature ecosystem | early | ✓ (statamcp.com, cookbook) | ✓ (11k installs) | ✓ |

`stata-code` is the younger, MIT-licensed, agent-native alternative in this problem space. Among the AGPL options, SepineTam's `stata-mcp` is currently more mature; `stata-code` is aimed at cases where copyleft contagion is unacceptable and agents need structured results.

---

## Roadmap

### Done (v0.2 — May 2026)

- v1.0 result schema ([SCHEMA.md](SCHEMA.md))
- `pystata`-based runner with native-typed `r()`, `e()`, and matrices
- Multi-session via Stata frames
- Per-line error attribution: line number, context, commands_executed
- Graph capture: `png` / `svg` / `pdf` with ref store
- Log truncation with ref store
- Warning extraction: 5 categories + generic notes
- 32-kind error taxonomy with canonical suggestions
- MCP server: 8 tools
- Jupyter kernel: rewired to the v1.0 pipeline
- Matrix size cap + `get_matrix(ref)` for large matrices (>10k cells)
- Cooperative cancellation: `cancel(session_id)` / MCP `cancel_session`
- JSON Schema artifact auto-generated from `schema.py`: [`schema/run_result.schema.json`](schema/run_result.schema.json)
- VS Code extension published to the Marketplace as [`brycewang-stanford.stata-code-vscode`](https://marketplace.visualstudio.com/items?itemName=brycewang-stanford.stata-code-vscode): sidebar (sessions / last result / run history / logs / graphs), code-lens cell runner, status bar, diagnostics, MCP child-process spawn
- Clean-room license policy ([LICENSE-POLICY.md](LICENSE-POLICY.md))

### Next Up

- **v0.3** — Console fallback for Stata 11–16, re-implemented against the v1.0 schema
- **v0.3** — Hard timeout / mid-Stata interrupt; design and tradeoffs in [`docs/design/hard_timeout.md`](docs/design/hard_timeout.md)
- **v0.4** — extra VS Code polish (esbuild bundle, lighter VSIX, command palette UX)
- **v1.0** — Stable schema, PyPI / VS Code Marketplace publishing

See [SCHEMA.md §7](SCHEMA.md) for explicitly out-of-scope items.

---

## Testing

```bash
pip install -e ".[dev,mcp,kernel]"
pytest                              # full suite (144 tests)
pytest -m "not stata_required"      # CI subset; no Stata needed
pytest -m "stata_required" -v       # Stata-only integration tests
```

The `stata_required` marker tags the real-Stata integration tests. CI uses `pytest -m "not stata_required"` so it does not collect them. Locally without Stata, those tests skip cleanly with the `"pystata / Stata 17+ not available"` message.

---

## Contributing

- Read [LICENSE-POLICY.md](LICENSE-POLICY.md) before opening a PR.
- Add a one-line acknowledgement to your first PR description; the template is in the policy file.
- Tests are required for any new schema field or runner behavior.

---

## License

The code is licensed under [MIT](./LICENSE). [LICENSE-POLICY.md](LICENSE-POLICY.md) explains how this project relates to other Stata projects.

## Trademark Notice

Stata is a registered trademark of StataCorp LLC. This project is independent and not affiliated with or endorsed by StataCorp.

## Acknowledgements

The Stata tooling landscape that this project builds on and learns from is surveyed in [References-tools.md](References-tools.md). All listed projects retain their own licenses and authorship; please consult each repository before reuse.

---
---

## 中文版 / Chinese version

> 面向 LLM 智能体的 Stata 桥接工具 —— **一个 Python 核心，多种前端入口**。

`stata-code` 让你可以从现代开发环境中驱动 Stata：LLM 智能体（Claude Code、Cursor、Claude Desktop）、Jupyter notebook，或 VS Code 编辑器。它们共享同一个 Python 核心，并返回稳定、结构化、**适合智能体读取**的结果格式。

```text
                    ┌────────────────────────────────────────┐
                    │     stata-code core (Python)           │
                    │                                        │
                    │   • pystata adapter (Stata 17+)        │
                    │   • v1.0 统一结果 schema               │
                    │   • 默认节省 token                     │
                    │   • 通过 Stata frames 支持多 session   │
                    │   • 结构化 typed errors + 建议         │
                    └────────────────────────────────────────┘
                       ↑              ↑              ↑
              ┌────────┴────┐  ┌──────┴─────┐  ┌────┴────────────┐
              │  Jupyter    │  │  MCP       │  │  VS Code        │
              │  kernel     │  │  server    │  │  extension      │
              └─────────────┘  └────────────┘  └─────────────────┘
```

**当前状态：v0.2（2026 年 5 月）** —— core、MCP server 和 Jupyter kernel 已经可以在 Stata 18 MP 上端到端运行。当前测试：144 passing（88 个不需要 Stata 的单元测试 + 56 个真实 Stata 集成测试）。许可证：**MIT**。

---

## 为什么做这个项目

Stata 的 AI / agent 工具生态现在比较分散，详见 [References-tools.md](References-tools.md)：

- 现有 MCP server（[SepineTam/stata-mcp](https://github.com/sepinetam/stata-mcp)、[tmonk/mcp-stata](https://github.com/tmonk/mcp-stata)）使用 **AGPL-3.0**，不适合闭源或商业集成。
- 常用的 VS Code AI 插件（[hanlulong/stata-mcp](https://github.com/hanlulong/stata-mcp)）是 MIT，但 MCP server 被打包在插件内部，不方便单独复用。
- 每个工具都用自己的方式封装 `pystata`，返回结构不统一，智能体需要为不同工具写特殊处理。
- 很多工具一开始是为人类交互设计的，再接到 MCP 上；它们经常把 200 行日志和 base64 图片直接塞进回复，默认就大量消耗 token。

`stata-code` 要填补的就是这个空位：

1. **MIT 许可证**，没有 copyleft 传染问题。
2. 所有前端共享同一个结果格式：[SCHEMA.md](SCHEMA.md)。
3. 默认面向智能体：typed errors、结构化 `r()` / `e()`、log refs、graph refs、suggestion seeds。
4. 一个 core，多个入口：Jupyter kernel、MCP server、VS Code 扩展。

如果你关心 AGPL/GPL Stata 项目的 clean-room 边界，请看 [LICENSE-POLICY.md](LICENSE-POLICY.md)。

---

## 安装

要求：**Stata 17+**（自带 `pystata`）和 **Python 3.10+**。

```bash
# 从 PyPI 安装
pip install stata-code

# 同时安装 MCP server 和 Jupyter kernel 的额外依赖
pip install "stata-code[mcp,kernel]"

# 或者从源码安装（开发用 editable install）
git clone https://github.com/brycewang-stanford/stata-code.git
cd stata-code
pip install -e ".[mcp,kernel]"
```

> **命名说明。** PyPI 上的发行包名是 `stata-code`（带连字符），
> 但 Python 导入名是 `stata_code`（下划线 —— Python 标识符不能包含连字符）。
> 和 `scikit-learn` → `import sklearn` 是同样的约定。
> 所以：`pip install stata-code`，`from stata_code import run`。

注意：`pystata` **不在 PyPI 上**，它随 Stata 一起安装。`stata-code` 会自动在 macOS 的 `/Applications/Stata/utilities/pystata` 以及 Linux / Windows 的对应位置寻找它。如果你的 Stata 安装在其他位置，请在导入前把 `pystata` 加到 `PYTHONPATH`。

---

## 快速开始

完整 cookbook 在 [`examples/`](examples/)：基础回归、DiD、图形、多 session、大矩阵。

### 作为 Python library

```python
from stata_code import run

r = run("sysuse auto, clear")
r = run("regress mpg weight")

if r.ok:
    print(r.results.e.scalars["r2"])           # 0.6515 (native float)
    print(r.results.e.macros["cmd"])           # "regress"
    b = r.results.e.matrices["b"]
    print(dict(zip(b.cols, b.values[0])))      # {"weight": -0.006, "_cons": 39.44}
else:
    print(r.error.kind, r.error.message)       # ErrorKind.VARNAME_NOT_FOUND, "..."
    for s in r.error.suggestions:
        print("hint:", s.action)               # "Did you mean `mpg`?"
```

### 作为 MCP server

`pip install stata-code` 之后，`stata-code-mcp` 会出现在你的 `PATH` 中。可以接到 Claude Code、Cursor、Claude Desktop 等任何兼容 MCP 的客户端里。

#### 用 `claude mcp add` 接入 Claude Code（推荐）

如果你还没有安装 Claude Code，请先看 [anthropics/claude-code](https://github.com/anthropics/claude-code)。

最快的方式是 `claude mcp add` 命令。根据想要的可见范围选 scope：

```bash
# user scope —— 一次安装，本机所有 Claude Code workspace 全局可用
claude mcp add stata-code --scope user -- stata-code-mcp

# local scope —— 仅当前 workspace（本地 Claude 配置，不会提交到仓库）
claude mcp add stata-code --scope local -- stata-code-mcp

# project scope —— 写入仓库内的 ./.mcp.json，和协作者共享
claude mcp add stata-code --scope project -- stata-code-mcp
```

接着运行 `claude`，输入 `/mcp` 确认 `stata-code` 出现并带有 8 个工具（`stata_run`, `stata_info`, `get_log`, `get_graph`, `get_matrix`, `list_sessions`, `cancel_session`, `reset_session`）。

#### 用 `uvx`（不必全局 pip install）

如果不想全局 `pip install stata-code`，可以用 [`uv`](https://github.com/astral-sh/uv) 临时运行：

```bash
claude mcp add stata-code --scope user -- uvx --from stata-code stata-code-mcp
```

`uvx` 会在首次启动时下载并缓存 `stata-code`。注意：`pystata` **不在 PyPI 上**，仍需要在宿主机上能找到。runner 会自动把标准 Stata 安装路径（macOS 上的 `/Applications/Stata/utilities/pystata` 等）加到 `sys.path`；如果你的 Stata 在别处，请用 env 设置 `PYTHONPATH`。

#### 手动 JSON 配置（Cursor / Claude Desktop / 兜底方案）

对于没有 `mcp add` CLI 的客户端，直接编辑配置文件即可（`~/.claude/mcp.json`、Cursor settings、Claude Desktop 的 `claude_desktop_config.json` 等）：

```json
{
  "mcpServers": {
    "stata-code": {
      "command": "stata-code-mcp"
    }
  }
}
```

如果 `stata-code-mcp` 不在 `PATH` 上，也可以以 module 方式运行：

```bash
python -m stata_code.mcp
```

MCP server 注册了 8 个工具：

| 工具 | 用途 |
| --- | --- |
| `stata_run` | 执行 Stata code，返回 v1.0 RunResult JSON |
| `stata_info` | 返回 Stata edition、version 和 capabilities |
| `get_log` | 通过 `log://` ref 获取完整日志 |
| `get_graph` | 通过 `graph://` ref 获取图形 bytes（`ImageContent`） |
| `get_matrix` | 通过 `matrix://` ref 获取矩阵 `{rows, cols, values}` |
| `list_sessions` | 列出 live sessions |
| `cancel_session` | 协作式取消某个 session 的下一次 `stata_run` |
| `reset_session` | 清空某个 session 的数据 |

### 作为 Jupyter kernel

```bash
stata-code-kernel install --user
```

也可以直接以 module 方式安装：

```bash
python -m stata_code.kernel install --user
```

然后打开 notebook，选择 **Stata** kernel。Stata 命令会在 cell 中运行，日志、图形和 warnings 会以内联方式显示。

### 作为 VS Code 扩展

配套扩展已发布到 Marketplace：[`brycewang-stanford.stata-code-vscode`](https://marketplace.visualstudio.com/items?itemName=brycewang-stanford.stata-code-vscode)。它会以子进程方式启动 `stata-code-mcp`，并提供侧边栏（sessions / last result / run history / logs / graphs）、`.do` 文件的 code-lens "Run cell"、状态栏指示器，以及来自 v1.0 typed errors 的内联诊断。

```bash
# 从 VS Code 命令行
code --install-extension brycewang-stanford.stata-code-vscode
```

或者打开 VS Code 的 **Extensions** 侧栏，搜索 `stata-code`。

扩展仍然依赖系统 Python 上能导入 `stata-code`（`pip install stata-code`），从而保证 `stata-code-mcp` 在 `PATH` 上可用。和其它前端一样，需要 Stata 17+ 和有效的 Stata 许可证。

---

## 默认节省 token

典型的 `stata_run` 响应比现有 MCP server 直接返回日志和图片的方式小约 **10 倍**。核心设计有三点：

1. **日志默认只返回 `head` + `tail` + `ref`**。默认各 20 行；完整日志可以按需用 `get_log(ref)` 获取。Stata 回归日志可能有约 6,000 tokens，`stata-code` 默认约 600 tokens。
2. **图形默认返回 refs，不内联 base64**。一个 30 KB PNG 转成 base64 约 50,000 tokens；返回 ref 可以让智能体只在真正需要渲染时再取 bytes。
3. **错误是结构化 typed errors**。智能体可以判断 `err.kind == "varname_not_found"`，而不是正则解析英文日志。

例如，变量名写错时返回的是结构化错误：

```json
{
  "ok": false,
  "rc": 111,
  "error": {
    "kind": "varname_not_found",
    "varname": "mpgg",
    "line": 3,
    "context": {
      "before": ["use auto"],
      "failing": "summarize mpgg",
      "after": []
    },
    "suggestions": [
      {"action": "Did you mean `mpg`?", "command": "describe"}
    ]
  }
}
```

完整 schema 见 [SCHEMA.md](SCHEMA.md)。

---

## 架构

```text
stata_code/
├── core/
│   ├── _runtime.py    # process-singleton pystata wrapper
│   ├── _refs.py       # LRU ref store for log/graph/matrix payloads
│   ├── schema.py      # Pydantic v2 models for the v1.0 result schema
│   ├── errors.py      # rc → ErrorKind mapping + suggestion seeds
│   └── runner.py      # the one execute(); collects everything via sfi
├── mcp/
│   └── server.py      # MCP server (8 tools)
└── kernel/
    └── kernel.py      # Jupyter kernel
```

`runner.py` 是唯一直接接触 Stata 的地方。Jupyter kernel 和 MCP server 都只导入它，然后把结果翻译成各自的传输格式。

---

## 对比

| | stata-code | SepineTam/stata-mcp | hanlulong/stata-mcp | nbstata |
| --- | --- | --- | --- | --- |
| 许可证 | **MIT** | AGPL-3.0 | MIT | GPL-3.0 |
| 独立 MCP | ✓ | ✓ | 与 VS Code 捆绑 | — |
| Jupyter kernel | ✓ | — | — | ✓ |
| 统一结果格式 | ✓ ([SCHEMA.md](SCHEMA.md)) | per-tool | per-tool | per-tool |
| 默认节省 token | ✓ (log refs, graph refs) | — | — | — |
| 结构化错误和建议 | ✓ (32 kinds) | — | — | — |
| 多 session | ✓ (Stata frames) | partial | — | — |
| 生态成熟度 | early | ✓ (statamcp.com, cookbook) | ✓ (11k installs) | ✓ |

`stata-code` 是这个问题空间里更年轻的、MIT 许可证的、agent-native 的替代方案。AGPL 方案里，SepineTam 的 `stata-mcp` 目前更成熟；`stata-code` 的目标是服务那些不能接受 copyleft 传染、又需要结构化智能体接口的场景。

---

## 路线图

### 已完成（v0.2 —— 2026 年 5 月）

- v1.0 result schema ([SCHEMA.md](SCHEMA.md))
- 基于 `pystata` 的 runner，原生类型化的 `r()`、`e()` 和矩阵
- 通过 Stata frames 支持多 session
- 行级错误归属：line number、context、commands_executed
- 图形捕获：`png` / `svg` / `pdf` + ref store
- 日志截断 + ref store
- 警告抽取：5 类 + 通用 notes
- 32 类错误分类法 + 标准化建议
- MCP server：8 个工具
- Jupyter kernel：接入 v1.0 pipeline
- 矩阵大小上限 + 大矩阵的 `get_matrix(ref)`（>10k cells）
- 协作式取消：`cancel(session_id)` / MCP `cancel_session`
- 从 `schema.py` 自动生成 JSON Schema 工件：[`schema/run_result.schema.json`](schema/run_result.schema.json)
- VS Code 扩展已发布到 Marketplace [`brycewang-stanford.stata-code-vscode`](https://marketplace.visualstudio.com/items?itemName=brycewang-stanford.stata-code-vscode)：侧边栏（sessions / last result / run history / logs / graphs）、code-lens cell runner、状态栏、诊断、MCP 子进程
- Clean-room 许可证策略 ([LICENSE-POLICY.md](LICENSE-POLICY.md))

### 下一步

- **v0.3** —— Stata 11–16 的 console fallback，按 v1.0 schema 重新实现
- **v0.3** —— 硬超时 / Stata 执行中断；设计与权衡见 [`docs/design/hard_timeout.md`](docs/design/hard_timeout.md)
- **v0.4** —— VS Code 体验打磨（esbuild 打包、更轻的 VSIX、命令面板 UX）
- **v1.0** —— 稳定 schema，PyPI / VS Code Marketplace 正式发布

明确不做的范围见 [SCHEMA.md §7](SCHEMA.md)。

---

## 测试

```bash
pip install -e ".[dev,mcp,kernel]"
pytest                              # 完整测试集（144 个）
pytest -m "not stata_required"      # CI 子集，不需要 Stata
pytest -m "stata_required" -v       # 仅 Stata 集成测试
```

`stata_required` marker 标记真实 Stata 集成测试。CI 使用 `pytest -m "not stata_required"`，因此不会收集这些测试。本地没有 Stata 时，这些测试也会用 `"pystata / Stata 17+ not available"` 信息 cleanly skip。

---

## 贡献

- 提 PR 前请先读 [LICENSE-POLICY.md](LICENSE-POLICY.md)。
- 第一个 PR description 里请加一行 acknowledgement，模板在 policy 文件里。
- 新增 schema field 或 runner 行为时必须补测试。

---

## 许可证

代码使用 [MIT](./LICENSE)。[LICENSE-POLICY.md](LICENSE-POLICY.md) 说明本项目如何处理和其他 Stata 项目的关系。

## 商标声明

Stata 是 StataCorp LLC 的注册商标。本项目是独立项目，不隶属于 StataCorp，也未获得 StataCorp 背书。

## 致谢

本项目参考和学习的 Stata 工具生态整理在 [References-tools.md](References-tools.md)。其中列出的项目保留各自的许可证和作者归属；复用前请查看对应仓库。
