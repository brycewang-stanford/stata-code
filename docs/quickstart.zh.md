# stata-code 快速上手（中文）

面向实证/计量研究者：让 AI agent（Claude Code、Cursor、Codex 等）直接、可靠地
运行你的 Stata，并拿到**结构化、带类型的结果**——而不是一堆需要 agent 去猜的日志文本。

本页覆盖从零到跑通的最短路径。完整文档见 [README.md](../README.md) 与
[SCHEMA.md](../SCHEMA.md)。

---

## 三种安装方式，按“折腾程度”从低到高

### 1) 零 Python：下载独立二进制（最省事）

不需要安装 Python，也不需要配置任何环境。从 Releases 下载对应平台的
`stata-code` 可执行文件即可：

```bash
# 直接用（示例为 Linux）
./stata-code --version
./stata-code run analysis.do --backend console
./stata-code lint analysis.do
```

配合 **console 后端**（见下），二进制 + Stata 命令行 = **完全不依赖 Python** 的
一条龙：agent 写代码 → 跑 Stata → 拿到带类型的 `RunResult`。

### 2) VS Code 扩展一键装

在 VS Code / Cursor 里安装扩展
`brycewang-stanford.stata-code-vscode`。若它检测到 MCP server 未安装，会弹出
**“Install automatically”**：点一下，它会在工作区里建一个 `.venv` 并装好
`stata-code`，无需你手动敲 pip。也可在命令面板运行
**“Stata: Set Up MCP Server (create .venv)”**。

### 3) pip 安装（给已有 Python 环境的用户）

```bash
pip install "stata-code[mcp]"
stata-code doctor          # 只读体检：Python、pystata、Stata CLI、客户端配置
```

---

## 两种执行后端，覆盖 Stata 13+ 到 19

| 后端 | 需要 | 适用 | 特点 |
| --- | --- | --- | --- |
| `pystata`（默认） | Stata **17+** + pystata | 交互式、多 session | 数据常驻内存，跨调用保持会话；硬超时/取消 |
| `console` | Stata **13+** 命令行 | 批处理、无 pystata | 无需 Python 依赖；每次调用无状态；暂不抓图 |

命令行里用 `--backend` 选择（默认 `auto`：有 pystata 用 pystata，否则回退 console）：

```bash
stata-code run analysis.do --backend console
```

找不到 Stata 命令行时，设置环境变量指向它：

```bash
export STATA_CODE_STATA_CLI=/usr/local/stata18/stata-mp   # 例
```

无论哪个后端，返回的都是**同一套 v1.0 结构化结果**：带类型的 `r()`/`e()`、
估计系数表、32 类错误分类 + 修复建议。

---

## 接入 Claude Code（MCP）

```bash
stata-code setup --claude        # 写入项目级 .mcp.json（会保留其它 server、自动备份）
# 或： stata-code setup --cursor / --vscode / --all
```

然后运行 `claude`，`/mcp` 里应能看到 `stata-code` 及其 19 个工具
（`stata_run`、`lint_do`、`inspect_data`、`install_package` 等）。

---

## 命令安全护栏（默认开启）

为了让 agent 能放心地“无人值守”跑，`shell`、`winexec`、`erase`、`rm`、`rmdir`、
`!` 等会操作系统/删文件的命令会在**执行前被拦截**，返回
`policy_blocked`（不会真正运行）。可按需放宽：

```bash
STATA_CODE_COMMAND_POLICY=off      # 完全关闭
STATA_CODE_POLICY_ALLOW=shell      # 放行指定命令（逗号分隔）
```

---

## 一分钟自检

```bash
stata-code doctor                  # 环境体检
stata-code lint analysis.do        # 跑前静态检查：花括号、缺 end、悬空 ///
stata-code run -e "sysuse auto, clear" -e "regress mpg weight" --json
```

看到 `"ok": true` 和带类型的 `results.estimation` 就说明整条链路通了。
