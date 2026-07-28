<p align="center">
  <img src="branding/logo/horizontal@1024.png" alt="stata-code logo" width="520" />
</p>

<p align="center">
  <a href="README.en.md">English</a> | <a href="README.md"><strong>中文</strong></a>
</p>

# stata-code

[![PyPI](https://img.shields.io/pypi/v/stata-code.svg)](https://pypi.org/project/stata-code/)
[![Python](https://img.shields.io/pypi/pyversions/stata-code.svg)](https://pypi.org/project/stata-code/)
[![License](https://img.shields.io/pypi/l/stata-code.svg)](https://github.com/brycewang-stanford/stata-code/blob/main/LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/brycewang-stanford/stata-code/test.yml?branch=main&label=tests)](https://github.com/brycewang-stanford/stata-code/actions/workflows/test.yml)
[![Downloads](https://static.pepy.tech/badge/stata-code/month)](https://pepy.tech/project/stata-code)
[![VS Code](https://img.shields.io/visual-studio-marketplace/v/brycewang-stanford.stata-code-vscode.svg?label=vscode)](https://marketplace.visualstudio.com/items?itemName=brycewang-stanford.stata-code-vscode)
[![VS Code Installs](https://img.shields.io/visual-studio-marketplace/i/brycewang-stanford.stata-code-vscode.svg)](https://marketplace.visualstudio.com/items?itemName=brycewang-stanford.stata-code-vscode)
[![VS Code Downloads](https://img.shields.io/visual-studio-marketplace/d/brycewang-stanford.stata-code-vscode.svg?label=vscode%20downloads)](https://marketplace.visualstudio.com/items?itemName=brycewang-stanford.stata-code-vscode)
[![Rating](https://img.shields.io/visual-studio-marketplace/r/brycewang-stanford.stata-code-vscode.svg)](https://marketplace.visualstudio.com/items?itemName=brycewang-stanford.stata-code-vscode)
[![GitHub release](https://img.shields.io/github/v/release/brycewang-stanford/stata-code)](https://github.com/brycewang-stanford/stata-code/releases)
[![GitHub stars](https://img.shields.io/github/stars/brycewang-stanford/stata-code?style=social)](https://github.com/brycewang-stanford/stata-code)

<div align="center">

<table>
  <tr>
    <td align="center">
      <a href="https://copaper.ai"><img src="https://raw.githubusercontent.com/brycewang-stanford/stata-code/main/branding/partners/copaper-logo.png" alt="CoPaper.AI" width="200" /></a>
    </td>
    <td width="48"></td>
    <td align="center">
      <a href="https://sccei.fsi.stanford.edu/reap"><img src="https://raw.githubusercontent.com/brycewang-stanford/stata-code/main/branding/partners/stanford-reap-logo.png" alt="Stanford REAP — 斯坦福中国经济与制度研究中心" width="280" /></a>
    </td>
  </tr>
</table>

<sub><strong>Stanford REAP × CoPaper.AI</strong> · 面向实证研究的产学研 AI 工具箱</sub>

</div>

<p align="center">
  <img src="branding/github-instructions.png" alt="stata-code: agent-native Stata bridge — one Python core, multiple frontends (Jupyter kernel, MCP server, VS Code extension)" width="720" />
</p>

> 面向 LLM 智能体的 Stata 桥接工具 —— **一个 Python 核心，多种前端入口**。

`stata-code` 让你可以从现代开发环境中驱动 Stata：LLM 智能体（Claude Code、Cursor、Claude Desktop）、Jupyter notebook，或 VS Code 编辑器。它们共享同一个 Python 核心，并返回稳定、结构化、**适合智能体读取**的结果格式。

**面向实证经济学家。** 用自然语言驱动 Stata：**一次对话里跑完 DiD、IV、RDD 和出版级 `esttab` 表**——再把每个估计在 Stata 与 Python 两套实现上交叉核对，只采信结果一致的那一个（Cunningham 跨包稳健性检验）。

**60 秒上手**（配合 [Claude Code](https://github.com/anthropics/claude-code)，无需全局安装）：

```bash
claude mcp add stata-code --scope user -- uvx --from "stata-code[mcp]" stata-code-mcp
```

然后直接问：

> *“用 `data/cfps_panel.dta` 跑一个工人月工资对处理变量的双向固定效应回归（控制变量 `age age2 edu industry`），再用 Callaway-Sant'Anna 检验异质处理效应，最后输出 `esttab` 表。”*

`stata-code` 会自动写 do 文件、运行、把表格回传并解读结果——还能用 [StatsPAI](https://github.com/brycewang-stanford/StatsPAI) 把同一个 ATT 再估一遍，确认两套结果一致。这些工作流以一键 MCP prompts（`did_event_study`、`iv_2sls`、`rdd`、`publication_table`、`cross_validate_did`）的形式提供，背后是一个按需调用的 [recipe 库](skills/stata-code/references/recipes/)。

**为什么选 `stata-code`：** MIT 许可证 · 同时提供 MCP server、内置 agent skill、Jupyter kernel、VS Code 扩展 **和** 终端命令行（`stata-code run`）· 统一的结构化、省 token 的结果格式（typed errors、原生 `r()` / `e()`）· **支持 Stata 17+（pystata）或 Stata 13+（无需 pystata 的 console 后端）** · 零 Python 独立二进制 · 面向无人值守 agent 的默认命令安全护栏 · 配合 StatsPAI 做跨栈交叉验证（Cunningham 检验）。

```text
                    ┌────────────────────────────────────────┐
                    │     stata-code core (Python)           │
                    │                                        │
                    │   • pystata 17+ / console 13+ backends │
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

此外还有第四个前端——**终端命令行**（`stata-code run` / `lint` / `setup`），让任何能调用 shell 的 agent（或纯终端）拿到同一套带类型的 `RunResult`；core 支持两种后端：**pystata**（Stata 17+，内存会话）或 **console 后端**（Stata 13+ 批处理，无需 pystata），两者返回完全相同的 schema。

**当前状态：v0.11（2026 年 7 月）** —— core、MCP server、Jupyter kernel、VS Code 扩展和命令行都已经在 Stata 18 MP 上端到端跑通；console 后端把覆盖范围扩展到无需 pystata 的 Stata 13+。v0.11 是一个「agent 使用体验」版本：返回体可裁剪、长任务可后台执行、do 文件内报错可定位、失败运行的 log 句柄自动关闭、生成文件自动上报 —— 详见 [changelog](CHANGELOG.md)。测试套件覆盖 schema、runner、console 解析器、MCP、kernel、notebook、run-index、subprocess pool、命令安全、linter 和 VS Code 等模块；CI 也检查 lint、类型、schema 生成、包元数据和 VSIX 打包。许可证：**MIT**。快速上手见 [docs/quickstart.zh.md](docs/quickstart.zh.md)。

当前代码树明确支持的三类用户 / agent 工作流：

- **在 Jupyter notebook 里跑 Stata 代码。** `pip install "stata-code[kernel]"` + `stata-code-kernel install --user` 会注册一个名为 **Stata** 的 kernel，Jupyter Notebook、JupyterLab、以及 VS Code 的 Jupyter 扩展都能在 kernel 选择器里看到它。Cell 里直接写 Stata 命令，日志、图形和警告会内联渲染（自 v0.5 起 kernel logo 已一起打包进 PyPI wheel，VS Code 的 Jupyter kernel picker 也能正常显示）。详见下文 [作为 Jupyter kernel](#作为-jupyter-kernel)。
- **可选的 agent「修复并重跑」循环。** `stata_run` 在每次失败时都会返回结构化的 `error.kind/line/context` 和 `suggestions`。默认情况下 Claude Code 只把它当作诊断信息上报；但如果你明确说「帮我修到跑通」「修复并反复运行直到成功」，agent 就会用同一组字段去改 `.do` 文件、再调 `stata_run`，直到代码通过。这个修复循环是 **opt-in** 的：默认失败 = 诊断，不是自动改写授权。详见下文 [Agent 工作流里的报错恢复](#agent-工作流里的报错恢复)。
- **经济学实证工作流指南。** 随包 skill 和 cookbook 覆盖现代 DiD、IV/弱工具变量、RDD、表格导出、data-MCP 到 Stata 的交接、以及跨包/跨栈 parity audit。`stata-code` 负责运行和审计 Stata 这一侧；R、Python、官方数据 MCP 仍是独立工具，通过显式 handoff 文件和 source metadata 衔接。详见 [`skills/stata-code/references/`](skills/stata-code/references/) 和 [`examples/`](examples/)。

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

要求：**Python 3.10+**，外加以下任一种 Stata —— **Stata 17+**（用其自带的 `pystata`，
支持内存内会话），或 **Stata 13+**（走 console 后端，不需要 `pystata`，无状态）。

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

安装后可以用只读 doctor 检查本机环境：

```bash
stata-code doctor
stata-code doctor --json          # 机器可读输出
stata-code doctor --no-stata-probe # 跳过实时 Stata 初始化
stata-code doctor --workspace /path/to/project --no-user-config-scan
```

doctor 会报告 package/Python 版本、MCP 和 Jupyter extras、`pystata` 发现结果、
`PATH` 上的 console scripts、常见项目级/用户级 MCP client 配置文件、
client/VS Code 配置提示，以及 best-effort 的 Stata 版本/edition 探测。它不会
改 shell、Stata、Claude、Cursor 或 VS Code 配置。

---

## 快速开始

完整 cookbook 在 [`examples/`](examples/)：基础回归、DiD、图形、多 session、大矩阵。

### 作为 Python library

包级 `run()` / `execute()` API 使用和 MCP server 相同的 subprocess-backed
runner，因此长任务会遵守 `timeout_ms`，`pystata` 对 stdout 的重定向也会被隔离在 worker 进程中，不会污染调用方进程。

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

### 从命令行使用（Bash）

任何能 shell out 的 agent 或脚本都能用上同一套结构化引擎 —— **不需要 MCP**。
`stata-code run` 可以执行 `.do` 文件、一个或多个 `-e` 片段，或从 stdin 读入的代码，
并打印 `RunResult`：

```bash
stata-code run analysis.do                  # 跑 do 文件，输出文本摘要
stata-code run -e "sysuse auto" -e "regress mpg weight"
stata-code run analysis.do --json           # 完整 RunResult JSON（给 agent 用）
echo "summarize price" | stata-code run -    # 从 stdin 读代码
stata-code run model.do --graphs out/        # 同时把图形导出到 out/
stata-code run job.do --session modelA --timeout-ms 120000
```

成功退出码是 `0`，Stata / adapter 出错时是 `1`，因此可以直接接进 CI 和"改了再跑"的
脚本循环。`stata-code lint analysis.do` 则在完全不启动 Stata 的前提下做静态检查
（大括号不配对、缺 `end`、悬空的 `///`）。

**后端 —— Stata 13+ 且没有 pystata。** `--backend` 决定代码怎么跑：`pystata`
（Stata 17+，内存内会话）、`console`（Stata 13+ 批处理，不需要 pystata，无状态），
或 `auto`（默认：有 pystata 就用，否则退到 console）。console 后端驱动 Stata 的命令行
可执行文件，并把日志解析成同样的带类型 `RunResult` —— 带类型的 `r()`/`e()`、估计表、
以及错误分类 —— 所以老版本 Stata 和无 pystata 的环境同样是一等公民：

```bash
stata-code run analysis.do --backend console
export STATA_CODE_STATA_CLI=/usr/local/stata18/stata-mp   # 自动找不到时手动指定
```

**零 Python 二进制。** 独立的 `stata-code` 可执行文件（由
[`scripts/build_standalone.py`](scripts/build_standalone.py) 构建，现成的 CI workflow
模板见 [`packaging/standalone.github-workflow.yml`](packaging/standalone.github-workflow.yml)）
把运行时一起打包，不需要安装 Python。配合 `--backend console`，这就是一条完全不依赖
Python、又能拿到带类型 Stata 结果的路径。

### 会话 daemon（跨调用保持数据）

`stata-code run` 默认每次调用都起一个新进程，进程一退，内存里的数据就没了。
`daemon` 子命令把 subprocess pool 挪进一个常驻进程，通过 Unix socket 对外服务，
于是连续两次 `run --daemon` 会落在**同一个 Stata 会话**上：

```bash
stata-code run --daemon -e 'sysuse auto, clear' -e 'gen z = price/1000'
stata-code run --daemon -e 'summarize z'      # 新进程，但 z 还在
stata-code run --daemon -e 'regress price mpg'
stata-code run --daemon -e 'display e(r2)'    # 上一个进程留下的 e() 也还在
```

第一次 `run --daemon` 会自动把 daemon 拉起来，不需要手动 start。也可以显式管理：

```bash
stata-code daemon start                    # 后台启动（--foreground 留在当前终端）
stata-code daemon status                   # pid、uptime、活跃 session 列表
stata-code daemon status --json
stata-code daemon stop
stata-code daemon restart
stata-code daemon start --idle-timeout 0   # 0 = 永不因空闲退出
```

几个要点：

- **会话隔离照旧。** `--session other` 在 daemon 里仍然是独立的 Stata frame / worker，
  彼此看不到对方的数据。
- **默认空闲 30 分钟自动退出**，免得忘了关的 daemon 一直占着 Stata license。
  用 `--idle-timeout` 调整，`0` 表示禁用。
- **只监听 Unix socket，不开 TCP。** daemon 会执行任意 Stata 代码，所以刻意只暴露在
  mode-0700 目录下的 mode-0600 socket 上。[命令安全](#命令安全)护栏依然生效。
- **需要 pystata 后端。** `--backend console` 本身就是无状态批处理，套 daemon 没有意义，
  因此会直接报错而不是静默失效。
- **socket 路径过长时自动降级。** `sun_path` 上限约 104 字节；home 目录很深或
  `XDG_RUNTIME_DIR` 很长时，会自动改用 `/tmp` 下一个由原路径哈希得出的短路径。
  所有入口都用同一套推导，因此仍能找到同一个 daemon。

`stata-code run`（不加 `--daemon`）、`run --daemon` 和 MCP server 三条路走的是同一个
引擎、返回同一份 `RunResult` schema，区别只在于 Stata 会话活多久。

### 一条命令配置客户端

`stata-code setup` 把 MCP server 条目写进客户端配置 —— 它是只读的 `doctor` 那个
「会改文件」的对应物。它会保留配置里其他的 server，并在覆盖任何文件之前先做备份：

```bash
stata-code setup --all                 # Claude Code、Cursor、VS Code（project scope）
stata-code setup --claude --dry-run    # 只预览，不写入
stata-code setup --vscode --python .venv/bin/python   # 指定解释器
stata-code setup --codex               # 打印可复制粘贴的 TOML 片段
```

### 命令安全

默认情况下，runner 会在命令抵达 Stata **之前**拦截 OS 逃逸和文件删除类命令
（`shell`、`winexec`、`erase`、`rm`、`rmdir`，以及 `!` shell 转义），因此一个自主运行的
agent 循环没办法删文件或执行任意 shell 命令。被拦截时返回 `policy_blocked` 结果
（`rc=-4`），而不是真的去执行。这是护栏，不是沙箱；可以用环境变量调整：

```bash
STATA_CODE_COMMAND_POLICY=off      # 完全关闭护栏
STATA_CODE_POLICY_ALLOW=shell      # 放行特定命令（逗号分隔）
STATA_CODE_POLICY_BLOCK=python     # 额外拦截某些命令
```

### 作为 MCP server

`pip install "stata-code[mcp]"` 之后，`stata-code-mcp` 会出现在你的 `PATH` 中。可以接到 Claude Code、Cursor、Claude Desktop 等任何兼容 MCP 的客户端里。

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

接着运行 `claude`，输入 `/mcp` 确认 `stata-code` 出现并带有 21 个工具（`stata_run`, `stata_run_status`, `list_background_runs`, `stata_info`, `get_log`, `search_log`, `get_graph`, `get_matrix`, `inspect_data`, `lint_do`, `install_package`, `list_sessions`, `cancel_session`, `reset_session`, `notebook_outline`, `notebook_get_cell`, `notebook_locate`, `notebook_edit_cell`, `notebook_insert_cell`, `notebook_delete_cell`, `list_runs`）。

#### Agent 工作流里的报错恢复

`stata_run` 不会自行改写源 `.do` 文件或替你改代码。它执行提交的 Stata 代码，所以代码本身仍可能照常生成日志、图形、表格或其他输出。Stata 报错时，`stata_run` 返回结构化诊断（`error.kind`, `error.message`, `error.line`, `error.context`）和尽力生成的 `suggestions`。这支持两种不同的 Claude Code 工作流：

- 如果你说的是「运行这个 do-file」或「验证这段代码」，Claude 可以只报告失败原因和建议的下一步，不修改源文件。
- 如果你明确说「帮我修到跑通」或「修复并反复运行直到成功」，Claude 可以基于同一组结构化错误字段修改 `.do` 文件，再调用 `stata_run` 继续迭代。

如果需要自动修复循环，请明确说出来。否则，失败的运行应先被视为诊断结果，而不是自动改写代码的授权。

#### 用 `uvx`（不必全局 pip install）

如果不想全局 `pip install stata-code`，可以用 [`uv`](https://github.com/astral-sh/uv) 临时运行：

```bash
claude mcp add stata-code --scope user -- uvx --from "stata-code[mcp]" stata-code-mcp
```

`uvx` 会在首次启动时下载并缓存 `stata-code`。注意：`pystata` **不在 PyPI 上**，仍需要在宿主机上能找到。runner 会自动把标准 Stata 安装路径（macOS 上的 `/Applications/Stata/utilities/pystata` 等）加到 `sys.path`；如果你的 Stata 在别处，请用 env 设置 `PYTHONPATH`。

#### 通过 plugin marketplace 接入 Claude Code

本仓库还带有 Claude Code 插件清单（`.claude-plugin/`）。把 marketplace 加入你的 Claude Code 配置后，两条命令即可同时接好 MCP server 和教会 Claude v1.0 结果 schema 的 agent skill：

```bash
claude plugin marketplace add brycewang-stanford/stata-code
claude plugin install stata-code
```

插件会注册 `stata-code` MCP server，并安装 [`stata-code` skill](skills/stata-code/SKILL.md)，让 Claude 学会按 `error.kind` 分支、惰性调用 `get_log(ref)`、并直接使用 notebook 编辑工具，无需每个会话重新解释。

#### 其它 MCP client（Cursor / Claude Desktop / Cline / Continue / Windsurf / Antigravity）

大多数非 Claude Code 的 MCP client 都接受同一段 JSON 配置。把它放进对应 client 的 MCP 配置文件即可：

| Client | 配置文件 |
| --- | --- |
| Claude Desktop | macOS：`~/Library/Application Support/Claude/claude_desktop_config.json`；Windows：`%APPDATA%\Claude\claude_desktop_config.json` |
| Cursor | `~/.cursor/mcp.json`（用户级）或 `<workspace>/.cursor/mcp.json`（项目级） |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` |
| Cline（VS Code） | settings 中的 `cline.mcpServers` |
| Continue | `~/.continue/config.json` 的 `experimental.modelContextProtocolServers` |
| Antigravity / 通用 | `~/.claude/mcp.json` 或该 client 文档指定的位置 |

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

如果 `stata-code-mcp` 安装在项目内 virtualenv（推荐用于可复现环境），建议在
client 配置里写绝对路径，例如 `/abs/path/to/.venv/bin/stata-code-mcp`。

#### MCP 故障排查

如果 `stata_run` 返回 `adapter_crash`，并出现 `worker emitted non-JSON: '\n'`，
请升级到 `stata-code>=0.6.4`，然后重启 MCP client，让它启动新的 server 进程。
同时确认 client 解析到的是预期的 `stata-code-mcp`；项目内 virtualenv 安装
应使用 `.venv/bin/stata-code-mcp` 的绝对路径，不要依赖全局 `PATH`。

如果 OpenAI 系客户端返回 `API Error: 400 Invalid schema for function
'mcp__stata-code__notebook_insert_cell'`，并提到顶层 `oneOf`，请升级到
`stata-code>=0.6.5`，然后重启 MCP client。旧 server 进程在重启前仍会继续
暴露旧 schema。

MCP server 注册了 21 个工具：

| 工具 | 用途 |
| --- | --- |
| `stata_run` | 执行 Stata code，返回 v1.0 RunResult JSON；支持 `include_results` / `include_estimation` 控制返回体大小，`timeout_ms` 硬超时，`run_in_background` 后台执行 |
| `stata_run_status` | 轮询后台运行（`run_in_background=true`）的状态与结果，可用 `wait_ms` 阻塞等待 |
| `list_background_runs` | 列出本 server 追踪的后台运行 |
| `stata_info` | 返回 Stata edition、version 和 capabilities |
| `get_log` | 通过 `log://` ref 获取完整日志 |
| `search_log` | 在已存储的 `log://` payload 内搜索匹配行 |
| `get_graph` | 通过 `graph://` ref 获取图形 bytes（`ImageContent`） |
| `get_matrix` | 通过 `matrix://` ref 获取矩阵 `{rows, cols, values}` |
| `inspect_data` | 运行 `describe` + `codebook`，返回紧凑的数据集元数据 |
| `lint_do` | 在执行前静态检查 do 文件源代码（花括号不匹配、缺少 `end`、悬空 `///`） |
| `install_package` | 安装 SSC 或显式 `net install` 包，并验证命令可解析 |
| `list_sessions` | 列出 live sessions |
| `cancel_session` | 取消某个 session；subprocess-backed 路径会终止运行中的 worker，也会短路尚未开始的运行 |
| `reset_session` | 清空某个 session 的数据 |
| `notebook_outline` | `.ipynb` 的 cell 索引（cell_id、类型、源代码预览） |
| `notebook_get_cell` | 单个 cell 的完整源代码 + 节流版输出摘要 |
| `notebook_locate` | 用 snippet / regex / 报错文本定位 cell |
| `notebook_edit_cell` | 原子替换 cell 源代码（保留 id，清空 outputs） |
| `notebook_insert_cell` | 插入新 cell，分配新的 nbformat 4.5+ UUID |
| `notebook_delete_cell` | 按 id 删除 cell |
| `list_runs` | 查询 run-bundle manifest（按 notebook / cell_id / session / since / ok 过滤，用 limit / offset 翻页） |

对于新版 MCP 客户端，这些工具会返回 `structuredContent`，并在 tool
metadata 里声明 `outputSchema`；同时仍保留序列化 JSON text block，兼容旧客户端。
server 还暴露 MCP resources：

| Resource | 用途 |
| --- | --- |
| `stata://schema/run-result` | `stata_run` 结构化输出的 JSON Schema |
| `stata://server/capabilities` | server instructions、tools、resource templates |
| `stata://sessions` | 当前 subprocess-backed Stata sessions |
| `log://...` | 被截断运行结果背后的完整日志 |
| `graph://...` | 捕获到的 graph image bytes |
| `matrix://...` | 延迟获取的大矩阵 payload |

同时提供 MCP prompts：`run_do_file_and_report`、`debug_stata_error`、
`fix_and_rerun_until_passes`、`replication_audit`、
`plan_cross_stack_parity_audit`、`data_mcp_to_stata_handoff`、
`summarize_estimation_results`、`run_notebook_cell_and_report`、
`fix_and_rerun_notebook_cell`、`did_event_study`、`iv_2sls`、`rdd`、
`publication_table` 和 `cross_validate_did`，用于常见 agent 工作流。

### 作为 Jupyter kernel

`stata-code` 的 Jupyter 支持是以 **kernel** 形式打包在 Python 包里的 —— JupyterLab 插件市场里**没有**独立的 "stata-code 插件"。安装分两步：先 `pip install` 安装带 `kernel` extra 的包，再把 kernelspec 注册到 Jupyter。

**前置条件**：本机已经安装 Stata 17+ 且持有合法许可证（kernel 通过 `pystata` 调用本地 Stata），同一个 Python 环境里已经装好 `jupyter`/`jupyterlab`，Python 版本 ≥ 3.10。

```bash
# 1. 安装带 kernel extra 的 stata-code（会同时装上 ipykernel）
pip install "stata-code[kernel]"

# 2. 把 kernelspec 注册到当前用户的 Jupyter data dir
stata-code-kernel install --user
# 等价命令：
# python -m stata_code.kernel install --user
```

检查 kernel 是否注册成功：

```bash
jupyter kernelspec list
# 输出里应该能看到名为 `stata` 的条目
```

然后打开 Jupyter Notebook / JupyterLab（或 VS Code 中的 `.ipynb`），在 kernel 选择器里挑 **Stata**，cell 里直接写 Stata 命令即可，日志、graphs 和 warnings 会以内联方式显示。

> JupyterLab 的 Extension Manager 只能安装前端 JS 扩展，**装不了 kernel**。所以上面的 `pip install` + `install --user` 是唯一支持的安装路径。

### 作为 VS Code 扩展

配套扩展已发布到 Marketplace：[`brycewang-stanford.stata-code-vscode`](https://marketplace.visualstudio.com/items?itemName=brycewang-stanford.stata-code-vscode)。它会以子进程方式启动 `stata-code-mcp`，并提供语法高亮、`**#` section 和 `program define` 的 Outline、`.do` 文件的 code-lens "Run cell" / "Run section"、**七视图侧边栏**（sessions / last result / **data 变量浏览器** / run history / logs / graphs / **outputs**）——其中包含一个 agent-native 版的 Stata **变量窗口**，以及一个把每次运行写到磁盘的 `esttab` 表格和 `export` 文件呈现出来的 **Outputs** 面板——状态栏指示器、补全、帮助跳转、保守变量重命名，以及来自 v1.0 typed errors 的内联诊断。

```bash
# 从 VS Code 命令行
code --install-extension brycewang-stanford.stata-code-vscode
```

或者打开 VS Code 的 **Extensions** 侧栏，搜索 `stata-code`。扩展同时发布在 [Open VSX](https://open-vsx.org/)，因此 Cursor、Windsurf 等 VS Code 兼容编辑器不必经过微软 Marketplace 也能安装。

首次激活时，扩展会在 `PATH`（以及 workspace 下的 `.venv` / `venv`）里探测 `stata-code-mcp`。如果找不到，会弹出一次性的安装提示，附上准确的 `pip install "stata-code[mcp]"` 命令——选择 **Don't show again** 可对当前扩展版本永久静默。

扩展仍然依赖系统 Python 上安装了 MCP extra（`pip install "stata-code[mcp]"`），从而保证 `stata-code-mcp` 在 `PATH` 上可用，并且能导入 MCP SDK。和其它前端一样，需要 Stata 17+ 和有效的 Stata 许可证。

#### Cell 与 section 约定

扩展识别 `.do` 文件里两类互补的结构标记。二者可以混用在同一个文件里，互不冲突：

| 标记 | 用途 | 示例 |
| --- | --- | --- |
| `* %% [标题]` | Cell 边界。每个标记有一个 **▶ Run Cell** code-lens；"Run Cell" 提交该标记到下一个标记之间的内容。与 `kylebutts/vscode-stata` 的 Jupyter 风格 cell 约定兼容。 | `* %% 02 model fit` |
| `**# 标题` … `**###### 标题` | Section 标题，1–6 级。每个标题有一个 **▶ Run Section** code-lens，并进入 Outline 视图。"Run Section" 提交该标题到下一个同级或更高级标题之间的内容，与 `ZihaoVistonWang.stata-all-in-one` 的层级执行模型一致。 | `**## DiD specification` |

`program define … end` 代码块也会出现在 Outline 里，嵌套在所属 section 之下。

如果扩展或 MCP client 找不到 server，请在同一个 Python 环境里运行
`stata-code doctor --no-stata-probe`。它会报告 `stata-code-mcp` 是否在
`PATH` 上，并提示 GUI client 常见的绝对路径或 `python -m stata_code.mcp`
兜底配置。它也会读取当前 workspace 和用户目录里常见的 MCP 配置文件，告诉你
client 是否已经指向 `stata-code`。

---

## 默认节省 token

典型的 `stata_run` 响应比现有 MCP server 直接返回日志和图片的方式小约 **10 倍**。核心设计有四点：

1. **日志默认只返回 `head` + `tail` + `ref`**。完整日志可以按需用 `get_log(ref)` 获取，也可以用 `search_log(ref, pattern)` 直接在 ref 里检索。Stata 回归日志可能有约 6,000 tokens，`stata-code` 默认约 600 tokens。
2. **图形默认返回 refs，不内联 base64**。一个 30 KB PNG 转成 base64 约 50,000 tokens；返回 ref 可以让智能体只在真正需要渲染时再取 bytes。确实需要内联时，图形会以真正的 MCP image content block 返回 —— 视觉模型能直接看到，而不是塞在 JSON 字符串里、既烧 token 又看不见的 base64。
3. **一次估计只描述一次**。默认（`include_results: "scalars"`）保留 `r()` / `e()` 的 scalars 和 macros，而把每个矩阵降级成只带形状的 `matrix://` stub。否则同一批数字会被编码四遍 —— `e(b)`、`e(V)` 的行列标签、`e(beta)`、`r(table)` —— 而 `results.estimation` 里其实已经有带类型的系数表了。一个 123 项的回归返回体从约 57 KB 降到约 28 KB。原始数值仍然只差一次 `get_matrix(ref)`；`include_results: "full"` 可恢复旧行为。
4. **错误是结构化 typed errors**。智能体可以判断 `err.kind == "varname_not_found"`，而不是正则解析英文日志。

固定效应很多的场景还有两个开关：`include_estimation: "summary"` 保留模型层面的信息但丢掉逐项系数，`max_coefficients` 则给系数表设上限。两种情况下 `estimation.n_coefficients` 都仍然报告模型真实的项数，`coefficients_truncated` 会标记被截断 —— 所以裁剪过的表不会被误当成一个更小的模型。

例如，变量名写错时返回的是结构化错误：

```json
{
  "ok": false,
  "rc": 111,
  "error": {
    "kind": "varname_not_found",
    "varname": "mpgg",
    "line": 3,
    "source_file": null,
    "context": {
      "before": ["use auto"],
      "failing": "summarize mpgg",
      "after": []
    },
    "suggestions": [
      {"action": "Did you mean `mpg`?", "command": "describe"}
    ],
    "recovery": {
      "category": "user_code",
      "retriable": false,
      "needs_code_change": true,
      "needs_user_input": false
    }
  }
}
```

当出错的命令位于你调用的脚本里（`do "analysis.do"`）时，`line` 和 `context` 指向的是**那个脚本内部**的行，`source_file` 给出文件路径 —— 智能体不需要再把文件重新读一遍去找出错行。

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
│   ├── runner.py      # in-process execute(); collects everything via sfi
│   └── _pool.py       # subprocess workers for public API / MCP hard timeouts
├── mcp/
│   └── server.py      # MCP server (21 tools)
└── kernel/
    └── kernel.py      # Jupyter kernel
```

`runner.py` 是唯一直接接触 `pystata` 的地方。包级 Python API 和 MCP server 会先走 `_pool.py`，由隔离的 worker subprocess 调用 `runner.execute()`；Jupyter kernel 为了 notebook 交互性仍使用 in-process runner。

---

## 对比

| | stata-code | SepineTam/stata-mcp | hanlulong/stata-mcp | nbstata |
| --- | --- | --- | --- | --- |
| 许可证 | **MIT** | AGPL-3.0 | MIT | GPL-3.0 |
| 独立 MCP | ✓ | ✓ | 与 VS Code 捆绑 | — |
| Jupyter kernel | ✓ | — | — | ✓ |
| 统一结果格式 | ✓ ([SCHEMA.md](SCHEMA.md)) | per-tool | per-tool | per-tool |
| 默认节省 token | ✓ (log refs, graph refs) | — | — | — |
| 结构化错误和建议 | ✓ (34 kinds) | — | — | — |
| 多 session | ✓ (Stata frames) | partial | — | — |
| 生态成熟度 | early | ✓ (statamcp.com, cookbook) | ✓ (11k installs) | ✓ |

`stata-code` 是这个问题空间里更年轻的、MIT 许可证的、agent-native 的替代方案。AGPL 方案里，SepineTam 的 `stata-mcp` 目前更成熟；`stata-code` 的目标是服务那些不能接受 copyleft 传染、又需要结构化智能体接口的场景。

### 和直接调用 Stata 命令行比

先把话说明白：**你完全可以不装任何工具，直接从 shell 调 Stata。** 大多数 Stata
安装都自带一个命令行可执行文件，一次性执行的场景下它挺好用，没必要为此引入依赖：

```bash
/Applications/Stata/StataMP.app/Contents/MacOS/stata-mp -b do analysis.do
printf 'sysuse auto, clear\nsummarize mpg\nexit, clear\n' | stata-mp -q
```

值得知道的是这条路会在哪里失效。下面两列跑的是**同一个 Stata 二进制、同一份日志**
（`stata-code` 用的是 `--backend console`），差别只在于外面那层包装：

| | 裸 `stata-mp -b` | `stata-code run` |
| --- | --- | --- |
| 出错时的退出码 | **`0`** —— 静默失败 | `1` |
| 怎么知道出了错 | 自己 grep 日志里的 `r(111);` | `ok=False`、`rc=111` |
| 错误分类 | 无 | `[varname_not_found]`（共 34 类） |
| 修复建议 | 无 | "Run `describe` to list available variables" |
| 取回归系数 | 从对齐的 ASCII 表里抠 | `e.scalars["r2"] → 0.6515312529087511`（float） |
| 跨调用保持数据 | 不能 | `run --daemon`（见下节） |

第一行是最容易吃亏的地方：Stata 批处理模式**即使代码报错也返回退出码 0**。
在自动化循环里，agent 看到 `exit 0` 就会继续往下走，把失败的结果当成有效结果用。

选择建议：

- **一次性执行**（"跑一下这个 do 文件给我看结果"）—— 裸命令行就够了，别过度设计。
- **脚本 / CI**（需要靠退出码判断成败）—— 用 `stata-code run`，最小代价换到可靠信号。
- **迭代式分析**（多轮改设定、读中间结果、基于 `e()` 决定下一步）—— 用
  `run --daemon` 或走 MCP，否则无状态和文本解析这两个问题会在多轮里叠加放大。

> 不加 `--daemon` 时，`stata-code run` **每次调用都起一个新进程**：同一次调用里的
> 多个 `-e` 共享 session，但进程退出后内存里的数据就没了。加上 `--daemon` 之后
> 数据会留在常驻进程里，详见 [会话 daemon](#会话-daemon跨调用保持数据)。

---

## 路线图

### 已完成（当前代码树）

- v1.0 result schema ([SCHEMA.md](SCHEMA.md))
- 基于 `pystata` 的 runner，原生类型化的 `r()`、`e()` 和矩阵
- 通过 Stata frames 支持多 session（`session_id` 接受 `[A-Za-z0-9_-]+`；例如 `model-a` 这类 id 会在内部映射到合法的私有 frame 名，但返回结果仍回显公开 id）
- 常驻会话 daemon（`stata-code daemon`、`run --daemon`）：多次 CLI 调用共享同一个活的 Stata 会话，走 mode-0600 Unix socket，支持空闲自动退出
- 行级错误归属：line number、context、commands_executed
- 图形捕获：`png` / `svg` / `pdf` + ref store，并记录来源命令归属
- 日志截断 + ref store
- 警告抽取：5 类 + 通用 notes
- 34 类错误分类法 + 标准化建议，以及机器可读的 `recovery` 判定（可重试 / 需改代码 / 需人工介入）
- MCP server：21 个工具，覆盖执行、notebook 导航 / 检索 / 原子化编辑、运行索引（`list_runs`）、日志检索（`search_log`）、数据集检查（`inspect_data`）、静态检查（`lint_do`）和包安装（`install_package`）
- 命令安全护栏：`shell`、`winexec`、`erase`、`rm`、`rmdir`、`!` 等 OS 逃逸 / 删除文件命令在执行前被拦截；可通过 `STATA_CODE_COMMAND_POLICY` / `STATA_CODE_POLICY_ALLOW` / `STATA_CODE_POLICY_BLOCK` 配置
- Bash / 终端入口：`stata-code run`（`.do` 文件、`-e` 片段或 stdin）打印同一套结构化 `RunResult`，任何能调用 shell 的 agent 都可消费；`stata-code lint` 运行静态检查；`stata-code setup` 写入 MCP 客户端配置
- Console（批处理）后端（`core/console.py`、`--backend console`、`run_console()`）：驱动 Stata 命令行、把日志解析成同一套带类型的 `RunResult`，支持 **Stata 13+ 且无需 pystata**
- 零 Python 独立二进制（`scripts/build_standalone.py` + CI 工作流模板 `packaging/standalone.github-workflow.yml`）；配合 `--backend console` 是完全不依赖 Python 的一条龙
- VS Code 一键上手：扩展可自动在工作区创建 `.venv` 并安装 server（命令面板：“Stata: Set Up MCP Server”）
- 中文快速上手指南：[docs/quickstart.zh.md](docs/quickstart.zh.md)
- Jupyter kernel：接入 v1.0 pipeline，kernel logo 已随 wheel 一起打包
- 返回体预算：`include_results`（默认把矩阵降级为 `matrix://` stub）、`include_estimation`、`max_coefficients`，需要原始数值时用 `get_matrix(ref)` 按需拉取
- 公共 Python API 和 MCP server 的 subprocess-backed 硬超时与取消：`timeout_ms`（现已成为 `stata_run` 的正式入参，并且把排队时间也计入预算 —— 同一 session 冲突时返回 `rc=-5` / `session_busy`，不再无限阻塞）、`cancel(session_id)`、MCP `cancel_session`
- 长任务后台执行：`run_in_background` 立即返回 job id，用 `stata_run_status`（可 `wait_ms` 有界阻塞）和 `list_background_runs` 轮询
- `do` / `run` 脚本内部的报错定位：`error.line` 和 `error.context` 落在被调用文件内，`error.source_file` 给出文件路径；失败的运行同样带有完整可检索的 log
- log 句柄卫生：失败运行泄漏的 log 句柄会被自动关闭（`auto_close_logs`），中途 abort 的脚本不会让该 session 后续每次运行都以 r(604) 失败
- 生成文件上报：`result.outputs` 列出每次运行写出的表格、导出文件和数据集，与 run bundle 选项无关
- `.ipynb` 单 cell 修复闭环：`notebook_outline` / `notebook_get_cell` / `notebook_edit_cell`，并通过 `expected_source` 做乐观并发控制；`stata_run` 回显 `origin_cell_id`
- 持久化 run bundle + `list_runs`：按 cell / origin / session / since / ok 查询 `manifest.json`，并用 limit / offset 翻页
- 只读 `stata-code doctor` / `verify` 诊断：检查 package 版本、extras、
  `pystata` 发现、console scripts、client 配置提示，以及可选的实时 Stata
  版本探测
- 经济学实证工作流层：现代 DiD、IV/弱工具变量、RDD、表格导出、data-MCP handoff、跨包/跨栈 parity audit 的 skill references 和 cookbook examples
- 从 `schema.py` 自动生成 JSON Schema 工件：[`schema/run_result.schema.json`](schema/run_result.schema.json)
- VS Code 扩展已发布到 Marketplace [`brycewang-stanford.stata-code-vscode`](https://marketplace.visualstudio.com/items?itemName=brycewang-stanford.stata-code-vscode)：语法高亮、section outline/navigation、code-lens cell/section runner、七视图侧边栏（sessions / last result / data 变量浏览器 / run history / logs / graphs / outputs）、状态栏、补全、保守变量重命名、诊断、MCP 子进程
- Clean-room 许可证策略 ([LICENSE-POLICY.md](LICENSE-POLICY.md))

### 下一步

- 长任务的流式 / 增量进度（`log.complete:false`、部分日志行）。v0.11 的 `run_in_background` 已经让 20 分钟的 `boottest` / `csdid` 不再阻塞调用方，但运行中的任务在结束前仍然不汇报任何中间结果
- Stata 11–16 的 console fallback，按 v1.0 schema 重新实现
- 决定 Jupyter kernel 是否也迁到 subprocess pool，或者继续清楚记录当前为了交互性保留 in-process runner 的取舍
- VS Code 体验打磨：Extension Host 端到端测试、首次启动诊断、命令面板 UX
- **v1.0** —— 稳定 schema，覆盖更广的 Stata edition

明确不做的范围见 [SCHEMA.md §7](SCHEMA.md)。

---

## 测试

```bash
pip install -e ".[dev,mcp,kernel]"
pytest                              # 完整测试集；本机有 Stata 时包含真实 Stata 集成测试
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

---

<div align="center">

<table>
  <tr>
    <td align="center">
      <a href="https://copaper.ai"><img src="https://raw.githubusercontent.com/brycewang-stanford/stata-code/main/branding/partners/copaper-logo.png" alt="CoPaper.AI" width="200" /></a>
    </td>
    <td width="40"></td>
    <td align="center">
      <a href="https://sccei.fsi.stanford.edu/reap"><img src="https://raw.githubusercontent.com/brycewang-stanford/stata-code/main/branding/partners/stanford-reap-logo.png" alt="Stanford REAP" width="280" /></a>
    </td>
  </tr>
</table>

<table>
  <tr>
    <td align="center">
      <a href="https://copaper.ai"><img src="https://raw.githubusercontent.com/brycewang-stanford/stata-code/main/branding/partners/copaper-qrcode.png" alt="访问 copaper.ai" width="160" /></a><br/>
      <strong>访问 <a href="https://copaper.ai">copaper.ai</a></strong>
    </td>
    <td align="center">
      <img src="https://raw.githubusercontent.com/brycewang-stanford/stata-code/main/branding/partners/copaper-wechat.jpg" alt="CoPaper.AI 微信" width="160" /><br/>
      <strong>微信公众号：CoPaper.AI</strong>
    </td>
  </tr>
</table>

<sub>由 <a href="https://copaper.ai"><strong>CoPaper.AI</strong></a> 维护，孵化于 <a href="https://sccei.fsi.stanford.edu/reap"><strong>Stanford REAP / SCCEI</strong></a> · 实证研究 AI 助手</sub>

</div>
