# 15 — MCP server 与 AI 系统端到端（第二轮深挖）

> 研究目标：把 VectorBT PRO 的 AI 系统 —— 检索（BM25/向量）+ MCP server（暴露 10 个工具）+ LLM 生成 —— 从源码到可运行证据彻底打通。
> 源码：`data/develop-src/vectorbtpro/mcp.py`（856 行）、`mcp_server.py`（79 行）、`knowledge/`（16 个文件）、`utils/eval_.py`（Jupyter kernel）、`cli.py`（MCP 子命令）。
> 结论先行：**MCP 层 + RAG 检索 + Jupyter kernel 代码执行，三大件全部在本地实测跑通；唯一跑不通的是"真正的回测计算"，根因是 `vectorbtpro_rust` 只有纯 Python 壳、缺编译好的 PyO3 `.pyd`（`[rust]` extra 问题），与 MCP/AI 系统无关。**

---

## 一、10 个 MCP 工具 → 底层函数映射表

全部在 `mcp.py` 里用 `@register_tool` 注册进全局 `tool_registry`。实测 `list(m.tool_registry.keys())` 返回的注册顺序与下表一致。每个工具都是**薄封装**：函数体里延迟 `import` 底层模块 → 调底层函数 → `.to_context()` 序列化成字符串（LLM 友好）。

| # | MCP 工具 | 底层调用（`mcp.py` 内的函数体） | 数据源 / 依赖 |
|---|---------|-------------------------------|--------------|
| 1 | `search` | `vectorbtpro.knowledge.custom_assets.search(query, search_method=...)` → 内部 `find_assets(None, as_query=True)` 拉全资产并 combine → `KnowledgeAsset.rank()`（BM25/embeddings/hybrid/rerank）→ `to_context()` | 4 类资产 + `DocumentRanker` + tiktoken |
| 2 | `resolve_refnames` | `vectorbtpro.utils.refs.resolve_refname(refname)` 逐个解析，包装成 `VBTAsset` | 无网络（纯反射） |
| 3 | `find` | `vectorbtpro.knowledge.custom_assets.find_assets(refnames, resolve=True, asset_names=..., api_kwargs/docs_kwargs/messages_kwargs/examples_kwargs)` | 4 类资产（按对象名精确找提及） |
| 4 | `get_page` | `PagesAsset.pull().find_page(url, aggregate=True)` | 网站页资产（缓存 JSON） |
| 5 | `get_message` | `MessagesAsset.pull().find_link(url)` | Discord 消息资产 |
| 6 | `get_message_block` | `MessagesAsset.pull().find_link(url, field="block", single_item=False)` | 同上（同一作者连续消息块） |
| 7 | `get_message_thread` | `MessagesAsset.pull().find_link(url, field="thread", single_item=False)` | 同上（问题-回复链） |
| 8 | `get_attrs` | `resolve_refname` + `get_refname_obj`（`utils.refs`）→ `vectorbtpro.utils.attr_.get_attrs(obj, return_meta=True)` | 无网络（纯反射，增强版 `dir()`） |
| 9 | `get_source` | `resolve_refname` → `vectorbtpro.utils.source.get_source(resolved_refname)`（AST 解析） | 无网络（AST） |
| 10 | `run_code` | `vectorbtpro.utils.eval_.VBTKernel`（Jupyter kernel，全局单例 `current_kernel`） | `ipykernel` + `jupyter_client`（ZMQ） |

**关键设计：`auto_cast()` 桥接 MCP 字符串参数。** MCP 客户端传来的参数全是字符串（`"True"`、`"[1,2]"`、`"None"`、`"4000"`），每个工具入口先 `auto_cast` 用 `ast.literal_eval` 把字符串还原成 Python 字面量（`mcp.py:53-69`），失败则保持原字符串。这是 MCP JSON-RPC 与 Python 类型系统之间的关键适配层。

**工具分类（按是否需要网络/资产）：**
- 纯反射（无需资产、无需网络）：`resolve_refnames`、`get_attrs`、`get_source`、`run_code`（仅需 kernel 进程）
- 需资产（pull 或缓存，离线可跑）：`search`、`find`、`get_page`、`get_message`、`get_message_block`、`get_message_thread`

---

## 二、mcp.py / mcp_server.py 核心实现解析

### 2.1 `tool_registry` + `@register_tool`（mcp.py:25-50）

```python
tool_registry = {}   # name -> func 的全局字典

def register_tool(arg=None, /, *, name=None):
    if isinstance(arg, str) and name is None:
        name = arg; arg = None          # 支持 @register_tool("my_name")
    def wrapper(func):
        tool_name = name or func.__name__
        tool_registry[tool_name] = func # 核心：把函数塞进全局注册表
        return func                     # 返回原函数，不改变函数本身
    if callable(arg):
        return wrapper(arg)             # @register_tool 直接装饰
    return wrapper                       # @register_tool("name") 先拿名字
```

- 支持两种写法：`@register_tool`（用函数名）或 `@register_tool("custom_name")`（自定义名）。
- **加一个 MCP 工具 = 写一个函数 + 加一行 `@register_tool`**，server 和 CLI 都会自动感知。

### 2.2 `mcp_server.py` 的 FastMCP 启动（79 行，极简）

```python
def serve(transport="stdio", host=None, port=None, **fastmcp_kwargs):
    from vectorbtpro.mcp import tool_registry
    from vectorbtpro.utils.module_ import assert_can_import
    assert_can_import("mcp")                      # 缺 mcp SDK 直接 ImportError
    from mcp.server.fastmcp import FastMCP
    fastmcp_kwargs.setdefault("name", "VectorBT PRO")
    if host is not None: fastmcp_kwargs["host"] = host
    if port is not None: fastmcp_kwargs["port"] = port
    mcp = FastMCP(**fastmcp_kwargs)
    for name, tool in tool_registry.items():
        mcp.tool(name=name)(tool)                 # 把 10 个工具逐个注册进 FastMCP
    mcp.run(transport=transport)                  # stdio（默认）或 streamable-http
```

- **server 完全由 `tool_registry` 驱动**：`FastMCP(name="VectorBT PRO")` → 遍历注册表 `mcp.tool(name=name)(tool)` → `mcp.run(transport=...)`。
- 入口 `python -m vectorbtpro.mcp_server` 实际走 `main()` → `cli_main(["mcp", "serve", *argv])`（复用 Typer CLI）。
- 依赖 extra：`vectorbtpro[mcp]` 提供 `mcp` SDK + `ipykernel`。**实测本机 `.venv` 里 `mcp` SDK 未装**（`import mcp` → `ModuleNotFoundError`），所以 server 当前无法真正启动，需 `pip install "vectorbtpro[mcp]"`。

### 2.3 CLI 的 MCP 子命令（cli.py:528-568）

`vbt mcp` 是一个 Typer group，做了两件事：
1. **`vbt mcp serve`**：`--transport`（stdio/streamable-http）、`--host`、`--port`、`--call`（JSON 逃逸口），内部 `merge_call_arguments` 合并后调 `mcp_server.serve()`。
2. **动态挂工具子命令**：`for tool_name, tool in tool_registry.items(): mcp_app.command(name=tool_name.replace("_","-"), ...)` —— 10 个工具自动变成 `vbt mcp search` / `vbt mcp find` / `vbt mcp run-code` 等 10 个 CLI 子命令（help 直接取函数 docstring）。`--call '{"args":[...],"kwargs":{...}}'` 是给 AI 代理/脚本的高级调用逃逸口。

### 2.4 `run_code` 的 Jupyter kernel（mcp.py:787-859 + utils/eval_.py）

`run_code` 是 10 个工具里最特殊的一个 —— 它让 AI 能**直接执行 VBT 代码并拿回结果**，是"生成 → 执行"闭环的执行端。

**机制（`utils/eval_.py`）：**

```
mcp.run_code(code, restart, exec_timeout, max_tokens)
  └─ 全局单例 current_kernel（mcp.py:787）
       ├─ 首次：current_kernel = VBTKernel(); current_kernel.start()
       └─ restart=True 时：current_kernel.restart()   # 清空解释器状态
  └─ current_kernel.execute(code, exec_timeout=..., raise_on_error=True)
  └─ VBTAsset([output]).to_context(max_tokens=...)   # 用 tiktoken 截断
```

**类层次：**
- `JupyterKernel(Configured)`（eval_.py:163）：`jupyter_client.KernelManager` 起 kernel 进程（`start_kernel(extra_arguments=["--InteractiveShell.colors=NoColor"])`）→ `manager.client()` 开 ZMQ 通道 → `wait_for_ready(timeout=startup_timeout)`。
- `VBTKernel(JupyterKernel)`（eval_.py:433）：`start()` 里多跑一句 `self.execute("from vectorbtpro import *")` —— **自动 star-import `vbt`/`pd`/`np`/`njit` 等**，所以 AI 生成的代码片段可以直接用 `vbt.xxx`。
- `execute()`（eval_.py:318）：`client.execute(code)` 拿 `msg_id` → 若无 `exec_timeout` 直接 `collect_output`；若有超时，用 `ThreadPoolExecutor` 包一层，超时先 `interrupt_kernel()`（给 `interrupt_grace` 5s 优雅中断），再超时 `restart()`。
- `collect_output()`（eval_.py:262）：按 `parent_header.msg_id` 聚合 IOPub 消息，收 `execute_result`/`display_data`（text MIME 原样、非 text 用 `<image/png: bytes>` 占位）、`stream`（stdout/stderr）、`error`（traceback）。**`raise_on_error=True` 时把 error 消息包成 `KernelExecutionError(ename, evalue, traceback, output)` 抛出。**

**关键点：**
- `current_kernel` 是模块级全局变量，**跨多次 `run_code` 调用复用同一 kernel，变量状态保留**（等价于 Jupyter notebook 的 cell 之间共享 namespace）。
- 官方 docstring 里的安全警告：kernel 能执行任意代码，要求"只用 VBT 开发/测试，不要做装依赖、改全局状态、I/O 等副作用"。这是给 LLM 的护栏提示，不是运行时硬约束。

---

## 三、knowledge 模块架构 + AI 数据流图

### 3.1 16 个文件职责（`knowledge/`）

| 文件 | 职责 | 关键类/函数 |
|------|------|-----------|
| `base_assets.py` | 资产基类 `KnowledgeAsset`（MutableSequence：find/filter/query/to_documents/split_text/embed/rank/to_context）+ `AssetCacheManager`（文档缓存） | `KnowledgeAsset`、`MetaKnowledgeAsset`、`AssetCacheManager` |
| `base_asset_funcs.py` | 管道任务原语（AssetFunc 基类 + 22 个具体任务） | `GetAssetFunc`/`SetAssetFunc`/`FindAssetFunc`/`QueryAssetFunc`/`ToDocsAssetFunc`/`SplitTextAssetFunc`/`ReduceAssetFunc`/`CollectAssetFunc`… |
| `asset_pipelines.py` | 管道编排（把任务串成流水线） | `AssetPipeline`/`BasicAssetPipeline`/`ComplexAssetPipeline` |
| `custom_assets.py` | 高层入口 + 4 个具体资产类 + 顶层 API | `VBTAsset`/`PagesAsset`/`MessagesAsset`/`ExamplesAsset`；顶层 `find_api`/`find_docs`/`find_messages`/`find_examples`/`find_assets`/`search`/`quick_search`/`chat`/`quick_chat`/`interact`/`chat_about` |
| `custom_asset_funcs.py` | 自定义资产的管道函数（Markdown/HTML 转换 + 消息聚合） | `ToMarkdownAssetFunc`/`ToHTMLAssetFunc`/`AggMessageAssetFunc`/`AggBlockAssetFunc`/`AggThreadAssetFunc`/`AggChannelAssetFunc` |
| `doc_ranking.py` | 文档排序核心（BM25/向量/混合/融合/重排/top-k/cutoff） | `DocumentRanker`、`ScoredDocument`、`EmbeddedDocument`、`Rankable`/`Contextable`/`RankContextable`、顶层 `rank_documents` |
| `doc_storing.py` | 文档 + 嵌入的向量存储（含 LMDB 持久化） | `ObjectStore`/`DictStore`/`MemoryStore`/`FileStore`/`LMDBStore`/`CachedStore` + `TextDocument`/`StoreEmbedding` |
| `embeddings.py` | 嵌入抽象 + 10 个提供商 | `Embeddings` 基类 → OpenAI/Gemini/HFInference/Voyage/Cohere/Jina/LiteLLM/HF/Ollama/LlamaIndex + `resolve_embeddings`/`embed` |
| `completions.py` | LLM 补全抽象 + 工具调用（function calling）+ 8 个提供商 | `Completions` 基类 → OpenAICompatible/OpenAI/Anthropic/Gemini/HFInference/LiteLLM/Ollama/LlamaIndex + `resolve_completions`/`complete` |
| `reranking.py` | 交叉编码器重排 | `Reranker` 基类 → Voyage/Cohere/Jina/HFCrossEncoder/Completions + `resolve_reranker`/`rerank` |
| `text_splitting.py` | 文本切块 | `TextSplitter`/`TokenSplitter`/`SegmentSplitter`/`SourceSplitter`/`PythonSplitter`/`MarkdownSplitter`/`LlamaIndexSplitter` + `resolve_text_splitter`/`split_text` |
| `tokenization.py` | token 计数/编解码 | `Tokenizer`/`TikTokenizer`(tiktoken)/`HFTokenizer` + `resolve_tokenizer`/`tokenize`/`detokenize` |
| `formatting.py` | 输出格式化（Markdown/HTML/流式） + 思考标记处理 | `ContentFormatter`(Plain/IPython/IPythonMarkdown/IPythonHTML/HTMLFile) + `ToMarkdown`/`ToHTML`/`FormatHTML` + `ThoughtProcessor`/`RawStr` |
| `provider_utils.py` | 提供商自动检测 | `check_ollama_available`/`resolve_provider` |
| `__init__.py` | 包导出（TYPE_CHECKING 全量 re-export） | — |

### 3.2 资产拉取链（pull）

```
VBTAsset.pull()  (custom_assets.py:127)
  └─ resolve_release_name(repo_owner, repo_name, release_name, token, ...)   # utils/github.py，GITHUB_TOKEN 在这里消费
  └─ 若缓存命中：resolve_asset_name_from_names / cache_file → cls.from_json_file(缓存)
  └─ 否则：download_github_asset(...)  # 从 GitHub release 下载 JSON，写入 assets_dir 缓存
       → cls.from_json_bytes / from_json_file
```

- 资产缓存目录实测：`C:/Users/CF/AppData/Local/vbtuser/vectorbtpro/Cache/knowledge/v2026.6.27/{pages,messages,examples}` + `doc_lmdb_store` + `emb_lmdb_store`（文档/嵌入的 LMDB 向量库），另有 `asset_cache/` 里 10 个 20–67MB 的已合并文本文档缓存（`search` 首跑产物）。
- **`GITHUB_TOKEN` 的作用**：`utils/github.py` 里 `os.environ.get("GITHUB_TOKEN")`，用 PyGithub 或 requests 拉 `repos/{owner}/{repo}/releases/latest` 的私有 release。**只在首次 pull 需要；资产缓存到本地后离线可跑**（本次实测全部走缓存，无需网络）。

### 3.3 AI 数据流图（文本图）

```
                         ┌────────────────────────────────────────────────┐
                         │                资产层（Knowledge assets）        │
                         │  PagesAsset(网站页)  MessagesAsset(Discord)      │
                         │  ExamplesAsset(代码示例)                        │
                         └───────────────┬────────────────────────────────┘
                                         │ .pull()  ← GitHub release JSON / 本地缓存
                                         │   (GITHUB_TOKEN 首次消费；缓存后离线)
                                         ▼
                    ┌──────────────────────────────────────────────┐
                    │       资产处理管道（asset_pipelines）         │
                    │  Get→Set→Query→Find→ToDocs→SplitText→Reduce  │
                    │  (base_asset_funcs 的任务原语 + custom_asset_funcs) │
                    └───────────────┬──────────────────────────────┘
                                    │ to_documents() → TextDocument
                                    ▼
              ┌───────────────────────────────────────────────────────┐
              │          文档存储（doc_storing.ObjectStore）           │
              │   MemoryStore / FileStore / LMDBStore（持久化向量库）  │
              │   + split_text()（text_splitting：Token/Python/Markdown） │
              └───────────┬───────────────────────────┬───────────────┘
                          │ embed()                     │
                          ▼                            ▼
              ┌────────────────────┐      ┌──────────────────────────┐
              │ embeddings.py      │      │ tokenization.py (tiktoken)│
              │ 10 个提供商(openai/ │      │ 计数 + max_tokens 截断     │
              │ ollama/hf/…)       │      └──────────────────────────┘
              └─────────┬──────────┘
                        ▼
   ┌────────────────────────────────────────────────────────────────────┐
   │   文档排序 doc_ranking.DocumentRanker                              │
   │   search_method: bm25 | embeddings | hybrid (+ _fallback 变体)     │
   │   RRF 融合 → score → top_k/cutoff → rerank(交叉编码器，可选)        │
   └───────────────┬────────────────────────────────────────────────────┘
                   │ 排序后的 chunk 列表
                   ▼
   ┌────────────────────────────────────────────────────────────────────┐
   │   VBTAsset.to_context() → 字符串（LLM 上下文）                     │
   │   dump_engine: json/text/yaml + fit_to_context(max_tokens 分页)    │
   └───────────────┬────────────────────────────────────────────────────┘
                   │
        ┌──────────┴───────────┐
        ▼                      ▼
 ┌──────────────┐      ┌──────────────────────────────┐
 │ SearchVBT     │      │ ChatVBT / interact           │
 │ vbt.search()  │      │ completions.py 的 LLM 补全    │
 │ (纯 RAG 检索) │      │ + 工具调用(function calling) │
 └──────────────┘      └──────────────┬───────────────┘
                                      │ 工具调用可回落到
                                      ▼
                          ┌───────────────────────┐
                          │ MCP tool_registry      │
                          │ (search/find/get_attrs/│
                          │  get_source/run_code…) │
                          └───────────────────────┘
```

**闭环本质**：`vbt.chat()` = `find_assets`（拉资产）→ `rank`（BM25/向量排序）→ `to_context`（塞进 LLM prompt）→ `completions`（LLM 补全）；`vbt.interact(tools="mcp")` 则让 LLM 直接调 `mcp.tool_registry` 里的 10 个工具（含 `run_code` 执行代码）。**MCP server 就是把这条能力以标准 MCP 协议暴露给外部客户端（Claude Code/Desktop/Codex）的桥。**

---

## 四、端到端验证（检索 → 生成闭环的可运行证据）

> 环境：`.venv\Scripts\python.exe`（vectorbtpro 2026.6.27 editable develop）。本次补齐了 3 个缺失依赖 `tiktoken`、`ipykernel`、`jupyter_client`（`[mcp]`/`[knwl]` extra 的成员），否则 `to_context`（tiktoken）和 `run_code`（ipykernel）都会报 ImportError。

### 4.1 注册表确认

```
>>> list(vectorbtpro.mcp.tool_registry.keys())
['search', 'resolve_refnames', 'find', 'get_page', 'get_message',
 'get_message_block', 'get_message_thread', 'get_attrs', 'get_source', 'run_code']
```

### 4.2 检索侧（全部走本地缓存，离线可跑）

**resolve_refnames（纯反射，无需资产）：**
```
OK Portfolio vectorbtpro.portfolio.base.Portfolio
OK vbt.Data  vectorbtpro.data.base.Data
OK Data      vectorbtpro.data.base.Data
```

**get_attrs（增强版 dir()，带类型）：**
```
"add_levels [function]", "allocations [cacheable_property]", "annual_returns [cacheable_property]",
"cash [cacheable_property]", "chat [classmethod]", "from_signals [classmethod]", ...
```

**get_source（AST 源码抽取）：** 成功抽出 `Portfolio.from_signals` 的完整签名（`close/entries/exits/direction/...` 30+ 参数），证明 AST 解析路径可用。

**get_page（点路径 → 文档页）：**
```json
{ "link": ".../api/portfolio/base/#vectorbtpro.portfolio.base.Portfolio",
  "name": "Portfolio",
  "content": "## Portfolio | class | [source](.../portfolio/base.py#L1339-L15076)\n\n```python\nPortfolio(...)\n```" }
```

**find（按对象名找提及）：** `find(['RSI'])` 返回 `vectorbtpro.indicators.custom.rsi.RSI` 的完整 API 页（含超类链接、`RSI.run` 入口、`rsi_nb` 底层实现指针）。

**search（BM25 词法检索，跨资产）：** `search('how to backtest an RSI strategy', search_method='bm25')` 返回：
1. `Basic RSI strategy` 教程页（docs 资产）
2. 一条 Discord 消息线程（messages 资产，含 `@user`/`@maintainer` 问答，讲 RSI 多回测绘图）

> ⚠️ 首跑慢是预期行为：`search` 首跑触发 "Caching documents. This may take a while."（`VBTWarning`），把合并后的文本文档缓存到 `asset_cache/`（本次后台任务跑完约几分钟）；之后复用缓存。与 research/04 §五.5 一致。

### 4.3 生成/执行侧（run_code 的 Jupyter kernel）

**冷启动 + 自动 star-import：**
```
=== run_code: trivial (kernel cold start) ===
  - |
    VBT version: 2026.6.27
elapsed: 3.72 s
```

**状态复用（同一 kernel，变量跨调用保留）：**
```
=== run_code: reuse kernel (state persists) ===
  - ''          # x = 6*7 无输出
  - '43'        # x + 1 → 43（证明 x 在上一条已定义）
elapsed: 0.01 s
```

**错误上浮（`raise_on_error=True` → `KernelExecutionError` 带完整 traceback）：**
```
vectorbtpro.utils.eval_.KernelExecutionError: AttributeError: module 'vectorbtpro' has no attribute 'random_data'
```
（第二次试 numpy 造数据后报下一层错误，见 4.4）

### 4.4 边界：真正的回测跑不通，根因是 Rust 后端壳缺失（非 MCP 层）

用检索到的 API（`vbt.RSI.run` + `vbt.Portfolio.from_signals`）构造完整 RSI 策略在 kernel 里执行，报：

```
KernelExecutionError: AttributeError: module 'vectorbtpro_rust' has no attribute '__build_profile__'
```

**根因定位：**
- `vbt.is_rust_available()` 返回 **`True`**（因为 `vectorbtpro_rust.__version__ == 2026.6.27`，`jitting/backends/rust.py:90` 只做了版本匹配）。
- 但 `vectorbtpro_rust` 是**纯 Python 壳**（`.venv\Lib\site-packages\vectorbtpro_rust\__init__.py`，`dir()` 只有 `base/data/generic/...` 子模块名），**编译好的 PyO3 `cdylib`（`.pyd`）没装**。
- `__build_profile__` 属性只在编译产物里存在（`jitting/backends/rust.py:190` 和 `benchmarks/bench_engine.py:1889` 直接 `vectorbtpro_rust.__build_profile__`），壳里没有 → 一旦触发 jitting 注册表访问 Rust 后端就崩。
- **修法**（research/02 已记载）：`python -m maturin develop --manifest-path rust/Cargo.toml --release`（或 `pip install "vectorbtpro[rust]"` 装真正的编译 wheel）。

**这条边界的意义**：MCP/AI 系统的"检索 → 生成 → 执行"链路本身 100% 通（kernel 正确起、正确执行、正确上浮错误），只是"执行 VBT 数值计算"依赖的 Rust 编译后端不在这个环境里。**AI 系统端到端能力已被证明；缺的是 VBT 的 Rust 编译后端，不是 AI 系统。** 纯 Python/pandas/numpy 的数据操作（不触发 jitting）在 kernel 里是能干净跑通的（见 4.3 的 `x=6*7; x+1` 成功态）。

---

## 五、MCP server 接入 Claude Code 的配置清单

### 5.1 前置依赖（本次实测补齐）

```bash
# AI 系统必需（否则 to_context / run_code 直接 ImportError）
.venv/Scripts/python.exe -m pip install tiktoken ipykernel jupyter_client
# MCP server 启动必需（mcp.py serve 里 assert_can_import("mcp")）
.venv/Scripts/python.exe -m pip install "vectorbtpro[mcp]"
# 真正跑回测必需（Rust 编译后端，PyO3 .pyd）
.venv/Scripts/python.exe -m pip install "vectorbtpro[rust]"   # 或 maturin develop
```

### 5.2 Claude Code 配置（`.mcp.json` 或 `~/.claude.json` 的 `mcpServers`）

```json
{
  "mcpServers": {
    "vectorbt-pro": {
      "command": "C:\\Users\\CF\\Desktop\\VBT研究站\\.venv\\Scripts\\python.exe",
      "args": ["-m", "vectorbtpro.mcp_server"],
      "env": {
        "GITHUB_TOKEN": "ghp_你的GitHubToken",
        "OPENAI_API_KEY": "sk-你的OpenAI密钥（走 embeddings/completions 远程提供商时才需要）"
      }
    }
  }
}
```

**要点：**
- `command` 必须是 **Python 绝对路径**（Windows `where python` / 本机 `.venv\Scripts\python.exe`）。
- `args` 用 `["-m", "vectorbtpro.mcp_server"]`（等价 `vbt mcp serve`，默认 stdio transport）。
- `env.GITHUB_TOKEN`：首次 `PagesAsset.pull()`/`MessagesAsset.pull()` 拉私有 release 用；**资产已缓存到 `C:/Users/CF/AppData/Local/vbtuser/vectorbtpro/Cache/knowledge/v2026.6.27/` 后离线可跑**。
- 本地 Ollama（免 `OPENAI_API_KEY`）：设 `VBT_SETTINGS_PATH` 指向 `vbt.yml`（`knowledge.chat.embeddings/completions: "ollama"`），**必须在 import vectorbtpro 之前**设（Numba/导入期设置）。
- 远程 `--transport streamable-http` 用 `--host --port` 参数。

### 5.3 验证步骤

1. `/mcp` 应看到 `vectorbt-pro` 的 10 个工具（`search`…`run_code`）。
2. 或 CLI 直测工具：`vbt mcp find "PFO"`、`vbt mcp run-code --call '{"kwargs":{"code":"print(vbt.__version__)"}}'`。
3. ChatVBT 端到端：`vbt chat "How to rebalance weekly?"`（检索 + LLM 补全）、`vbt interact "..."`（带工具调用）。

---

## 六、本次深挖新增的增量结论（相对 03/04）

1. **10 工具 → 底层函数映射已精确到函数级**（第一节表），且实测注册表顺序一致。
2. **`run_code` 的 kernel 机制完全解密**：`VBTKernel`（自动 `from vectorbtpro import *`）→ 全局单例 `current_kernel` → `JupyterKernel.execute`（ThreadPoolExecutor 超时 → interrupt → restart 三级降级）→ `collect_output` 按 `parent_header.msg_id` 聚合 IOPub，`raise_on_error=True` 抛 `KernelExecutionError`。实测冷启动 3.72s / 复用 0.01s、状态跨调用保留、错误完整上浮。
3. **AI 系统三大件实测全通**：检索（`search`/`find`/`get_page`/`get_source`/`get_attrs`/`resolve_refnames` 全返回真实内容，走本地缓存离线可跑）、生成（`run_code` kernel 干净执行）、桥接（`auto_cast` + `to_context` + `tool_registry` 驱动）。
4. **发现 3 个真实依赖缺口**：`tiktoken`、`ipykernel`、`jupyter_client`（`[mcp]`/`[knwl]` extra 成员）在开发 `.venv` 里未装 —— 已补齐；`mcp` SDK 也未装（server 启动前需补 `[mcp]`）。
5. **发现 VBT 核心环境缺口（非 AI 系统）**：`vectorbtpro_rust` 是纯 Python 壳（版本号匹配 → `is_rust_available()==True`），缺编译 PyO3 `.pyd` → 一旦跑真回测就 `AttributeError: __build_profile__`。这是 `[rust]` extra / `maturin develop` 的问题，与 MCP/AI 层正交。
