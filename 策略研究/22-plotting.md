# VBT 绘图可视化（Plotly）研究

> 研究方式：本地源码 `data/develop-src/vectorbtpro/`（`vectorbtpro 2026.6.27`，即当前 `.venv` 实际安装的 editable 源）+ 本机实跑验证。
> 所有「已验证」片段均在 `plotly 7.0.0` + `kaleido 1.3.0` + `matplotlib 3.11.1` 下实际跑通。

---

## 〇、环境与依赖（先说结论，这是最大坑区）

| 依赖 | 版本 | 作用 |
|------|------|------|
| `plotly` | **7.0.0** | 绘图引擎（VBT 的 Figure 就是 plotly 的 `go.Figure`） |
| `kaleido` | **1.3.0** | `fig.write_image()` 静态导出（PNG/SVG/PDF） |
| `matplotlib` | **3.11.1** | ⚠️ **隐藏依赖**：VBT 的 `utils/colors.py::adjust_lightness` 用了 matplotlib 的颜色转换，`pf.plot()` 画订单标记时必现报错，不装它 plotly/kaleido 装了也没用 |

**安装命令**（实测）：
```bash
.venv/Scripts/python.exe -m pip install plotly kaleido matplotlib
```

**关键坑 0 —— kaleido 挂起问题已不存在**：
任务清单里提到的 `kaleido==0.1.0post1` 修图片挂起，是旧版 kaleido（原版已 archived 在 0.2.1）。现在 pip 默认装的是社区维护 fork **kaleido 1.3.0**，`write_image` 实测 **2.08s 出图，无挂起**。不要手动降级到 0.1.0post1。

**关键坑 1 —— plotly 7.x 兼容**：
VBT 源码只做 `assert_can_import("plotly")`（不锁版本），实测 plotly 7.0.0 下 `pf.plot()` / `lineplot` / `ohlcv.plot` 全部正常。但 plotly 7 移除了 `fig.add_traces()`（复数）等旧 API，VBT 内部全部用 `fig.add_trace`（单数），自己写代码时也要用 `add_trace`。

**关键坑 2 —— Rust 坑**：跑回测前必须关掉 auto backend（与前几轮一致）：
```python
vbt.settings['jitting']['backends']['auto_mode'] = False
```

---

## 一、绘图能力清单（各 plot 方法画什么、返回什么）

### 1.1 架构：`plot()` 是 `plots()` 的别名

VBT 所有「可分析对象」（Portfolio / Orders / Trades / Data / Indicator 输出 / 信号访问器）都继承 `Analyzable → PlotsBuilderMixin`。`plots()` 是核心（基于 `plotly.subplots.make_subplots` 的子图构建器），`plot()` 只是别名：

```python
# 源码 portfolio/base.py:15890
plot = Analyzable.plots
```

验证：`pf.plot == pf.plots` → **True**。

**所有 plot 方法统一返回 `plotly.graph_objects.Figure`**（`isinstance(fig, go.Figure)` 为 True），少数支持 `return_fig=False` 时返回 trace 更新器。这意味着拿到 fig 后可以用 plotly 原生 API 继续改。

### 1.2 Portfolio —— 16 个可用子图

`pf.subplots` 全部键（源码 `portfolio/base.py:15762` 的 `_subplots` 配置）：

| 子图名 | 画什么 | 对应 `plot_xxx` |
|--------|--------|-----------------|
| `orders` | 价格线上标记买卖订单（默认子图之一） | `plot_orders` |
| `trades` | 价格线上标记持仓区间 | `plot_trades` |
| `trade_pnl` | 每笔交易盈亏柱状（默认子图之一） | `plot_trade_pnl` |
| `trade_signals` | 交易信号标记 | `plot_trade_signals` |
| `cash_flow` | 现金流 | `plot_cash_flow` |
| `cash` | 现金余额曲线 | `plot_cash` |
| `asset_flow` | 资产流 | `plot_asset_flow` |
| `assets` | 持仓资产数量 | `plot_assets` |
| `asset_value` | 持仓市值 | `plot_asset_value` |
| `value` | **权益曲线**（账户总价值） | `plot_value` |
| `cumulative_returns` | **累计收益**（默认子图之一） | `plot_cumulative_returns` |
| `drawdowns` | 回撤标记（阴影区） | `plot_drawdowns` |
| `underwater` | 水下曲线（drawdown 百分比） | `plot_underwater` |
| `gross_exposure` | 总敞口 | `plot_gross_exposure` |
| `net_exposure` | 净敞口 | `plot_net_exposure` |
| `allocations` | 资产配置比例 | `plot_allocations` |

**默认子图只有 3 个**（源码 `_settings.py:2040`）：
```python
vbt.settings['portfolio']['plots']['subplots']  # -> ['orders', 'trade_pnl', 'cumulative_returns']
```

⚠️ 注意：任务清单里说的「权益、回撤、持仓」子图，对应 VBT 术语是 `value`（权益）、`drawdowns`/`underwater`（回撤）、`assets`/`asset_value`/`allocations`（持仓）——**这些不在默认 3 个子图里**，要用 `subplots="all"` 或 `subplots=[...]` 显式指定。

### 1.3 其他对象的 plot 方法

| 对象 | 入口 | 画什么 |
|------|------|--------|
| `Data` | `data.plot(feature=..., base=...)` | 单 feature 折线（base=1 重定基） |
| `Data`（含 OHLC） | `data.plot(symbol=...)` | **OHLC 蜡烛图**（走 `.vbt.ohlcv.plot`） |
| `Indicator`（内置） | `rsi.plot()` / `ma.plot()` | 指标曲线；MA 类自带 `plot_close=True` 叠加价格 |
| `信号访问器` | `mask.vbt.signals.plot()` | 信号阶梯线（hv 阶跃） |
| `信号访问器` | `entries.vbt.signals.plot_as_entries(y, fig=)` | 在已有图上标记买点三角 |
| `信号访问器` | `exits.vbt.signals.plot_as_exits(y, fig=)` | 标记卖点三角 |
| `Orders` / `Trades` | `pf.orders.plot()` / `pf.trades.plot()` | 订单/交易记录图 |

### 1.4 信号访问器（`.vbt.signals`）专用绘图

源码 `signals/accessors.py:3583`：
- `plot()` —— 信号布尔序列，画成 `shape="hv"` 的阶跃线（0/1 阶梯），y 轴 tick 显示 false/true。
- `plot_as_markers(y, fig)` —— 把信号画成散点标记（圆点）。
- `plot_as_entries(y, fig)` / `plot_as_exits(y, fig)` —— 买点用**蓝色圆点**、卖点用**蓝色圆点**（默认同一颜色），可叠加到价格/指标图上。这是「指标图上标信号」的标准做法。
- `plot_as_entry_marks` / `plot_as_exit_marks` —— 进阶版（范围标记）。

---

## 二、回测绘图实战

### 2.1 最小回测 + 默认图（已验证）

```python
import vectorbtpro as vbt
vbt.settings['jitting']['backends']['auto_mode'] = False   # Rust 坑

price = vbt.GBMData.fetch(["SYM1"], seed=1).get().iloc[:400]
rsi = vbt.RSI.run(price, window=14)
entries = rsi.rsi.vbt.crossed_below(30)
exits = rsi.rsi.vbt.crossed_above(70)

pf = vbt.Portfolio.from_signals(price, entries, exits, init_cash=1000.0, fees=0.001, freq="1d")

fig = pf.plot()                      # 默认 3 子图
fig.show()                           # Jupyter 内联 / script 里开浏览器
```

实测结果：
- `type(fig)` → `Figure`，`isinstance(fig, go.Figure)` → True
- 默认 3 子图标题：`['Orders', 'Trade PnL', 'Cumulative Returns']`，11 条 trace。

### 2.2 指定子图 / 全部子图（已验证）

```python
# 只看权益 + 回撤
fig = pf.plot(subplots=["value", "drawdowns", "underwater"])
# -> ['Value', 'Drawdowns', 'Underwater']

# 全部 16 个子图
fig_all = pf.plot(subplots="all")
# -> 16 个标题，53 条 trace
```

**各子图含义**（理解图的关键）：
- **Orders**：价格线 + 买入/卖出订单箭头（三角形朝上=买、朝下=卖），`check_is_not_grouped=True` 意味着多资产时每列分开画。
- **Trade PnL**：每笔平仓交易的盈亏柱状图（绿盈红亏）。
- **Cumulative Returns**：账户累计收益率曲线，带 0 基线（`pass_hline_shape_kwargs=True`）。
- **Value**：账户总权益曲线（含现金 + 持仓市值）。
- **Drawdowns**：权益曲线 + 回撤期阴影（跌到谷底再恢复的区间）。
- **Underwater**：水下曲线（当前价值相对历史高点的跌幅百分比）。

---

## 三、指标 / OHLC / 信号绘图实战

### 3.1 指标 plot（已验证）

```python
# MA.plot() 自带 plot_close=True → 自动叠加 close 价格
ma = vbt.MA.run(price, window=20)
fig_ma = ma.plot()
# traces: ['Close', 'MA']   ← 一条线图自动把价格+均线画在一起

# RSI.plot() 带 30/70 阴影带
fig_rsi = rsi.plot()          # 默认 limits=(30, 70)
# traces: ['RSI']，1 个 shape（30~70 的阴影矩形）
```

要点：
- 内置指标都实现了专属 `plot()`（`indicators/custom/*.py`），例如 `rsi.py:76` 的 `plot(column, limits=(30,70), rsi_trace_kwargs, fig=...)`。
- `MA.plot(plot_close=True)` 是「指标叠加价格」的**内置现成方案**，不用自己拼。

### 3.2 OHLC K 线（已验证）

```python
data = vbt.GBMOHLCData.fetch(["SYM1"], seed=1)
fig = data.plot(symbol="SYM1")          # -> Candlestick trace，名字 'OHLC'
```

- `data.plot(symbol=...)` 当数据含 OHLC 时自动走 `.vbt.ohlcv.plot()`，默认 `ohlc_type="candlestick"`。
- 换风格：`.vbt.ohlcv.plot(ohlc_type="OHLC")` → `Ohlc` trace（条形 OHLC，非蜡烛）。
- **带成交量**：`plot_volume=True`，但前提是数据有 `Volume` 列。

⚠️ **坑 3 —— GBMOHLCData 没有 Volume**：
`vbt.GBMOHLCData.fetch(...).get()` 返回 **4 个 DataFrame 的 tuple（Open/High/Low/Close，无 Volume）**。对它 `data.plot(symbol=..., plot_volume=True)` 会报 `AttributeError: 'NoneType' object has no attribute 'shape'`（因为 `self.volume is None`）。

要画成交量，需要 5 列 OHLCV。手工构造（单层 feature 列名，已验证）：

```python
import numpy as np, pandas as pd
idx = pd.date_range('2020-01-01', periods=120, freq='D')
# ... 生成 open/high/low/close/volume ...
df = pd.DataFrame({'Open': open_, 'High': high, 'Low': low, 'Close': close, 'Volume': vol}, index=idx)
fig = df.vbt.ohlcv.plot(plot_volume=True)
# traces: [('Candlestick', 'OHLC'), ('Bar', 'Volume')]
```

注意：`.vbt.ohlcv` 访问器要求列名是**单层 feature 名**（`Open/High/Low/Close/Volume`，大小写敏感）。用 `(symbol, feature)` 双层 MultiIndex 时 `volume` 识别不到（`ohlcv.volume is None`）。

### 3.3 信号标记叠加到价格图（已验证）

```python
fig = price.vbt.lineplot(trace_kwargs=dict(name="Price"))
fig = entries.vbt.signals.plot_as_entries(price, fig=fig)
fig = exits.vbt.signals.plot_as_exits(price, fig=fig)
# traces: ['Price', 'Entries', 'Exits']
```

---

## 四、指标叠加价格的三种做法

### 做法 A：内置指标自带的 plot（最简单）
```python
vbt.MA.run(price, window=20).plot()   # 自动带 close
```

### 做法 B：`.vbt.lineplot(fig=fig)` 链式叠加（VBT 风格）
```python
fig = price.vbt.lineplot(trace_kwargs=dict(name="Close"))
fig = ma.ma.vbt.lineplot(trace_kwargs=dict(name="MA20"), fig=fig)
# traces: ['Close', 'MA20']，同一 y 轴
```
这是最推荐的 VBT 原生叠加方式：`lineplot` 接受 `fig=` 参数，往已有 Figure 上续加 trace。

### 做法 C：裸 plotly `fig.add_trace`（完全自定义）
```python
fig = price.vbt.lineplot(trace_kwargs=dict(name="Close"))
fig.add_trace(go.Scatter(x=price.index, y=ma.ma.iloc[:,0], mode="lines", name="MA20(go.Scatter)"))
# traces: ['Close', 'MA20(go.Scatter)']
```

⚠️ 注意：任务清单提到的 `fig.add_traces`（复数）在 plotly 7 里已废弃/移除，**用 `fig.add_trace`（单数）**。

### 做法 D：`make_subplots` 手工拼多面板（价格+RSI+信号，已验证）

```python
from plotly.subplots import make_subplots
import plotly.graph_objects as go

fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03,
                    row_heights=[0.7, 0.3], subplot_titles=["价格 + MA20", "RSI(14)"])
fig.add_trace(go.Scatter(x=price.index, y=price.iloc[:,0], name="Close"), row=1, col=1)
fig.add_trace(go.Scatter(x=price.index, y=ma.ma.iloc[:,0], name="MA20"), row=1, col=1)
fig.add_trace(go.Scatter(x=price.index, y=rsi.rsi.iloc[:,0], name="RSI"), row=2, col=1)
fig.add_hline(y=30, line_dash="dot", line_color="green", row=2, col=1)
fig.add_hline(y=70, line_dash="dot", line_color="red", row=2, col=1)
```

（⚠️ 这个裸 go.Scatter 版本导出 PNG 会触发 kaleido Timestamp 坑，见 §六坑 4；做导出时把 `x=price.index` 换成 `x=price.index.astype(str)`。）

---

## 五、自定义布局 + 导出（书记报告可复用）

### 5.1 自定义标题 / 颜色 / 主题（已验证）

```python
fig = pf.plot(subplots=["value", "drawdowns"])
fig.update_layout(
    title="我的回测 · RSI(14) 超买超卖策略",
    template="plotly_dark",                       # 也可用 vbt 主题 vbt_dark
    font=dict(family="Microsoft YaHei", size=13),
)
fig.update_yaxes(title_text="账户权益", row=1, col=1)
```

**VBT 内置主题**（`vbt.settings['plotting']['themes']`）：`light` / `dark` / `seaborn`，默认 `light`。

```python
vbt.settings.set_theme("dark")     # 把 layout.template 切成 "vbt_dark"，并更新 color_schema
vbt.settings.set_theme("light")    # 切回
```

**颜色 schema**（`vbt.settings['plotting']['color_schema']`，涨绿跌红等）：
```
increasing=#26a69a（涨/绿）, decreasing=#ee534f（跌/红）, lightblue/lightorange/lightgreen/lightred/...
blue=#1f77b4, orange=#ff7f0e, green=#2ca02c, red=#dc3912, purple=#9467bd, ...
```

### 5.2 导出 HTML（已验证，书记报告主力）

```python
fig.write_html("pf_demo.html")
# 实测 4.4 MB —— plotly.js 全部内联，单文件可直接双击打开 / 发给别人
```

### 5.3 导出 PNG / SVG（kaleido，已验证）

```python
fig.write_image("pf_demo.png", width=1200, height=700, scale=1)   # 69 KB，2.08s
fig.write_image("pf_demo.svg", width=1200, height=700)            # 32 KB
```

导出的 demo 文件在 `research/_plot_tmp/`：`pf_demo.html` / `pf_demo.png` / `pf_demo.svg` / `dark.png` / `vbt_lineplot.png`。

**落地能力结论**：HTML（单文件、可交互）→ PNG/SVG（静态、可进报告）→ 两条路都验证通。书记生成报告时，HTML 用于交互演示，PNG 用于嵌入 markdown/word。

---

## 六、关键坑汇总

### 坑 1：matplotlib 是隐藏依赖
`pf.plot()` 画订单时调用 `utils/colors.py::adjust_lightness`，内部 `assert_can_import("matplotlib")`。只装 plotly+kaleido 会报：
```
ImportError: Please install matplotlib>=3.2.0.
```
→ 必须一起装 `matplotlib`。

### 坑 2：kaleido 挂起问题已不存在
旧版 kaleido（0.1.0post1 那类）有图片导出挂起的 bug。现在 pip 默认是社区 fork **kaleido 1.3.0**，实测正常，不要降级。

### 坑 3：GBMOHLCData 无 Volume
`GBMOHLCData.get()` 返回 4 元组（OHLC，无 Volume），`plot_volume=True` 会崩。要成交量就手工造 5 列 OHLCV DataFrame（单层 `Open/High/Low/Close/Volume` 列名）再走 `.vbt.ohlcv.plot(plot_volume=True)`。

### 坑 4（最重要）：kaleido 导出遇 `pd.Timestamp` 报错
**症状**：用裸 `go.Scatter(x=price.index, ...)` 拼的图，`fig.write_image()` 报：
```
TypeError: Type is not JSON serializable: Timestamp
```
**原因**：VBT 自己的 `pf.plot()`/`lineplot` 内部有 `clean_labels()`（`generic/plotting.py`），会把 `pd.Timestamp` 转成 ISO 字符串；而裸 `go.Scatter(x=DatetimeIndex)` 把 `Timestamp` 对象原样塞进图数据，kaleido 的 JSON 序列化不吃。

**实测矩阵**：
| x 传法 | 导出结果 |
|--------|----------|
| `x=price.index`（DatetimeIndex） | ❌ FAIL |
| `x=price.index.to_numpy()`（datetime64） | ❌ FAIL |
| `x=price.index.tolist()`（pandas 3 返回 Timestamp） | ❌ FAIL |
| `x=price.index.astype(str)` | ✅ OK |
| VBT 自带 `price.vbt.lineplot()` | ✅ OK（内部已清洗） |

**workaround**：优先用 VBT 自己的绘图函数（自动清洗）；必须裸 `go.Scatter` 时把索引转字符串 `x=price.index.astype(str)`（或用 `[t.isoformat() for t in price.index]` 保持日期语义）。注意 `.astype(str)` 会把 x 轴变分类字符串轴，追求真日期轴用 isoformat 列表。

### 坑 5：notebook vs script 差异
- 默认 renderer（`plotly.io.renderers.default`）实测是 **`browser`**。所以：
  - **Jupyter**：`fig.show()` 内联渲染（渲染器是 notebook）。
  - **脚本/CLI**：`fig.show()` 会尝试**打开浏览器**，无头环境会失败或无输出。脚本里要么用 `fig.write_html/write_image` 落盘，要么 `fig.show(renderer="browser")` 前先 `pio.renderers.default = "png"` 之类。
- VBT 的 `use_widgets` 默认 `False`；若要 ipywidgets 交互组件（`use_widgets=True`）需额外装 `ipywidgets`（当前环境未装）。
- `use_resampler` 默认 `False`；大数据画图可选装 `plotly-resampler`（源码 `generic/plotting.py:581` 有可选导入）。

### 坑 6：plotly 7 API 变化
`fig.add_traces()`（复数）已移除 → 用 `fig.add_trace`。自己写叠加时注意。

---

## 七、总结（给后续轮次/书记的结论）

1. **入口统一**：所有对象 `xxx.plot()` 都是 `plots()` 的别名，返回 `go.Figure`，拿到后可用 plotly 原生 API 无限改造。
2. **回测图**：`pf.plot()` 默认只有 Orders / Trade PnL / Cumulative Returns 3 子图；权益(`value`)、回撤(`drawdowns`/`underwater`)、持仓(`assets`/`asset_value`) 要用 `subplots=["..."]` 或 `"all"` 显式开。
3. **指标叠加价格**：首选 `MA.plot(plot_close=True)` 内置方案；通用做法是 `price.vbt.lineplot()` + `ind.vbt.lineplot(fig=fig)` 链式叠加；信号标记用 `entries.vbt.signals.plot_as_entries(price, fig=fig)`。
4. **导出落地**：HTML（单文件交互，4.4MB）和 PNG/SVG（kaleido，秒级）两条路都验证通，书记可直接复用。
5. **四个必踩坑**：装 matplotlib（隐藏依赖）→ 用 kaleido 1.3（勿降级）→ 裸 go.Scatter 导出前把索引 `.astype(str)` → plotly 7 用 `add_trace` 单数。
