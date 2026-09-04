# 60 — OKX 官方 API 能力调研（震荡套利 / 批量下单 / 账户级止盈）

> 调研方式：直接用环境内已配的 `okx-trade-mcp` + `okx-market` 两个 MCP 服务器实测（只读调用，未下单）。
> 调研日期：2026-09-04

## TL;DR（结论速览）

1. **OKX 官方 API 能完整替代总部 exec 的 5 个接口，且每一项都更强**：下单（4 种订单类型 + 7 种算法单 vs 总部只有市价）、止盈止损（attached TP/SL + OCO + 跟踪止损 + 改价 vs 总部固定 OCO 且止损有 bug）、批量（官方一次 20 单 vs 总部逐个）、平仓（一键平单币 vs 总部平仓）。
2. **官方没有「总权益 +5% 一键全平」的现成接口**，需要客户端自己监控权益（`account_get_balance_all`）+ 枚举持仓（`account_get_positions`）+ 循环平仓（`swap_close_position` / 批量 20 单）。但官方把「平仓」这一步做得很干净（`autoCxl` 自动撤掉该币挂着的止盈止损单）。
3. **最值得接进来的三样**：① 算法止盈止损单（OCO / 跟踪止损 / 改价）② 批量下单（`swap_batch_orders` 一次 20 单）③ 聪明钱信号模块（多空比 + 资金流，可作为震荡区间方向偏置的过滤层）。
4. **托管网格/DCA 机器人不建议接**：它做的是固定区间挂单网格，和我们的「贴近支撑做多/贴近阻力做空 + 账户级止盈」逻辑不同，且托管在 OKX 端难做全局权益止盈，反而拆成两套风控。
5. **两个关键配置坑（实测发现）**：当前 API key 权限是 `read_only,trade`（能交易，但不能划转/提现）；账户是 **对冲模式 `long_short_mode`**（下单必须带 `posSide`，否则报错）；账户结算币是 **USDC/USDG**（USDS 保证金），和行情里的 `USDT-SWAP` 不是一套，下单前要确认用哪个 instId。

---

## 环境现状（两个 MCP server 的边界）

| 服务器 | 版本 | 实际模块 | 用途 |
|---|---|---|---|
| `okx-trade-mcp` | 1.4.3-beta.2 | **全模块 enabled**（spot/swap/futures/option/account/event/news/smartmoney/earn/bot/skills） | 交易 + 信号 + 托管机器人，主战场 |
| `okx-market` | 1.4.2 | **只留 market 行情**（其余全部 `MODULE_FILTERED`） | 纯只读行情/指标/筛选，无需 key |

实测 `account_get_config` 返回真实账户（说明鉴权已配好）：
- `perm: read_only,trade` — **能读能交易，但不能划转/提现/理财**（对策略够用）
- `posMode: long_short_mode` — **对冲模式**，每个交易对可同时持多/空两个方向（对震荡套利其实是加分项）
- `settleCcy: USDC`（settleCcyList: USDC/USDG）— 账户默认走 USDS 保证金，不是 USDT
- `level: VIP3`、`acctStpMode: cancel_maker`

---

## 一、下单能力（vs 总部 exec：只有市价）

官方下单分两层：

### 1. 普通单 `swap_place_order`（永续，实测 ctVal=0.01 BTC / lotSz=0.01 / tickSz=0.1 / 杠杆 100x）
`ordType` 支持 **market / limit / post_only / fok / ioc** 五种，还带：
- `tgtCcy`: base_ccy（张）/ quote_ccy（按 USDT 名义额）/ margin（按保证金）三种下单单位 —— **直接「按 USDT 金额下多少仓位」**，不用自己算张数
- `reduceOnly`: 只减仓不反手
- `stpMode`: cancel_maker/cancel_taker/cancel_both 自成交保护
- `clOrdId`: 客户端单号（幂等，防重复下单）

### 2. 算法单 `swap_place_algo_order`（总部 exec 完全没有）
`ordType` 支持 **conditional / oco / move_order_stop / trigger / chase / iceberg / twap** 七种：
- **trigger**（触发单）：价格到阻力位才挂空单、到支撑位才挂多单 —— 天然贴合「贴近阻力做空、贴近支撑做多」的挂单逻辑
- **iceberg / twap**：大额拆单，不砸盘
- **chase**：智能追价

> 对震荡套利最有用的两个：`trigger`（预埋阻力/支撑挂单）+ `twap/iceberg`（批量开仓时不推价格）。

---

## 二、止盈止损（vs 总部 exec：自动设 OCO，且记忆里记录止损/fill_px 有 bug）

官方止盈止损有三层，全比总部强：

### 1. 挂单自带 TP/SL（`swap_place_order` 的附加参数）
下单同时挂 `tpTriggerPx` + `slTriggerPx`，且：
- `tpOrdKind: condition / limit` — 止盈可以是触发市价，也可以是限价（不吃滑点）
- `tpTriggerPxType / slTriggerPxType: last / index / mark` — 触发价可选最新价/指数价/标记价（**用 mark 价止损可避开插针**）
- `slOrdPx: -1` = 触发后按市价止损

### 2. 独立算法单（`swap_place_algo_order`）
- **oco**：TP+SL 同时挂，先触发哪个撤另一个
- **conditional**：单独挂 TP 或 SL
- **move_order_stop**：**跟踪止损**（callbackRatio 回调比例 / callbackSpread 回调价差 / activePx 激活价）—— 总部 exec 没有跟踪止损
- `cxlOnClosePos`: 持仓平掉后自动撤掉挂在它身上的 TP/SL 单

### 3. 改价（总部 exec 没有）
- `swap_amend_order`：改未成交单的价格/数量
- `swap_amend_algo_order`：改已挂 TP/SL 的触发价/数量
- **部分平仓**：`swap_place_algo_order` 的 `sz`（张数）或 `closeFraction`（关仓比例）可只平一部分

> 结论：官方止盈止损比总部 exec 强在 ① 可选 mark 价止损（防插针）② 跟踪止损 ③ 随时改止损价 ④ 支持部分平仓。这几项对震荡套利的「贴近支撑/阻力 + 单笔止损」是直接升级。

---

## 三、批量操作（vs 总部 exec：只能逐个下单）

官方有**原生批量接口**，一次最多 **20 单**：
- `swap_batch_orders`（action = place / cancel / amend 三合一，永续/交割通用）
- `swap_batch_cancel` / `swap_batch_amend`
- `spot_batch_orders` / `spot_batch_cancel` / `spot_batch_amend`
- `futures_batch_orders` / `futures_batch_cancel` / `futures_batch_amend`
- `option_batch_cancel`

> 结论：**「多币同时开仓」官方原生支持**（`swap_batch_orders` action=place，一次 20 个币）。总部 exec 需要循环调 20 次下单接口，官方一次搞定，且原子性/速度更好。

---

## 四、账户级止盈 / 风控（vs 总部 exec：没有）

**官方没有「总权益 +5% 自动一键全平」的现成接口**，但有配套的原料，可以拼出来：

| 需要的环节 | 官方接口 | 说明 |
|---|---|---|
| 监控总权益 | `account_get_balance_all`（trading+funding+valuation 一键全量） | 一个调用拿到总权益，客户端比对 +5% 阈值 |
| 枚举所有持仓 | `account_get_positions`（跨所有类型）/ `swap_get_positions` / `futures_get_positions` | 拿到全部开仓 |
| 平单币 | `swap_close_position`（市价平整个持仓，`autoCxl` 自动撤该币挂的 TP/SL 单） | 干净平仓 |
| 批量平仓 | `swap_batch_orders`（action=place + reduceOnly）或循环 close | 一次平最多 20 个 |
| 单笔止损 | 见「二」，挂单自带 SL / 独立 conditional / 跟踪止损 | 逐笔风控 |
| 持仓上限/杠杆 | `account_set_position_mode`（net/hedge 切换）、`swap_set_leverage`、`account_get_max_size` | 开仓前查最大可开 |

> 结论：**账户级止盈 = 客户端监控权益（`account_get_balance_all` 轮询）+ 触发后枚举持仓 + `swap_close_position` 逐币平 / `swap_batch_orders` 批量平**。官方把这套「平仓」动作做得很干净（`autoCxl` 连挂单一起撤），但「触发条件」必须自己写在策略里，OKX 服务端没有这个触发。

---

## 五、信号/数据模块（smartmoney / news / indicator）

实测全部可返回真实数据：

### smartmoney（聪明钱，官方独有，总部 exec 无）
- `smartmoney_get_traders_by_filter` — 牛人榜（按 pnl / pnlRatio / winRate / maxDrawdown 过滤排序，实测返回真实榜单：P7 第一名 pnl 88万 USDT、胜率 61.9%）
- `smartmoney_get_signal_overview_by_filter` — **聚合多空比 / 加权进场价 / 资金流向 / 1h·24h·7d 变化**（分档 pnlTier/winRateTier/aumTier 过滤）
- `smartmoney_get_signal_trend_by_filter` — 多空共识的时间序列
- `smartmoney_get_trader_positions` / `smartmoney_get_trader_orders_history` — 单个交易员当前持仓/历史单
- `smartmoney_search_trader` — 昵称反查 authorId

> 对策略用处：**震荡套利的难点是「判断现在是不是震荡区间」**。聪明钱的 `longShortRatio` + `capitalFlow` 可以当一个**方向偏置过滤层**——多空比极偏时少做逆势单，多空比均衡时确认震荡格局。但注意：这是**滞后的公开聚合数据**（research/30~43 已证「公开数据+零售成本没有容易 alpha」），只适合做过滤/确认，不适合当主信号。

### news / sentiment（官方独有）
- `news_get_latest` / `news_get_by_coin` / `news_search` — 币圈新闻（按币过滤、重要度、语言）
- `news_get_coin_sentiment` / `news_get_sentiment_ranking` — 情绪快照/趋势
- `news_get_economic_calendar` — 宏观日历（CPI/NFP/FOMC）

> 对策略用处：震荡套利最怕「突发消息打破区间」。可以挂一个**重大消息预警**（news 按 important=high 过滤），重大事件前暂停开仓。

### indicator（行情侧，`market_list_indicators` 实测 90+ 指标）
对「识别震荡区间」直接有用的：`bbwidth`（布林带宽，测挤压）、`bbpct`（%B，测价格在带内位置）、`donchian`（通道突破）、`keltner`（肯特纳通道）、`range-filter`（区间过滤）、`atr`（波动率）、`supertrend`、`fisher`、`qqe`、`rsi`。
还有 `top-long-short`（多空持仓比）指标，直接取市场多空比。

---

## 六、网格 / DCA 托管机器人（bot.grid / bot.dca）

官方有完整的托管机器人，但**不建议接来做震荡套利**：

- `grid_create_order`（spot/contract 网格）：固定上下界 + N 格，托管在 OKX 端自动低买高卖；带 tpTriggerPx/slTriggerPx 网格级止盈止损；`grid_get_liquidate_price` 可预估爆仓价
- `dca_create_order`（现货/合约马丁）：`initOrdAmt` + `safetyOrdAmt` + `tpPct` + `slPct` 的马丁格尔加仓

> 为什么不接：① 网格是「固定区间挂单」，我们的策略是「贴近支撑/阻力单笔开 + 账户级 +5% 全平」，逻辑不匹配；② 托管在 OKX 端的机器人**难以做「总权益止盈」**（机器人只认自己的网格区间，不认账户总权益）；③ 会拆成两套风控，反而难统一。结论：**网格/DCA 只适合当成「独立低风险理财」，不适合并入主策略**。

---

## 七、vs 总部 exec 对比表

| 能力 | 总部 exec（5 接口） | OKX 官方 API | 对策略提升 |
|---|---|---|---|
| 下单类型 | 只有市价 | 市价/限价/post_only/fok/ioc + 7 种算法单（trigger/iceberg/twap/chase/oco/conditional/跟踪止损） | ★★★ 预埋阻力支撑挂单、大额拆单 |
| 止盈止损 | 自动 OCO（记忆记录止损有 bug） | attached TP/SL + OCO + 跟踪止损 + 改价 + 部分平仓 + mark 价止损 | ★★★ 防插针、可调止损、可部分平仓 |
| 批量下单 | 逐个循环 | `swap_batch_orders` 一次 20 单 | ★★★ 多币同时开仓 |
| 账户级止盈 | 无 | 无现成，但 `account_get_balance_all`+枚举持仓+`swap_close_position`（autoCxl）可拼 | ★★ 需自写触发循环 |
| 信号/数据 | 无 | smartmoney 多空比/资金流 + news 预警 + 90 指标 | ★★ 方向过滤 + 突发预警 |
| 账户信息 | 查余额/持仓 | 更全（valuation、账单、最大可开、手续费率） | ★ |

---

## 八、能不能直接替代总部 exec？

**能，且应该替。** 对照总部 exec 的 5 个接口：

| 总部 exec 接口 | 官方替代 | 差异 |
|---|---|---|
| 下单 | `swap_place_order` / `swap_place_algo_order` | 官方强得多（算法单 + 按 USDT 名义额下单） |
| 平仓 | `swap_close_position` | 官方带 `autoCxl`，更干净 |
| 查持仓 | `account_get_positions` / `swap_get_positions` | 等价 |
| 查余额 | `account_get_balance_all` | 官方多 valuation 总权益 |
| 健康检查 | （无对应，客户端自判） | 用 `system_get_capabilities` 或直接调任意只读接口验证 |

**但有两个必须处理的坑**：
1. **对冲模式 `long_short_mode`**：官方下单/设杠杆/查持仓都要带 `posSide`（long/short/net）。当前策略是「单方向交替开平」，对冲模式下要么每个接口老老实实传 `posSide`，要么用 `account_set_position_mode` 切成 `net_mode`（单向持仓模式，和总部 exec 行为一致，切换前提是无持仓无挂单）。
2. **结算币 USDC/USDG**：账户 settleCcy 是 USDC，行情里主力是 `USDT-SWAP`。下单前要确认目标合约到底是 USDT 保证金还是 USDS 保证金，instId 别搞混。

---

## 九、结论：值得接进来什么

**建议按优先级接三样：**

1. **算法止盈止损（P0，直接替换总部 exec 的 OCO）**
   用 `swap_place_order` 的 attached TP/SL（`slTriggerPxType=mark` 防插针）+ `swap_place_algo_order` 的 `move_order_stop`（跟踪止损）+ `swap_amend_algo_order`（动态改止损）。把总部 exec 那个有 bug 的自动 OCO 整个换掉。

2. **批量下单（P0，替换逐个下单）**
   `swap_batch_orders` 一次开 20 个币，配合 `tgtCcy=quote_ccy`（按 USDT 金额）和 `trigger` 算法单（预埋阻力/支撑位）。这是「多币同时开仓」的官方解法。

3. **账户级止盈闭环（P1，自写触发循环）**
   `account_get_balance_all` 轮询总权益 → 触发 +5% → `account_get_positions` 枚举 → `swap_close_position`（autoCxl）逐币平。官方没有现成触发，但这套拼装很简单，且平仓动作比总部 exec 干净。

**建议只用不接（拿来参考，不进主策略）：**
- **smartmoney 多空比/资金流**：做震荡区间判定的方向偏置过滤器（不是主信号）。
- **news 重大消息预警**：打破区间前暂停开仓。
- **market 的 bbwidth/donchian/atr/range-filter**：量化「当前是不是震荡市」，替代拍脑袋。

**不建议接：**
- **网格/DCA 托管机器人**：逻辑不匹配、难做账户级止盈、拆两套风控。

**下一步要拍板的（上报给蜂王/用户）：**
1. 要不要切成 `net_mode`（单向持仓），还是保留 `long_short_mode` 并在所有下单接口统一传 `posSide`。
2. 交易标的走 USDT 保证金还是 USDS 保证金（账户 settleCcy=USDC），决定 instId 用 `-USDT-SWAP` 还是 USDS 系列。
3. 是否要把总部 exec 的下单/平仓接口逐步切到 OKX 官方（建议切，但需先在小仓位跑通 `posSide` 与结算币两件事）。
