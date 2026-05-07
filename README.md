# stata_code

> 面向 LLM 智能体的 Stata 桥接工具 - **一个 Python 核心，多种前端入口**。
> Agent-native Stata bridge - **one Python core, multiple frontends**.

`stata_code` 让你可以从现代开发环境中驱动 Stata：LLM 智能体（Claude Code、Cursor、Claude Desktop）、Jupyter notebook，或计划中的 VS Code 编辑器入口。它们共享同一个 Python 核心，并返回稳定、结构化、**适合智能体读取**的结果格式。

`stata_code` lets you drive Stata from modern environments: an LLM agent (Claude Code, Cursor, Claude Desktop), a Jupyter notebook, or a planned VS Code editor session. All frontends share one Python core and return a stable, structured, **agent-friendly** result schema.

```text
                    ┌────────────────────────────────────────┐
                    │     stata_code core (Python)           │
                    │                                        │
                    │   • pystata adapter (Stata 17+)        │
                    │   • v1.0 unified result schema         │
                    │   • token-economy defaults             │
                    │   • multi-session via Stata frames     │
                    │   • typed errors + suggestions         │
                    └────────────────────────────────────────┘
                       ↑              ↑              ↑
              ┌────────┴────┐  ┌──────┴─────┐  ┌────┴────────────┐
              │  Jupyter    │  │  MCP       │  │  VS Code glue   │
              │  kernel     │  │  server    │  │  (planned)      │
              └─────────────┘  └────────────┘  └─────────────────┘
```

**当前状态 / Status: v0.2 (May 2026)** - core、MCP server 和 Jupyter kernel 已经可以在 Stata 18 MP 上端到端运行。当前测试：144 passing（88 个不需要 Stata 的单元测试 + 56 个真实 Stata 集成测试）。许可证：**MIT**。

**Status: v0.2 (May 2026)** - the core, MCP server, and Jupyter kernel work end-to-end against Stata 18 MP. Current test suite: 144 passing tests (88 no-Stata unit tests + 56 real-Stata integration tests). License: **MIT**.

---

## 为什么做这个项目 / Why this exists

Stata 的 AI / agent 工具生态现在比较分散，详见 [References-tools.md](References-tools.md)：

The Stata AI / agent tooling landscape is fragmented; see [References-tools.md](References-tools.md):

- 现有 MCP server（[SepineTam/stata-mcp](https://github.com/sepinetam/stata-mcp)、[tmonk/mcp-stata](https://github.com/tmonk/mcp-stata)）使用 **AGPL-3.0**，不适合闭源或商业集成。
  Existing MCP servers ([SepineTam/stata-mcp](https://github.com/sepinetam/stata-mcp), [tmonk/mcp-stata](https://github.com/tmonk/mcp-stata)) are **AGPL-3.0**, which is not a fit for closed-source or commercial integration.

- 常用的 VS Code AI 插件（[hanlulong/stata-mcp](https://github.com/hanlulong/stata-mcp)）是 MIT，但 MCP server 被打包在插件内部，不方便单独复用。
  The popular VS Code AI extension ([hanlulong/stata-mcp](https://github.com/hanlulong/stata-mcp)) is MIT, but it bundles the MCP server inside the extension, making standalone reuse awkward.

- 每个工具都用自己的方式封装 `pystata`，返回结构不统一，智能体需要为不同工具写特殊处理。
  Each tool wraps `pystata` with its own result shape, so agents have to special-case each integration.

- 很多工具一开始是为人类交互设计的，再接到 MCP 上；它们经常把 200 行日志和 base64 图片直接塞进回复，默认就大量消耗 token。
  Many existing tools were designed for humans first and then bolted onto MCP; they often dump long logs and base64 graph blobs into every reply, burning tokens by default.

`stata_code` 要填补的就是这个空位：

`stata_code` is designed to fill that gap:

1. **MIT 许可证**，没有 copyleft 传染问题。
   **MIT-licensed**, with no copyleft contagion.

2. 所有前端共享同一个结果格式：[SCHEMA.md](SCHEMA.md)。
   One shared result schema for every frontend: [SCHEMA.md](SCHEMA.md).

3. 默认面向智能体：typed errors、结构化 `r()` / `e()`、log refs、graph refs、suggestion seeds。
   Agent-native by default: typed errors, structured `r()` / `e()`, log refs, graph refs, and suggestion seeds.

4. 一个 core，多个入口：Jupyter kernel、MCP server、计划中的 VS Code glue。
   One core, multiple frontends: Jupyter kernel, MCP server, and planned VS Code glue.

如果你关心 AGPL/GPL Stata 项目的 clean-room 边界，请看 [LICENSE-POLICY.md](LICENSE-POLICY.md)。

For the project's clean-room policy around AGPL/GPL Stata projects, see [LICENSE-POLICY.md](LICENSE-POLICY.md).

---

## 安装 / Install

要求：**Stata 17+**（自带 `pystata`）和 **Python 3.10+**。

Requirements: **Stata 17+** (with `pystata` shipped by Stata) and **Python 3.10+**.

```bash
# from PyPI
pip install stata_code

# with the MCP server and Jupyter kernel extras
pip install "stata_code[mcp,kernel]"

# or from source (editable install for development)
git clone https://github.com/brycewang-stanford/stata-code.git
cd stata-code
pip install -e ".[mcp,kernel]"
```

注意：`pystata` **不在 PyPI 上**，它随 Stata 一起安装。`stata_code` 会自动在 macOS 的 `/Applications/Stata/utilities/pystata` 以及 Linux / Windows 的对应位置寻找它。如果你的 Stata 安装在其他位置，请在导入前把 `pystata` 加到 `PYTHONPATH`。

Note: `pystata` is **not** on PyPI; it ships with Stata. `stata_code` auto-discovers it on macOS at `/Applications/Stata/utilities/pystata` and at equivalent Linux / Windows paths. If your install is elsewhere, add it to `PYTHONPATH` before importing.

---

## 快速开始 / Quick Start

完整 cookbook 在 [`examples/`](examples/)：基础回归、DiD、图形、多 session、大矩阵。

See [`examples/`](examples/) for end-to-end cookbook entries: basic regression, DiD, graphs, multi-session, and large matrices.

### 作为 Python library / As a Python Library

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

### 作为 MCP server / As an MCP Server

安装后，`stata-code-mcp` 会出现在你的 `PATH` 中。把下面的配置加到 Claude Code（`~/.claude/mcp.json` 或 Claude Code settings UI）、Cursor、Claude Desktop 等支持 MCP 的客户端里：

After install, `stata-code-mcp` is on your `PATH`. Add this to Claude Code (`~/.claude/mcp.json` or the Claude Code settings UI), Cursor, Claude Desktop, or another MCP-compatible client:

```json
{
  "mcpServers": {
    "stata": {
      "command": "stata-code-mcp"
    }
  }
}
```

也可以直接以 module 方式运行：

Or run it as a module:

```bash
python -m stata_code.mcp
```

MCP server 注册了 8 个工具：

The MCP server registers 8 tools:

| Tool | 用途 / Purpose |
| --- | --- |
| `stata_run` | 执行 Stata code，返回 v1.0 RunResult JSON / Execute Stata code and return a v1.0 RunResult JSON |
| `stata_info` | 返回 Stata edition、version 和 capabilities / Report Stata edition, version, and capabilities |
| `get_log` | 通过 `log://` ref 获取完整日志 / Fetch the full log behind a `log://` ref |
| `get_graph` | 通过 `graph://` ref 获取图形 bytes (`ImageContent`) / Fetch graph bytes behind a `graph://` ref |
| `get_matrix` | 通过 `matrix://` ref 获取矩阵 `{rows, cols, values}` / Fetch matrix payloads behind a `matrix://` ref |
| `list_sessions` | 列出 live sessions / Enumerate live sessions |
| `cancel_session` | 协作式取消某个 session 的下一次 `stata_run` / Cooperatively cancel the next `stata_run` for a session |
| `reset_session` | 清空某个 session 的数据 / Drop a session's data |

### 作为 Jupyter kernel / As a Jupyter Kernel

```bash
stata-code-kernel install --user
```

也可以直接以 module 方式安装：

Or install it as a module:

```bash
python -m stata_code.kernel install --user
```

然后打开 notebook，选择 **Stata** kernel。Stata 命令会在 cell 中运行，日志、图形和 warnings 会以内联方式显示。

Then open a notebook and select the **Stata** kernel. Stata commands run in cells; logs, graphs, and warnings render inline.

---

## 默认节省 token / Token-Economy Defaults

典型的 `stata_run` 响应比现有 MCP server 直接返回日志和图片的方式小约 **10 倍**。核心设计有三点：

A typical `stata_run` response is about **10x smaller** than servers that dump logs and images directly. Three design choices drive this:

1. **日志默认只返回 `head` + `tail` + `ref`**。默认各 20 行；完整日志可以按需用 `get_log(ref)` 获取。Stata 回归日志可能有约 6,000 tokens，`stata_code` 默认约 600 tokens。
   **Logs return `head` + `tail` + `ref`** by default. Full logs are fetched on demand via `get_log(ref)`. A Stata regression log can be about 6,000 tokens; `stata_code` returns about 600 by default.

2. **图形默认返回 refs，不内联 base64**。一个 30 KB PNG 转成 base64 约 50,000 tokens；返回 ref 可以让智能体只在真正需要渲染时再取 bytes。
   **Graphs return refs, not inline base64**. A 30 KB PNG can become about 50,000 base64 tokens; returning a ref avoids that unless the agent actually needs the bytes.

3. **错误是结构化 typed errors**。智能体可以判断 `err.kind == "varname_not_found"`，而不是正则解析英文日志。
   **Errors are typed**. Agents can check `err.kind == "varname_not_found"` instead of regex-parsing English logs.

例如，变量名写错时返回的是结构化错误：

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

完整 schema 见 [SCHEMA.md](SCHEMA.md)。

The full schema is in [SCHEMA.md](SCHEMA.md).

---

## 架构 / Architecture

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

`runner.py` is the only place that touches Stata. The Jupyter kernel and MCP server both import from it and only translate results into their own transports.

---

## 对比 / Comparison

| | stata_code | SepineTam/stata-mcp | hanlulong/stata-mcp | nbstata |
| --- | --- | --- | --- | --- |
| License / 许可证 | **MIT** | AGPL-3.0 | MIT | GPL-3.0 |
| Standalone MCP / 独立 MCP | ✓ | ✓ | bundled with VS Code | - |
| Jupyter kernel | ✓ | - | - | ✓ |
| Unified result schema / 统一结果格式 | ✓ ([SCHEMA.md](SCHEMA.md)) | per-tool | per-tool | per-tool |
| Token-economy defaults / 默认节省 token | ✓ (log refs, graph refs) | - | - | - |
| Typed errors + suggestions / 结构化错误和建议 | ✓ (32 kinds) | - | - | - |
| Multi-session / 多 session | ✓ (Stata frames) | partial | - | - |
| Mature ecosystem / 生态成熟度 | early | ✓ (statamcp.com, cookbook) | ✓ (11k installs) | ✓ |

`stata_code` 是这个问题空间里更年轻的、MIT 许可证的、agent-native 的替代方案。AGPL 方案里，SepineTam 的 `stata-mcp` 目前更成熟；`stata_code` 的目标是服务那些不能接受 copyleft 传染、又需要结构化智能体接口的场景。

`stata_code` is the younger, MIT-licensed, agent-native alternative in this problem space. Among the AGPL options, SepineTam's `stata-mcp` is currently more mature; `stata_code` is aimed at cases where copyleft contagion is unacceptable and agents need structured results.

---

## 路线图 / Roadmap

### 已完成 / Done (v0.2 - May 2026)

- v1.0 result schema ([SCHEMA.md](SCHEMA.md))
- 基于 `pystata` 的 runner，支持 native-typed `r()`、`e()`、matrices
- Multi-session via Stata frames
- Per-line error attribution: line number、context、commands_executed
- Graph capture: `png` / `svg` / `pdf` with ref store
- Log truncation with ref store
- Warning extraction: 5 categories + generic notes
- 32-kind error taxonomy with canonical suggestions
- MCP server: 8 tools
- Jupyter kernel: rewired to the v1.0 pipeline
- Matrix size cap + `get_matrix(ref)` for large matrices (>10k cells)
- Cooperative cancellation: `cancel(session_id)` / MCP `cancel_session`
- JSON Schema artifact auto-generated from `schema.py`: [`schema/run_result.schema.json`](schema/run_result.schema.json)
- VS Code extension scaffold ([`vscode/`](vscode/)): `Run Selection`、graph webview、MCP child-process spawn
- Clean-room license policy ([LICENSE-POLICY.md](LICENSE-POLICY.md))

### 下一步 / Next Up

- **v0.3** - Console fallback for Stata 11-16, re-implemented against the v1.0 schema
- **v0.3** - Hard timeout / mid-Stata interrupt; design and tradeoffs in [`docs/design/hard_timeout.md`](docs/design/hard_timeout.md)
- **v0.4** - VS Code Marketplace publishing; the scaffold and graph webview already work in dev host
- **v1.0** - Stable schema, PyPI / VS Code Marketplace publishing

明确不做的范围见 [SCHEMA.md §7](SCHEMA.md)。

See [SCHEMA.md §7](SCHEMA.md) for explicitly out-of-scope items.

---

## 测试 / Testing

```bash
pip install -e ".[dev,mcp,kernel]"
pytest                              # full suite (144 tests)
pytest -m "not stata_required"      # CI subset; no Stata needed
pytest -m "stata_required" -v       # Stata-only integration tests
```

`stata_required` marker 标记真实 Stata 集成测试。CI 使用 `pytest -m "not stata_required"`，因此不会收集这些测试。本地没有 Stata 时，这些测试也会用 `"pystata / Stata 17+ not available"` 信息 cleanly skip。

The `stata_required` marker tags the real-Stata integration tests. CI uses `pytest -m "not stata_required"` so it does not collect them. Locally without Stata, those tests skip cleanly with the `"pystata / Stata 17+ not available"` message.

---

## 贡献 / Contributing

- 提 PR 前请先读 [LICENSE-POLICY.md](LICENSE-POLICY.md)。
  Read [LICENSE-POLICY.md](LICENSE-POLICY.md) before opening a PR.

- 第一个 PR description 里请加一行 acknowledgement，模板在 policy 文件里。
  Add a one-line acknowledgement to your first PR description; the template is in the policy file.

- 新增 schema field 或 runner 行为时必须补测试。
  Tests are required for any new schema field or runner behavior.

---

## 许可证 / License

代码使用 [MIT](./LICENSE)。[LICENSE-POLICY.md](LICENSE-POLICY.md) 说明本项目如何处理和其他 Stata 项目的关系。

The code is licensed under [MIT](./LICENSE). [LICENSE-POLICY.md](LICENSE-POLICY.md) explains how this project relates to other Stata projects.

## 商标声明 / Trademark Notice

Stata 是 StataCorp LLC 的注册商标。本项目是独立项目，不隶属于 StataCorp，也未获得 StataCorp 背书。

Stata is a registered trademark of StataCorp LLC. This project is independent and not affiliated with or endorsed by StataCorp.

## 致谢 / Acknowledgements

本项目参考和学习的 Stata 工具生态整理在 [References-tools.md](References-tools.md)。其中列出的项目保留各自的许可证和作者归属；复用前请查看对应仓库。

The Stata tooling landscape that this project builds on and learns from is surveyed in [References-tools.md](References-tools.md). All listed projects retain their own licenses and authorship; please consult each repository before reuse.
