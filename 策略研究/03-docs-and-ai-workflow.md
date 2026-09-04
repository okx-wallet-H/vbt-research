# 03 — 文档学习路径 + AI 工作流集成

> 研究目标：VectorBT® PRO（v2026.6.27）的官方文档学习地图，以及它区别于其他量化库的核心特色 —— 对 AI 助手的深度友好（llms.txt + MCP server + ChatVBT + CLI）。

---

## 一、完整学习地图（六大板块 + 推荐顺序）

### 1.1 六大板块

稳定链接网站 `https://vectorbt.pro/pvt_ff8edc14/` 的顶部导航即六个板块，各解决一类问题：

| 板块 | 入口 | 解决什么问题 | 关键子页 |
|------|------|-------------|---------|
| **Getting started** | `getting-started/installation/` | 安装、环境、发布说明 | Installation、Release notes |
| **Features** | `features/overview/` | 功能总览，含 Intelligence（AI）板块 | `features/intelligence/`（ChatVBT/SearchVBT/MCP） |
| **Tutorials** | `tutorials/overview/` | 从零到一的实战教程（10 个） | 见 1.3 |
| **Documentation** | `documentation/overview/` | 概念讲解 + 各模块文档 | fundamentals、building-blocks、indicators、portfolio |
| **API** | `api/` | 完整 API 参考（每个类/函数） | 按 `vectorbtpro` 包结构组织 |
| **Cookbook** | `cookbook/overview/` | 配置、knowledge（AI）等主题配方 | `cookbook/configuration/`、`cookbook/knowledge/` |

另有 **Terms**（服务条款）和 **Discord**（`https://discord.gg/eQ9sVr5vb9`，社区知识库的数据源之一）。

### 1.2 官方推荐阅读顺序（首页 "First steps"）

1. **Fundamentals** —— `documentation/fundamentals/` + `documentation/building-blocks/`（先建立心智模型）
2. **Basic RSI strategy** —— `tutorials/basic-rsi/`（第一个完整策略）
3. **SuperFast SuperTrend** —— `tutorials/superfast-supertrend/` + `documentation/indicators/`（指标 + 高性能）
4. **Signal development** —— `tutorials/signal-development/`（信号开发）
5. **Portfolio** —— `documentation/portfolio/from-orders/` + `documentation/portfolio/from-signals/`（回测核心）
6. **Cross-validation** —— `tutorials/cross-validation/`（参数验证）
7. **Freedom** —— `api/` 自由查阅 + QuantGPT 聊天（`https://www.quantgpt.chat/`）

### 1.3 Tutorials 全列表（10 个）

| 教程 | slug | 内容 |
|------|------|------|
| Basic RSI strategy | `basic-rsi` | RSI 入场/出场的最简策略 |
| SuperFast SuperTrend | `superfast-supertrend` | SuperTrend 指标高性能实现 |
| Signal development | `signal-development` | 构建与打磨自定义交易信号 |
| Stop signals | `stop-signals` | 止损出场逻辑 |
| MTF analysis | `mtf-analysis` | 多时间周期分析 |
| Portfolio optimization | `portfolio-optimization` | 组合级参数与配置优化 |
| Pairs trading | `pairs-trading` | 配对交易均值回归 |
| Patterns and projections | `patterns-and-projections` | 形态识别与价格投射 |
| Cross-validation | `cross-validation` | 多数据切分验证策略 |
| More tutorials | `more-tutorials` | 更多进阶指南 |

### 1.4 关键概念（fundamentals 页）

VBT 设计哲学类似 **Pandas，而非 backtrader 框架**，核心概念：

- **Stack 三层栈**：NumPy（C 加速数组）→ Pandas（时间序列特性）→ Numba（for 循环跑出机器码速度）。VBT 的模式：取出 NumPy 数组 → 跑 Numba 编译函数 → 把结果包回 Pandas。
- **Accessors 访问器**：Pandas 扩展机制，不继承直接挂 `vbt`（如 `sr.vbt.rolling_mean(3)`），可在原生 Pandas 与 VBT 之间切换。每个 accessor 期待"现成数据"（如 returns accessor 期待收益率而非价格）。
- **Multidimensionality 多维化**：每一列视为一个独立回测实例（而非一个特征）；open/high/low/close 各自独立数组传入，多个回测可堆叠向量化处理。
- **Labels 标签**：全程保留列标签；超参配置用层级（多级）column 标识，例如 MACD 窗口各成一级，便于分组对比。
- **Broadcasting 广播**：按**绝对位置**（而非标签）广播行列；可广播常量、按行/按列/按元素数组。
- **Flexible indexing 弹性索引**：内存高效的广播替代，从任意形状数组中选一个元素，几乎零额外内存开销。

---

## 二、MCP server 完整配置指南

### 2.1 它是什么

VBT 自带一个标准 **MCP（Model Context Protocol）server**，把 VBT 的文档、API、Discord 知识库、源码、甚至实时代码执行暴露给 Claude Code / Claude Desktop / Codex 等 MCP 客户端。这是 VBT 区别于其他量化库的杀手锏 —— AI 助手能"活"在 VBT 知识库里。

### 2.2 启动方式（两种等价）

```bash
# 方式一：模块入口（推荐，给 MCP 客户端用）
python -m vectorbtpro.mcp_server

# 方式二：CLI 入口（vbt mcp serve）
vbt mcp serve
```

- `--transport`：`stdio`（默认，本地 MCP 客户端用）或 `streamable-http`（远程）。
- 底层用 `mcp.server.fastmcp.FastMCP`，server 名 "VectorBT PRO"。
- 依赖 extra：`vectorbtpro[mcp]`（含 `mcp` + `ipykernel`）。

### 2.3 暴露的 10 个 MCP 工具

| 工具 | 作用 |
|------|------|
| `search` | 语义/混合检索 VBT 知识（asset_names: api/docs/messages/examples；search_method: bm25/embeddings/hybrid） |
| `resolve_refnames` | 把引用名解析为全限定名（`vbt.Data` → `vectorbtpro.data.base.Data`） |
| `find` | 按对象名找提及它的文档/示例/Discord 消息 |
| `get_page` | 按 URL 取文档页内容（支持完整 URL / 相对路径 / 锚点 / 点路径） |
| `get_message` | 取 Discord 消息内容 |
| `get_message_block` | 取同一用户短时内连续发送的消息块 |
| `get_message_thread` | 取问题-回复链（thread） |
| `get_attrs` | 列对象属性（类似增强版 `dir()`，含类型和 refname） |
| `get_source` | 用 AST 解析取任意对象的源码 |
| `run_code` | 在 Jupyter kernel 里跑代码片段（自动 `from vectorbtpro import *`） |

### 2.4 Claude Code 配置 JSON

Claude Code 的 MCP 配置（`.mcp.json` 项目级，或 `~/.claude.json` 用户级 `mcpServers`）：

```json
{
  "mcpServers": {
    "vectorbt-pro": {
      "command": "python",
      "args": ["-m", "vectorbtpro.mcp_server"],
      "env": {
        "GITHUB_TOKEN": "ghp_你的GitHubToken",
        "OPENAI_API_KEY": "sk-你的OpenAI密钥"
      }
    }
  }
}
```

**关键点：**
- `command` 必须是 **Python 的绝对路径**（Windows 用 `where python` 查，Linux/macOS 用 `which python`）。
- `env` 里的 `GITHUB_TOKEN`（私有仓库/知识资产拉取）和 `OPENAI_API_KEY`（嵌入/补全，若走 OpenAI 默认提供商）会传给 server 进程。
- 若用本地 Ollama 做嵌入/补全，可省略 `OPENAI_API_KEY`，改在 settings 文件里配 `ollama`。
- Codex 用同款配置，只是语法为 TOML：`command = "..."` / `args = ["-m", "vectorbtpro.mcp_server"]`。
- Claude Desktop 的本地 server 连接指南：`https://modelcontextprotocol.io/docs/develop/connect-local-servers`。

### 2.5 环境变量（重点）

| 变量 | 作用 |
|------|------|
| `GITHUB_TOKEN` | 认证从 GitHub release 拉取知识资产（`PagesAsset.pull()` / `MessagesAsset.pull()`）。源码 `vectorbtpro/utils/github.py` 里 `os.environ.get("GITHUB_TOKEN")`，用 PyGithub 或 requests 拉 latest release。 |
| `VBT_SETTINGS_PATH` | 指向自定义 settings 文件（`vbt.cfg` / `vbt.yml` / `vbt.toml`）的完整路径。**必须在 `import vectorbtpro` 之前设置**（Numba 等导入期设置才生效）。 |
| `VBT_SETTINGS_NAME` | 指定不同的识别文件名（默认 `"vbt"`）。 |
| `VBT_DOTENV_PATH` | 自定义 `.env` 文件路径。 |
| `OPENAI_API_KEY` / `HF_TOKEN` 等 | 各嵌入/补全提供商的 API 密钥。 |

`.env` 文件在 import 时**自动加载**（不覆盖已存在的环境变量），可同时放 `VBT_SETTINGS_PATH` 和 API key。

### 2.6 嵌入/补全提供商配置（`VBT_SETTINGS_PATH` 的实际用途）

settings 里的 `knowledge.chat` 子配置控制整个 AI 工作流的提供商，默认全部 `"auto"`（自动检测：检查已装包 + `OPENAI_API_KEY` / `HF_TOKEN` 环境变量 + Ollama 是否可用）。

**嵌入提供商（`chat.embeddings_configs.*`，默认模型）：**

| 短名 | 类 | 默认模型 | 本地/远程 |
|------|-----|---------|----------|
| `openai` | OpenAIEmbeddings | text-embedding-3-large (1024 维) | 远程 |
| `gemini` | GeminiEmbeddings | gemini-embedding-001 | 远程 |
| `hf_inference` | HFInferenceEmbeddings | Qwen/Qwen3-Embedding-8B | 远程 HF |
| `voyage` | VoyageEmbeddings | voyage-4-large | 远程 |
| `cohere` | CohereEmbeddings | embed-v4.0 | 远程 |
| `jina` | JinaEmbeddings | jina-embeddings-v4 | 远程 |
| `litellm` | LiteLLMEmbeddings | text-embedding-3-large | 远程代理 |
| `hf` | HFEmbeddings | Qwen/Qwen3-Embedding-0.6B（sentence_transformers） | **本地** |
| `ollama` | OllamaEmbeddings | Qwen/Qwen3-Embedding-0.6B | **本地** |
| `llama_index` | LlamaIndexEmbeddings | openai 子配置 | 混合 |

**补全提供商（`chat.completions_configs.*`，默认模型 —— 本快照 v2026.6.27 源码中的值）：**

| 短名 | 类 | 默认 model / quick_model |
|------|-----|------------------------|
| `openai` | OpenAICompletions | gpt-5.4 / gpt-5.4-mini |
| `anthropic` | AnthropicCompletions | claude-sonnet-4-6 / claude-haiku-4-5 |
| `gemini` | GeminiCompletions | gemini-2.5-flash / gemini-2.5-flash-lite |
| `hf_inference` | HFInferenceCompletions | openai/gpt-oss-120b / openai/gpt-oss-20b |
| `litellm` | LiteLLMCompletions | gpt-5.4 / gpt-5.4-mini |
| `ollama` | OllamaCompletions | qwen3:4b / qwen3:0.6b（本地） |
| `llama_index` | LlamaIndexCompletions | openai 子配置 |

> 注：`gpt-5.4`、`claude-sonnet-4-6`、`claude-haiku-4-5` 等为本快照源码里的默认模型名，实际可用模型以账号权限为准，可在 settings 文件覆盖。

**自定义 settings 示例（`vbt.yml`，用 `VBT_SETTINGS_PATH` 指向它）—— 把嵌入和补全都切到本地 Ollama：**

```yaml
knowledge:
  chat:
    embeddings: "ollama"
    completions: "ollama"
```

---

## 三、llms.txt 说明

VBT 提供 4 个 **LLM 友好文档文件**（`https://vectorbt.pro/pvt_ff8edc14/` 根下），是给 AI 爬虫/助手用的机器可读站点地图：

| 文件 | 内容 |
|------|------|
| `llms.txt` | 全站文档站点地图（约 200 条，含全部板块索引） |
| `llms-docs.txt` | 通用文档（getting started / features / tutorials / cookbook 等非 API 内容） |
| `llms-api.txt` | API 文档专用 |
| `llms-full.txt` | 完整文档合并 |

**格式结构：**
1. 首行 H1 标题（站点名，如 `# VectorBT PRO`）
2. `>` 引用行：项目简介
3. `##` 二级标题划分板块（Getting started → Features → Tutorials → Documentation → Cookbook → API → Optional），嵌套用 `## Tutorials > SuperFast SuperTrend` 表示层级
4. 条目：`- [页面名](URL): 一句话描述`

**额外技巧：** 给任意页面 URL 追加 `.md` 即可拿到 Markdown 源码（如 `tutorials/cross-validation.md`），方便直接喂给 LLM 或本地分析。

---

## 四、ChatVBT 说明

ChatVBT 是 VBT 内置的 RAG 聊天系统，把"检索 + 排序 + LLM 补全 + 工具调用"串成一条龙。

### 4.1 五个核心入口

| API | 作用 |
|-----|------|
| `vbt.search("...")` | **SearchVBT**：纯 RAG 检索（embed → rank → retrieve），不调 LLM |
| `vbt.quick_search("...")` | BM25 离线词法检索（无需嵌入/网络，快） |
| `vbt.chat("...")` | **ChatVBT**：检索 + LLM 补全，返回上下文感知回答 |
| `vbt.quick_chat("...")` | BM25 + quick_mode 的快速版 chat |
| `vbt.interact("...", tools="all")` | **函数调用（function calling）** 版 chat，模型可调用工具 |

### 4.2 典型用法（来自官方 Intelligence 页）

```python
>>> env["GITHUB_TOKEN"] = "<YOUR_GITHUB_TOKEN>"
>>> env["OPENAI_API_KEY"] = "<YOUR_OPENAI_API_KEY>"
>>> vbt.chat("How to rebalance weekly?", formatter="html")
```

函数调用版（v2025.10.15 起支持，跨所有 LLM）：

```python
>>> vbt.interact(
...     "How to backtest a weekly rebalancing strategy with vbt.PF.from_orders?",
...     tool_display_format="compact",
...     formatter="html",
... )
```

也支持按类调用：`vbt.Portfolio.chat("...")`、`vbt.PortfolioOptimizer.chat("...")`。

### 4.3 `interact` 的 tools 参数

- `"registry"` —— 用已注册的全部工具
- `"mcp"` —— 用 `vectorbtpro.mcp.tool_registry` 里的 MCP 工具
- `"all"` —— 两者都用（默认）
- 或传工具名/函数列表

### 4.4 CLI 版

```bash
vbt chat "What is PFO?"                # 检索 + LLM 回答
vbt quick-chat "..."                   # BM25 快速版
vbt interact "List attributes of PFO"  # 带工具调用
vbt mcp serve                          # 启动 MCP server
vbt mcp find "PFO"                     # 直接调用任意 MCP 工具
vbt mcp run-code --call '{"kwargs": {"code": "print(vbt.__version__)"}}'
```

`--call` 是给 AI 代理/高级用户的 JSON 逃逸口，支持 `{"args": [...], "kwargs": {...}}`。

### 4.5 相关特性（features/intelligence 页补充）

- **Reranking 重排**：`rerank=True`，提供商 `cohere` / `jina` / `voyage` / `hf_cross_encoder` / `completions`。
- **Reasoning steps 推理步骤**：OpenAI 模型 `reasoning=dict(effort="low", summary="auto")`。
- **Source refactorer 源码重构**：`vbt.refactor_source(source, attach_knowledge=True, model="gpt-5-mini", show_diff=True)`，用 TODO/FIXME 注释当指令。
- **Knowledge assets 知识资产**：`PagesAsset`（网站页）、`MessagesAsset`（Discord 历史），支持 `find_code()`、Markdown/HTML 转换、离线浏览；首次 `pull()` 下载，之后用缓存。

---

## 五、本地源码关键实现发现

### 5.1 `vectorbtpro/mcp_server.py`（60 行，极简）

- 入口 `main()`：argparse 解析 `--transport`（stdio/streamable-http）。
- `assert_can_import("mcp")` → `from mcp.server.fastmcp import FastMCP`。
- 核心 3 行：`mcp = FastMCP("VectorBT PRO")` → `for name, tool in tool_registry.items(): mcp.tool(name=name)(tool)` → `mcp.run(...)`。
- 结论：**server 完全由 `mcp.py` 的 `tool_registry` 驱动**，加一个工具只需 `@register_tool` 装饰。

### 5.2 `vectorbtpro/mcp.py`（856 行，10 个工具 + 注册机制）

- `tool_registry = {}` 全局注册表；`register_tool` 装饰器（支持 `@register_tool` 或 `@register_tool("name")`）。
- `auto_cast(value)`：用 `ast.literal_eval` 把字符串自动转 Python 字面量（MCP 传参都是字符串，这个函数让 `"True"`、`"[1,2]"`、`"None"` 正确还原）。
- 每个工具函数都是**薄封装**：延迟 import（`from vectorbtpro.knowledge.custom_assets import ...` 在函数体里），调底层 knowledge 函数，再用 `.to_context()` 序列化。
- `run_code` 最特殊：维护全局 `current_kernel`（`VBTKernel`，Jupyter kernel），自动 `from vectorbtpro import *`，`restart` 参数可重启 kernel，`exec_timeout` 限时，`raise_on_error=True`。这就是"AI 能直接跑 VBT 代码"的机制。

### 5.3 `vectorbtpro/cli.py`（696 行，Typer CLI）

- 控制台入口 `vbt = vectorbtpro.cli:main`（见 pyproject `[project.scripts]`）。
- 顶层命令：`chat` / `quick-chat` / `interact` / `mcp`（group）。
- `mcp` group：`serve` + 动态把 `tool_registry` 里每个工具挂成子命令（`run_code` → `mcp run-code`）。
- `--call` 载荷解析（JSON 或 Python 字面量，`{"args":[...],"kwargs":{...}}`），显式 CLI 参数覆盖 `--call` 值。
- 工具命令的 help 直接取函数 docstring，用 `\b` 标记保留段落换行。

### 5.4 `vectorbtpro/knowledge/`（知识库子系统，16 个文件）

- `custom_assets.py`：`chat` / `quick_chat` / `interact` / `search` / `quick_search` / `chat_about` / `find_assets` 顶层函数；`VBTAsset` / `PagesAsset` / `MessagesAsset` / `ExamplesAsset` 类。
- `completions.py`：`Completions` 抽象基类 → `OpenAICompatibleCompletions` → `OpenAICompletions` / `AnthropicCompletions` / `OllamaCompletions` + HuggingFace / LiteLLM / LlamaIndex 后端。
- `embeddings.py`：`Embeddings` 基类 + 10 个提供商；`resolve_provider` 自动检测逻辑（检查已装包 + `OPENAI_API_KEY` / `HF_TOKEN` + Ollama 可用性）。
- `reranking.py` / `text_splitting.py` / `tokenization.py` / `doc_ranking.py` / `doc_storing.py` / `formatting.py`：RAG 流水线各环节。

### 5.5 `vectorbtpro/_settings.py`（settings 系统）

- `knowledge` frozen_cfg → `chat` flex_cfg → `embeddings` / `completions` / `reranker` 默认 `"auto"` + 各自 `*_configs`（见 2.6）。
- 文件末尾（3418 行起）：`settings_name = os.environ.get("VBT_SETTINGS_NAME", "vbt")`；若 `VBT_SETTINGS_PATH` 在环境中则 `settings.load_update(...)`。
- `.env` 自动加载（`vectorbtpro.load_dotenv`），不覆盖已存在环境变量。

### 5.6 `vectorbtpro/utils/github.py`（GITHUB_TOKEN 的实际消费点）

- `resolve_release_name()`：`os.environ.get("GITHUB_TOKEN")` → 用 PyGithub（`Github(auth=Auth.Token(token))`）或 requests（`Authorization: token ...`）拉 `repos/{owner}/{repo}/releases/latest`。知识资产 `pull()` 走这条链路。

---

## 六、速查：配好 VBT + Claude Code 的完整清单

1. **装环境**：Python 3.11+（官方示例 3.12），`git` + `gh` 登录。
2. **装包**：`uv pip install -U "vectorbtpro[base] @ git+https://github.com/polakowo/vectorbt.pro.git@v2026.6.27"`；AI 功能加 `[mcp]` 和 `[knwl]` extra。
3. **设 GITHUB_TOKEN**：放 `.mcp.json` 的 `env`，或 `gh auth login`。
4. **选提供商**：默认 `auto` 自动检测；本地化用 Ollama（`vbt.yml` 里 `knowledge.chat.embeddings/completions = "ollama"`，`VBT_SETTINGS_PATH` 指向它）。
5. **配 Claude Code**：`.mcp.json` 写 `{"mcpServers":{"vectorbt-pro":{"command":"<python绝对路径>","args":["-m","vectorbtpro.mcp_server"],"env":{"GITHUB_TOKEN":"..."}}}}`。
6. **验证**：Claude Code 里 `/mcp` 应能看到 `vectorbt-pro` 的 10 个工具；或直接 `vbt mcp find "PFO"` 测工具；`vbt chat "..."` 测 ChatVBT。
