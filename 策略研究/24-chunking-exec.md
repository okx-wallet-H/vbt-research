# VBT 分块（chunking）与并行执行引擎（exec）深度研究

> 研究对象：`vectorbtpro/chunking/core.py`（2885 行）、`vectorbtpro/chunking/registry.py`（420 行）、`vectorbtpro/base/chunking.py`、`vectorbtpro/utils/execution.py`（3500 行）
> 实测环境：Windows 11 x64 / Python 3.12.10 / vectorbtpro 2026.6.27（editable，源码在 `data/develop-src/`）/ **24 核 CPU**
> 所有耗时数据均为本机实测（`bench_chunk_exec.py` / `bench_chunk_diag.py` / `bench_processpool.py`，已落盘项目根目录）。

---

## 〇、先纠正命名（任务里的名字 vs 真实 API）

任务清单里的 `vbt.exec`、`vbt.Exec`、`@vbt.executed` 都**不存在**。实测 `hasattr(vbt, ...)` 全为 False。真实 API：

| 任务里的名字 | 真实 API | 说明 |
|--------------|----------|------|
| `vbt.exec` | **`vbt.execute`** | 顶层函数，执行一批 Task |
| `vbt.Exec` | **`vbt.Executor`** | 执行器类（引擎 + 分块 + 合并的编排者） |
| `@vbt.executed` | **`@vbt.iterated`** | 装饰器，按某个参数迭代执行一个函数 |
| `vbt.chunked` | **`@vbt.chunked`** ✅ | 装饰器，把函数输入切块处理 |
| `vbt.Chunked` | **`vbt.Chunked`** ✅ | 基类，另有 `ChunkedArray`/`ChunkedCount`/`ChunkedShape`/`ChunkedFlexArray`/`ChunkedGroupLens`/`ChunkedGroupMap` 子类 |
| `chunk_len`/`chunked` 参数 | ✅ 存在 | 但 `chunked` 是 bool/str/dict 选项（见 §二·6），`chunk_len` 是切块长度 |

另外，「numba 并行 / pathos / dask / mpire」里的 **numba 并行其实是另一根轴**：那是 jitting 层的 `parallel=True`（`prange`，即 `_parallel` 后端，见 02/05 号研究），跟本文讲的**执行引擎**（任务调度层）不是一回事。本文只讲执行引擎这一层。

---

## 一、分块机制（`@vbt.chunked` / `Chunked` / `chunk_len`）

### 1.1 整体流程：`Chunker.run` 四步

`@vbt.chunked` 装饰器内部就是 `Chunker.run(func, *args)`，固定走 4 步（源码 `chunking/core.py:2201`）：

```
1. 生成 chunk 元数据 ChunkMeta（idx/start/end/indices）
2. 按 arg_take_spec 把每个参数切成块，产出一批 Task
3. execute(tasks, engine=..., ...) 交给执行引擎跑
4. merge_func（concat/column_stack/自定义）把块结果拼回整体
```

`ChunkMeta` 只有 4 个字段：`uuid`（块缓存用）、`idx`（块序号）、`start`/`end`（切片区间）、`indices`（显式索引列表，优先级高于 start/end）。分块边界由 `iter_chunk_meta` 统一生成。

### 1.2 分块边界规则（`iter_chunk_meta`，源码 362 行）

```python
list(vbt.iter_chunk_meta(size=10, n_chunks=3))      # [(0,4),(4,7),(7,10)]  divmod 均分，余数摊到前面
list(vbt.iter_chunk_meta(size=10, chunk_len=4))     # [(0,4),(4,8),(8,10)]  按步长切，末块收尾
```

- `n_chunks` 与 `chunk_len` **互斥**，只能给一个；都给抛 ValueError。
- `n_chunks="auto"` / `chunk_len="auto"` → `multiprocessing.cpu_count()`（本机 24）。
- `min_size`：size 小于它时只返回一个整块（避免切太碎）。
- `chunk_len` 必须知道 `size`；`n_chunks` 可以在不知道 size 时只按个数切（每块 start/end 为 None）。

### 1.3 动态推导 size：Sizer 体系

`size` 可以写死，也可以从参数里动态推导（`Sizer`，源码 120 行起）：

| Sizer | 推导方式 | 适用 |
|-------|----------|------|
| `LenSizer(arg_query='a')` | `len(obj)` | 序列 |
| `CountSizer(arg_query='n')` | 直接取 int | 计数 |
| `ShapeSizer(axis=...)` | `obj.shape[axis]` | 形状元组 |
| `ArraySizer(axis=...)` | `np.asarray(obj).shape[axis]` | 数组（常用） |

`size`/`n_chunks`/`chunk_len` 都能传 `Sizer` 实例或 callable，由 `Chunker.get_chunk_meta_from_args` 在运行时 `apply(ann_args)` 求值。

### 1.4 怎么切每个参数：ChunkTaker 体系

`arg_take_spec` 是一个 dict，key 是参数名（或 int 位置 / Regex），value 是 `ChunkTaker`：

| Taker | 行为 |
|-------|------|
| `ChunkSlicer()` | 按 start:end 切片（默认） |
| `ChunkSelector()` | 按 idx 选单个元素（`select=True` 场景） |
| `ArraySlicer(axis=1)` / `ShapeSlicer(axis=1)` | 沿指定轴切 2D 数组/形状 |
| `FlexArraySlicer(axis=1, flex=...)` | 灵活数组（标量/1D/2D 广播）切片 |
| `ArgsTaker(...)` / `KwargsTaker(...)` | 切 `*args`/`**kwargs` 里的嵌套容器 |
| `SequenceTaker` / `MappingTaker` | 切 list / dict |

三种等价写法（文档示例，均已验证）：

```python
# 写法1：arg_take_spec 显式指定
@vbt.chunked(n_chunks=2, size=vbt.LenSizer(arg_query='a'),
             arg_take_spec=dict(a=vbt.ChunkSlicer()), merge_func="concat")
def f(a): return a

# 写法2：参数注解（type annotation）
@vbt.chunked(n_chunks=2)
def f(a: vbt.ChunkSlicer()) -> vbt.MergeFunc("concat"): return a

# 写法3：包装值（运行时动态指定）
@vbt.chunked(n_chunks=2, merge_func="concat")
def f(a): return a
f(vbt.ChunkedArray(np.arange(10)))
```

### 1.5 一致性验证（实测）

**结论：分块对「无状态运算」精确，对「有状态/滚动运算」会破坏跨块 carry-over。**

| 运算 | 分块 == 不分块 | 说明 |
|------|----------------|------|
| `np.mean`（4 块） | ✅ | 4 块均值 [12,37,62,87]，再平均 = 整体均值 49.5 |
| `np.abs`（8 块 concat） | ✅ 完全相等 | 逐元素无状态，天然可切 |
| `ffill`（8 块 concat） | ✅ 完全相等 | 前向填充只看本块内部历史 |
| **`rolling_mean`（8 块 concat）** | ❌ **不一致** | 滚动窗口跨块丢失（见下） |

**rolling 分块不一致的精确定位**（`bench_chunk_diag.py` DIAG 1）：1M 数组 `window=20` 分 4 块，整体 NaN 数 19（正常滚动预热），分块后 NaN 数 76 = 19 + 3×19。不一致位置**恰好是除第一块外每块开头 `window-1=19` 个**（250..268、500..518、750..768）。根因：每块各自从零开始滚动，块边界处没有把上一块末尾的滚动状态 carry-over 过来。

**这是分块最关键的边界**：`rolling_mean`/`rolling_std`/`ewm`/`cumsum`/`cumprod` 这类**跨位置有状态**的算子，不能简单用 `merge_func="concat"` 切块。要正确分块，得用 `chunk_meta` 手动做块间重叠（overlap）或由算子内部支持 carry-over（`records/chunking.py` 里就有针对记录数组的专门合并逻辑，见 §四）。

### 1.6 `chunked=` 选项参数（用户层）

`chunked` 是一个 **bool / str / dict 选项**，由 `resolve_chunked_option` 解析（源码 2753 行）：

- `False`/`None` → 不分块（禁用）
- `True` → 用默认 chunking 设置
- `"threadpool"` → 等价 `dict(engine="threadpool")`（**字符串就是引擎名**）
- `dict(...)` → 直接作为 `chunked` 的 kwargs

挂在 `Data.fetch`（`data/base.py:5325`）、`data/custom/gbm.py`、`data/custom/random.py`，以及 `generic/accessors.py` 大量方法（rolling/apply/resample 等）上，形如 `.vbt.rolling(..., chunked=True)`。全局默认在 `settings["chunking"]["option"]`（默认 `False`）。

---

## 二、并行执行引擎（`vbt.execute` / `vbt.Executor` / `@vbt.iterated`）

### 2.1 引擎清单（源码 `utils/execution.py`，7 个具体引擎 + 1 抽象基类）

| 引擎类 | 引擎名 | 底层实现 | 本机可用性 |
|--------|--------|----------|-----------|
| `SerialEngine` | `serial` | 纯 for 循环 | ✅ 默认 |
| `ThreadPoolEngine` | `threadpool` | `concurrent.futures.ThreadPoolExecutor` | ✅ |
| `ProcessPoolEngine` | `processpool` | `concurrent.futures.ProcessPoolExecutor` | ✅（但 Windows spawn 开销巨大，见 §三·3） |
| `PathosEngine` | `pathos` | pathos 的 ThreadPool/ProcessPool/ParallelPool | ❌ 未装 |
| `MpireEngine` | `mpire` | mpire.WorkerPool（use_dill） | ❌ 未装 |
| `DaskEngine` | `dask` | `dask.delayed` + `dask.compute` | ❌ 未装 |
| `RayEngine` | `ray` | `ray.remote` + 对象存储 | ❌ 未装 |

实测 `importlib.util.find_spec`：pathos / mpire / dask / distributed / ray / multiprocess / cloudpickle 全部 **MISSING**。**本机开箱可用的只有 stdlib 三件套 serial / threadpool / processpool**。

引擎名可映射到类（`settings["execution"]["engines"]`，`_settings.py:551`），`engine=` 传字符串名、`ExecutionEngine` 子类/实例、或任意 callable 都行。

### 2.2 三个接入口

```python
# 1) 函数级：一次跑一批 Task
results = vbt.execute(tasks, engine="threadpool", show_progress=False)

# 2) 装饰器：按某个参数迭代执行
@vbt.iterated(engine="threadpool")          # 真实名字，不是 @vbt.executed
def f(a, window): ...

# 3) 分块装饰器：切块后交给引擎
@vbt.chunked(n_chunks=8, engine="threadpool", merge_func="concat")
def g(a): ...
```

`Executor`（编排者）关键参数：`engine`/`engine_config`、`n_chunks`/`chunk_len`/`min_size`、`distribute="tasks"|"chunks"`、`warmup`、`merge_func`、`show_progress`。

### 2.3 `distribute` 两种分派模式（`Executor.run`，源码 2821 行）

- `distribute="tasks"`（默认）：**任务级**分发，每个 task 单独喂给引擎（`ThreadPoolExecutor.submit` 每个函数一次）。
- `distribute="chunks"`：**块级**分发，先把一个块内所有 task 压缩成一个 `build_serial_chunk`（用 id 去重，`execute_serially` 串行跑），再把这些「串行块」分发给引擎 —— 适合块内 task 很小、想减少 submit 次数/序列化体积的场景。

---

## 三、串行 vs 并行实测耗时（24 核 Windows）

### 3.1 threadpool 生效的规模拐点（24 个 numba `rolling_std` 任务）

| 每任务元素数 | serial | threadpool | 加速比 |
|--------------|--------|-----------|--------|
| 10,000 | 2.2 ms | 2.2 ms | **1.01x**（无收益） |
| 100,000 | 21.7 ms | 6.6 ms | **3.26x** |
| 500,000 | 102.7 ms | 18.4 ms | **5.60x** |
| 1,000,000 | 207.2 ms | 32.8 ms | **6.31x** |
| 2,000,000 | 411.4 ms | 62.8 ms | **6.55x**（饱和） |

**拐点在 ~100K 元素/任务**：10K 时线程创建/调度开销刚好抵消收益（1.01x），100K 起才有 3x+。加速比在 6.5x 附近**饱和**（不是 24x），因为 `rolling_std` 是**内存带宽敏感**而非纯 CPU 敏感。

### 3.2 任务数拐点（固定 1M 元素/任务）

| n_tasks | serial | threadpool | 加速比 |
|---------|--------|-----------|--------|
| 2 | 20.2 ms | 10.3 ms | 1.95x |
| 4 | 34.3 ms | 12.9 ms | 2.67x |
| 8 | 69.1 ms | 15.8 ms | 4.38x |
| 12 | 102.5 ms | 19.6 ms | 5.23x |
| 24 | 207.6 ms | 32.5 ms | 6.38x |

任务数越多加速越接近核心数上限，但仍是 ~6.5x 封顶（内存带宽墙）。

### 3.3 GIL 持重型 vs GIL 释放型

| 任务 | serial | threadpool | 结论 |
|------|--------|-----------|------|
| numba `rolling_std`（释放 GIL）24×2M | 406 ms | 63 ms | **6.41x**，线程真并行 |
| 纯 Python `sum`（持有 GIL）24×200K | 182 ms | 186 ms | **0.98x**，GIL 锁死无加速 |

**numba kernel 释放 GIL，所以 threadpool 能真正多核并行；纯 Python 循环持 GIL，threadpool 白搭。** 这是 Windows 上选 threadpool 的核心理由。

### 3.4 processpool 在 Windows 的实测（spawn 固定开销）

`concurrent.futures.ProcessPoolExecutor` 在 Windows 用 **spawn**，每个 worker 进程会**重新 import `__main__` 模块**（进而重导入整个 vectorbtpro，约 5 秒）。单次 `vbt.execute(engine="processpool")` 的墙钟时间：

| 任务 | 单次 processpool 耗时 | 对比 |
|------|----------------------|------|
| numba 2M ×4 | **5.35 s** | threadpool 3 次才 0.26 s（约 87 ms/次） |
| numba 10K ×4 | **5.15 s** | serial/threadpool 仅 3~4 ms |
| python sum 500K ×4 | **4.13 s** | threadpool 3 次 0.241 s |

**结论：本机 processpool 有 ~5 秒的 spawn 固定开销（每个 worker 重导入 vectorbtpro），中小任务完全不可用，只有单次任务跑几分钟以上才可能摊平。** 另外 spawn 语义要求所有模块级代码用 `if __name__ == "__main__"` 保护，否则每个 worker 都会重跑一遍（`bench_chunk_diag.py` 第一版就踩了这个坑，导致模块级打印在 8 个 worker 里洪水式重放）。

### 3.5 分块 + 并行叠加（8M 元素 `rolling_std` 分 8 块）

| 方式 | 耗时 |
|------|------|
| 不分块（单核） | 67.2 ms |
| 分 8 块 serial | 80.0 ms（分块调度有少量额外开销） |
| 分 8 块 threadpool | **29.5 ms → 对不分块 2.28x** |

分块 + threadpool 叠加有效（把一个大数组切成 8 块并行算再 concat），但注意 §一·5：rolling 算子分块会有边界 NaN 问题（这里只测了速度，数值上块边界不一致）。

---

## 四、结论：分块与并行的适用边界

### 4.1 并行（exec 引擎）什么时候值得

1. **任务数 ≥4 且每任务 ≥100K 元素**（numba GIL 释放型）时，`threadpool` 才划算；每任务 <50K 时线程开销吃掉收益（实测 10K=1.01x）。
2. **Windows 首选 `threadpool`**：numba kernel 释放 GIL，线程池即可真多核；24 核上实测最大 ~6.5x（内存带宽封顶，别指望线性 24x）。
3. **纯 Python CPU 密集（GIL 持有）任务**：threadpool 无效（0.98x），理论该用 processpool，但本机 processpool 有 5s spawn 固定开销，**中小任务别碰**；真要用请换 `pathos`/`mpire`（需额外 `pip install`，且 mpire 默认 `use_dill` 才能传闭包）。
4. **dask / ray 本机未装**；且都有 Windows 坑（ray 分布式模式 Windows 支持弱、需对象存储序列化，官方自己注释「计算量不够大别用」；dask 要用多进程调度需 `dask[distributed]`）。不是装完就能赢。
5. **加速比封顶 ~6.5x 不是 24x**：`rolling_std` 这类算子受内存带宽限制，越多的核越早撞墙。要选「每任务计算量够大 + 核数够多」的甜点区。

### 4.2 分块（chunking）什么时候值得、什么时候踩坑

1. **适用：无状态 / 可独立计算的运算** —— 按资产列切、按参数组合切、`np.abs`/`ffill` 这类逐位置独立或只看局部历史的算子，分块 + concat 结果精确等于不分块（实测完全相等）。
2. **不适用：跨位置有状态算子** —— `rolling_mean`/`rolling_std`/`ewm`/`cumsum`/`cumprod`。简单 `ChunkSlicer` + `merge_func="concat"` 会把每块当独立序列，块边界丢失滚动状态：实测 1M 分 8 块，NaN 从 19 个涨到 76 个，**不一致位置恰是每块开头 window-1 个**。要正确分块必须做块间 overlap（用 `chunk_meta` 手动切含重叠区间）或靠算子自带 carry-over。
3. **分块的主要收益是省内存 + 提供并行任务粒度**，不是无脑提速：8M 数组分 8 块 serial 反而比不分块慢一点（80ms vs 67ms，切块+concat 有调度开销）；只有配上 `engine="threadpool"` 才 2.28x 反超。
4. **分块与并行是正交的两根轴**：`chunker` 决定「切几块、怎么切、怎么拼」（控内存、定任务粒度），`engine` 决定「这些块怎么跑」（串行/多线程/多进程）。`@vbt.chunked(engine="threadpool")` 就是两者叠加的标准姿势。

### 4.3 一句话决策表

| 场景 | 建议 |
|------|------|
| 参数网格/多资产扫描，每任务 ≥100K 元素 | `engine="threadpool"`（或 `chunked=True` + threadpool） |
| 纯 Python 长任务（GIL 密集） | 上 pathos/mpire 多进程（本机 processpool 5s spawn 别用） |
| 大数组滚动/累计算子 | **别裸切块**；要么单核跑，要么用带 overlap 的手动 chunk_meta |
| 小数据（<50K/任务） | 串行即可，并行倒亏 |
| 想省内存跑超大数据 | `@vbt.chunked(n_chunks="auto", chunk 间无状态)` + threadpool |

### 4.4 关键源码位置

- 分块核心：`data/develop-src/vectorbtpro/chunking/core.py`（`Chunker.run` 2201、`iter_chunk_meta` 362、`chunked` 装饰器 2380、`resolve_chunked_option` 2753、`resolve_chunked` 2806）
- 分块注册表：`chunking/registry.py`（`register_chunkable` / `ch_reg`，把 Numba 内核注册成可 chunk 的 setup）
- 数组分块扩展：`base/chunking.py`（`ChunkedFlexArray`/`FlexArraySlicer`/`GroupLensSlicer` 等）
- 执行引擎：`utils/execution.py`（7 个引擎类 + `Executor.run` 2610 + `execute` 3185 + `iterated` 3320）
- 默认配置：`_settings.py:510`（execution，`engine="SerialEngine"`、`distribute="tasks"`）、`:621`（chunking，`option=False`、`skip_single_chunk=True`）

---

## 附：复现材料

- `bench_chunk_exec.py` —— 分块一致性 + threadpool/serial 对比 + 分块并行叠加（A/B/C 三部分）
- `bench_chunk_diag.py` —— rolling 分块边界不一致定位 + 无状态运算精确性 + processpool（注意：此文件第一版模块级代码会在 processpool spawn 时重放，DIAG1/2 结论仍有效，processpool 数据已用干净版重测）
- `bench_processpool.py` —— 干净的 processpool 单次墙钟耗时（全部 `if __name__ == "__main__"` 保护）
- 均为 `median` 计时，warmup 后 repeat；跑前已加 `vbt.settings["jitting"]["backends"]["auto_mode"] = False` 退回 Numba。
