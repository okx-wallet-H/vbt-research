# VBT 代码模式 + Benchmark 结论

> 来源：`vectorbt.pro-main/benchmarks/`（OVERVIEW.md、README.md）、`tests/`、`vectorbtpro/` 源码 docstring、官方教程（basic-rsi）、vectorbt-pro MCP examples。
> 版本基准：vectorbtpro 2026.4.7（benchmark 生成于 2026-06-25，Apple M3 arm64 / CPython 3.11.8 / NumPy 2.3.4 / Numba 0.62.1 / rustc 1.94.1）。

---

## 一、Benchmark 性能结论汇总

### 1. 缩写与后端含义

| 缩写 | 全称 | 含义 |
|------|------|------|
| `nb` | Numba | Numba JIT 编译后端，**速度基准（baseline）** |
| `rs` | Rust | Rust 编译扩展（`vectorbtpro-rust` 包），分 `rs` 和 `rs_raw` |
| `rs_raw` | Rust raw | 用「原始候选计时」跑的 Rust 后端，去掉缓存/预处理开销后测得的最底层耗时 |
| `ab` | AutoBench | 运行时在 Serial/Parallel 模式下自动选择最快具体后端 |
| `abm` | AutoBenchMixed | 在「串行 + 并行具体内核」之间自动选择，报告在 `Mixed` 段 |
| `_serial` / `_parallel` | 执行模式 | 单线程串行 / 多线程并行 |
| `X_VS_Y` | 加速比 | `runtime(Y) / runtime(X)`，>1.00x 表示 X 更快 |

### 2. 核心数据（摘自 OVERVIEW.md）

**绝对运行时（median，不同输入规模）：**

| 规模 | nb | rs | rs_raw | 结论 |
|------|-----|-----|--------|------|
| 1 元素 | 374.86 ns | 1.71 us | **83.82 ns** | rs_raw 是 nb 的 ~4.5x |
| 100 元素 | 1.12 us | 2.29 us | **541.10 ns** | rs_raw 最快 |
| 1K 元素 | 4.90 us | 4.04 us | 2.15 us | 三档接近 |
| 10K 元素 | 29.83 us | 17.67 us | 16.00 us | rs/rs_raw 领先 |
| 100K 元素 | 159.15 us | 124.12 us | 116.29 us | rs/rs_raw 略快 |
| 1M 元素 | 1.85 ms | 1.40 ms | 1.45 ms | 大数组收敛，rs 略快 |

**加速比关键数字（rs_raw vs nb_serial）：**

- 1 元素组：median **1.98x**，max **296.26x**
- 100 元素组：median **1.75x**，max **102.01x**
- 1K 元素组：median **1.77x**，max **110.23x**
- 10K~1M：median 稳定在 **1.3~1.6x**，个别 kernel 可达 100x+

**串行 vs 并行（nb/rs 的 `_parallel_vs_serial`）：**

- 小数组（100~10K）：median 只有 **0.03x~0.58x**（并行反而更慢，线程调度开销大于收益）
- 大数组（100K~1M）：median 升到 **1.37x~1.82x**（并行才开始有意义）

### 3. 性能三档：什么时候用哪个

```
纯数组（NumPy 向量化）
  └─ 快、零编译、最简单 → 一次性计算、小数据、原型验证
Numba（nb，默认后端）
  └─ 更快 + 灵活性最高 → 自定义 kernel、闭包、参数网格、回测主力
Rust（rs / rs_raw，需另装 vectorbtpro-rust）
  └─ 最快、最底层 → 性能关键的生产路径、超大数组/高频调用
AutoBench（ab / abm）
  └─ 运行时自动选 → 「不想纠结」的通用默认
```

**决策规则：**

1. **写策略/研究 → 用 Numba（`nb`）**。它是默认后端，能 JIT 编译带闭包的 Python 函数，语法自由，还能 `@njit` 自定义 kernel；性能对绝大多数回测场景足够。
2. **追求极致吞吐 / 已经跑在性能瓶颈上 → 用 Rust（`rs_raw`）**。在 kernel 层面 rs_raw 可稳定快 1.3~2x，个别场景（如 tiny kernel、纯内存操作）可达 100x+。代价是需要 `pip install vectorbtpro[rust]` + `maturin` 编译，部署成本高。
3. **纯 NumPy 数组**适合「只算一次、不值得 JIT 预热」的小任务；VBT 内部大量 kernel 本身就是纯数组实现。
4. **AutoBench（`ab`/`abm`）** 会在每个调用里自动挑最快后端，是「让框架替你做决定」的安全选择，但会有少量选择开销（1 元素组 median 834ns，比 rs_raw 的 83ns 高一个量级）。
5. **并行只在数据量大（≥100K 元素）时开**；小数组开 `parallel` 反而拖慢，默认串行即可。
6. 官方教程对**指标实现**也给了类似三档规则：TA-Lib（C，最快）→ VBT 自带指标（Numba，快 + 能画图）→ 其他库（功能多）。

### 4. Benchmark 工具怎么用（复现/自测）

```bash
# 定向测：1 个输入模型 + 指定后端 + 过滤 case 名
python -m vectorbtpro.benchmarks.bench_engine_cli \
  --backend nb --backend rs --input-model 1d --pattern returns

# 全矩阵报告
python -m vectorbtpro.benchmarks.bench_matrix_cli --full
```

程序化接口：

```python
from vectorbtpro import *
results = vbt.run_benchmarks(input_model="1d", backend_ids=("nb", "rs"), patterns=["returns"])
```

> 注意：benchmark 数字对 CPU/OS/Python/NumPy/Numba/rustc 版本、是否 release 编译极其敏感，跨机器对比要带 environment 块。

---

## 二、可复用代码模式（6+）

### 模式 1：RSI 策略回测（官方标准配方）

**最小可运行代码：**

```python
from vectorbtpro import *

# 1. 下载数据（自动缓存）
data = vbt.YFData.pull("BTC-USD", start="2020-01-01", missing_index="drop")
close = data.get("Close")

# 2. 跑指标
rsi = vbt.RSI.run(close, window=14)

# 3. 生成 entry/exit 信号（用 crossed_below/above，而不是 > / <，避免连续重复信号）
entries = rsi.rsi_crossed_below(30)   # RSI 下穿 30 → 超卖买入
exits   = rsi.rsi_crossed_above(70)   # RSI 上穿 70 → 超买卖出

# 4. 清洗信号（去掉一个持仓周期内的重复 entry/exit）
clean_entries, clean_exits = entries.vbt.signals.clean(exits)

# 5. 回测
pf = vbt.Portfolio.from_signals(
    close,
    entries=clean_entries,
    exits=clean_exits,
    size=100,           # 每次买 100 美元
    size_type="value",  # 按金额而非股数
    init_cash="auto",   # 自动凑够保证金
    fees=0.001,         # 手续费
    freq="1D",
)

# 6. 看结果
print(pf.stats())
pf.plot().show()
```

**适用场景**：任何「指标 → 阈值信号 → 回测」的策略原型。这是官方 basic-rsi 教程的完整流水线，是最常用、最该背下来的配方。

**关键点**：
- 用 `crossed_below/above` 而不是 `rsi < 30`，因为 `rsi < 30` 会在超卖区间里连续为 True，产生一堆重复买入信号。
- `entries.vbt.signals.clean(exits)` 是官方推荐的「保留每个周期首个信号」做法。
- `init_cash="auto"` 让 VBT 自动分配足够资金，`size_type="value"` 按固定金额买入。

---

### 模式 2：自定义指标（IndicatorFactory + from_apply_func，SuperTrend 式）

SuperTrend 在源码里就是这么做出来的（`vectorbtpro/indicators/custom/supertrend.py`）：

```python
from vectorbtpro import *
from numba import njit

# 用 Numba 写纯计算 kernel（高性能、可广播）
@njit
def my_apply_func(i, ts, p):
    # ts: 输入数组，p: 参数
    return ts * p

# 用 IndicatorFactory 把 kernel 包装成指标类
MyInd = vbt.IndicatorFactory(
    class_name="MyInd",
    short_name="myind",
    input_names=["ts"],
    param_names=["p"],
    output_names=["out"],
).with_apply_func(
    my_apply_func,
    p=2,              # 默认参数
)

ind = MyInd.run(close, p=[1, 2, 3])  # 传列表 = 参数广播，一次算三组
print(ind.out)   # 输出带 p 参数层级的多列 DataFrame
```

官方 SuperTrend 的真实写法（可直接套用）：

```python
SUPERTREND = vbt.IndicatorFactory(
    class_name="SUPERTREND",
    short_name="supertrend",
    input_names=["high", "low", "close"],
    param_names=["period", "multiplier"],
    output_names=["trend", "direction", "long", "short"],
).with_apply_func(
    nb.supertrend_nb,
    period=7,
    multiplier=3,
)

# 用法：一次跑多个参数组合，输出自动带 (period, multiplier) 层级
st = vbt.SUPERTREND.run(high, low, close, period=[7, 10], multiplier=[3, 4])
entries = st.long      # 多头信号
exits = st.short       # 空头信号
pf = vbt.Portfolio.from_signals(close, entries=entries, exits=exits)
```

**适用场景**：任何内置指标没有、需要自己写的指标；希望指标能像 `vbt.RSI` 一样支持参数广播、自动生成 `xxx_crossed_below/above` 方法、可画图。

**关键点**：
- `with_apply_func` 要求函数签名是 `(i, *inputs, *params)`，返回一个数组。
- 传列表参数 = 自动参数广播（见模式 4）。
- 更高级的 `with_custom_func`（见模式 7）能完全控制输入形状。

---

### 模式 3：信号开发（entry/exit 信号 → 订单 → 回测）

从「裸信号」到「可回测订单」的完整链路：

```python
from vectorbtpro import *

# 产生裸信号
entries = (close > close.vbt.ma(20)) & (rsi.rsi < 40)
exits   = (close < close.vbt.ma(20)) | (rsi.rsi > 60)

# 信号清洗：一个持仓周期只保留首 entry / 首 exit
clean_entries, clean_exits = entries.vbt.signals.clean(exits)

# 信号统计
clean_entries.vbt.signals.total()                            # 信号总数
ranges = clean_entries.vbt.signals.between_ranges(target=clean_exits)
ranges.duration.mean(wrap_kwargs=dict(to_timedelta=True))    # 平均持仓时长

# 信号 → 订单 → 回测（from_signals 内部完成信号→订单的转换）
pf = vbt.Portfolio.from_signals(
    close,
    entries=clean_entries,
    exits=clean_exits,
    direction="longonly",        # 或 "both" / "shortonly"
    sl_stop=0.05,                # 止损 5%
    tp_stop=0.10,                # 止盈 10%
    upon_long_conflict="opposite",  # 冲突处理
    accumulate=False,            # 是否加仓
)

# 看订单与成交
print(pf.orders.records_readable)    # 订单明细
print(pf.trades.records_readable)    # 成交明细
```

**适用场景**：需要精细控制入场/出场逻辑、止损止盈、多空方向的策略。

**关键点**：
- `from_signals` 自动把信号转成订单；「信号生成」和「信号执行」分离是官方推荐的做法（可以分开调参数、分开画图）。
- 用 `sl_stop`/`tp_stop` 直接挂止损止盈；`upon_*_conflict` 控制持仓冲突时的行为。

---

### 模式 4：参数网格 + 广播优化（vbt.Param + @vbt.parameterized）

VBT 的高性能参数扫描核心是**广播**：把参数做成数组维度，一次跑完所有组合，而不是 for 循环。

```python
from vectorbtpro import *

# 写法 A：直接给指标/回测传列表参数，自动广播成多列
rsi = vbt.RSI.run(close, window=[7, 14, 21])   # 3 组，一次算完
pf = vbt.Portfolio.from_signals(
    close,
    entries=rsi.rsi_crossed_below(30),
    exits=rsi.rsi_crossed_above(70),
    freq="1D",
)
print(pf.total_return())   # 自动变成多列，每列一个参数组合
print(pf.stats(agg_func=None))  # 展平所有组合的统计

# 写法 B：@vbt.parameterized 装饰器，把函数变成参数扫描器
@vbt.parameterized(merge_func="column_stack")
def my_strategy(sr, window):
    r = vbt.RSI.run(sr, window=window).rsi
    return r.vbt.crossed_below(30)

res = my_strategy(close, vbt.Param([7, 14, 21]))  # vbt.Param 标记「这是一个参数维度」
```

带引擎的高级写法（多参数笛卡尔积 + 并行 + 分块）：

```python
@vbt.parameterized(chunk_len="auto", merge_func="column_stack", engine="threadpool")
def run_strategy(price, fast, slow, tsl):
    ma_fast = vbt.MA.run(price, window=fast).ma
    ma_slow = vbt.MA.run(price, window=slow).ma
    entries = ma_fast.vbt.crossed_above(ma_slow)
    exits = ma_fast.vbt.crossed_below(ma_slow)
    return vbt.Portfolio.from_signals(
        price, entries=entries, exits=exits,
        tsl_stop=tsl,  # 参数化止损
    )

pf = run_strategy(close, vbt.Param([5, 10]), vbt.Param([20, 30]), vbt.Param([0.01, 0.02]))
```

**适用场景**：任何需要扫描多组参数找最优组合的场景（调参、寻优）。

**关键点**：
- **多维数组 = 参数网格**：传 `[7, 14, 21]` 就是一次跑 3 组，多参数就是笛卡尔积，全部自动广播对齐。
- `vbt.Param(...)` 显式标记参数维度；`@vbt.parameterized` 是函数级封装。
- 大网格用 `engine="threadpool"` 并行、`chunk_len="auto"` 分块省内存。

---

### 模式 5：交叉验证 / walk-forward（vbt.cv_split，防过拟合）

官方 `@vbt.cv_split` 装饰器 = `split` + `parameterized` 的组合：训练集扫全网格，测试集只跑「训练集选出最优的那组参数」。

```python
from vectorbtpro import *

@vbt.cv_split(
    splitter="from_n_rolling",          # 滚动窗口切分（walk-forward）
    splitter_kwargs=dict(n=3, split=0.5),  # 3 个窗口，前 50% 训练 / 后 50% 测试
    takeable_args=["sr"],
    merge_func="concat",
)
def f(sr, window):
    r = vbt.RSI.run(sr, window=window).rsi
    entries = r.vbt.crossed_below(30)
    exits = r.vbt.crossed_above(70)
    pf = vbt.Portfolio.from_signals(sr, entries, exits)
    return pf.total_return()   # 训练集用这个指标选最优 window

# 每个 split 里：训练集扫 window 网格 → 挑最优 → 测试集用最优 window 跑一次
result = f(close, vbt.Param([7, 14, 21]))
# 返回带 (split, set) 层级的结果，set_0=训练，set_1=测试
```

返回完整网格（看每个参数在每个 split 的表现）：

```python
grid, selection = f(close, vbt.Param([7, 14, 21]), _return_grid="all")
```

底层等价写法（更可控）：

```python
# 用 Splitter 手动切分
splitter = vbt.Splitter.from_n_rolling(index, n=10, split=0.8)
for split_idx, (train_mask, test_mask) in enumerate(splitter):
    ...
```

**适用场景**：防止过拟合的参数寻优——训练集找参数、测试集验证，产出 OOS（样本外）绩效。

**关键点**：
- `selection="max"` 用 `np.nanargmax` 选最优参数（默认），也可用自定义模板。
- `from_n_rolling` = walk-forward 滚动窗口；`from_n_expanding` = 扩张窗口；`from_ranges` = 任意区间。
- 训练/测试集必须在同一线程/进程跑（内部用 `grid_results_map` 存取网格结果）。

---

### 模式 6：数据下载 + 缓存（YFData / BinanceData）

```python
from vectorbtpro import *

# 单个标的：pull 会本地缓存，二次调用直接读缓存
data = vbt.YFData.pull("BTC-USD", start="2020-01-01", missing_index="drop")
close = data.get("Close")

# 多标的：一次拉多个，自动对齐
data = vbt.YFData.pull(["BTC-USD", "ETH-USD", "SOL-USD"], missing_index="drop")
close = data.get("Close")   # 返回多列 DataFrame

# Binance 加密数据（教程默认示例）
data = vbt.BinanceData.pull("BTCUSDT")
open_price = data.get("Open")

# 显式 update 刷新缓存
vbt.YFData.update("BTC-USD")

# 存到本地文件（HDF5 等），跨进程复用
data.save("my_data")          # 存
data = vbt.Data.load("my_data")  # 读
```

**适用场景**：任何策略的第一步——取数。`pull`/`download` 都带本地缓存，重复运行不会反复请求 API。

**关键点**：
- `vbt.YFData`（yfinance）、`vbt.BinanceData`、`vbt.CCXTData`、`vbt.HDFData` 等统一走 `Data` 基类的 `pull/download/update/save/load` 接口。
- `missing_index="drop"` 处理多标的日期错位。
- `data.get("Close")` / `data.close` 都能取到 OHLCV 字段。

---

### 模式 7：高级自定义指标（with_custom_func，完全掌控输入形状）

需要自己处理二维数组（多列多参数）时，用 `with_custom_func` 而不是 `with_apply_func`：

```python
from vectorbtpro import *
from numba import njit

# 自定义计算函数：i 是参数索引，ts 是输入
def apply_func(i, ts, p, a, b=10):
    return ts * p[i] + a + b

@njit
def custom_func(ts, p, *args):
    # 用 vbt 的 apply_and_concat 自动广播/拼接所有参数组合
    return vbt.base.combining.apply_and_concat_one_nb(
        len(p), apply_func, ts, p, *args
    )

MyInd = vbt.IndicatorFactory(
    input_names=["ts"],
    param_names=["p"],
    output_names=["out"],
).with_custom_func(custom_func)

ind = MyInd.run(close, [1, 2, 3], a=5, b=100)
```

**适用场景**：指标逻辑涉及复杂输入形状、逐列处理、需要精细控制广播与拼接方式的高级场景。

**关键点**：
- `with_apply_func` 适合「一列一列算」；`with_custom_func` 适合「自己控制整张二维数组」。
- 用 `vbt.base.combining.apply_and_concat_one_nb` 把逐参数调用合并成一个 Numba kernel，避免 Python for 循环。
- `param_product=True` 可开启所有参数的笛卡尔积。

---

### 模式 8：结果分析（stats / returns accessor / 信号可视化）

```python
from vectorbtpro import *

pf = vbt.Portfolio.from_signals(close, entries, exits)

# 一键统计
pf.stats()                       # 全量绩效指标（年化、夏普、回撤…）
pf.stats(agg_func=None)          # 参数网格时展平
pf.total_return()
pf.sharpe_ratio()
pf.max_drawdown()

# 收益 / 回撤
pf.returns()                     # 收益序列
pf.daily_returns()
pf.get_drawdowns()               # 回撤对象

# 信号可视化
fig = rsi.plot()
entries.vbt.signals.plot_as_entries(rsi.rsi, fig=fig)
exits.vbt.signals.plot_as_exits(rsi.rsi, fig=fig)
fig.show()
```

**适用场景**：拿到回测结果后的绩效评估与可视化。

**关键点**：`pf.stats()` 是最常用的「一行看全部」；参数网格回测时加 `agg_func=None` 或 `agg_func=...` 才能看每个组合的指标。

---

## 三、官方推荐的标准回测流程总结

官方教程（basic-rsi）呈现的「标准配方」是一条固定的流水线，可直接套用到任何策略：

```
1. 取数（缓存）
   vbt.YFData.pull("BTC-USD") / vbt.BinanceData.pull("BTCUSDT")
   → data.get("Close")  取需要的字段

2. 算指标
   vbt.RSI.run(close, window=14)   → rsi.rsi
   （指标选择规则：TA-Lib 最快 > VBT 内置 Numba 快+可画图 > 其他库功能多）

3. 生成信号
   entries = rsi.rsi_crossed_below(30)
   exits   = rsi.rsi_crossed_above(70)
   （用 crossed_below/above，不用裸比较，避免连续重复信号）

4. 清洗信号
   clean_entries, clean_exits = entries.vbt.signals.clean(exits)
   （一个持仓周期只保留首 entry / 首 exit）

5. 回测建模
   pf = vbt.Portfolio.from_signals(close, entries=clean_entries, exits=clean_exits,
                                   size=100, size_type="value", init_cash="auto")

6. 评估 + 可视化
   pf.stats() / pf.plot().show()

7. 进阶（可选）：
   - 参数网格：传列表参数 / vbt.Param + @vbt.parameterized → 一次跑全部组合
   - 防过拟合：@vbt.cv_split → walk-forward 交叉验证，样本外绩效
   - 性能：默认 Numba；瓶颈时切 Rust 后端（vectorbtpro-rust）
```

**两条贯穿始终的 VBT 核心哲学：**

1. **广播 = 高性能参数扫描**。传列表/数组参数 → 自动多列广播 → 一次跑完所有参数组合，替代手写 for 循环。这是 VBT 快于普通回测框架的根本原因。
2. **信号与执行分离**。先生成 entry/exit 信号（可独立统计、画图、清洗），再交给 `Portfolio.from_signals` 转订单回测。信号是纯布尔数组，天然支持广播与复用。

**性能三档总览（什么时候升级）：**

| 场景 | 后端选择 |
|------|---------|
| 原型、研究、自定义 kernel | Numba（默认 `nb`，灵活 + 够快） |
| 只算一次的小任务 | 纯 NumPy 数组 |
| 性能瓶颈、生产路径 | Rust（`rs_raw`，稳定快 1.3~2x，个别 100x+） |
| 不想纠结、让框架决定 | AutoBench（`ab` / `abm`） |
| 数据量 ≥100K 元素 | 才开 `_parallel` 并行 |
