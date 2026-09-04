# 51 — 链上筹码成本分布数据源侦察

> 目标：给「策略一：主流币链上筹码分布择时」找数据源。核心指标是**「每个价位套着多少筹码/资金」**（BTC URPD、ETH 成本分布），辅助是 MVRV / SOPR / realized price 这类聚合成本周期指标。

## 一句话结论（先看这个）

**最可行**：免费走 **CoinMetrics Community API**（MVRV / realized cap，无 key）+ **Blocklens MCP** / **BGeometrics**（SOPR）拿聚合成本周期信号；要真正的「每个价位套多少筹码」**URPD / CBD 逐价位分布，付费只有 Glassnode（$799/mo 起）或 CryptoQuant（$99/mo，最便宜且带 realized price bands + Claude MCP connector）**。

**缺什么**：免费可用的**逐价位成本分布**（URPD/CBD）源**不存在**——所有免费源（CoinMetrics/Blocklens/BGeometrics/BRK）都只给聚合 realized cap / MVRV / SOPR，不给每个价格桶的筹码量。DIY 解析 BTC UTXO set 可行但重（Bitcoin Core 全节点 + 全 UTXO set + 历史逐日价格回填）。

---

## 一、外部数据源清单

| 源 | 成本分布相关指标 | 免费/付费 | 历史深度 | API 端点 |
|----|----------------|-----------|---------|---------|
| **Glassnode** | **URPD（UTXO Realized Price Distribution，每个价位筹码量，正是目标）**、CBD（地址平均成本+时间热力图）、MVRV、SOPR（LTH/STH）、realized price/cap、UTXO age bands / HODL Waves | Free tier 仅 T1 指标 + **24h 延迟**，不含 API；API 需 **Professional $799/mo** 或 Institutional $1799/mo；Advanced $29–99/mo 无 API | 完整（2010 起），日/时/10min | `api.glassnode.com/v1/metrics/{category}/{name}`，`X-Api-Key` |
| **CryptoQuant** | SOPR、MVRV、NVT、**realized price bands（筹码成本带）**、UTXO 筹码成本带、exchange flow（inflow/outflow/reserve）、miner flow、stablecoin reserve（245+ 指标） | **Basic 免费**（~30 指标，延迟）；Advanced $29/mo；Professional $99/mo；Premium $799/mo 无额度限制 | 完整，block/hour/day 粒度 | `docs.cryptoquant.com`；另有 **Claude AI MCP connector**（keyless sandbox：`mcp.cryptoquant.com/mcp`，待验证） |
| **Santiment** | SOPR、MVRV、realized value（均列为 restricted 指标） | Free 1000 calls/mo，但 restricted 指标**只给 1 年历史 + 30 天滞后**；Pro $49/mo；**Max $249/mo 解除滞后** | 付费完整；免费受限 | `api.santiment.net`（GraphQL SanAPI） |
| **CoinMetrics** | **CapRealUSD（realized cap）**、**CapMVRVCur（MVRV）**、NVTAdj、AdrActCnt、SplyAct1d | **Community API 完全免费、无 key、10 req/6s**；Network Data Pro 付费 | 完整（1d 频率） | `community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc&metrics=CapMVRVCur,CapRealUSD&frequency=1d` |
| **Messari** | on-chain metrics（asset/network/stablecoin timeseries） | **2026 年 6 月停掉 Free/Lite/Pro self-serve**，现 Enterprise 报价制（$9.9k–25.5k/yr）；x402 按次付费，列表端点 $0，timeseries $0.15–0.25/次 | 完整 | `api.messari.io` |
| **OKX** | **无 MVRV/SOPR/URPD 链上估值指标**；只有社媒情绪（mention/bullish/bearish）、链上基础数据（block count/size/rewards）、Wallet API（地址余额/交易，含 UTXO） | 公共 API 需签名 key | — | `web3.okx.com/api/v6/dex/market/social/sentiment/symbol` 等 |

### 免费 / 开源 DIY（链上原始数据）

| 源 | 能拿什么 | 免费/付费 | 说明 |
|----|---------|-----------|------|
| **BRK / Bitview**（bitview.space） | realized cap、**MVRV**、**SOPR**、NVT（8000+ 指标） | **开源 MIT，无 key，无 rate limit**，可 self-host（`cargo install brk_cli`） | 从 Bitcoin Core 本地解析，最接近「免费拿到全部聚合指标」 |
| **Blocklens MCP**（blocklens.co） | 109 指标，realized price / MVRV（基础层）、SOPR（Pro 层） | Demo 无需 key，**60 天历史**，不限请求 | MCP 形态，可直接挂进环境 |
| **BGeometrics / bitcoin-data.com** | Realized Price、MVRV Z-Score、SOPR、NUPL | **免费 key**（注册），8 req/hr、15 req/day | 有现成 `GET /api/v1/mvrv` 类端点 |
| **Blockchair** | 地址/UTXO 集查询 | 免费测试 1000 calls/day | **无现成 UTXO age distribution 聚合端点**，只能逐地址查 |
| **Dune** | SQL 查 BTC/ETH 原始数据（可自建成本分布） | 免费只能看/复制社区 dashboard；**跑 query 需 Analyst $75/mo** | 社区有 realized price / cost basis 类公开 dashboard 可白嫖视图 |

---

## 二、onchainos-cli 实测结论

**状态：无法实测——授权过期。**

- 全部工具（`token_cluster_supported_chains`、`portfolio_chains`、`signal_chains`、`leaderboard_chains`、`token_holders`、`token_cluster_overview`、`market_price`、`token_info`）都返回：
  ```
  API error (code=53017): Your Agentic Wallet Authorization has expired.
  Please log in again or refresh your authorization.
  ```
- 也就是说 onchainos-cli 整个 MCP server 都依赖 **Agentic Wallet 登录**，当前授权过期，需要用户重新登录才能拉数据。

**从工具 schema 静态判断（即使能登录，它也不是成本分布工具）：**

| 工具 | 实际给什么 | 是成本分布吗 |
|------|-----------|-------------|
| `token_holders` | 持币地址/数量分布 | ❌ 持币数量分布 |
| `token_cluster_overview` | holder 簇集中度、rug pull %、新地址 % | ❌ |
| `token_cluster_top_holders` | top 10/50/100 holder 簇分析 | ❌ |
| `token_top_trader` | 盈利地址（聪明钱） | ❌ |
| `token_advanced_info` | 风险等级、creator、dev 持仓、holder 集中度 | ❌ |

**关键问题答案：**
1. **是「持币数量分布」还是「成本分布」？** —— 是**持币数量/地址分布 + 聪明钱标签**（KOL/whale/sniper/dev/bundler），**完全没有「什么价位套了多少筹码」的成本概念**。
2. **能不能查原生 BTC？** —— **不能**。所有工具都要求 `token contract address`（ERC20 / Solana），`chain` 默认 `ethereum`，无原生 BTC UTXO 概念，也无 bitcoin chain 选项。

**结论**：onchainos-cli 是 DEX/聪明钱/持币画像分析工具，**不是链上成本分布工具**；且当前 auth 过期需重登。

---

## 三、逐价位成本分布（URPD/CBD）到底谁有

| 指标 | 含义 | 免费源 | 付费源 |
|------|------|--------|--------|
| **URPD**（UTXO Realized Price Distribution） | 每个价位桶里、当前 UTXO 集「最后移动时的价格」对应的筹码量 = **每个价位套多少 BTC** | ❌ 无免费 | Glassnode（$799/mo 起）；CryptoQuant「UTXO 筹码成本带」 |
| **CBD**（Cost Basis Distribution） | 每个价位桶里、按地址平均成本聚合的筹码量 + 时间热力图（可看累积区演化） | ❌ 无免费 | Glassnode（ETH/BTC 均支持） |
| realized price / cap | 全体持有者平均成本（聚合单值） | ✅ CoinMetrics `CapRealUSD`、Blocklens、BGeometrics、BRK | 各家都有 |
| MVRV | 市值 / realized cap（周期高低点） | ✅ CoinMetrics `CapMVRVCur`、Blocklens、BGeometrics、BRK | 各家都有 |
| SOPR | 已花费输出利润率（LTH/STH 变体） | ✅ BGeometrics、BRK；Blocklens SOPR 是 Pro 层 | Glassnode、CryptoQuant、Santiment |

---

## 四、给蜂王的建议路线

1. **先免费验证策略（本周可做）**：用 **CoinMetrics Community API**（无 key）拉 BTC/ETH 的 `CapMVRVCur`（MVRV）+ `CapRealUSD`（realized cap → realized price = realized cap / supply），配合现成 `backtest/` 框架跑 MVRV 择时，看用户心法在聚合成本信号上有没有 edge。成本 $0。
2. **补 SOPR / 更细指标**：接 **Blocklens MCP**（Demo 无需 key）或 **BGeometrics**（免费 key）拿 SOPR / MVRV Z-score。
3. **要真正的逐价位筹码分布（URPD/CBD）**：只有付费。**CryptoQuant Professional $99/mo 是性价比最优**（比 Glassnode $799 便宜一个量级，且带 realized price bands + 有 Claude MCP connector 可能直接挂进环境，keyless sandbox 待验证）。Glassnode 数据最权威但贵。
4. **DIY 兜底**：BRK/Bitview（开源 MIT）可自建，从 Bitcoin Core 本地算 MVRV/SOPR/realized cap；但逐价位 URPD 需要全 UTXO set + 历史逐日价格回填，工程量重，仅当付费源不可得时再上。

---

## 来源

- Glassnode：https://unchaindata.xyz/tools/glassnode 、https://docs.glassnode.com/further-information/metric-guides/price-distribution/urpd-utxo-realized-price-distribution.md 、https://docs.glassnode.com/further-information/metric-guides/sopr/lth-sopr.md
- CryptoQuant：https://docs.cryptoquant.com/guides/plans-and-limits 、https://www.cryptoquant.com/ko/pricing 、https://www.kucoin.com/news/flash/cryptoquant-integrates-on-chain-data-into-claude-ai-via-new-connector
- Santiment：https://academy.santiment.net/products-and-plans/sanapi-plans/ 、https://santiment.github.io/articles/access-plans/
- CoinMetrics：https://gitbook-docs.coinmetrics.io/network-data/network-data-overview/economics/valuation.md 、https://github.com/coinmetrics/docs-website
- Messari：https://docs.messari.io/api-reference/x402-payments 、https://costbench.com/software/onchain-analytics/messari/
- 免费/开源：https://charts.bgeometrics.com/bitcoin_api.html 、https://www.npmjs.com/package/blocklens-mcp-server 、https://explore.market.dev/ecosystems/bitcoin/projects/brk 、https://blockchair.com/api 、https://github.com/The-OSINT-Newsletter/OSINT-Tools-Library/blob/GitBook/osint-tools/dune.md
- OKX Onchain OS：https://web3.okx.com/ru/onchainos/dev-docs/market/market-social-sentiment-symbol 、https://web3.okx.pro/zh-hant/onchainos/dev-docs/market/onchaindata-block-reference
- URPD 概念：https://www.kucoin.com/knowledge-base/Analysis/what-is-urpd-in-crypto
