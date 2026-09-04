# 32 — 给趋势策略加做空：SOL/DOGE 下行 alpha 到底值多少

> 背景：`research/30-okx-practical.md` 发现框架是 long-only，SOL/DOGE 在回测区间单边跌
> （SOL -24%、DOGE -48%），long-only 只能空仓避跌、吃不到做空收益。本文给趋势策略加做空
> （`direction="both"`），重验「做空到底多赚多少、值不值得上双向」。
>
> 全部数字来自 `backtest/output/shorting_results.json`，脚本 `shorting_compare.py`（实跑，可复现）。

---

## 一句话结论

**做空是真实的下行 alpha，但只对「慢速趋势策略（SMA）× 单边下跌币（SOL/DOGE）」成立。**
默认参数下，SMA 双向比单向：SOL **+19.9% → +54.9%（+35pp）**、DOGE **-14.2% → +19.0%（+33pp，负转正）**。
但 SuperTrend 这类快速翻转策略做空在 SOL 上反而帮倒忙（-38.7% → -58.5%，-20pp，被 whipsaw）。
更关键的是：**诚实样本外（walk-forward + 选参）下，naive 双向并不稳健**——SMA·SOL 双向 OOS
（+17.5%）反而低于单向（+30.5%），overfit gap 从 2.6pp 涨到 9.3pp。**所以「无脑双向」不值得直接上，
但做空对下行币有结构价值，正确路径是加「方向过滤」的做空，而不是 always-in-market 双向翻转。**

---

## 一、数据与方法（与 30 号完全同源）

| 项 | 值 |
|----|----|
| 数据源 | OKX `/market/history-candles`，USDT 本位永续（`-USDT-SWAP`） |
| 币种 | BTC / ETH / SOL / DOGE / BNB（ETH/BNB 作「上涨币」对照） |
| K 线 | 4H，每币 3000 根，区间 2025-04-14 ~ 2026-08-27（约 16.4 个月） |
| 手续费 | 0.05% 单边 |
| 方向 | `longonly` / `shortonly` / `both` 三档，默认参数（零选参） |
| 做空信号 | SMA：快线死叉慢线 = 做空、金叉 = 平空；SuperTrend：方向翻空做空、翻多平空（= 多单信号的镜像） |
| walk-forward | `from_n_rolling(n=10, split=0.5)`，每步前 50% 训练扫全网格选参、后 50% 只用选中参数跑一遍 |

> 两个口径要说清：**全样本默认参数** = 结构性 alpha（不做选参，只看「做空这个动作」本身贡献多少）；
> **walk-forward OOS** = 诚实样本外（含选参，看「双向选参」是否稳健）。30 号已经证明全样本会
> 系统性高估 3~10 倍，所以本文两个口径都报，结论以 OOS 为准。

**做空的正确姿势（本机实测，坑已踩平）**：`vbt.Portfolio.from_signals(close, entries, exits, short_entries=..., short_exits=...)`——
**只传 short 信号、不传 `direction`**。两者同时传会抛
`ValueError: Direction and short signal arrays cannot be used together`（传了 short 信号后 VBT 自动识别双向，
`entries/exits` 自动当 long 信号解释）。`Direction` 枚举 = LongOnly=0 / ShortOnly=1 / Both=2。

---

## 二、全样本默认参数：做空的结构性 alpha

> 「做空增量」= both − longonly，即做空这一侧多赚（或少赚）的部分；「做空占比」= 增量 / both，
> 衡量做空对最终收益的贡献度。

| 策略 | 币 | 买持 | longonly | shortonly | **both** | 做空增量 | 做空占比 |
|------|----|----|----|----|----|----|----|
| SMA | SOL | -24.2% | +19.9% | +29.2% | **+54.9%** | **+35.0pp** | 64% |
| SMA | DOGE | -47.7% | -14.2% | +38.6% | **+19.0%** | **+33.1pp** | 174% |
| SMA | ETH(涨) | +48.4% | +62.3% | -12.5% | +42.1% | **-20.2pp** | — |
| SMA | BTC(平) | -7.3% | +23.9% | +29.3% | +60.2% | +36.3pp | 60% |
| SMA | BNB(涨) | +18.9% | +4.0% | -23.3% | -20.2% | -24.2pp | — |
| SuperTrend | SOL | -24.2% | -38.7% | -32.3% | **-58.5%** | **-19.8pp** | — |
| SuperTrend | DOGE | -47.7% | -12.9% | +36.4% | **+18.8%** | **+31.7pp** | 169% |
| SuperTrend | ETH(涨) | +48.4% | +11.2% | -11.3% | -1.3% | -12.6pp | — |

**读出来的三件事**：

1. **SMA 做空在下行币上是真肉**：SOL 双向 +54.9%（做空贡献 64%）、DOGE 从 -14.2% 转正 +19.0%
   （做空侧 shortonly 单独就 +38.6%）。买持 -24%/-48% 的下跌被完整转化成了收益。
2. **SuperTrend 做空「看币」**：DOGE 有效（+31.7pp），但 SOL 上做空反而更差（-19.8pp）——
   SuperTrend 翻转太频繁，在 SOL 这种高波动下跌里被「空头挤压」来回打脸；long-only 至少能空仓躲，
   双向却是「始终在场」被动挨打。SMA 的慢均线交叉少、拿得住下跌段，所以能吃满下行 alpha。
3. **双向没有方向过滤，在上涨币上做空吃回撤**：SMA·ETH 双向 +42.1% < 单向 +62.3%（做空 -20pp）；
   SuperTrend·ETH 双向直接归零（-1.3%）。「始终在场」= 赌持续有趋势，但上涨币的回调会把空单 squeeze 掉。

---

## 三、诚实样本外（walk-forward）：naive 双向不稳健

| 策略 | 币 | 方向 | OOS累计 | OOS胜率 | gap | Spearman |
|------|----|----|----|----|----|----|
| SMA | SOL | longonly | **+30.5%** | 40% | 2.6pp | -0.16 |
| SMA | SOL | **both** | +17.5% | 60% | **9.3pp** | -0.19 |
| SMA | DOGE | longonly | -12.2% | 20% | 7.1pp | -0.14 |
| SMA | DOGE | **both** | **-4.0%** | 40% | 9.9pp | +0.09 |
| SuperTrend | SOL | longonly | -18.1% | 30% | 7.3pp | +0.03 |
| SuperTrend | SOL | **both** | **-36.8%** | 30% | 14.3pp | +0.09 |
| SuperTrend | DOGE | longonly | +15.2% | 30% | 1.2pp | -0.33 |
| SuperTrend | DOGE | **both** | +14.8% | 50% | 2.9pp | -0.19 |

**关键发现（这是本文最重要的部分）**：

- **SMA·SOL 双向 OOS 反而低于单向**（+17.5% vs +30.5%）。虽然 OOS 胜率从 40% 升到 60%，但
  overfit gap 从 2.6pp 涨到 9.3pp（3.6 倍）——双向多了一整套 short 参数要选，样本内更容易把
  「恰好躲过某段 squeeze 的参数」刷出来，样本外就还回去。逐 fold 看：both 在 fold 2 吃了个
  -21.6% 的空头大亏（longonly 同期只有 -9.0%），一根把复利打下来了。
- **SMA·DOGE 双向略好但仍为负**（-4.0% vs -12.2%，胜率 20%→40%），并没有「做空救活 DOGE」。
- **SuperTrend·SOL 双向 OOS -36.8% 是灾难**，把 long-only 本就惨的 -18.1% 再砍一刀。
- **唯一「几乎不亏」的是 SuperTrend·DOGE**：双向 +14.8% ≈ 单向 +15.2%，但胜率 30%→50%、gap 翻倍。

**结论**：全样本里「做空多赚 35pp」的结构性 alpha，一旦进入「参数网格选参 + walk-forward」，
优势大幅缩水甚至反转。原因不是做空没 alpha，而是**naive 双向把参数空间翻倍 + 无方向过滤**，
过拟合更重。这跟 30 号「全样本会系统性高估」是同一个坑，只是这次做空侧把坑挖得更深。

---

## 四、做空到底值多少（量化下行 alpha，已验证）

**默认参数（零选参，纯结构性）口径**：

| 币 | 策略 | 做空增量 | 说明 |
|----|------|----|----|
| SOL | SMA | **+35.0pp**（+19.9%→+54.9%） | 做空贡献 64% |
| DOGE | SMA | **+33.1pp**（-14.2%→+19.0%） | 做空把整体负转正 |
| DOGE | SuperTrend | **+31.7pp**（-12.9%→+18.8%） | 同上 |
| SOL | SuperTrend | **-19.8pp**（-38.7%→-58.5%） | 做空帮倒忙（whipsaw） |
| ETH(涨) | SMA | -20.2pp（+62.3%→+42.1%） | 上涨币做空吃回撤 |

即：**对 SOL/DOGE 这类单边跌币，SMA 做空能贡献 +33~35pp 的结构性 alpha，DOGE 更是靠做空从亏转盈；
但 SuperTrend 做空只对 DOGE 有效、对 SOL 是负贡献。**

**诚实样本外（walk-forward + 选参）口径**：上述优势不成立。SMA·SOL 双向 OOS +17.5% < 单向 +30.5%；
SMA·DOGE 双向 -4.0%（仍为负）；SuperTrend·SOL 双向 -36.8%。**做空在 OOS 下没有稳定跑赢单向。**

---

## 五、值不值得上双向 —— 结论与下一步

**不值得直接上 naive always-in-market 双向。** 三条理由：

1. **无方向过滤**：双向在上涨币（ETH/BNB）上做空吃回撤（SMA·ETH -20pp、BNB -24pp），
   是把「看多/看空」的判断外包给了策略信号本身，而信号在震荡/反转段会双向挨打。
2. **参数空间翻倍、过拟合更重**：双向多一套 short 参数要选，walk-forward gap 普遍翻 2~3.6 倍，
   OOS 胜率虽然上去了（因为「始终在场」总有一个方向在赚），但复利收益反而下降——典型的
   「胜率虚高、收益被个别大亏吃掉」。
3. **SuperTrend 这类快速翻转策略做空天然危险**：翻转快 = 被 squeeze 的频率高，双向把它
   long-only「空仓躲跌」的唯一优点也剥夺了。

**但做空本身对下行币有真实结构价值，正确路径是「带方向过滤的做空」**（下一步候选，按优先级）：

1. **regime filter 做空**：先用一个独立的慢指标（长期均线斜率 / ADX / 200MA 上下方）判定
   「当前是下行趋势」，只在下行 regime 里允许空单、上行 regime 里只做多、震荡 regime 里空仓。
   这能把 SOL/DOGE 的 +33~35pp 下行 alpha 保留下来，同时避开上涨币的回调 squeeze 和震荡 whipsaw。
   这是比「无脑 both」收益/风险比高得多的打法，也是 30 号「给 RSI 加趋势过滤网」的同一思路。
2. **CPCV / 多随机种子**：用 `PurgedWalkForwardCV` + 多个随机种子看 OOS 分布，确认做空 alpha 不是单路径运气。
3. **拉长历史**：4H 可回拉到 2021 年，看「做空」在真熊市（2022）里是否更值钱——当前 16 个月窗口
   只有一个「分化市」，做空的区间特异性还没坐实。

---

## 六、接口改动（本任务落地的东西）

| 文件 | 改动 | 兼容性 |
|------|------|--------|
| `backtest/strategy.py` | 加 `Signals(entries, exits, short_entries, short_exits)` 命名元组 + `full_signals()` 产出四信号（趋势策略做空 = 多单镜像）+ `is_long_short()`；`STRATEGIES` 加 `long_short` 标记（sma/supertrend=True，rsi=False） | 策略函数签名不变，`run_strategy()` 仍返回 2 元组，**零破坏** |
| `backtest/backtest.py` | `from_signals()` 透传 short 信号；`run_backtest(..., direction="longonly")` 加 `direction` 三档（longonly/shortonly/both） | 默认 `longonly`，行为与旧版**完全一致** |
| `backtest/optimize.py` | `grid_scan()` / `walk_forward()` 加 `direction` 参数，抽 `_signals_for_direction()` 复用 | 默认 `longonly`，旧调用不变 |
| `shorting_compare.py` | 新增：做空验证脚本（多币 × 三方向全样本 + SOL/DOGE walk-forward），产出 `output/shorting_results.json` | 新增，不动旧脚本 |

用法（在项目根目录）：

```python
from backtest import data, backtest

md = data.load_okx_data(["SOL", "DOGE"], bar="4H", limit=3000)
close = md.close["SOL"]

pf_lo = backtest.run_backtest("sma", close, fees=0.0005, freq="4h")                     # longonly（默认）
pf_both = backtest.run_backtest("sma", close, direction="both", fees=0.0005, freq="4h")  # 双向做空
pf_short = backtest.run_backtest("sma", close, direction="shortonly", fees=0.0005, freq="4h")

pf_st = backtest.run_backtest("supertrend", close, high=md.high["SOL"], low=md.low["SOL"],
                              direction="both", fees=0.0005, freq="4h")
```

> 坑备忘：long-only 策略（rsi）传 `direction="both"` 会抛 `ValueError`（提示仅支持 longonly）；
> `signal.py` 执行层仍为 long-only（生成实时下单信号），做空的部署链（空单信号 → okx `side=sell` +
> `posSide=short`）不在本次范围，留到「regime filter 做空」落地时一并接。

---

## 附录：SMA·SOL 逐 fold OOS（看清「做空把一根大亏吃进去」）

```
sma·SOL longonly  [ -3.6  +26.8  -9.0  -3.6  -4.5   0     0    +2.8  +1.0  +22.8 ]  → 累计 +30.5%
sma·SOL both      [ +3.2  +26.8 -21.6  -3.7  -4.6   0    +4.0  +5.1  +2.3  +11.6 ]  → 累计 +17.5%
sma·DOGE longonly [ -1.8   0   +14.9  -5.4 -16.1   0    -3.2  -7.0  -5.9  +15.7 ]  → 累计 -12.2%
sma·DOGE both     [+11.2   0   +15.3  -5.8 -19.8   0    -5.7  -8.5  +1.4  +13.5 ]  → 累计  -4.0%
```

看 sma·SOL：both 在 fold 2 吃了个 **-21.6%**（longonly 同期 -9.0%）——做空侧在 IS 里选出的参数
在 OOS 上撞上了一次空头挤压，一根把「胜率从 40% 提到 60%」的好看统计全吃回去了。这就是
「做空有 alpha、但 naive 双向不稳健」的直观含义。
