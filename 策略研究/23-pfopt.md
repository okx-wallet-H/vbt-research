# 23 — 投资组合优化与再平衡（pfopt）

> 研究方式：本地源码 `vectorbt.pro-main/vectorbtpro/portfolio/pfopt/base.py`（4257 行）+ 本机 `vectorbtpro 2026.6.27` 实跑验证。
> 所有「已验证」代码均在本机 venv 跑通（`scipy` 可用；`pypfopt`/`cvxpy`/`riskfolio`/`universal` 未装）。

---

## 一、核心结论（TL;DR）

1. `vbt.PortfolioOptimizer` 是**权重（allocations）生成器**，`vbt.Portfolio.from_optimizer` 把它转成**目标权重再平衡回测**（`size_type="targetpercent"`）。
2. **任务清单里假设的 `from_mean_variance` / `from_inverse_volatility` 在本版本不存在**。实际工厂族是：`from_uniform`（等权）/ `from_random`（随机）/ `from_allocations`（给定权重）/ `from_filled_allocations` / `from_initial` / `from_allocate_func`（点式自定义）/ `from_optimize_func`（区间式滚动优化）/ `from_pypfopt`（PyPortfolioOpt 均值方差）/ `from_riskfolio`（Riskfolio）/ `from_universal_algo`（在线组合）。
3. **均值方差两条路**：`from_pypfopt`（需装 PyPortfolioOpt + cvxpy，本机未装）；或 `from_optimize_func` + 自写优化函数（scipy 的 SLSQP / 解析解即可，无需额外装包）。
4. 实战（6 年合成 5 资产，月频再平衡、6M 回看窗）：**逆波动率 / 最小方差把夏普从等权的 0.70 提到 ~1.14，最大回撤从 -0.20 压到 -0.13**；而「用估计均值做的 max-Sharpe（经典均值方差）」最不稳——收益最高但**夏普掉回 0.69、回撤加深到 -0.28**（估计均值过拟合的经典症状）。

---

## 二、PortfolioOptimizer 工厂族

类在 `vectorbtpro/portfolio/pfopt/base.py`，顶层别名 `vbt.PFO == vbt.PortfolioOptimizer`。核心结构：`wrapper`（价格索引/列）+ `alloc_records`（`AllocRanges` 或 `AllocPoints` 记录）+ `allocations`（权重二维数组）。

### 2.1 两条底层原语

| 原语 | 记录类型 | 定位方式（索引器） | 模板变量 |
|------|----------|--------------------|----------|
| `from_allocate_func` | `AllocPoints`（点） | `PointIdxr`：`every` / `at_time` / `on` / `index_points` | `i`（步号）、`index_point` |
| `from_optimize_func` | `AllocRanges`（区间） | `RangeIdxr`：`every` / `lookback_period` / `split_every` / `start` / `end` / `index_ranges` | `i`、`index_start`、`index_end`、`index_slice` |

- `from_allocate_func`：在**每一个再平衡点**调用 `allocate_func`，函数不接触历史数据（权重规则无状态）。适合等权、逆波动率（只需当前截面信息时也可）、随机等。
- `from_optimize_func`：把时间轴切成**区间**，每个区间末端做一次优化（`optimize_func` 拿到的只是该区间内的数据切片），区间末 + `alloc_wait` 落仓。适合「用过去 N 个月数据算权重」的滚动再平衡（逆波动率、均值方差的标准用法）。

### 2.2 工厂清单（源码实测）

| 工厂 | 作用 | 依赖 | 本机可用 |
|------|------|------|----------|
| `from_uniform(wrapper, **kw)` | 等权（每列 `1/n_cols`） | 无 | ✅ |
| `from_random(wrapper, direction="longonly", n=None, seed=None)` | 随机权重（Numba 实现，可多空） | 无 | ✅ |
| `from_allocations(wrapper, allocations)` | 给定权重。DataFrame→按 index 当日历；Series/dict→均匀应用；ndarray→广播 | 无 | ✅ |
| `from_initial(wrapper, allocations)` | 只在第 0 根 bar 落一次仓（= `from_allocations(on=0)`） | 无 | ✅ |
| `from_filled_allocations(allocations)` | 从**已填满**的权重数组反推再平衡点 | 无 | ✅ |
| `from_allocate_func(wrapper, allocate_func, *args, ...)` | 点式自定义分配函数 | 无 | ✅ |
| `from_optimize_func(wrapper, optimize_func, *args, ...)` | 区间式滚动优化函数 | 无 | ✅ |
| `from_pypfopt(wrapper=None, **kw)` | PyPortfolioOpt（默认 Ledoit-Wolf 协方差 + 均值历史收益 + 有效前沿） | `pypfopt`+`cvxpy` | ❌ 未装 |
| `from_riskfolio(returns, wrapper=None, **kw)` | Riskfolio（均值风险 / HRP 等） | `riskfolio` | ❌ 未装 |
| `from_universal_algo(algo, S, ...)` | 在线组合（OLMAR / CRP 等） | `universal` | ❌ 未装 |

顶层还有两个快捷函数：`vbt.pypfopt_optimize`（调 PyPortfolioOpt 得权重 dict）、`vbt.riskfolio_optimize`（调 Riskfolio）。`from_pypfopt`/`from_riskfolio` 就是把这些函数套进 `from_optimize_func` 的薄封装。

### 2.3 关键机制（源码级）

**（a）optimize_func 契约**（`from_optimize_func`，非 jitted 路径）：函数被模板替换后调用，接收切片数据，返回长度 = 列数的权重（数组 / Series / dict 均可）。数据切片用 `vbt.RepEval("returns.iloc[index_slice]", context=...)` 传入：

```python
r_arg = vbt.RepEval("returns.iloc[index_slice]", context=dict(returns=returns))

def inv_vol_opt(r):                       # r = 该回看窗内的收益 DataFrame
    inv = 1.0 / r.std()
    return inv / inv.sum()                # 返回 Series/数组/dict 皆可

pfo = vbt.PortfolioOptimizer.from_optimize_func(
    close.vbt.wrapper, inv_vol_opt, r_arg,
    every="MS", lookback_period="6MS")    # 每月再平衡，用过去 6 个月数据
```

**（b）`every` + `lookback_period` 语义**：`every` = 再平衡频率；`lookback_period` = 每个再平衡点往前看多久的数据窗口。二者可参数化（`lookback_period=vbt.Param(["3MS","6MS"])` 直接网格搜索）。

**（c）`alloc_wait=1` 防未来函数（实测验证）**：区间是 `[start, end)`（end 开区间），优化只用 end 之前的数据；落仓在 `alloc_idx = end - 1 + alloc_wait = end`（默认 `alloc_wait=1`）。实测第一个区间：

```
range[0]: start=2019-02-01  end=2019-08-01  alloc_idx=2019-08-01
```
即「用 2019-02-01 ~ 2019-07-31 的收益，在 2019-08-01 收盘调仓」，**无未来函数**。若 `alloc_wait=0` 会提前一天落仓。

**（d）`rescale_to`（多空）**：把正负权重分别归一化到指定区间（如 `rescale_to=(-1, 1)`）。正权重除以其总和缩放，负权重同样，互不影响。

---

## 三、`Portfolio.from_optimizer` 再平衡机制

签名（`portfolio/base.py`）：

```python
Portfolio.from_optimizer(
    close, optimizer,
    pf_method="from_orders",   # 或 "from_signals"
    squeeze_groups=True, dropna=None, fill_value=np.nan,
    size_type="targetpercent",  # 核心：目标百分比
    direction=None,             # None 时按权重符号自动推断
    cash_sharing=True, call_seq="auto", group_by=None, **kwargs)
```

执行链（源码 6101-6154 行）：

1. `optimizer.fill_allocations(...)` 把稀疏权重**填成满长 DataFrame**（再平衡日有值，其余为 NaN）。
2. 推断 `direction`：权重含正含负 → `"both"`；全正 → `"longonly"`；全负 → `"shortonly"`（取绝对值）。
3. 走 `from_orders(close, size=size, size_type="targetpercent", ...)`（或 `from_signals(order_mode=True, accumulate=True)`）。

**为什么是「再平衡」而不是「买入持有」**：`size_type="targetpercent"` 让 `size` 被解释为「占组合总市值的目标百分比」，每到一个再平衡日，`from_orders` 会下单把各资产**拉回目标权重**（`cash_sharing=True` + `call_seq="auto"` 处理多资产同 tick 的现金与买卖顺序）。非再平衡日的 NaN 不产生订单。官方 docstring 例子清楚展示了权重在两次再平衡之间随价格漂移、再平衡日「snap back」到目标值。

---

## 四、组合优化实战

### 4.1 合成 5 资产（中心化噪声，实际统计 = 目标值，可复现）

```python
import numpy as np, pandas as pd, vectorbtpro as vbt
from scipy.optimize import minimize

vbt.settings['jitting']['backends']['auto_mode'] = False   # Rust 坑

np.random.seed(42)
n = 6 * 252
dates = pd.bdate_range("2019-01-02", periods=n)
params = {"BOND":(0.04,0.08), "GOLD":(0.06,0.14), "EQUITY":(0.11,0.20),
          "TECH":(0.19,0.32), "CRYPTO":(0.03,0.50)}          # (年化漂移, 年化波动)
returns = {}
for name, (mu, sig) in params.items():
    z = np.random.randn(n); z = (z - z.mean()) / z.std()      # 零均值单位方差
    returns[name] = mu/252 + (sig/np.sqrt(252)) * z            # 实际均值/波动 = 目标值
returns = pd.DataFrame(returns, index=dates)
close = 100 * (1 + returns).cumprod()
```

实际（==目标）年化 Sharpe：TECH 0.594 > EQUITY 0.550 > BOND 0.500 > GOLD 0.428 > **CRYPTO 0.060**。CRYPTO 是「高波动、低 Sharpe」的陷阱资产。

### 4.2 四个权重规则

```python
def uniform_opt(r):                       # 等权
    return np.full(len(r.columns), 1/len(r.columns))

def inv_vol_opt(r):                       # 逆波动率
    inv = 1.0 / r.std();  return inv / inv.sum()

def min_vol_opt(r):                       # 最小方差 (GMV)，scipy SLSQP
    cov = r.cov().values; m = cov.shape[0]
    res = minimize(lambda w: w @ cov @ w, np.full(m, 1/m), method="SLSQP",
                   bounds=[(0,1)]*m, constraints=[{"type":"eq","fun":lambda w: w.sum()-1}])
    return res.x

def max_sharpe_opt(r):                    # 经典均值方差（解析切点，long-only 截断）
    w = np.linalg.pinv(r.cov().values) @ r.mean().values
    w = np.clip(w, 0, None);  return w / w.sum()
```

统一用 `from_optimize_func(..., every="MS", lookback_period="6MS")`，保证**四个策略再平衡日期完全一致、回看窗一致、手续费一致**，唯一变量是权重规则（公平对比）：

```python
r_arg = vbt.RepEval("returns.iloc[index_slice]", context=dict(returns=returns))
for name, fn in rules.items():
    pfo = vbt.PortfolioOptimizer.from_optimize_func(close.vbt.wrapper, fn, r_arg,
                                                    every="MS", lookback_period="6MS")
    pf  = vbt.Portfolio.from_optimizer(close, pfo, init_cash=10000.0, fees=0.001, freq="1D")
```

### 4.3 实测权重与绩效（6 年，月频，fees=0.001）

| 策略 | BOND | GOLD | EQUITY | TECH | CRYPTO | 总收益 | 夏普 | 最大回撤 |
|------|------|------|--------|------|--------|--------|------|----------|
| 等权 equal_weight | 0.20 | 0.20 | 0.20 | 0.20 | 0.20 | 0.4743 | 0.699 | -0.201 |
| 逆波动率 inverse_vol | 0.419 | 0.242 | 0.167 | 0.105 | 0.067 | 0.4861 | **1.135** | **-0.129** |
| 最小方差 min_vol (GMV) | 0.539 | 0.319 | 0.074 | 0.052 | 0.016 | 0.4100 | 1.099 | -0.136 |
| 最大夏普 max_sharpe | 0.295 | 0.239 | 0.225 | 0.154 | 0.086 | **0.6049** | 0.689 | -0.278 |

（权重为 `pfo.allocations.mean()` 的时间平均；收益/夏普/回撤为 `pf.total_return` / `pf.sharpe_ratio` / `pf.max_drawdown`。）

**解读**：
- **等权 → 逆波动率**：夏普 0.70 → 1.14（≈+63%），回撤 -0.20 → -0.13。几乎全部提升来自**把 CRYPTO 从 20% 砍到 6.7%**、把 BOND 提到 42%。
- **最小方差**：比逆波动率更保守（BOND 53.9%、CRYPTO 仅 1.6%），夏普略低（1.10）但更稳；收益也低（0.41），因为过度集中在低波动低收益的 BOND。
- **max-Sharpe（经典均值方差）**：收益最高（0.60，多配了高漂移的 EQUITY/TECH），但**夏普掉回等权水平（0.69）、回撤反而最深（-0.28）**——因为 6 个月窗口估计的均值噪声极大，权重来回跳，追高被套。这是「均值方差对输入均值极度敏感」的教科书式实证：**只估计协方差（逆波动率/GMV）比连均值一起估计（max-Sharpe）稳健得多**。

> 附：本合成数据各资产零相关（独立噪声），所以风险类方法都往低波动的 BOND 集中。真实市场里资产有相关结构，GMV/风险平价会利用低相关来分散；但「风险类方法天然低配高波动资产、比等权提高风险调整收益」这一结论不变。

---

## 五、再平衡机制 + 关键坑

### 5.1 手续费影响（逆波动率，月频）

| fees | 总收益 | 夏普 | 累计手续费 |
|------|--------|------|-----------|
| 0.0   | 0.4914 | 1.145 | 0.00 |
| 0.001 | 0.4861 | 1.135 | 42.33 |
| 0.002 | 0.4808 | 1.125 | 84.42 |
| 0.005 | 0.4652 | 1.096 | 209.32 |

手续费对「目标权重再平衡」是**线性损耗**，6 年 0.5% 单边约吃掉 2.6 个点收益、0.05 个夏普。

### 5.2 再平衡频率（逆波动率，fees=0.001）

| every | 总收益 | 夏普 | 最大回撤 | 订单数 |
|-------|--------|------|----------|--------|
| 1W | 0.4995 | 1.153 | -0.130 | 1380 |
| 2W | 0.5021 | 1.159 | -0.130 | 690 |
| MS | 0.4861 | 1.135 | -0.129 | 315 |
| QS | 0.4266 | 1.034 | -0.124 | 105 |

高频小幅更优（权重不过时），但**订单数线性增长**，实际要用手续费权衡；季度再平衡明显掉队（权重过时）。

### 5.3 坑清单

1. **工厂名不对**：没有 `from_mean_variance` / `from_inverse_volatility`。均值方差走 `from_pypfopt`（要装库）或 `from_optimize_func`+自写（scipy）；逆波动率纯自写几行。
2. **`wrapper` 必传**：所有工厂第一个参数要 `close.vbt.wrapper`（不是 DataFrame）。`from_random` 尤其容易漏（第 1 路报告已提）。
3. **第三方优化库未装**：`from_pypfopt`/`from_riskfolio`/`from_universal_algo` 分别需 `pypfopt`+`cvxpy` / `riskfolio` / `universal`，本机均未装，`assert_can_import` 会直接报错。
4. **dict 分配缺列 = NaN**：`from_allocations`/`from_initial` 传 dict 时，缺的列被填 NaN（该列永远不建仓）。实测 `from_initial(wrapper, {"BOND":0.6,"GOLD":0.2,"EQUITY":0.2})` 里 TECH/CRYPTO 是 NaN。必须补全所有列，或传数组。
5. **lookback 有预热期**：`lookback_period="6MS"` 时第一个再平衡点在 ~6 个月后（现金等待）。`from_uniform`（无 lookback）从第 0 天就满仓，因此做对比时要么用同一 `from_optimize_func` 框架对齐日期，要么切片到共同起始日，否则不公平。
6. **max-Sharpe 不稳**：用估计均值做切点组合会过拟合，实盘/回测常比等权还差。优先用只依赖协方差/波动的规则（逆波动率、GMV、风险平价）。
7. **`alloc_wait` 默认 1 才防未来**：改 0 会提前一天落仓，引入轻微未来函数。
8. **多空用 `rescale_to`**：权重含负号时若 `size_type="targetpercent"` 想表达「多头 100% + 空头 100%」要 `rescale_to=(-1,1)`；`from_optimizer` 会自动把 `direction` 判成 `"both"`。
9. **指标是属性**：`pf.total_return`（属性），`pf.stats()`（方法）——同 01 报告。优化器侧 `pfo.stats()` 含 `coverage` / `overlap_coverage` / `mean_allocation` 等（源码 `_metrics`）。

---

## 六、最小可运行代码（整段，已验证）

```python
import numpy as np, pandas as pd, vectorbtpro as vbt
from scipy.optimize import minimize

vbt.settings['jitting']['backends']['auto_mode'] = False

np.random.seed(42)
n = 6 * 252
dates = pd.bdate_range("2019-01-02", periods=n)
params = {"BOND":(0.04,0.08),"GOLD":(0.06,0.14),"EQUITY":(0.11,0.20),
          "TECH":(0.19,0.32),"CRYPTO":(0.03,0.50)}
returns = {}
for name,(mu,sig) in params.items():
    z = np.random.randn(n); z = (z - z.mean())/z.std()
    returns[name] = mu/252 + (sig/np.sqrt(252))*z
returns = pd.DataFrame(returns, index=dates)
close = 100*(1+returns).cumprod()

r_arg = vbt.RepEval("returns.iloc[index_slice]", context=dict(returns=returns))

def inv_vol_opt(r):
    inv = 1.0/r.std(); return inv/inv.sum()

pfo = vbt.PortfolioOptimizer.from_optimize_func(
    close.vbt.wrapper, inv_vol_opt, r_arg, every="MS", lookback_period="6MS")
pf  = vbt.Portfolio.from_optimizer(close, pfo, init_cash=10000.0, fees=0.001, freq="1D")

print(pfo.allocations.head())      # 再平衡权重
print(pf.total_return, pf.sharpe_ratio, pf.max_drawdown)
```
