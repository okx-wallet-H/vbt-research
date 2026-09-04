# 54 — 永续合约订单簿深度数据源侦察

> 任务：核实 OKX 永续合约**订单簿深度**数据的可用性——实时订阅 + 历史下载 + 鉴权，判断能不能喂 research/45 的「订单簿失衡」信号。
> 结论对标：45 号报告信号 4「订单簿失衡」依赖 L2 深度数据（45 号原话：L2 深度 2023.3 起、历史数据下载系统异步生成文件、状态「待拉」）。本报告把这个「待拉」核实成「能拉，怎么拉」。
> 方法：官方历史数据下载中心正文（firecrawl 实测）+ 官方 WebSocket 文档 + 本环境 okx-market MCP 实测（`market_get_orderbook` 真实返回）。

---

## 一句话结论

**能拿到永续合约历史订单簿深度，但不是从 API 拉，而是从 OKX 官方「历史数据下载中心」下载 L2 订单表文件，最早 **2023 年 3 月**（~3.5 年）**，免费但需 OKX 账号登录、异步生成文件。实时侧公共 WebSocket 免费、无需 key，直接订阅 `books`(400档增量)/`books5`(前5档)/`bbo-tbt`(L1逐笔)。

**对 45 号信号 4 的判定**：数据源成立——L2 深度 2023.3 起可离线批量下载，足够回测「订单簿失衡」信号；但历史深度是**下载中心文件**（快照/增量为待实测），不是 `history-candles` 那种 API 分页流，落地时要做「文件下载 + 解析 + 本地重建订单簿」的工程，且实时 TBT 400 档逐笔要 VIP5+ 门槛。

---

## 一、数据源清单

### 1.1 实时订单簿深度（公共 WebSocket，免费，无需 key）

端点：`wss://ws.okx.com:8443/ws/v5/public`（公共频道无需 login 消息）

| Channel | 深度 | 更新类型 | 推送频率 | 门槛 |
|---------|------|---------|---------|------|
| `bbo-tbt` | L1 最佳买卖 | 逐笔（tick-by-tick） | 实时 | **公开，全部用户** |
| `books5` | 前 5 档 | 每次全量快照 | 100ms | 公开，全部用户 |
| `books` | 400 档 | 首帧全量 + 之后增量（需本地合并 + `checksum` 校验） | 100ms 节流 | 公开，全部用户 |
| `books50-l2-tbt` | 50 档 | 逐笔 | 10ms | **VIP4+（实名）** |
| `books-l2-tbt` | 400 档 | 逐笔 | 10ms | **VIP5+（实名）** |

- 永续合约 instId 格式：`BTC-USDT-SWAP`（spot 是 `BTC-USDT`，futures 是 `BTC-USD-250328`），channel 名和消息结构三者完全一致，只换 instId。
- 订阅方式：单个 `subscribe` 消息，`args` 数组一次订阅多个 instId/channel；订阅/退订请求每连接每小时 240~480 次（批量塞进 args 规避）。
- 连接限制：每 key 最多 100 个 WS 连接；单条消息 payload ≤ 64KB；无数据 30s 断连；心跳 ping/pong ~20s。
- `books` 增量机制：首帧全量，之后只推**变化的档位**，`sz=0` 表示该档删除；用 `seqId`/`prevSeqId` 排序列、`checksum`(CRC32) 校验本地簿是否失步。**坑**：不合并增量时 `books` 的 best bid/ask 会交叉失真（第三方库 dccd 因此直接改用 `books5` 全量快照）。

### 1.2 实时订单簿 REST（免费，本环境已实测）

`GET /api/v5/market/books?instId=BTC-USDT-SWAP&sz=5` —— 本环境 `mcp__okx-market__market_get_orderbook` 已跑通，返回：

```
asks: [[价格, 数量, 订单数(已废弃=0), 做市商单量], ...]
bids: [[价格, 数量, 订单数, 做市商单量], ...]
ts, seqId
```

**字段结构（四元组，REST 与 WS 一致）**：`[price, sz, ordCount, sz_liq]`，其中 `sz`=该档挂单量（币数），`sz_liq`=该档做市商单量，`ordCount` 已废弃恒 0。永续合约 `sz` 单位是合约张数（受 ctVal 影响），做「订单簿失衡」要按币/美元归一化。

### 1.3 历史数据下载中心（官方，免费，需账号，异步生成文件）

来源：`okx.com/zh-hans/historical-data`（firecrawl 抓正文确认），官方描述「**免费下载**现货和期货市场历史交易数据」。

| 数据类型 | 最早可下载 | 说明 |
|---------|-----------|------|
| **订单表（Order Book）** | **2023 年 3 月起 L2 订单表数据** | ← 45 号要的 L2 深度，核实成立 |
| 历史交易 | 2021 年 9 月起 | 逐笔成交 |
| K 线（OHLC） | 2023 年 7 月起 | |
| 资金费率 | 2022 年 3 月起 | 永续 |
| 借币利率 | 2021 年 12 月起 | |

- **关键核实（45 号原话「异步生成文件」）**：下载中心是「点『去下载』→ 生成文件 → 下载」的异步生成流程，不是 API 分页流。是否必须登录、生成的是**快照**还是**增量**格式，官方页面正文未写明（JS 下载中心），**标待实测**。
- **与 API 的关系**：OKX **API 本身没有历史深度回放端点**——REST `books` 只给当前快照，WS `books` 只给实时增量流，**过去某时段的完整 L2 演变无法通过官方 API 事后拉**，只能走下载中心文件，或当时自己录制。

### 1.4 第三方历史订单簿（替代，付费为主）

| 源 | 覆盖 | 免费/付费 |
|----|------|-----------|
| **Tardis.dev** | OKX 合约 `books-l2-tbt`（逐笔 L2）+ `books`（增量）；现货 `books` 增量 2023-02-24~2024-07-19 及 2026-05-21 后 | 每月首日 CSV 可无 key 下载；完整回放需付费 API key |
| Kaiko / CoinAPI / Deltix | 商业 L2 订单簿 | 付费 |

> Tardis 给的 OKX 深度是**增量（incremental_book_L2）**，与官方实时 WS v5 格式一致，可直接回放重建簿——比官方下载中心更「可编程」，但完整覆盖要付费。

---

## 二、三个重点问题的明确结论

### 重点一：实时订阅（bbo / depth5 / depth400）

**有，全部免费公开，无需 key。** 对应关系：`bbo-tbt`（L1 逐笔）、`books5`（前 5 档全量 100ms）、`books`（400 档快照+增量 100ms）。限频：订阅请求 240~480/时/连接，100 连接/key，单条 64KB。逐笔 TBT 400 档（`books-l2-tbt`）要 VIP5+，普通 400 档（`books`）公开但 100ms 节流 + 需本地合并增量。

### 重点二：历史订单簿深度（重点核实项）

**有，但走下载中心文件，不是 API。** 核实结果：
- ✅ 官方下载中心「订单表」= **L2 订单表数据，从 2023 年 3 月起**（firecrawl 抓官方正文确认，比 45 号写的「2023.3」一致）。
- ✅ 「异步生成文件」机制核实：官方页面是「点去下载 → 生成文件」的下载中心，不是 API 流（45 号线索成立）。
- ⚠️ **两个待实测**：① 下载是否必须登录 OKX 账号（页面正文未写，下载中心普遍要求登录）；② 订单表文件是**快照还是增量**格式（45 号/Tardis 语境下更可能是增量 book，需打开文件确认字段）。
- ❌ **API 无历史深度回放**：`history-candles`/`history-trades` 能分页拿历史 K 线/成交，但**没有对应的 `history-books`**。想事后拿「上周的完整 L2 演变」，官方 API 做不到，只有下载中心或自录。

### 重点三：鉴权 / 免费

| 项 | 鉴权 | 免费/付费 |
|----|------|-----------|
| 实时 WS 公共频道（books/books5/bbo-tbt） | 无需 key | 免费 |
| REST 公共行情（books/candles/trades） | 无需 key（有 IP 限频） | 免费 |
| 历史数据下载中心（含订单表 L2） | OKX 账号登录（待实测确认） | **免费** |
| TBT 400 档逐笔 `books-l2-tbt` | 实名 + VIP5 | 需 VIP5 交易等级（付费档） |
| 第三方 Tardis 完整历史回放 | API key | 付费 |

---

## 三、能不能喂 45 号「订单簿失衡」信号

**能，分两条路：**

1. **回测路（历史）**：官方下载中心下 2023.3 起的 L2 订单表文件（免费、批量、离线）→ 解析成逐档 bid/ask → 按 45 号信号 4 的公式算「订单簿失衡」→ 用 `backtest/` 框架过 CPCV/Monte Carlo。这是主路，覆盖 ~3.5 年，够验证。工程成本在「文件下载 + 解析 + 本地重建/对齐」。
2. **实盘路（实时）**：公共 WS 订阅 `books5`（前 5 档失衡，够用且免本地增量合并）或 `books`（400 档，需合并+checksum）→ 实时失衡信号。免费、无需 key，当天就能挂。

**注意点（喂信号前必看）**：
- 永续 `sz` 单位是合约张数，跨币对比失衡要先按 `ctVal`（合约面值）归一化成美元/币。
- `books` 增量不合并会交叉失真；简单起步用 `books5` 全量快照（dccd 库的实战选择）。
- 历史文件是「某一时刻的簿」还是「连续演变」取决于文件是快照还是增量——**先下一个小文件确认格式再写解析**，别假设。

---

## 四、待实测清单

1. 官方下载中心「订单表」点进去后：是否需登录、文件格式（CSV.gz?）、字段是快照还是增量、单文件覆盖时段/间隔。
2. 下载中心是否区分「永续/交割/现货」订单表，还是混在一起；永续 `BTC-USDT-SWAP` 的 L2 文件粒度。
3. 实时 WS `books` 增量 + checksum 合并的本地实现（已有 `scripts/` 可复用 frida/pwsh，但订单簿本地簿重建是新的）。

---

## 来源

- OKX 官方历史数据下载中心（firecrawl 抓正文）：https://www.okx.com/zh-hans/historical-data ——「订单表：2023 年 3 月起 L2 订单表数据」「免费下载」
- OKX WebSocket 公共频道（books/books5/bbo-tbt/books-l2-tbt 门槛）：OKX API guide https://my.okx.com/docs-v5/trick_en/ ；TBT 订阅规则变更公告 https://okexsupport.zendesk.com/hc/en-us/articles/5399923178253
- 本环境实测：`mcp__okx-market__market_get_orderbook` → `GET /api/v5/market/books` 真实返回 asks/bids 四元组
- 第三方库（OKXSource，books5 vs books 增量坑、无历史深度 API）：https://download-crypto-currencies-data.readthedocs.io/en/latest/okx.html
- Tardis.dev OKX 历史深度覆盖：https://docs.tardis.dev/historical-data-details/okex-futures
- Stack Overflow（OKX API 无历史深度回放，只有下载中心/自录）：https://stackoverflow.com/questions/79964942
