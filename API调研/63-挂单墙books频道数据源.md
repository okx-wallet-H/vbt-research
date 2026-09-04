# 63 — 订单簿挂单墙数据源（books 频道）速查

> api-researcher · 任务 #4 之「订单簿挂单墙数据源」· 2026-09-04
> 数据源本身已被 `research/54`（books 频道全景 + 历史下载）与 `_research_repo/订单簿/61`（挂单墙识别 + 稳定性）完整覆盖，且事件研究脚本 `orderbook_wall_event.py` 已经在实跑（books + trades 双频道）。本文只补一份「挂单墙用 books 频道怎么接」的数据源规格，不重复 54/61 的结论。

---

## 1. 结论先行

**挂单墙的数据源是 OKX 公共 WebSocket `books` 频道（400 档，免费无需 key），已经接好（`orderbook_wall_event.py` 在跑）。** 墙的「量」字段就是每档四元组里的 `sz`（挂单量），`sz_liq`（做市商单量）可进一步区分墙是不是做市商堆的。REST `/api/v5/market/books` 拿快照，WS `books` 拿实时墙变动——两个都已实测可用。

---

## 2. 数据源三条路（怎么拿）

| 路 | 端点/频道 | 用途 | 状态 |
|---|---|---|---|
| REST 快照 | `GET /api/v5/market/books?instId=X-SWAP&sz=400`（MCP `market_get_orderbook`） | 单次抓墙、离线分析 | ✅ 本环境实测 |
| WS 实时 | `wss://ws.okx.com:8443/ws/v5/public` 订阅 `books`（400 档，100ms 节流，首帧 snapshot + 之后增量） | 实时盯墙、碰墙事件 | ✅ 61 号已接、`orderbook_wall_event.py` 在跑 |
| WS 轻量 | 同端点订阅 `books5`（前 5 档全量） | 只要近端墙、免增量合并 | 可选，54 号推荐起步 |

- 订阅消息：`{"op":"subscribe","args":[{"channel":"books","instId":"DOT-USDT-SWAP"}]}`
- 增量机制：首帧 `action=snapshot` 全量，之后 `action=update` 只推变化档位，`sz==0` 表示该档撤单；生产环境要按 `seqId/prevSeqId/checksum` 校验本地簿是否失步（短抓可不做）。

---

## 3. 挂单墙的字段（关键）

每档是四元组 `[price, sz, ordCount, sz_liq]`：

| 字段 | 含义 | 对墙的作用 |
|---|---|---|
| `price` | 价位 | 墙的位置（整数关口优先） |
| `sz` | 该档挂单量（永续=合约张数） | **墙量**——识别「绝对量前 5 大 / ≥邻域中位 5x」的墙 |
| `ordCount` | 已废弃，恒 0 | 忽略 |
| `sz_liq` | 该档做市商/强平单量 | 区分「做市商堆的墙」vs「散户堆的墙」 |

本环境实测 BTC-USDT-SWAP（REST 快照，`sz=20`）：顶档极薄（0.01~0.02 张），深一点有大档（451.55 / 292.32 / 90.33 张），`sz` 和 `sz_liq` 都返回了。永续 `sz` 单位是**合约张数**，跨币比墙量要按 `ctVal`（合约面值）归一化成美元/币。

---

## 4. 已有产物（别重复造）

| 文件 | 内容 |
|---|---|
| `_research_repo/订单簿/61-orderbook-wall.md` | 墙识别结果（XRP/DOT/SOL 实测，墙量÷中位=100~3500x）+ 墙稳定性 + 墙 vs K 线 S/R（差一个数量级） |
| `_research_repo/订单簿/orderbook_wall_event.py` | 碰墙事件研究（touch → 1/5/15min 采样 → 反弹/穿透判定），books+trades 双频道 |
| `_research_repo/订单簿/wall_events.jsonl` / `wall_run.log` | 事件研究实时产物 |
| `research/54-futures-orderbook-data.md` | books 频道全景 + 历史 L2 下载中心（2023.3 起）+ TBT 门槛 |
| `backtest/orderbook_capture.py` / `orderbook_stability.py` | 抓快照 / 墙稳定性时间线脚本 |

---

## 5. 一句话给项目

**数据源侧没有遗留问题**：挂单墙的 books 频道免费、已接、字段清楚（`sz` 是墙量）。真正的 open 问题是 61 号末尾的「墙 = 价格反转点」是否成立——那是事件研究（`orderbook_wall_event.py` 正在跑）的结论，不是数据源的问题。
