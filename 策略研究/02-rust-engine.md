# VectorBT PRO — Rust 高性能引擎研究

> 研究对象：`vectorbt.pro-main/rust/`（PyO3 扩展 `vectorbtpro-rust`）
> 版本：2026.6.27（与主 Python 包强绑定）

## 一、Rust crate 总览

### 1.1 定位

`vectorbtpro-rust` 不是独立库，而是 VectorBT PRO 的**编译型后端**。它是一个 PyO3 扩展模块（`crate-type = ["cdylib"]`），被单独打成 Python 包（`pip install vectorbtpro-rust` 或 `pip install "vectorbtpro[rust]"`），通过 VBT 的 **jitting registry（JIT 注册表）** 挂回到 Python 层的 Numba 函数上。

核心设计哲学（README 原文提炼）：

> Rust **镜像（mirror）** 选定的 Numba 函数，Numba 作为 Rust 的**行为基准（behavioral oracle）**和兜底实现。Rust 不替换 Python/Numba 层，而是「同一逻辑任务」的另一种后端实现。

- 版本严格匹配：`vectorbtpro_rust.__version__ == 主包版本` 才视为可用（`vbt.is_rust_available()`）。
- 目录结构**逐模块镜像** Python 包：`rust/src/portfolio/` ↔ `vectorbtpro/portfolio/`，`rust/src/generic/` ↔ `vectorbtpro/generic/` 等。
- 每个函数命名带 `_rs` 后缀，与 Python 的 `_nb` 后缀函数一一对应（如 `sharpe_ratio_1d_nb` ↔ `sharpe_ratio_1d_rs`）。

### 1.2 依赖（Cargo.toml）

| 依赖 | 版本 | 用途 |
|------|------|------|
| `pyo3` | 0.29（`extension-module`） | Python 桥接、`#[pyfunction]`、GIL 管理 |
| `numpy` | 0.29 | 零拷贝 NumPy 数组桥接（`PyReadonlyArray` 等） |
| `ndarray` | 0.16 | 纯 Rust 端多维数组（View/Array） |
| `nalgebra` | 0.35（仅 `std`） | 仅用于 `generic/base.rs` 的 polyfit（SVD 求解） |
| `rand` + `rand_chacha` | 0.10 | 随机信号生成、回测随机性（ChaCha8Rng） |
| `rayon` | 1.12 | 数据并行（按列/按 chunk 并行） |

### 1.3 编译与安装（maturin）

`rust/pyproject.toml` 使用 **maturin** 作为 build backend：

```toml
[build-system]
requires = ["maturin>=1.7,<2.0"]
build-backend = "maturin"

[tool.maturin]
features = ["pyo3/extension-module"]
module-name = "vectorbtpro_rust"
profile = "release"
locked = true
```

本地构建方式（README「Building」）：

```bash
python -m maturin develop --manifest-path rust/Cargo.toml            # debug
python -m maturin develop --manifest-path rust/Cargo.toml --release  # 生产/基准
python -m maturin build   --manifest-path rust/Cargo.toml --release  # 只出 wheel 不装
```

**Release 优化配置**（`[profile.release]`）：
- `lto = true`（链接期优化，跨 crate 内联）
- `codegen-units = 1`（最大化单模块内联，代价是编译慢）
- `strip = true`（剥离符号，减体积）

### 1.4 模块注册结构（lib.rs）

`lib.rs` 定义顶层 `#[pymodule] fn vectorbtpro_rust`，注册 11 个子模块：`utils`、`base`、`data`、`generic`、`indicators`、`ohlcv`、`signals`、`labels`、`records`、`returns`、`portfolio`。每个子模块的 `register()` 用 `m.add_submodule` + `sys.modules.set_item` 挂载。深层子模块同理（如 `portfolio.from_signals`）。

### 1.5 代码规模

全 crate **89,204 行**（`wc -l`），其中 **728 个 `#[pyfunction]`**（全部 `_rs` 后缀）、**894 处 `py.detach`**（释放 GIL）、**26 个文件使用 rayon**、**586 处 `unsafe`**。

按行数排序（KB 与任务描述一致）：

| 模块 | 行数 | 说明 |
|------|------|------|
| `returns/mod.rs` | 10,377 | 收益/风险指标（sharpe/sortino/alpha/beta/drawdown…）|
| `portfolio/from_signals.rs` | 9,616 | **信号回测主循环（最大单文件回测引擎）** |
| `signals/mod.rs` | 6,862 | 信号生成（entry/exit 随机、stop、rank…）|
| `indicators/mod.rs` | 6,807 | 技术指标（MA/RSI/MACD/BBANDS/ATR/ADX…）|
| `portfolio/records.rs` | 6,347 | 订单/成交/日志记录处理 |
| `generic/base.rs` | 6,268 | 通用数组算法（select、repartition、polyfit…）|
| `portfolio/analysis.rs` | 6,030 | 组合分析指标（value/exposure/pnl/returns…）|
| `portfolio/core.rs` | 5,760 | 回测核心状态机（订单执行、止损检查）|
| `generic/rolling.rs` | 4,052 | 滚动窗口引擎（sum/mean/std/ewm/rank…）|
| `generic/records.rs` | 3,741 | 记录型滚动（pattern、sim range）|
| `labels/mod.rs` | 2,414 | 标签生成（趋势/突破标签）|
| `records/mod.rs` | 2,013 | 记录容器引擎（generate_ids、unstack…）|
| `portfolio/enums.rs` | 1,966 | 镜像 Numba 枚举 + 记录结构体 |

---

## 二、各模块职责

### 2.1 `utils/`（内部工具，不对外暴露全部）

| 文件 | 职责 |
|------|------|
| `dtype.rs` | **dtype 分发核心**：`NumericDType` 枚举（bool/i8…u64/f32/f64 共 11 种）+ `dispatch_numeric_input!`/`dispatch_numeric_output!` 宏 + `NumericCast`/`NumericAccumulator` 等 trait |
| `array.rs` | `apply_cols_2d` 等「按列套函数」工具、`copy_array_view`、argsort 辅助 |
| `datetime.rs` | 时间戳/频率/时区转换（1,393 行） |
| `math.rs` | 通用数学（nan-safe 运算、插值等） |
| `checks.rs` | 参数校验（数组形状、dtype 断言） |
| `random.rs` | `resolve_seed`/`split_seed` 种子管理（ChaCha8Rng） |

**关键设计 — dtype 单态化（monomorphization）**：NumPy 数组的运行时 dtype 先映射为 `NumericDType` 枚举，再通过宏生成 11 路 `match` 分派到具体 Rust 泛型类型（`$body!($arr, f64)` 等），等价于 Numba 的 JIT 类型特化，但发生在**编译期**。

### 2.2 `base/`（核心抽象层）

| 文件 | 职责 |
|------|------|
| `flex_indexing.rs` | **FlexArray1dView / FlexArray2dView** —— 贯穿全 crate 的核心抽象（详见 §四） |
| `indexing.rs` | `normalize_index`（负索引/Python 语义）、`normalize_lens` |
| `grouping.rs` | `prepare_group_map` —— 分组/列映射准备 |
| `indexes.rs` | 索引（index/freq）处理 |
| `resampling.rs` | 重采样辅助（as_i64_array 等） |
| `reshaping.rs` | reshape 工具 |

### 2.3 `generic/`（通用计算引擎，复用关键）

| 文件 | 职责 |
|------|------|
| `base.rs` | 通用数组算法：`select_indices_1d`、`repartition`（按 counts 重排）、`polyfit`（**唯一用 nalgebra SVD 的地方**）、`value_count_index`、nan 系列函数 |
| `rolling.rs` | **滚动窗口引擎**：rolling/expanding 的 sum/prod/mean/std/zscore、`ewm_mean`/`ewm_std`（指数加权）、`vidya`、MA、`rolling_rank`、`rolling_min/max`、`rolling_ols`、`rolling_corr/cov`、`rolling_pattern_similarity` 等 |
| `apply_reduce.rs` | **分组 reduce 引擎**：flatten_grouped、nth/first/last/min/max/mean/median/std/sum/prod/count/argmin/argmax、`describe_reduce`、`cov_reduce_grouped_meta`、`corr_reduce_grouped_meta`、`wmean_range_reduce` |
| `records.rs` | 记录型滚动（pattern 记录、sim range 边界解析） |
| `sim_range.rs` | 模拟范围（sim_start/sim_end）计算 |
| `splitting.rs` | 按分组切分 |
| `patterns.rs` | 模式相似度（`pattern_similarity`，供 rolling 用） |
| `iter_.rs` | 迭代辅助 |
| `enums.rs` | 镜像枚举（DistanceMeasure、ErrorType、InterpMode 等） |

### 2.4 `signals/`（信号生成，6,862 行）

- 随机信号：`generate_rand_*`（entry/exit/enex、按概率、交叉 cross）
- 止损信号：`generate_stop_ex`/`generate_stop_enex`/`generate_ohlc_stop_*`
- 信号位置/排名：`rank_sig_pos`、`rank_part_pos`、`distance_from_last`
- 清理与关系：`clean_enex`、`relation_idxs`、`between_ranges`、`partition_ranges`
- ravel/unravel：`ravel`/`unravel`/`unravel_between`（信号展平/还原）
- 聚合索引：`nth_index`、`norm_avg_index`（+ grouped）

### 2.5 `indicators/`（技术指标，6,807 行）

镜像 `vectorbtpro.indicators.nb`：MA/MSD、BBANDS（布林带/percent_b/bandwidth）、RSI（avg_gain/avg_loss）、Stochastic（stoch_k/stoch）、MACD（macd/macd_hist）、TR/ATR/ADX、OBV、OLS（ols/ols_pred/error/angle）、typical_price/VWAP、pivots（pivot_info/value/pivots）等。几乎每个都有 `*_1d` 与 `*`（2D 广播）两个变体。

### 2.6 `labels/`（标签生成，2,414 行）

future 统计标签（future_mean/std/min/max）、fixed_labels、mean_labels、趋势标签（bin/binc/bincs/pct/trend）、breakout_labels（突破）、pivots。

### 2.7 `ohlcv/`（780 行）

OHLC 数据重采样：`ohlc_every_1d`、`mirror_ohlc_1d`/`mirror_ohlc`（镜像 OHLC 拼接）。

### 2.8 `records/`（记录容器引擎，2,013 行）

记录型数据（订单/成交/信号）的容器操作：`generate_ids`（**并行版 + 串行版**）、`col_lens`/`col_map`、`is_col_sorted`/`is_col_id_sorted`、`first_n`/`last_n`/`random_n`、`top_n_mapped`/`bottom_n_mapped`、`mapped_value_counts_*`、`mapped_has_conflicts`、`mapped_coverage_map`、`unstack_mapped`/`repeat_unstack_mapped`/`unstack_index`。

**性能点**：用 `Array2::uninit`（`MaybeUninit`）预分配记录数组避免零初始化，配合 `repartition_uninit` 用 `unsafe { Vec::from_raw_parts }` 做**零拷贝**重排（见 §四）。

### 2.9 `returns/`（最大模块，10,377 行）

收益与风险指标全家族，每个函数基本都有 `_1d`/`_rs` 成对、以及 `rolling_*` 滚动变体：

- 基础：`get_return`、`returns`、`mirror_returns`、`cumulative_returns`、`final_value`、`total_return`
- 年化：`annualized_return`、`annualized_volatility`、`deannualized_return`
- 回撤：`max_drawdown`、`rolling_max_drawdown`
- 比率：`sharpe_ratio`（**README 的示例函数**，含 `rolling_sharpe_ratio_stream` 流式版）、`sortino_ratio`、`calmar_ratio`、`omega_ratio`、`information_ratio`、`downside_risk`
- 回归类：`alpha`/`beta`（含 rolling）

### 2.10 `portfolio/`（回测引擎核心，33,226 行，最大模块）

| 文件 | 职责 |
|------|------|
| `enums.rs` | **镜像 Numba 枚举**（PriceType、Direction、OrderType、SizeType、ConflictMode、LeverageMode…约 50 个 `i64` 常量 struct）+ 记录结构体（Order/OrderRecord/TradeRecord/LogRecord/FSOrderRecord）+ `RawOrderRecords`/`RawTradeRecords`（**零拷贝原始数组视图**）+ `FieldOffsets`（字段偏移） |
| `core.rs` | **回测状态机核心**：`execute_order`、`process_order_with_record_writer`、`check_stop_hit`/`check_limit_hit`/`check_td_stop_hit`/`check_tsl_th_hit`、`resolve_size`、`resolve_*_price`、`prepare_last_*` 系列 |
| `from_signals.rs` | **信号→组合主循环**（9,616 行，见 §三） |
| `from_orders.rs` | 订单→组合（按订单列表模拟，2,128 行） |
| `records.rs` | 订单/成交/日志记录的后处理（6,347 行） |
| `analysis.rs` | 组合分析：assets/position_mask/coverage/cash_flow/init_cash/value/exposure/allocations/total_profit/asset_pnl/market_value…（6,030 行） |
| `pfopt.rs` | 组合优化：`get_alloc_points`、`prepare_alloc_points`、`prepare_alloc_ranges`、`rescale_allocations`、`pick_idx/point/random_allocate_func`（981 行） |
| `call_seq.rs` | 调用序列（call sequence）调度（253 行） |
| `preparing.rs` | 参数预处理（77 行） |

---

## 三、为什么 `from_signals.rs` 这么大（回测核心循环）

`from_signals_rs`（入口，8931 行）签名有 **约 85 个参数**，几乎每个参数都是 `Option<PyReadonlyArrayDyn>`（flex 数组）。入口做的事：

1. 把每个 Python 输入转换成 `FlexArray2dView` / `FlexArray1dView`（`optional_flex_2d_view(close, f64::NAN)` 等），支持「标量 / 1D / 2D」三种形态。
2. 准备 `FSLastState`（每列运行状态：cash/position/debt/free_cash/val_price/value/pos_info + 各类 stop 信息）。
3. 根据 `call_seq`/订单类型，进入**不同的专用行循环**。

代码庞大的根因——**大量高度特化的循环副本**：

- 按订单类型分：市价单 vs 限价单 vs 止损单（limit/stop/tsl/tp/td/dt）
- 按执行模型分：`from_basic_signals`（简化路径）vs `from_signals`（完整路径）
- 按调用序列分：`auto_call_seq` 生成不同的顺序
- 按并行性分：串行版 + rayon 并行版（按 `ranges` 列分组并行）
- 每列循环内部还嵌套了 `skip_empty` 快速路径、`ffill_val_price` 快速路径、`standard_2d_slice` 快速路径

**主循环内部结构**（2199 行起，已读源码确认）：

```rust
for i in sim_start_i..sim_end_i {          // 行循环（时间步）
    // 1. cash deposit 处理
    // 2. 读 4 个信号布尔，位打包：
    last_signal[ci] = ((is_long_entry as i64) << 4)
                    | ((is_long_exit as i64) << 3)
                    | ((is_short_entry as i64) << 2)
                    | ((is_short_exit as i64) << 1);
    // 3. skip 快速路径：无信号且 val_price 跟踪 close 时，只做 mark-to-market + 计算收益，直接 continue
    // 4. 完整路径：逐列执行订单/止损状态机（execute_order、check_stop_hit 等）
}
```

关键设计点：
- **位打包信号**：4 个信号布尔压进一个 `i64`（`<<4|<<3|<<2|<<1`），一次比较即可判断「有无信号」。
- **SoA 运行状态**：`last_cash`/`last_position`/`last_val_price`/`last_signal` 都是按列 `Vec<f64>`/`Vec<i64>`，热循环内无堆分配。
- **三级快速路径**：
  1. `standard_2d_slice()` 检测数组是标准 layout 完整 2D → 拿到 `&[f64]` 切片，用 `unsafe { *slice.get_unchecked(flat_idx) }` 直接索引（绕过 flex 索引开销）。
  2. `scalar_flex_2d` / `track_cash_deposits` 等检测标量/常量 → 消除分支。
  3. `skip_empty` 无信号快速通道 → 跳过整个状态机。

---

## 四、Numba vs Rust 双引擎分工

### 4.1 分工模型

| 维度 | Numba（`_nb`） | Rust（`_rs`） |
|------|----------------|---------------|
| 角色 | **行为基准（oracle）+ 兜底实现** | **首选编译后端**（性能关键内核） |
| 编译 | JIT（首次调用编译，`@register_jitted` + cache） | AOT（maturin 编译 cdylib） |
| 调度 | `jitted="nb"` 直接解析 dispatcher | 通过 `RustBackendSpec` 挂到 Numba 任务上 |
| 触发 | Rust 不可用/不支持 dtype/不支持 parallel 时 fallback | 版本匹配 + 参数规格校验通过后自动优先 |
| 数据形态 | NumPy 数组 | 同样的 NumPy 数组（零拷贝桥接），内部 ndarray |

**核心机制 — jitting registry**：一个逻辑任务（task id = Numba 函数规范化名，如 `vectorbtpro.returns.nb.sharpe_ratio_1d_nb`）有多个实现（`nb`、`rs`）。`RustBackendSpec` 把 Rust 目标挂到 Numba 实现上：

```python
@vbt.register_jitted(
    backends=vbt.RustBackendSpec("vectorbtpro_rust.returns.sharpe_ratio_1d_rs"),
)
def sharpe_ratio_1d_nb(returns, ann_factor, ddof=0): ...
```

### 4.2 调度策略（三种模式）

1. **auto-prefer（默认）**：`auto_mode=True → "prefer"`，支持 Rust 的调用**自动优先 Rust**，失败则静默 fallback Numba。每次调度的稳态开销约几微秒。
2. **explicit**：`jitted="rs"` 严格模式，Rust 不可用/版本不匹配/不支持具体调用则**直接抛错**，不回退。
3. **AutoBench / AutoBenchMixed**：`jitted="auto_bench"`/`"ab"`/`"abm"`，读取 `benchmarks/cache.json` 基准记录，**用实测性能证据选后端**（串行 Rust / 并行 Rust / 并行 Numba 竞争）。

### 4.3 Rust 参与哪些计算

从注册表看，Rust 下沉了几乎全部**数组化数值内核**：
- `returns`（全部收益/风险指标）
- `generic.rolling` / `generic.apply_reduce`（滚动窗口、分组 reduce）
- `signals`（信号生成/清理）
- `indicators`（技术指标）
- `labels`（标签）
- `portfolio`（回测、记录处理、组合分析、pfopt）
- `records`（记录容器）
- `data`（合成数据生成）
- `ohlcv`（重采样）

Rust 明确**不覆盖**的：`float16`、复数 dtype；需 `supports_custom_*_globals=True` 才支持自定义 flex/returns/dtype 全局设置（默认假设默认全局，否则 auto 回退 Numba）。

---

## 五、关键性能设计总结

### 1. Flex Indexing（标量/1D/2D 广播零拷贝）

`base/flex_indexing.rs` 的 `FlexArray1dView` / `FlexArray2dView` 是贯穿全 crate 的核心抽象：

```rust
enum FlexArray2dView<'a, T> {
    Scalar(T),                                  // 标量 → 广播到所有 (i,col)
    OneDim { data: ArrayView1<T>, one_dim_by_col: bool },  // 1D → 按列广播
    TwoDim(ArrayView2<T>),                      // 2D → 完整矩阵
}
```

- 一个内核同时吃标量/1D/2D 输入，**不复制**（纯视图），在热循环内用 `get(i, col, rotate_rows, rotate_cols)` 解析逻辑下标。
- 访问器全部 `#[inline(always)]` + `unsafe { *data.uget(..) }`（未检查 get）消除边界检查。
- `choose_flex_idx`：`len==1 → 0`（广播）、`rotate → idx % len`（旋转），就是 NumPy 广播语义的 branch-free 版。
- `standard_2d_slice`：检测标准 layout 完整 2D 时返回 `&[T]` 切片，供热循环走 `get_unchecked` 最快路径。

### 2. dtype 单态化分发（编译期多态）

`utils/dtype.rs`：运行时 NumPy dtype → `NumericDType` 枚举（11 种）→ `dispatch_numeric_input!` 宏生成 11 路 match 到具体 Rust 类型。等价 Numba 的 `@njit` 类型特化，但无 JIT 编译成本、无 Python 对象开销。

### 3. GIL 释放 + Rayon 列并行

- 894 处 `py.detach(|| native_fn(...))`：PyO3 wrapper 先把 Python 参数转成 Rust 原生类型，**释放 GIL**，再跑纯 Rust 计算，结束后重新拿 GIL 返回 NumPy 数组。
- 并行策略是**按列并行**（`(0..n_cols).into_par_iter()`），或**按列分组 chunk 并行**（`generate_ids_parallel` 用 `par_chunks`）。`n_cols <= 1` 时自动退回串行，避免线程开销。
- 约定：`native`（串行）+ `native_parallel`（Rayon）+ `#[pyfunction] *_rs(..., parallel=false)` 三层结构，wrapper 按 `parallel` 参数路由。

### 4. 零拷贝原始结构化数组访问（RawOrderRecords）

`portfolio/enums.rs` 的 `RawOrderRecords` / `RawTradeRecords`：

- 从 NumPy 结构化数组 dtype 的 `fields` 解析**每个字段的字节偏移**（`record_field_offset`），拿到 `PyArrayObject` 原始 data 指针 + stride。
- 用 `unsafe { std::ptr::read_unaligned(base.add(offsets.size) as *const f64) }` **直接从字节流读字段**，完全绕过 Python namedtuple 物化，每条记录零分配。

### 5. MaybeUninit + 零拷贝重排（记录数组）

`records/mod.rs`：

- `Array2::uninit(shape)`（`MaybeUninit<T>`）预分配记录数组，**跳过零初始化**（回测会写入每个槽位）。
- `repartition_uninit`：调用 `generic::base::repartition` 后，用 `std::mem::forget` + `unsafe { Vec::from_raw_parts }` 把 `MaybeUninit<T>` 重解释为 `T`，实现记录数组的**零拷贝重排**。

### 6. 其他微观优化

- **位打包信号**（`from_signals`）：4 个布尔信号压进一个 i64。
- **skip_empty / ffill_val_price 快速通道**：无信号时跳过状态机，只做 mark-to-market。
- **`#[inline(always)]` 遍布**：`py_min`/`py_max`/`choose_flex_idx`/`get` 等热函数全部强制内联。
- **release profile**：`lto=true + codegen-units=1` 使跨 crate 内联最大化。
- **种子管理**：`ChaCha8Rng`（rand_chacha）+ `resolve_seed`/`split_seed` 保证随机可复现且可并行拆分。

---

## 六、一句话结论

VectorBT PRO 的 Rust 后端是「**Numba 函数的编译期镜像**」：通过 jitting registry 用 `RustBackendSpec` 把 `*_rs` 挂到 `*_nb` 上，auto-prefer 默认优先 Rust、失败回退 Numba。性能来自四个支柱——**Flex 索引零拷贝广播**、**dtype 编译期单态化**、**GIL 释放 + 按列 Rayon 并行**、**结构化数组零拷贝原始访问**（外加 MaybeUninit 记录数组、位打包信号、多级快速路径、LTO）。`from_signals.rs` 之所以 9,616 行，是因为它把「市价/限价/止损 × 简化/完整 × 串行/并行 × 多种快速路径」的特化循环全部展开成了独立代码路径。
