# 11 — 参数网格（Param）与广播机制深挖

> 方向：VBT 的灵魂「多维数组 = 参数网格」，一次跑完所有参数组合。
> 环境：vectorbtpro 2026.6.27（editable 指向 `data/develop-src/`），Python 3.12。
> 所有代码均用 `.venv/Scripts/python.exe` 实际跑通，标注「已验证」的片段可直接复跑。

---

## 0. 一句话结论

把参数从「标量」换成 `vbt.Param([...])` 列表，VBT 会在**不改变任何逻辑代码**的前提下，把函数**自动广播**成对每个参数组合各跑一遍，并把参数值**写进结果的列索引（MultiIndex）**里。这样「回测 12 组参数」和「回测 1 组参数」的代码长度一样，结果还自带参数标签，方便直接 `idxmax()` 找最优。

---

## 1. 广播机制原理

### 1.1 核心：标量 → 列表 → 多维数组 → 参数网格

VBT 设计哲学是「数组化」，参数也不例外。普通量化库里参数是个标量：

```python
rsi = vbt.RSI.run(close, window=14)          # window 是标量，结果 1 列
```

VBT 里把参数换成 `vbt.Param([5, 10, 15, 20])`，`window` 就从「标量」变成了「参数轴」：

```python
rsi = vbt.RSI.run(close, window=vbt.Param([5, 10, 15, 20]))   # window 是列表，结果 4 列
```

**已验证** 输出：

```
rsi.rsi shape: (1000, 4)
columns: Index([5, 10, 15, 20], name='rsi_window')
```

`window` 的 4 个取值变成了结果 DataFrame 的 4 列，列名就是参数值，列索引名是 `rsi_window`（= 参数名）。

### 1.2 两个参数 → 笛卡尔积 → 二维 MultiIndex

两个参数各自是一个「轴」，组合后就是二维网格，结果列变成 `MultiIndex`：

```python
@vbt.parameterized(merge_func='column_stack')
def my_ma(sr, window, wtype='simple'):
    return sr.vbt.ma(window, wtype=wtype)

res = my_ma(close, vbt.Param([3, 4, 5]), wtype=vbt.Param(['simple', 'exp']))
```

**已验证** 输出：

```
res.shape: (500, 6)            # 3 windows × 2 wtypes = 6 列
columns: MultiIndex[(3,'simple'), (3,'exp'), (4,'simple'), (4,'exp'), (5,'simple'), (5,'exp')]
```

这就是「多维数组 = 参数网格」的直观体现：**每个参数 = 一个维度，参数组合 = 笛卡尔积，组合标签 = MultiIndex 列**。

### 1.3 内部流程（源码级）

从源码 `utils/params.py` 追到底，整个广播走 5 步：

1. **找参数**：`Parameterizer.find_params_in_obj` 在函数实参里递归找出所有 `Param` 实例
2. **组合**：`combine_params` 把多个参数 list 组合成 `param_product`（字典：参数名 → 展开后的值 list）+ `param_index`（MultiIndex）
3. **注入**：`param_product_to_objs` 用 `replace_in_obj` 把每组值替换回原实参，生成 N 个「具体参数配置」
4. **执行**：`execute` 把 N 个任务交给执行引擎（Numba/Rust 后端），每个任务拿一组具体参数跑一次
5. **合并**：`merge_func`（如 `'column_stack'`）把 N 个结果按列拼成一个带 MultiIndex 的 DataFrame

关键函数签名（已核对）：

```python
combine_params(param_dct, ..., build_product=True, build_index=True, ...) -> (dict, pd.Index)
```

- `build_product=True`（默认）→ 笛卡尔积
- `build_product=False` → 逐对 zip

---

## 2. `vbt.Param` / `param_product` / `param_combine` / `@vbt.parameterized`

### 2.1 重大版本变化：`param_product` / `param_combine` 已删除

任务里提到的 `param_product`（笛卡尔积）和 `param_combine`（逐对组合）是 **OSS vectorbt 旧 API 的顶层函数**。在 VBT PRO 2026.6.27 里它们**已不存在**（`vbt.param_product` / `vbt.param_combine` 均 `<MISSING>`，全包源码也搜不到 `def param_product` / `def param_combine`）。

**现代等价物**：

| 旧 API（OSS vectorbt） | 新 API（VBT PRO 2026.6.27） |
|---|---|
| `vbt.param_product(dict)` 笛卡尔积 | `vbt.combine_params(dict, build_product=True)` |
| `vbt.param_combine(dict)` 逐对 zip | `vbt.combine_params(dict, build_product=False)` |
| 工厂 `run(..., param_product=True)` | 工厂 `run(..., param_product=True)`（参数同名保留） |

**已验证** 直接对比：

```python
dct = dict(fast=vbt.Param([5, 10]), slow=vbt.Param([20, 30]))

params, idx = vbt.combine_params(dct, build_product=True)
# params: {'fast': [5, 5, 10, 10], 'slow': [20, 30, 20, 30]}
# index:  [(5,20), (5,30), (10,20), (10,30)]     ← 4 个组合（笛卡尔积）

params2, idx2 = vbt.combine_params(dct, build_product=False)
# params2: {'fast': [5, 10], 'slow': [20, 30]}
# index2:  [(5,20), (10,30)]                       ← 2 个组合（逐对 zip）
```

### 2.2 `vbt.Param` 字段（源码 `utils/params.py` 的 `Param` 类）

| 字段 | 作用 |
|---|---|
| `value` | 参数值：标量 / list / np.ndarray / dict / pd.Series / pd.Index |
| `is_tuple` | True 时把 tuple 当作**单个值**（默认 tuple 会被当成多个值拆开） |
| `is_array_like` | True 时把 np.ndarray 当作**单个值** |
| `keys` | 自定义索引键（覆盖参数值默认作为列名） |
| `name` | 参数名（结果索引该层级的名字，默认取函数形参名） |
| `level` | 参数层级（同层逐对 zip，跨层笛卡尔积，见 2.4） |
| `condition` | 组合过滤条件（可用 `__其他参数名__` 引用别的参数，见 3.4） |
| `hide` | True 时该参数不出现在结果索引里 |
| `random_subset` | 从参数值里随机抽子集（整数个数 / 浮点比例） |
| `map_template` | 对 value 先套模板变换再展开 |
| `mono_reduce` / `mono_merge_func` | 单值合并（mono-chunk 场景，进阶用） |

### 2.3 `@vbt.parameterized` 装饰器

作用：**让任意自定义函数支持参数网格**。装饰后函数签名的任意实参都能传 `vbt.Param([...])`。

```python
@vbt.parameterized(merge_func='column_stack')     # merge_func 控制结果怎么拼列
def my_func(a, b, c):
    ...
```

- `merge_func='column_stack'`：单输出时按列拼接成 MultiIndex DataFrame；多输出（返回 tuple）时**每个输出各拼一份**，返回同长度的 tuple
- 装饰器背后是 `Parameterizer` 类（`utils/params.py`），流程见 1.3
- 参数可通过 `func.options` 属性或下划线前缀 kwargs 覆盖

**坑（重点）**：`@vbt.parameterized` 走 `combine_params(build_product=True)`，**默认就是笛卡尔积**；而指标工厂 `RSI.run(...)` 走 `param_product=False`，**默认是逐对 zip**（见 2.4）。两个入口默认行为相反，混用必踩坑。

### 2.4 `param_product`（笛卡尔积）vs 逐对 zip（原 param_combine）的区别

**已验证** 用 MA 指标工厂直接对比：

```python
ma  = vbt.MA.run(close, window=vbt.Param([3, 5]), wtype=vbt.Param(['simple', 'exp']))
ma2 = vbt.MA.run(close, window=vbt.Param([3, 5]), wtype=vbt.Param(['simple', 'exp']), param_product=True)
```

输出：

```
默认 (param_product=False):  shape (300, 2)  columns [(3,'simple'), (5,'exp')]     ← 逐对 zip
param_product=True:         shape (300, 4)  columns [(3,'simple'), (3,'exp'), (5,'simple'), (5,'exp')]  ← 笛卡尔积
```

语义：
- **逐对 zip**：`window[i]` 配 `wtype[i]`，要求两个列表**等长**（`combine_params` 内部用 `broadcast_params` 把等长列表对齐）。适合「成对参数」，如 `fast=(5,10,20)` 配 `slow=(20,30,40)` 想跑「(5,20)/(10,30)/(20,40)」三组而不是 9 组。
- **笛卡尔积**：所有组合全跑。适合「独立参数」，如 window × threshold 想要全部交叉。

同一个效果用 `Param(level=...)` 也能控制（见 3.5）。

---

## 3. 三个实际跑通的完整代码片段（已验证）

> 公共前置（3 段都用，含一个**必踩的环境坑**修复）：

```python
import vectorbtpro as vbt
import numpy as np, pandas as pd

# 坑：develop 源码 + 旧 rust wheel 版本不一致，Rust 后端 auto 判定会崩
# AttributeError: module 'vectorbtpro_rust' has no attribute '__build_profile__'
# 关掉 Rust 后端自动分派，退回 Numba：
vbt.settings['jitting']['backends']['auto_mode'] = False

np.random.seed(42)
n = 1000
close = pd.Series(100 * np.exp(np.cumsum(np.random.normal(0.0005, 0.02, n))),
                  index=pd.date_range('2020-01-01', periods=n, freq='1h'))
```

### 片段 1：单参数 RSI 扫描 → 参数变成列索引

```python
rsi = vbt.RSI.run(close, window=vbt.Param([5, 10, 15, 20]))
print(rsi.rsi.shape)            # (1000, 4)
print(rsi.rsi.columns.tolist()) # [5, 10, 15, 20]，列索引名 = 'rsi_window'
```

**输出**：`shape (1000, 4)`，`columns Index([5, 10, 15, 20], name='rsi_window')`。一行代码跑完 4 组 window，参数值就是列名。

### 片段 2：`@vbt.parameterized` 双参数 RSI 策略 → 12 组合 + 找最优

```python
@vbt.parameterized(merge_func='column_stack')
def rsi_sigs(close, window, threshold):
    rsi = vbt.RSI.run(close, window=window).rsi
    entries = rsi.vbt.crossed_below(threshold)   # RSI 下穿阈值 → 做多
    exits   = rsi.vbt.crossed_above(threshold)   # RSI 上穿阈值 → 平仓
    return entries, exits                        # 返回 tuple，两个输出各拼一份

entries, exits = rsi_sigs(close, vbt.Param([5, 10, 15, 20]), vbt.Param([20, 30, 40]))
pf = vbt.Portfolio.from_signals(close, entries=entries, exits=exits, freq='1h')

tr = pf.total_return            # 注意：是属性，不是方法！
print(tr)                       # 12 个值的 Series，索引 = MultiIndex (window, threshold)
best = tr.idxmax()              # (5, 40)
print('最优参数组合 =', best, '收益率 =', round(tr.max(), 4))
```

**输出**：

```
window  threshold
5       20           0.156793
        30           0.336100
        40           0.421579
10      20           0.013525
        ...
最优参数组合 = (5, 40)  收益率 = 0.4216
```

`entries`/`exits` 各 shape `(1000, 12)`，`pf.value` shape `(1000, 12)`，12 个组合全部一次性回测完成。

### 片段 3：全组合统计表 + 透视 + 按指标挑参数

```python
# 每个组合的完整 29 项指标（列 = 12 组合，行 = 指标）
st = pf.stats(per_column=True)          # 默认会按 mean 聚合并报警告，必须 per_column=True
stT = st.T                               # 转置成「行=组合，列=指标」
print(stT[['Total Return [%]', 'Sharpe Ratio', 'Total Trades']])
print('收益透视表：')
print(tr.unstack())                      # 把 MultiIndex 铺成 window × threshold 矩阵
```

**输出**（截取）：

```
                 Total Return [%]  Sharpe Ratio  Total Trades
window threshold
5      20               15.679258      3.377243           23
       30               33.610016      3.943617           54
       40               42.157871      3.673677           92
10     20                1.352540      1.836013            1
       ...
15     20                0.000000           NaN            0    ← 无交易，Sharpe 为 NaN
       ...

收益透视表：
threshold        20        30        40
window
5          0.156793  0.336100  0.421579
10         0.013525  0.107436  0.208024
15         0.000000  0.176383  0.308798
20         0.000000  0.045226  0.167589
```

一眼看出：window=5 且 threshold=40 收益最高（42.16%），threshold 越大收益越高、window 越小越敏感。

### 片段 4（进阶）：`condition` 跨参数过滤 —— 只跑合法组合

```python
@vbt.parameterized(merge_func='column_stack')
def ma_cross(fast, slow, close):
    fma = vbt.MA.run(close, window=fast).ma
    sma = vbt.MA.run(close, window=slow).ma
    return fma.vbt.crossed_above(sma)

res = ma_cross(
    vbt.Param([5, 10, 20, 30], name='fast'),
    vbt.Param([10, 20, 30, 50], name='slow', condition='x > __fast__'),  # 只要 slow > fast
    close,
)
print(res.shape, res.columns.tolist())
```

**输出**：`shape (200, 10)`，从 4×4=16 组里自动剔除 slow ≤ fast 的 6 组，只留 `(5,10)…(30,50)` 10 组。`condition='x > __fast__'` 里的 `x` 代表当前参数值，`__fast__` 引用另一个参数名。这是「避免跑无效组合」的核心手段。

### 片段 5（进阶）：`level` 控制同层 zip vs 跨层笛卡尔积

```python
res = ma_cross(
    vbt.Param([5, 10, 20], name='fast',  level=0),
    vbt.Param([10, 20, 30], name='slow', level=0),   # 同 level=0 → 逐对 zip
    close,
)
# shape (200, 3)，columns [(5,10), (10,20), (20,30)]
```

**输出**：`shape (200, 3)`，`[(5,10), (10,20), (20,30)]`。同 `level` 参数逐对 zip；不同 `level` 之间再笛卡尔积。这等价于「原 param_combine」的语义，但更灵活（可以多层嵌套）。

---

## 4. 性能对比实测：网格一次跑 vs for 循环

**已验证** 实测（30 组合 = 6 windows × 5 thresholds，2000 根 1h K 线，Numba 后端，先预热触发 JIT 编译后再计时）：

| 方式 | 耗时 | 单组合均摊 |
|---|---|---|
| 网格一次跑（`@vbt.parameterized`） | **0.2940 s** | 9.80 ms |
| for 循环逐个跑 | 0.5347 s | 17.82 ms |
| **加速比** | **1.82x** | — |

测量代码（核心）：

```python
WINDOWS, THRESHOLDS = [5,10,15,20,25,30], [20,25,30,35,40]   # 30 组合

# 网格：一次跑完，结果自带 MultiIndex
t0 = time.perf_counter()
entries, exits = rsi_sigs(close, vbt.Param(WINDOWS), vbt.Param(THRESHOLDS))
pf = vbt.Portfolio.from_signals(close, entries=entries, exits=exits, freq='1h')
t_grid = time.perf_counter() - t0

# for 循环：12 个独立调用，手动收集结果
t0 = time.perf_counter()
for w in WINDOWS:
    for th in THRESHOLDS:
        rsi = vbt.RSI.run(close, window=w).rsi
        e, x = rsi.vbt.crossed_below(th), rsi.vbt.crossed_above(th)
        pf = vbt.Portfolio.from_signals(close, entries=e, exits=x, freq='1h')
t_loop = time.perf_counter() - t0
```

**结论**：实测约 1.8x 加速。但这还不是主要收益——**真正的收益是代码量**：网格版一次调用跑完 30 组且结果自动带参数标签；for 循环要 30 次手动调用、手动拼 DataFrame、手动打标签。规模越大（组合数、数据长度）差距越明显，且 VBT 的 `Executor` 支持 `n_chunks`/`distribute` 分块和并行引擎（`execution.execute` 的 `executor` 参数），大数据量下可进一步放大加速。

---

## 5. 关键坑（务必记）

1. **`pf.total_return` 是属性不是方法**：`pf.total_return` ✅，`pf.total_return()` ❌（报 `'Series' object is not callable`）。绩效指标同理（`pf.value`、`pf.sharpe_ratio` 等）。

2. **Rust 后端 auto_mode 崩溃**：editable develop 源码 + 已装的 `vectorbtpro-rust` wheel 版本不一致（wheel 缺 `__build_profile__` 属性），默认 `auto_mode=True` 会在首次 JIT 时抛 `AttributeError: module 'vectorbtpro_rust' has no attribute '__build_profile__'`。**修复**：`vbt.settings['jitting']['backends']['auto_mode'] = False` 退回 Numba，或装配套的 rust wheel。

3. **两个入口默认行为相反**（最容易踩的语义坑）：
   - `@vbt.parameterized` → `combine_params(build_product=True)` → **默认笛卡尔积**
   - 工厂 `RSI.run(..., vbt.Param([...]))` → `param_product=False` → **默认逐对 zip**
   - 想要笛卡尔积时给工厂加 `param_product=True`；想要逐对时给参数设相同 `level`。

4. **`param_product` / `param_combine` 已删除**：2026.6.27 里 `vbt.param_product` / `vbt.param_combine` 都是 `<MISSING>`，用 `vbt.combine_params(build_product=True/False)` 替代（见 2.1 对照表）。

5. **找最优参数**：`tr.idxmax()` 在 MultiIndex 上返回**元组**（如 `(5, 40)`），不是标量。要铺成矩阵用 `tr.unstack()`；要按某指标排序用 `tr.sort_values(ascending=False)`。

6. **`pf.stats()` 多列默认聚合**：多组合 Portfolio 直接 `pf.stats()` 会按 mean 聚合成 1 列并发 `VBTWarning`。要每个组合一份指标必须 `pf.stats(per_column=True)`（返回 shape `(29 指标, 12 组合)`），再 `.T` 转置成「行=组合」。

7. **结果切片**：参数组合结果是 MultiIndex 列，可用 `pf.value[(5, 40)]` 取单组合、`pf.value.xs(5, level='window')` 取某参数下所有组合、`tr.unstack(level='threshold')` 换透视维度。

8. **无交易组合的 NaN**：某些参数组合可能 0 交易（如 window=15, threshold=20），其 Sharpe/胜率是 `NaN`，排序前先 `dropna()` 或 `fillna(0)`。

9. **`condition` 引用别的参数**：`condition='x > __fast__'`，`x` = 当前参数值，`__参数名__` = 其他参数当前值；参数名默认是函数形参名，可用 `Param(name='...')` 改。

10. **`Param` 的 tuple/array 陷阱**：默认 tuple 和 np.ndarray 会被当成「多个值」拆开。若某个参数值本身是 tuple（如 `(1, 2)` 一个坐标点），必须 `vbt.Param([...], is_tuple=True)` 或 `is_array_like=True`。

---

## 6. 源码地图（后续深挖入口）

| 内容 | 位置 |
|---|---|
| `Param` 类 / `combine_params` / `parameterized` 装饰器 | `vectorbtpro/utils/params.py` |
| `create_param_product`（笛卡尔积）/ `broadcast_params`（zip 对齐） | `vectorbtpro/utils/params.py:131/170` |
| `generate_param_combs`（操作树组合）/ `pick_from_param_grid` | `vectorbtpro/utils/params.py:83/276` |
| `Parameterizer`（装饰器背后的类） | `vectorbtpro/utils/params.py:1127` |
| 指标工厂 `run` 的 `param_product` 标志 | `vectorbtpro/indicators/factory.py:623` |
| 广播底层（`broadcast` / `broadcast_combs`） | `vectorbtpro/base/reshaping.py` |
| 执行引擎（`execute` / `Executor`，并行分块） | `vectorbtpro/utils/execution.py` |
| Rust 后端 auto 判定（崩溃点） | `vectorbtpro/jitting/backends/rust.py:190` |
