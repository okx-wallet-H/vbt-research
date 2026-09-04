# VectorBT® PRO 核心 API 研究

> 研究方式：本地源码 `vectorbt.pro-main/vectorbtpro/`（版本 `2026.6.27`）+ 本地 Python 实跑验证。
> 所有标注「已验证」的代码片段均在本机 `vectorbtpro 2026.6.27`（numba 启用）下实际跑通。

---

## 一、顶层 API 总览

### 1.1 导入约定（重要）

`from vectorbtpro import *` 默认 `star_import = "minimal"`，**只导入** `vbt`、`tp` 和少量工具（`np`/`pd`/`njit` 等），**不会**把 `Portfolio`、`Data` 等直接摊平到全局命名空间。官方推荐写法：

```python
import vectorbtpro as vbt
```

之后用 `vbt.Portfolio`、`vbt.Data`、`vbt.RSI` 等访问。`vbt.GBMData` 等必须用 `vbt.` 前缀（直接 `from vectorbtpro import *` 时这些名字不存在）。

### 1.2 顶层导出的核心对象清单（按模块分组）

| 模块 | 核心导出 |
|------|----------|
| `portfolio` | `Portfolio`、`PortfolioOptimizer`、`Orders`、`Trades`、`Positions`、`Drawdowns`、`EntryTrades`、`ExitTrades`、`FSOrders`、`FOPreparer`/`FSPreparer`/`FOFPreparer`、`pf_enums`、`pf_nb` |
| `signals` | `SignalFactory`、`SignalsAccessor`/`SignalsDFAccessor`/`SignalsSRAccessor`（经 `.vbt.signals` 访问）、`sig_enums`、`sig_nb` |
| `indicators` | `IndicatorFactory`、`IndicatorBase`、`indicator()`、`talib()`、`pandas_ta()`、内置指标（`RSI` `MA` `EMA` `MACD` `BBANDS` `ATR` `OBV` `STOCH` `VWAP` `SUPERTREND` `ADX` `HURST` …）、`ind_nb`、`ind_enums` |
| `data` | `Data`、`DataSaver`、`DataUpdater`、`YFData`、`BinanceData`、`CCXTData`、`GBMData`、`GBMOHLCData`、`RandomData`、`SyntheticData`、`HDFData`、`CSVData`、`ParquetData`、`FileData`、`LocalData`、`RemoteData`、`data_nb` |
| `records` | `Records`、`PriceRecords`、`Ranges`、`Orders`、`Trades`、`Positions`、`Drawdowns`、`rec_nb` |
| `returns` | `ReturnsAccessor`、`ReturnsDFAccessor`、`ReturnsSRAccessor`、`ret_nb`、`ret_enums` |
| `base` | `ArrayWrapper`、`Grouper`、`Resampler`、`Analyzable`、`Wrapping`、`CombineFunc`、`BCO`、`Param`、`broadcast*` |
| `accessors` | `Vbt_Accessor`、`Vbt_SRAccessor`、`Vbt_DFAccessor`、`Vbt_IDXAccessor`、`pd_acc`/`sr_acc`/`df_acc`/`idx_acc` |
| `utils` | `njit`、`prange`、`broadcast`、`broadcast_arrays`、`combine_indexes`、`combine_params`、`to_2d_array`、时间工具 |

### 1.3 命名对照（任务清单里的名字 vs 实际 API）

| 任务里的名字 | 实际情况 |
|--------------|----------|
| `vbt.Signal` | ❌ 不存在。信号 = `vbt.SignalFactory`（自定义）+ `.vbt.signals` 访问器（内置生成器） |
| `vbt.Indicator` | ❌ 不存在。指标 = `vbt.IndicatorFactory` + `vbt.indicator()` 函数 + 内置指标类（`vbt.RSI` 等） |
| `vbt.Returns` | ❌ 不存在。收益 = `vbt.ReturnsAccessor`（经 `.vbt.returns` 访问），指标在 `Portfolio` 上以属性暴露 |
| `Portfolio.from_orders_func` | 现名 **`Portfolio.from_order_func`**（单数 `order`） |
| `Indicator.from_apply_func` | 现名 **`IndicatorFactory.with_apply_func`** |

---

## 二、核心类详解

### 2.1 `vbt.Portfolio` — 组合模拟与绩效分析

**一句话用途**：VBT 的核心类，从「订单」或「信号」两种口径模拟投资组合，产出资金曲线、交易记录与全套绩效指标。两大主线：`from_signals`（信号驱动）/ `from_orders`（订单驱动）。

**四个主入口（classmethod）**：

```python
Portfolio.from_signals(
    close, entries=None, exits=None, *,
    direction=None, long_entries=None, long_exits=None,
    short_entries=None, short_exits=None,
    order_mode=False,                     # True 时用订单口径
    size=None, size_type=None, price=None,
    fees=None, fixed_fees=None, slippage=None,
    sl_stop=None, tp_stop=None, tsl_stop=None, tsl_th=None, td_stop=None,  # 止损/止盈
    init_cash=None, init_position=None, freq=None,
    group_by=None, seed=None, ...)          # 大量参数皆可广播/参数化

Portfolio.from_orders(
    close, size=None, size_type=None, direction=None,
    price=None, fees=None, fixed_fees=None, slippage=None,
    init_cash=None, init_position=None,
    group_by=None, call_seq=None, ...)

Portfolio.from_holding(
    close, direction=None, at_first_valid_in="close",
    close_at_end=None, dynamic_mode=False, **kwargs)   # 买入持有（底层转 from_signals）

Portfolio.from_order_func(
    close, *, order_func_nb=None, order_args=(),
    flex_order_func_nb=None, flex_order_args=(),
    pre_segment_func_nb=None, post_segment_func_nb=None,
    pre_row_func_nb=None, post_row_func_nb=None, ...)  # 自定义订单函数（原 from_orders_func）

Portfolio.from_optimizer(
    close, optimizer, pf_method="from_orders", size_type="targetpercent",
    cash_sharing=True, call_seq="auto", ...)            # 由组合优化器建仓
```

**绩效分析（重要：指标是 property，不是方法）**：

```python
pf.stats()                 # 方法 → Series（31 项指标）
pf.total_return            # 属性 → Series/标量
pf.sharpe_ratio            # 属性
pf.max_drawdown            # 属性（另有 pf.max_dd 为组合级口径）
pf.win_rate / pf.profit_factor / pf.expectancy / pf.calmar_ratio / pf.sortino_ratio
pf.returns_stats()         # 方法 → 收益统计
pf.get_total_return()      # 方法 → 底层数组版
```

Portfolio 指标 config 共 31 项（`pf.metrics` 的 key）：`start_index, end_index, total_duration, start_value, min_value, max_value, end_value, cash_deposits, cash_earnings, total_return, bm_return, total_time_exposure, max_gross_exposure, max_dd, max_dd_duration, total_orders, total_fees_paid, total_trades, win_rate, best_trade, worst_trade, avg_winning_trade, avg_losing_trade, avg_winning_trade_duration, avg_losing_trade_duration, profit_factor, expectancy, sharpe_ratio, calmar_ratio, omega_ratio, sortino_ratio`。

**记录访问**：

```python
pf.orders      # FSOrders（Records 子类）
pf.trades      # ExitTrades
pf.positions   # Positions
pf.drawdowns   # Drawdowns
pf.order_records   # 结构化 numpy 数组
pf.value / pf.cash / pf.asset_value / pf.returns / pf.drawdown / pf.allocations  # DataFrame
```

**绘图**：`pf.plot()`、`pf.plot_orders()`、`pf.plot_trades()`、`pf.plot_value()`、`pf.plot_drawdowns()`、`pf.plot_underwater()` 等。

**最小可运行代码（已验证）**：

```python
import vectorbtpro as vbt

price = vbt.GBMData.fetch(["SYM1", "SYM2"], seed=1).get().iloc[:800]
rsi = vbt.RSI.run(price, window=14)
entries = rsi.rsi.vbt.crossed_below(30)
exits = rsi.rsi.vbt.crossed_above(70)

pf = vbt.Portfolio.from_signals(price, entries, exits,
                                init_cash=1000.0, fees=0.001, freq="1d")
print(pf.total_return)        # 属性，按 (rsi_window, symbol) 分组
print(pf.stats())             # 31 项指标
print(pf.orders.count())      # 订单数
print(pf.trades.count())      # 交易数
```

### 2.2 信号 — `vbt.SignalFactory` + `.vbt.signals` 访问器

**一句话用途**：把布尔条件转成 entry/exit 信号（真值数组），是 `from_signals` 的输入。官方称为「最重要的教程之一」。

**内置信号生成（访问器链）**：

```python
s.vbt.crossed_below(30)   # 下穿
s.vbt.crossed_above(70)   # 上穿
s.vbt.signals.first()      # 每个信号段的第一个 True
s.vbt.signals.nth(2)       # 第 n 个 True
s.vbt.signals.clean()      # 清掉同向连续信号（只留反转点）
s.vbt.signals.empty_like(df)   # 空信号模板
s.vbt.signals.generate_random()   # 随机信号
s.vbt.signals.generate_stop_exits()  # 止损止盈信号
s.vbt.signals.rank() / pos_rank() / total() / rate()
# 逻辑运算直接用运算符
entries & exits   # 与
entries | exits   # 或
```

**自定义信号（SignalFactory，继承自 IndicatorFactory）**：

```python
def above_th(a, threshold):
    return a > threshold

MySig = vbt.SignalFactory(
    class_name="MySig", short_name="mysig",
    mode="entries",                    # entries | exits | both（默认）
    input_names=["a"], param_names=["threshold"],
).with_apply_func(above_th, takes_1d=True, threshold=110.0)

s = MySig.run(price)
s.entries          # 布尔信号（mode="both" 时另有 s.exits）
```

`mode` 控制输出：`FactoryMode.Entries` → 输出 `entries`；`Exits` → 输入含 `entries`、输出 `exits`；`Both`（默认）→ 输出 `entries` + `exits`。

**最小可运行代码（已验证）**：

```python
import vectorbtpro as vbt

price = vbt.GBMData.fetch(["SYM1"], seed=1).get().iloc[:800]
rsi = vbt.RSI.run(price, window=14)
entries = rsi.rsi.vbt.crossed_below(30)
exits = rsi.rsi.vbt.crossed_above(70)
print(entries.vbt.signals.first())     # DataFrame
print((entries & exits).sum())         # 逻辑与
```

### 2.3 指标 — `vbt.IndicatorFactory` + 内置指标

**一句话用途**：技术指标，输出可继续走 `.vbt.*` 访问器链（`rsi.rsi.vbt.crossed_below(30)`）。

**内置指标（`run` 统一入口）**：

```python
rsi = vbt.RSI.run(price, window=14)       # 输出 rsi.rsi
ma  = vbt.MA.run(price, window=20)        # 输出 ma.ma
macd = vbt.MACD.run(price)                # 输出 macd.macd / macd.macd_signal / macd.macd_hist
bb  = vbt.BBANDS.run(price)               # 输出 bb.upper / bb.middle / bb.lower
# 另有 vbt.EMA, vbt.ATR, vbt.OBV, vbt.STOCH, vbt.VWAP, vbt.SUPERTREND, vbt.ADX ...
# 输出名见 ind._output_names
```

**自定义指标（`with_apply_func`，原 `from_apply_func`）**：

```python
import pandas as pd

def rolling_mean_1d(a, window):           # takes_1d=True 时按列传入 1D 数组
    return pd.Series(a).rolling(window).mean().to_numpy()

MyMA = vbt.IndicatorFactory(
    class_name="MyMA", short_name="myma",
    input_names=["a"], param_names=["window"], output_names=["out"],
).with_apply_func(rolling_mean_1d, takes_1d=True)

res = MyMA.run(price, window=14)
res.out        # 结果
```

其他工厂方法：`IndicatorFactory.from_talib()`、`from_pandas_ta()`、`from_expr()`；快捷函数 `vbt.indicator()`、`vbt.talib()`、`vbt.pandas_ta()`。

**最小可运行代码（已验证）**：

```python
import vectorbtpro as vbt

price = vbt.GBMData.fetch(["SYM1"], seed=1).get().iloc[:800]
rsi = vbt.RSI.run(price, window=14)
print(rsi._output_names)      # ('rsi',)
print(rsi.rsi.tail(1))        # 最新 RSI 值
```

### 2.4 数据 — `vbt.Data` / `vbt.YFData`

**一句话用途**：统一的数据容器（`Data` 基类 + 各类数据源子类），`.get()` 拉数据，`DataSaver`/`DataUpdater` 做存取与增量更新。

```python
# 合成数据
price = vbt.GBMData.fetch(["SYM1", "SYM2"], seed=1).get()   # DataFrame
# 包装
d = vbt.Data.from_data(price)          # Data 实例
# 行情下载（需网络）
data = vbt.YFData.fetch(["AAPL", "MSFT"], start="2020-01-01").get()
```

数据源类：`YFData`（Yahoo）、`BinanceData`、`CCXTData`、`AlpacaData`、`PolygonData`、`TVData`；本地：`HDFData`、`CSVData`、`ParquetData`、`FileData`；合成：`GBMData`、`GBMOHLCData`、`RandomData`、`RandomOHLCData`。`YFData` 是 `Data` 子类，含 `fetch_symbol`/`fetch` 等方法（已确认类存在，下载需联网）。

**最小可运行代码（已验证）**：

```python
import vectorbtpro as vbt

price = vbt.GBMData.fetch(["SYM1", "SYM2"], seed=1).get()   # 合成行情
d = vbt.Data.from_data(price)
print(d.ndim)                     # 2
```

### 2.5 记录 — `vbt.Records` 及子类

**一句话用途**：订单/交易/持仓/回撤的结构化记录，是 `Portfolio` 的底层产物。`rec.records` 在 PRO 里返回 **DataFrame**（命名列），底层结构化数组经 `rec.records_arr` 访问。

**继承层级（已验证）**：

```
Records (Analyzable, Wrapping)
 ├─ PriceRecords
 │   └─ Orders
 └─ Ranges
     ├─ Drawdowns
     └─ Trades
         └─ Positions
```

**访问方式**：

```python
pf.orders.records      # DataFrame，列: id col signal_idx creation_idx idx size price fees side type stop_type
pf.trades.records      # DataFrame，列含 entry_price/exit_price/pnl/return/direction/status
pf.positions.records   # 同 trades
pf.drawdowns.records   # 列: id col start_idx valley_idx end_idx start_val valley_val end_val status
pf.orders.count()      # Series
pf.orders.stats()      # Series
pf.orders.readable     # 可读 DataFrame
pf.orders.records_arr  # 结构化数组
pf.order_records       # Portfolio 直接暴露的结构化 ndarray
```

**最小可运行代码（已验证）**：

```python
import vectorbtpro as vbt

price = vbt.GBMData.fetch(["SYM1"], seed=1).get().iloc[:800]
rsi = vbt.RSI.run(price, window=14)
entries = rsi.rsi.vbt.crossed_below(30)
exits = rsi.rsi.vbt.crossed_above(70)
pf = vbt.Portfolio.from_signals(price, entries, exits, init_cash=1000.0, freq="1d")
print(pf.orders.records.columns)      # DataFrame 列名
print(pf.trades.count())              # 交易数
```

### 2.6 收益 — `vbt.ReturnsAccessor`（`.vbt.returns`）

**一句话用途**：把价格转成收益、计算收益指标。**注意**：收益指标（`total_return`、`sharpe_ratio`、`max_drawdown` 等）主要在 `Portfolio` 上以**属性**暴露，裸 DataFrame 的 `.vbt.returns` 访问器提供的是 `daily()`/`cumulative()`/`resample()`/`sharpe_ratio()`/`max_drawdown()` 等方法。

```python
price.vbt.returns.daily()            # 日收益
price.vbt.returns.cumulative()       # 累计收益
price.vbt.returns.sharpe_ratio()     # 方法（Series）
price.vbt.returns.max_drawdown()     # 方法
price.vbt.returns.resample("1W")     # 重采样
```

**最小可运行代码（已验证）**：

```python
import vectorbtpro as vbt

price = vbt.GBMData.fetch(["SYM1"], seed=1).get().iloc[:800]
print(price.vbt.returns.daily().shape)      # 日收益 DataFrame
print(price.vbt.returns.sharpe_ratio())     # 夏普比率
```

### 2.7 访问器 — `vectorbtpro/accessors.py`

**一句话用途**：VBT 的 API 特色 —— 给 `pd.Series`/`pd.DataFrame`/`pd.Index` 注册 `.vbt` 命名空间，实现 `df.vbt.xxx` 链式调用。

**注册与继承层级**：

```plaintext
BaseIDXAccessor                 -> pd.Index.vbt.*
BaseSR/DFAccessor
  -> GenericSR/DFAccessor       -> pd.Series/DataFrame.vbt.*
      -> SignalsSR/DFAccessor   -> .vbt.signals.*
      -> ReturnsSR/DFAccessor   -> .vbt.returns.*
      -> OHLCVDFAccessor        -> .vbt.ohlcv.*
  -> PXSR/DFAccessor            -> .vbt.px.*
```

核心：`register_series_accessor("vbt")` / `register_dataframe_accessor("vbt")` / `register_index_accessor("vbt")`；`Accessor`（不缓存）与 `CachedAccessor`（缓存）。快捷入口 `vbt.pd_acc` / `vbt.sr_acc` / `vbt.df_acc` / `vbt.idx_acc`。子访问器注册用 `register_sr_vbt_accessor("signals", Vbt_SRAccessor)` 等。

### 2.8 组合优化 — `portfolio/pfopt/base.py`（`vbt.PortfolioOptimizer`）

**一句话用途**：分配权重（allocations）生成器，喂给 `Portfolio.from_optimizer` 做再平衡回测。实际模块是 `pfopt/` 目录，核心类在 `pfopt/base.py`。

```python
vbt.PortfolioOptimizer.from_random(wrapper, seed=1)        # 随机权重
vbt.PortfolioOptimizer.from_allocations(wrapper, allocs)  # 给定权重
vbt.PortfolioOptimizer.from_allocate_func(wrapper, func)  # 自定义分配函数
vbt.PortfolioOptimizer.from_optimize_func(...)            # 自定义优化函数
vbt.PortfolioOptimizer.from_pypfopt(...)                  # PyPortfolioOpt 集成
vbt.PortfolioOptimizer.from_riskfolio(...)                # Riskfolio 集成
# 顶层另有 vbt.pypfopt_optimize / vbt.riskfolio_optimize 快捷函数
```

**最小可运行代码（已验证）**：

```python
import vectorbtpro as vbt

price = vbt.GBMData.fetch(["SYM1", "SYM2", "SYM3"], seed=1).get().iloc[:500]
pfo = vbt.PortfolioOptimizer.from_random(price.vbt.wrapper, seed=1)  # 注意传 wrapper
pf = vbt.Portfolio.from_optimizer(price, pfo, init_cash=1000.0, fees=0.001, freq="1d")
print(pf.total_return)    # 标量（单组 cash_sharing=True 时 squeeze 成标量）
```

---

## 三、关键枚举（`vbt.pf_enums`）

采用 NamedTuple 风格的枚举，`from_signals`/`from_orders` 均接受字符串或整数：

| 枚举 | 取值 |
|------|------|
| `Direction` | `LongOnly=0` `ShortOnly=1` `Both=2`（`direction="longonly"`/`"both"`） |
| `SizeType` | `Amount` `Value` `Percent` `ValuePercent` `TargetAmount` `TargetValue` `TargetPercent`（及 `Percent100` 等；如 `size_type="targetpercent"`） |
| `OrderType` | `Market` `Limit` `Stop` `StopLimit` 等 |
| `CallSeqType` | `Auto` `Default` `Reversed` `Random` 等（`call_seq="auto"`） |

此外 `pf_enums` 里还有大量 NamedTuple 结构（`Order`、`ExecState`、`OrderContext`、`FlexOrderContext` 等），供自定义订单函数使用。

---

## 四、模块依赖关系简述

```
vectorbtpro/
├── utils/          # 最底层：array_/datetime_/numba jitting/execution/refs…
├── base/           # ArrayWrapper、Grouper、Resampler、Analyzable、broadcasting、combining
│   └── accessors   # BaseIDX/SR/DFAccessor（.vbt 访问器根）
├── generic/        # GenericSR/DFAccessor（通用访问器：fillna/rolling/to_2d_array…）
├── records/        # Records/PriceRecords/Ranges 基类（依赖 base）
├── returns/        # ReturnsAccessor + nb（依赖 base、generic；指标函数在 returns/nb.py）
├── indicators/     # IndicatorFactory/内置指标（依赖 base、records；SignalFactory 依赖 IndicatorFactory）
├── signals/        # SignalFactory + SignalsAccessor（依赖 indicators、records）
├── ohlcv/          # OHLCV 访问器 + nb
├── px/             # px 访问器（量价结合，依赖 ohlcv/signals/records）
├── portfolio/      # Portfolio + Orders/Trades/Positions/Drawdowns + preparing + nb
│   └── pfopt/      # PortfolioOptimizer（依赖 portfolio、records）
├── data/           # Data 基类 + 各数据源（依赖 base、records、utils）
├── labels/         # 标签系统（依赖 base）
├── knowledge/      # 文档/RAG 资产
└── accessors.py    # 顶层 Vbt_Accessor，串起所有子访问器
```

**核心依赖方向**：`utils` → `base` → `generic` → `records`/`returns` → `indicators` → `signals` → `ohlcv`/`px` → `portfolio`（含 `pfopt`）；`data` 相对独立（依赖 `base`/`records`）。`Portfolio` 是集大成者：依赖 signals（from_signals 走 FSPreparer）、records（orders/trades 记录）、returns（收益指标）、pfopt（组合优化）。

---

## 五、核心机制要点

1. **数组化 + 广播**：列=资产、行=时间；几乎所有参数（`size`/`fees`/`price`/`direction`…）都能按行/列/组广播，多维数组=参数网格（例：`RSI.run(price, window=[10,20,30])` 产生带 `rsi_window` 参数层的输出，Portfolio 指标索引里可见 `(rsi_window, symbol)` 层级）。
2. **两条主线**：`from_signals`（entries/exits 布尔数组 + `direction`/`size`）与 `from_orders`（size/price/type 订单）。底层都编译成 Numba 循环，性能来自 `pf_nb`。
3. **访问器链**：`df.vbt.xxx` 是 VBT 的灵魂，`indicator_output.vbt.crossed_below(30)` 让指标直接产出信号。
4. **指标是 property、stats 是方法**：`pf.total_return`（属性）vs `pf.stats()`（方法）——这是 PRO 与开源版 `pf.total_return()` 的常见坑。
