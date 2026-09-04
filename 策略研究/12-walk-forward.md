# 12 — Walk-Forward 交叉验证与防过拟合（splitter / cv_split / purging / embargo）

> 深度方向：把 VBT 的 walk-forward（滚动前向）交叉验证机制吃透，并用真实代码跑通「样本内选参 → 样本外验证」全管线，量化过拟合程度，验证 purging/embargo 的防标签泄漏。
> 环境：vectorbtpro 2026.6.27（editable develop 源码，`data/develop-src/vectorbtpro`），Python `.venv/Scripts/python.exe`。
> 源码依据：`vectorbtpro/generic/splitting/{base.py, decorators.py, purged.py}`。
> 社区共识来源：`research/04-discord-knowledge.md`（Sharpe 不可靠、单一 walk-forward 不够）。

---

## 〇、一个重要环境坑（先记下）

跑任何涉及指标/回测的代码前，先关掉 Rust 自动后端选择，否则会**间歇性**报错：

```
AttributeError: module 'vectorbtpro_rust' has no attribute '__build_profile__'
```

```python
from vectorbtpro import *
import vectorbtpro as vbt
vbt.settings.jitting.backends['auto_mode'] = False   # 钉死在 numba，避开 rs 自动选择
```

本文件所有「已验证」代码均带这一行。

---

## 一、Splitter 类族说明

### 1. 核心数据结构

`Splitter`（`vectorbtpro/generic/splitting/base.py:355`）本质上是**一张二维表 `splits_arr`**：

- 第一维 = **split**（第几次切分，即 walk-forward 的第几步）
- 第二维 = **set**（每个 split 内部的集合，通常 0=训练 train、1=测试 test）

每个格子里存一个 **range**：`FixRange`（固定区间）/ `RelRange`（带 offset、length 的相对区间）/ slice / 索引数组 / 布尔 mask / callable。运行时按 range 去切输入数据。

```
splits_arr 示意（n=3, split=0.5）：
             set_0(train)   set_1(test)
split 0      [0:16]         [16:33]
split 1      [33:49]        [49:66]
split 2      [66:82]        [82:99]
```

> 已验证：`vbt.Splitter.from_n_rolling(index, n=3, split=0.5)` 输出 3 个 split、每个 split 里 train/test 各约一半。

### 2. 工厂方法（`Splitter.from_*`，13 个）

| 方法 | 语义 | 关键参数 |
|------|------|----------|
| `from_splits` | 从「split 的迭代」直接建 | splits, set_labels, split_labels |
| `from_single` | 单个 split | split |
| `from_rolling` | 定长滚动窗口 | length, offset, offset_anchor, backwards |
| `from_n_rolling` | **n 个等长滚动窗口（walk-forward 主力）** | n, length, split |
| `from_expanding` | 扩张窗口 | min_length, offset |
| `from_n_expanding` | n 个等间距扩张窗口 | n, min_length, split |
| `from_ranges` | 按日历区间（每季度/每月…） | every="QS" 等 |
| `from_grouper` | 按分组标签切 | by 分组键 |
| `from_n_random` | 随机切 | n, seed |
| `from_sklearn` | 桥接 scikit-learn 交叉验证器 | skl_splitter |
| `from_purged` | 桥接 Purged CV（见第三节） | purged_splitter, pred_times, eval_times |
| `from_purged_walkforward` | 桥接 `PurgedWalkForwardCV` | n_folds, purge_td, … |
| `from_purged_kfold` | 桥接 `PurgedKFoldCV` | n_folds, purge_td, embargo_td, … |

**`split` 参数的语义**（贯穿所有 `from_*`）：把每个窗口**再切成 train/test 集合**。支持：

- `split=0.5` → 前 50% 训练 / 后 50% 测试
- `split=-50` → 最后 50 个元素做测试，其余训练
- `split=(1.0, mask/lambda)` → 一个 set 是全窗口、另一个按条件挑（见 `from_ranges` 官方例子）
- `split=None` → 整段窗口当作单个 set

### 3. `Splitter.apply`：真正干活的地方

`apply(func, *args, split=..., set_=..., merge_func=...)` 会：

1. 遍历每个 `(split_idx, set_idx)`，取出对应 range；
2. 把标成 `Takeable` 的参数按 range 切片（`take_range`）；
3. 把当前上下文（`split_idx` / `set_idx` / `split_label` / `range_` / `bounds` / …）塞进模板，替换函数签名里带 `vbt.Rep(...)` 的参数；
4. 逐个执行 `func`，最后按 `merge_func`（`concat` / `column_stack` / `row_stack` / …）合并结果，得到带 `(split, set)` 层级的结果。

### 4. `@vbt.split` 装饰器

`@vbt.split`（`decorators.py:25`）= 「resolve splitter → 把 `takeable_args` 包成 `Takeable` → 调 `splitter.apply`」。它只做**切分**，不涉及参数寻优。

### 5. `@vbt.cv_split`：split + parameterized 的化合

`@vbt.cv_split`（`decorators.py:335`）才是 walk-forward 防过拟合的核心。它的执行流程（读源码 `apply_wrapper` 得到）：

```
对每个 (split_idx, set_idx)：
  ├─ set_idx == 0（训练集）：
  │    用 parameterized 在【整个参数网格】上跑 func
  │    把网格结果存进 grid_results_map[(split_idx, 0)]
  │    → 返回训练集上的网格结果
  └─ set_idx != 0（测试集）：
       取出 grid_results_map[(split_idx, 0)]
       用 selection 在网格结果上选出「最优参数组合」（默认 np.nanargmax）
       只【用这一组参数】在测试集上跑一次 func
       → 返回样本外绩效
```

关键参数：

| 参数 | 默认 | 含义 |
|------|------|------|
| `splitter` / `splitter_kwargs` | — | 传给 `@vbt.split`（如 `from_n_rolling, dict(n=10, split=0.5)`） |
| `takeable_args` | — | 要按 split 切片的参数（通常是价格序列） |
| `selection` | `"max"` | 选最优参数的模板；`"max"`=`np.nanargmax`，`"min"`=`np.nanargmin`，也可自定义 `RepEval(...)` |
| `return_grid` | `False` | `True`/`"first"` 返回 `(网格, 选中结果)`；`"all"` 每个 set 都跑全网格 |
| `merge_func` | — | 结果合并方式，通常 `"concat"` |

**核心防过拟合机制**：训练集扫全网格、测试集只跑「训练集选出的那一组」。于是测试集（`set_1`）的绩效就是**真正的样本外（OOS）绩效**——它没有被参数选择过程污染。

> ⚠️ 官方源码明确警告：**同一 split 的 train/test 必须在同一线程/进程内执行**，因为网格结果通过 `grid_results_map` 内存字典传递。别给 `@cv_split` 套多进程执行引擎。

### 6. `selection="max"` 的一个坑（已验证）

`"max"` 是 `np.nanargmax`，它有两个副作用：

1. **忽略 NaN**（无成交的组合返回 NaN 会被跳过）；
2. **并列时取第一个**（多个组合 return 都是 0.0 时，会选索引最小的那个组合）。

实测：很多窗口里 MA 交叉策略不产生交易，`total_return=0.0`，`nanargmax` 就倾向选 `fast=第一个, slow=第一个`。这在「稀疏信号 + 短窗口」场景会系统性偏向小参数，选参时要注意（要么换更连续的目标函数，要么自定义 `selection` 模板）。

---

## 二、完整 walk-forward 管线实战（已验证）

### 实验设计

- **策略**：RSI 均值回归（官方 basic-rsi 配方）：RSI 下穿 `entry_low` 买入、上穿 `100-entry_low` 卖出，手续费 0.1%。
- **参数网格**：`window ∈ {7,14,21}` × `entry_low ∈ {20,30,40}` = 9 组。
- **切分**：`from_n_rolling(n=10, split=0.5)`，10 步滚动，每步前 50% 训练 / 后 50% 测试。
- **三套合成数据**（各 2000 天日线）：
  - `meanrev`：OU 均值回归过程（RSI 的真·有效市场）
  - `trend`：带漂移 + 正弦周期的趋势（RSI 会失效的市场）
  - `noise`：纯几何随机游走（无任何信号）

### 代码

```python
from vectorbtpro import *
import vectorbtpro as vbt
import numpy as np, pandas as pd
vbt.settings.jitting.backends['auto_mode'] = False

def make_price(kind, n=2000, seed=7):
    np.random.seed(seed)
    if kind == 'meanrev':
        theta, mu, sigma = 0.05, 0.0, 0.015
        x = np.zeros(n)
        for i in range(1, n):
            x[i] = x[i-1] + theta*(mu - x[i-1]) + sigma*np.random.normal()
        price = 100*np.exp(x)
    elif kind == 'trend':
        t = np.arange(n)
        log_ret = 0.0012 + 0.001*np.sin(t/40) + np.random.normal(0, 0.015, n)
        price = 100*np.exp(np.cumsum(log_ret))
    else:  # noise
        price = 100*np.exp(np.cumsum(np.random.normal(0, 0.015, n)))
    return pd.Series(price, index=pd.date_range('2017-01-01', periods=n, freq='D'), name=kind)

@vbt.cv_split(
    splitter='from_n_rolling',
    splitter_kwargs=dict(n=10, split=0.5),   # 10 步 walk-forward，前 50% 训练 / 后 50% 测试
    takeable_args=['price'],
    merge_func='concat',
)
def rsi_strat(price, window, entry_low):
    rsi = vbt.RSI.run(price, window=window).rsi
    entries = rsi.vbt.crossed_below(entry_low)
    exits   = rsi.vbt.crossed_above(100 - entry_low)
    pf = vbt.Portfolio.from_signals(price, entries, exits, freq='1D', fees=0.001)
    return pf.total_return          # 注意：vectorbtpro 里是属性，不是方法

# 跑一次：返回选中参数组合的 IS(set_0) / OOS(set_1) 绩效
sel = rsi_strat(price, vbt.Param([7,14,21]), vbt.Param([20,30,40]))
# sel.index = ['split','set','window','entry_low']
is_ret  = sel.xs('set_0', level='set')   # 样本内（训练集）最优参数的收益
oos_ret = sel.xs('set_1', level='set')   # 样本外（测试集）同一参数的收益

# 要拿完整网格（每个 split × 每个组合的 IS/OOS）：
grid, sel = rsi_strat(price, vbt.Param([7,14,21]), vbt.Param([20,30,40]), _return_grid='all')
```

### 输出（已验证，数字保留 4 位）

**meanrev（RSI 的真有效市场）**

```
per-split  IS(best) -> OOS(same combo)
split   IS_best     OOS
0        0.1767  -0.0488
1        0.1262   0.0850
2        0.1351   0.0000
3        0.0000   0.0000
4        0.1023   0.0718
5        0.0537   0.0493
6        0.0386   0.0412
7        0.1507   0.1427
8        0.1726   0.0000
9        0.0904   0.0671

MEAN IS_best (in-sample)      : +0.1046
MEAN OOS    (out-of-sample)   : +0.0408
Overfit gap  IS-OOS           : +0.0638
IS/OOS ratio                  : +2.56x
OOS win rate (>0)             : 60%
IS-OOS Spearman corr (mean)   : +0.193
```

**trend（RSI 的失效市场）**

```
MEAN IS_best (in-sample)      : +0.1034
MEAN OOS    (out-of-sample)   : -0.0072
Overfit gap  IS-OOS           : +0.1106
IS/OOS ratio                  : +14.38x
OOS win rate (>0)             : 30%
IS-OOS Spearman corr (mean)   : -0.116
```

**noise（无信号市场）**

```
MEAN IS_best (in-sample)      : +0.0901
MEAN OOS    (out-of-sample)   : -0.0394
Overfit gap  IS-OOS           : +0.1296
IS/OOS ratio                  : +2.29x
OOS win rate (>0)             : 20%
IS-OOS Spearman corr (mean)   : -0.009
```

### 三张表读出的结论

1. **即使策略真的有 edge（meanrev），样本内也会系统性高估 ~2.5 倍**：IS +10.5% → OOS +4.1%，gap +6.4%。这是「过拟合税」的基线——任何回测结果都要默认打个 2~3 折。

2. **纯噪声市场上，IS 也能刷出 +9% 的「假 edge」**，但 OOS 掉到 -3.9%，**OOS 胜率仅 20%，IS-OOS 秩相关 ≈ 0**。这就是过拟合的教科书特征：样本内曲线好看，样本外秩相关为零（参数好坏完全随机）。

3. **趋势市场上 RSI 均值回归 OOS 转负（-0.7%）、IS/OOS 高达 14.4x**——walk-forward 正确地暴露了「策略用错了市场」。若只看样本内 +10.3%，你会以为它还能赚钱。

4. **三个量化过拟合的黄金指标**（本次验证有效）：`OOS 胜率`、`IS-OOS Spearman 秩相关`、`Overfit gap / IS-OOS ratio`。真 edge → 胜率≥50% 且秩相关 >0；纯过拟合 → 胜率 ~20% 且秩相关 ≈ 0。

---

## 三、Purging / Embargo 防标签泄漏（已验证）

来源：`vectorbtpro/generic/splitting/purged.py`，即 Marcos López de Prado《Advances in Financial Machine Learning》的 purge/embargo 实现。

### 1. 为什么需要 purge

标准 k-fold / 朴素 walk-forward 有个漏洞：**标签（label）不是瞬时实现的**。一个样本在 t 时刻做预测，但它的收益要到 t+h 之后才实现。若训练集里混入「标签在测试窗口内才实现」的样本，模型就「偷看」了未来 → 标签泄漏。

purge 的核心：训练样本只保留 `eval_time + purge_td < 测试集首个预测时间` 的那些（`BasePurgedCV.purge`）。

### 2. `PurgedWalkForwardCV`：purge 验证

参数：`n_folds, n_test_folds, min_train_folds, max_train_folds, split_by_time, purge_td`；`pred_times`（何时预测）、`eval_times`（标签何时实现）。

**已验证实验**：48 天日线，标签滞后 5 天实现（`eval_times = pred_times + 5d`），`n_folds=6, n_test_folds=1, min_train_folds=2`。

```
fold_bounds = [0, 8, 16, 24, 32, 40]
split0: 朴素训练集(无 purge) = 16 个样本
        其中标签与测试窗口重叠的「泄漏样本」= 5 个
```

| purge_td | train 样本数 | 最后一个训练标签 eval_max | 说明 |
|----------|-------------|--------------------------|------|
| （无 purge） | 16 | — | 有 5 个样本的标签在测试窗口内实现 → **泄漏** |
| `0 days` | 11 | 测试起点前 1 天 | 基础 purge 已把 5 个重叠样本剔掉 |
| `5 days` | 6 | 测试起点前 6 天 | 再额外加 5 天安全缓冲 |

结论：**即使 `purge_td=0`，PurgedWalkForwardCV 也已经强制「训练标签不越入测试窗口」**（eval_max 严格 < 测试首预测时间）。`purge_td` 的作用是在标签实现时点**不确定**时再加一道安全边际——实务上持仓/信号的真实「落地」时点往往比 1 根 K 线更长，所以 `purge_td` 应设成 ≥ 你的最大标签滞后。

### 3. `PurgedKFoldCV`：purge + embargo 验证

`PurgedKFoldCV` 是**组合式（CPCV 风格）**：从 `n_folds` 个 fold 里取 `n_test_folds` 个做测试、其余做训练，遍历所有组合（测试 fold 可能在**中间**，前后都有训练样本）。多一个 `embargo_td`。

**embargo 的作用**（`PurgedKFoldCV.embargo`）：当测试 fold 在中间时，剔除「紧跟测试集评估期之后、预测时间落在 embargo 窗口内」的训练样本，防止序列自相关导致的间接泄漏。

**已验证实验**：120 天日线，标签滞后 5 天，`n_folds=10, n_test_folds=2, purge_td=5d`，选一个测试集在中间的 split。

| embargo_td | train 总数 | 测试集之后的训练样本 | 首个「测试后」训练预测时间 与 测试集最后评估时间的间隔 |
|-----------|-----------|--------------------|--------------------------------------------------|
| `0 days` | 81 | 7 | 1 天 |
| `5 days` | 76 | 2 | 6 天 |

结论：`embargo_td=5 days` 把测试集之后的 5 个训练样本剔掉，使「测试后首个训练预测」从测试评估结束后 1 天推远到 6 天。这就是 embargo 的**时间间隔防火墙**。

### 4. 桥接到 VBT 主 Splitter

```python
splitter = vbt.Splitter.from_purged_walkforward(
    index, n_folds=10, n_test_folds=1, min_train_folds=3,
    purge_td='5 days', pred_times=pred_times, eval_times=eval_times,
)
# 或者用 PurgedKFoldCV + from_purged_kfold(embargo_td=...)
```

拿到 `Splitter` 后，可以再喂给 `@vbt.cv_split(splitter=...)` 或 `@vbt.split(splitter=...)` 做带 purge 的完整 walk-forward。

---

## 四、结论：什么时候 walk-forward 可信，什么时候不够

### walk-forward 可信的前提

1. **有足够多的独立 split**（≥ 8~10 步），且每步测试窗口覆盖不同市场环境（牛/熊/震荡），避免单一路径的偶然性。
2. **OOS 胜率稳定 ≥ 50%，且 IS-OOS Spearman 秩相关 > 0**——说明「参数好坏」在样本外有延续性，不是随机命中。
3. **参数维度少、经济逻辑明确**（如 RSI 超买超卖），而不是几十个参数的大网格硬扫。
4. **配合 purge/embargo**，消除了标签泄漏后再看 OOS 才有意义。

### walk-forward 还不够的情形（引用社区共识，见 04-discord-knowledge.md §四）

1. **单一 walk-forward 路径不够**。社区资深用户共识：要走一条滚动路径容易过拟合到「这一条路径」。需要 **Monte Carlo 重抽样、CPCV（组合式 purge CV，即 `PurgedKFoldCV`）、System Parameter Permutation** 等稳健性测试组合。
2. **Sharpe Ratio 不可靠、不预测未来**。社区经验：样本外 total return 比 Sharpe 更稳；别用 IS 的 Sharpe 做选参指标（本实验也印证：选参用 total return 都会高估，Sharpe 更甚）。
3. **过拟合没有圣杯解**。社区共识：发现过拟合是好事（说明可以转向下一个 idea）；系统性交易的过拟合是长期对抗，不存在一劳永逸的检验。
4. **10~15 个高度相关参数**对冲意义小、互不相关等于两边下注——参数数量与结构本身要克制。
5. 作者（Oleg）原话：VBT 没有一刀切模板，跨验证教程才覆盖了不同场景（是否 numba 编译决定实现差异）。

### 一句话总结

> walk-forward（`@vbt.cv_split` + `Splitter`）能诚实地告诉你「样本内选出的参数在样本外还剩多少」，配合 purge/embargo 能堵住标签泄漏；但它只给**一条** OOS 路径，抗过拟合要再叠加 Monte Carlo / CPCV / 参数置换，并且永远别信 Sharpe。

---

## 五、防过拟合实用 Checklist（可直接照做）

1. **关 Rust 自动选择**：`vbt.settings.jitting.backends['auto_mode'] = False`，否则间歇性 `__build_profile__` 报错。
2. **指标访问用属性**：vectorbtpro 里 `pf.total_return` / `pf.sharpe_ratio` / `pf.max_drawdown` 是属性（返回标量），`pf.stats()` 才是方法。写成 `pf.total_return()` 会报 `'numpy.float64' object is not callable`。
3. **切分用 `from_n_rolling(n≥8, split=0.5)`**，测试窗口覆盖多段行情；数据量允许时用 `from_n_expanding`（扩张窗口，训练信息更多）。
4. **选参指标用 total return（或自定义模板），别用 Sharpe**（社区共识：Sharpe 样本外不可靠）。
5. **警惕 `selection="max"` 的并列取第一个**：稀疏信号会让很多组合 return=0.0 并列，选参系统性偏向小参数。必要时自定义 `selection` 模板。
6. **至少报三个数**：OOS 胜率（>50% 才可信）、IS-OOS Spearman 秩相关（>0 才有延续性）、Overfit gap / IS-OOS ratio（真 edge 通常 ≤3x，纯过拟合可达 10x+）。
7. **有标签滞后就上 purge**：`PurgedWalkForwardCV(purge_td=你的最大标签滞后)`；测试 fold 在中间（CPCV）时再上 `PurgedKFoldCV(embargo_td=...)`。
8. **单一 walk-forward 不够**：叠加 Monte Carlo / CPCV（`PurgedKFoldCV`）/ 参数置换，至少换 2~3 个随机种子复跑，看 OOS 分布而非单点。
9. **参数网格克制**：10~15 个高度相关参数没意义；优先做经济逻辑明确的少参数策略。
10. **发现过拟合是好事**：IS 好看 OOS 归零 = 直接转向下一个 idea，别在噪声里继续调参。
