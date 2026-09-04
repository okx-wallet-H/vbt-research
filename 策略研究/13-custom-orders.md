# 13 — 自定义下单：from_signals / from_orders / from_order_func 三层递进与 grid/DCA 实战

> 研究方式：本地源码 `data/develop-src/vectorbtpro/`（版本 `2026.6.27`，numba 启用）+ 本地 Python 实跑验证。
> 所有标注「已验证」的代码片段均在本机 `vectorbtpro 2026.6.27` 下实际跑通，输出为真实结果。
>
> ⚠️ **环境坑（本机必读）**：editable 的 `vectorbtpro` 指向 `data/develop-src/`，而 site-packages 里的 `vectorbtpro_rust` 是 release 构建、**没有 `__build_profile__` 属性**，会触发 `AttributeError: module 'vectorbtpro_rust' has no attribute '__build_profile__'`。跑任何回测前先加三行强制走 numba 后端：
>
> ```python
> import vectorbtpro_rust
> vectorbtpro_rust.__build_profile__ = "dev"   # 令 rust 后端 auto-eligibility 判 False，回落 numba
> import vectorbtpro as vbt
> ```

---

## 一、三层对比（核心结论）

VBT PRO 从「信号」到「订单」有三条路径，抽象层级从高到低：

| | `from_signals` | `from_orders` | `from_order_func` |
|---|---|---|---|
| 输入 | 布尔数组 `entries`/`exits` | 显式订单数组 `size`/`price`/`direction`… | 自定义 Numba 函数 `order_func_nb` |
| 抽象层级 | 最高（信号→自动成单） | 中（一次一条显式订单） | 最低（逐 bar 逐列回调，完全可控） |
| 一信号多档限价单 | ❌ 做不到 | ⚠️ 需预生成 size/price 数组 | ✅ `order_func_nb` + 状态数组 |
| 状态/记忆 | 受限于内置逻辑 | 无（纯数组） | ✅ `pre_sim_func_nb` 分配状态数组逐层传下 |
| 订单字段 | 由信号+size/price 合成 | 18 个 `Order` 字段（见下） | 18 个 `Order` 字段（`order_nb` 构造） |
| 订单记录 dtype | `fs_order_dt`（含 `type`/`stop_type`） | `order_dt`（无 `type`） | `order_dt`（无 `type`） |
| 适用场景 | 金叉死叉、RSI 等布尔策略 | 固定再平衡、已知订单序列 | **grid / DCA / 分批限价单** |

**关键区别一句话**：`from_signals` 是把「真值信号」翻译成订单；`from_orders` 是你直接给订单；`from_order_func` 是你写一个函数在**每个 (bar, 列) 交叉点**决定下什么单，VBT 只负责执行。

### 1.1 订单的 18 个字段（`Order` namedtuple）

订单对象由 `vectorbtpro.portfolio.enums.Order` 定义（`vbt.pf_enums.Order`），字段即「订单数组字段」：

```
size, price, size_type, direction, fees, fixed_fees, slippage,
min_size, max_size, size_granularity, cash_limit, leverage, leverage_mode,
reject_prob, price_area_vio_mode, allow_partial, raise_reject, log
```

关键字段语义（摘自源码 docstring，已验证枚举值）：

| 字段 | 默认值 | 含义 |
|---|---|---|
| `size` | `np.inf` | 下单数量。`np.inf`=用尽可用现金买入；`-np.inf`=卖光自由现金（direction 非 Both 时=平仓）；`np.nan`/`0`=跳过 |
| `size_type` | `SizeType.Amount`(0) | 见 `SizeType`：`Amount`(0)/`Value`(1)/`Percent`(2)/`TargetAmount`(6)/`TargetValue`(7)/`TargetPercent`(8)… |
| `direction` | `Direction.Both`(2) | `LongOnly`(0)/`ShortOnly`(1)/`Both`(2) |
| `price` | `np.inf` | 单价。`-np.inf`=当前 open；`np.inf`=当前 close（`PriceType` 还支持 `NextOpen`/`NextClose`/`NextValidOpen`/`NextValidClose`） |
| `fees` / `fixed_fees` / `slippage` | `0` | 手续费（比例）/固定费/滑点（比例，0.01=1%） |
| `leverage` / `leverage_mode` | `1.0` / `Lazy` | 杠杆倍数 / 模式 |
| `allow_partial` | `True` | 是否允许部分成交 |
| `price_area_vio_mode` | `Ignore`(0) | 成交价越出 bar 的 OHLC 区间时的处理：`Ignore`(0)/`Cap`(1)/`Error`(2) |
| `raise_reject` | `False` | 拒单时是否抛异常终止 |

### 1.2 ⚠️ 任务清单假设的纠正：没有 `type`/`limit_price`/`stop_price` 字段

任务清单里说「订单数组字段（size/price/type/limit_price/stop_price 等）」，**这与 PRO 实际不符**（已用 `inspect.signature` 验证）：

```python
sig = inspect.signature(vbt.Portfolio.from_orders)
'type' in sig.parameters        # False
'limit_price' in sig.parameters # False
'stop_price' in sig.parameters  # False
```

- `Order` namedtuple **没有 `type` 字段**；`from_orders`/`from_order_func` **没有 `limit_price`/`stop_price` 参数**。
- `OrderType`（`Market=0`/`Limit=1`）和 `StopType`（SL/TP/TSL/TD）枚举**只出现在 `from_signals` 的订单记录**里：`from_signals` 用 `fs_order_dt`（含 `type`/`stop_type` 字段，因为 `tp_stop`/`sl_stop` 会生成 limit 类订单）；`from_orders`/`from_order_func` 用 `order_dt`，字段只有 `id/col/idx/size/price/fees/side`，**没有 `type`**。
- 换句话说：**PRO 没有「挂一笔静态限价单让它自然成交」的原生机制**。限价单由你自己在 `order_func_nb` 里判断「价格是否触及价位」后返回订单，成交价 = 你设的 `price`。这正是作者在 Discord 说的："Track levels in order_func_nb and return a signal or order when a level is reached."

### 1.3 两条主线的记录 dtype（已验证）

```python
vbt.pf_enums.order_dt     # from_orders / from_order_func：id col idx size price fees side
vbt.pf_enums.fs_order_dt  # from_signals：id col signal_idx creation_idx idx size price fees side type stop_type
```

`pf.orders.records` 返回的是可读 DataFrame，默认列 `['id','col','idx','size','price','fees','side']`；完整结构化数组用 `pf.order_records`（或 `pf.orders.records_arr`）。

---

## 二、最小可运行示例（三层各一条）

### 2.1 from_signals（布尔→自动订单）

```python
import vectorbtpro as vbt

price = vbt.GBMData.fetch(["SYM"], seed=1).get().iloc[:800]
rsi = vbt.RSI.run(price, window=14)
entries = rsi.rsi.vbt.crossed_below(30)
exits = rsi.rsi.vbt.crossed_above(70)
pf = vbt.Portfolio.from_signals(price, entries, exits, init_cash=1000., fees=0.001, freq="1d")
```

### 2.2 from_orders（显式 size 数组）【已验证】

```python
import pandas as pd, vectorbtpro as vbt

close = pd.Series([1.0, 2, 3, 4, 5])
pf = vbt.Portfolio.from_orders(close, 10)   # 每根 bar 买 10 单位
print(pf.assets.tolist())  # [10.0, 20.0, 30.0, 40.0, 40.0]
print(pf.cash.tolist())    # [90.0, 70.0, 40.0, 0.0, 0.0]
```

`size`/`price`/`direction`/`size_type` 等全部可广播（按列=资产、按行=时间，或标量）。`size_type="targetpercent"` 可做目标权重再平衡（sell 先于 buy 时用 `call_seq="auto"`）。

### 2.3 from_order_func（自定义函数，最简）【已验证】

```python
from numba import njit
import pandas as pd, vectorbtpro as vbt

@njit
def order_func_nb(c, size):
    return vbt.pf_nb.order_nb(size=size)

close = pd.Series([1.0, 2, 3, 4, 5])
pf = vbt.Portfolio.from_order_func(close, order_func_nb=order_func_nb, order_args=(10,))
# 结果与 from_orders(close, 10) 完全一致
```

`order_func_nb(c, *order_args)` 第一个参数是 `OrderContext c`（`vbt.pf_enums.OrderContext`），后续是 `order_args`。返回一个 `Order`（用 `order_nb` 构造）或 `vbt.pf_enums.NoOrder`（本 bar 不下单）。

`c` 上能读到的关键字段（供决策）：

```python
c.i, c.col              # 当前行/列
c.close, c.open, c.high, c.low   # 当前 bar 价格（用 select_nb 取值）
c.position_now, c.cash_now, c.value_now   # 当前持仓/现金/组合价值
c.last_position, c.last_cash         # 每列最新状态数组
```

取值统一用 `vbt.pf_nb.select_nb(c, arr)`（按 `c.i`/`c.col` 从 flex 2D 数组取标量）或 `vbt.pf_nb.select_from_col_nb(c, col, arr)`（指定列）。

---

## 三、无状态 `order_nb` vs 有状态 `buy_nb`/`sell_nb`

### 3.1 结论（源码级已验证）

社区关键建议「自定义下单用无状态 `order_nb`，不要用 `nb_buy`/`nb_sell`」在 PRO 里的准确含义是：

| 函数 | 签名 | 有无状态 | 谁用 |
|---|---|---|---|
| `order_nb(size, price, size_type, direction, …)` → `Order` | 纯构造 | **无状态** | **用户在 `order_func_nb` 里调用** |
| `close_position_nb(…)` → `Order` | 纯构造（`size=0, size_type=TargetAmount`） | **无状态** | 用户在 `order_func_nb` 里调用 |
| `buy_nb(account_state, size, price, …)` → `(OrderResult, AccountState)` | 执行+状态转移 | **有状态** | VBT 内部执行路径 |
| `sell_nb(account_state, …)` → `(OrderResult, AccountState)` | 执行+状态转移 | **有状态** | VBT 内部执行路径 |
| `execute_order_nb(exec_state, order, …)` → `(OrderResult, ExecState)` | 执行+状态转移 | **有状态** | VBT 内部执行路径 |
| `process_order_nb(group, col, i, exec_state, order, …)` | 执行+写记录 | **有状态** | VBT 内部执行路径 |

### 3.2 为什么必须无状态

- `order_nb(...)` 只是**构造一个 `Order` namedtuple**，输入确定输出确定，不读不改任何账户状态，无副作用。
- `buy_nb`/`sell_nb` 需要你**手动传入 `AccountState`（cash/position/debt/locked_cash/free_cash）并自己接住返回的新状态**，若在 `order_func_nb` 里直接调用，会跟 VBT 模拟器自身维护的状态机**打架**（double-count 持仓/现金）。
- 正确的分工：**你的 `order_func_nb` 只"声明意图"（返回 `Order`），VBT 的 `process_order_nb`/`execute_order_nb` 去"执行并推进状态"**。你永远不需要碰 `AccountState`。

**「状态」需要跨 bar 记忆时怎么办？** —— 用 `pre_sim_func_nb` 分配一个可变数组（memory array），沿回调栈传下去，在 `order_func_nb` 里原地读写。这才是 grid/DCA 的正确状态机（见第四节），而不是去用有状态的 `buy_nb`。

---

## 四、grid trading 实战（两种实现，均已验证）

### 4.1 方案 A：目标仓位阶梯（无状态，最简单）【已验证】

**核心思想**：grid 本质是「目标持仓量 = 价格的分段阶梯函数」。跌一档目标仓位 +1（VBT 自动买 delta），涨一档目标仓位 -1（VBT 自动卖 delta）。用 `size_type=TargetAmount` 表达目标仓位，**无需任何状态数组**。

```python
import warnings; warnings.filterwarnings("ignore")
import vectorbtpro_rust; vectorbtpro_rust.__build_profile__ = "dev"
import vectorbtpro as vbt
import numpy as np, pandas as pd
from numba import njit

# 震荡行情（围绕 100 正弦波动，振幅 ~6.5）
np.random.seed(7)
n = 200; t = np.arange(n)
close = pd.Series(100 + 6 * np.sin(t / 25.0) + np.random.normal(0, 0.5, n))

sell_levels = np.array([102.0, 104.0, 106.0])  # 涨破 → 减仓
buy_levels  = np.array([94.0, 96.0, 98.0])      # 跌破 → 加仓
BASE = 3.0

@njit
def grid_order_func_nb(c, sell_levels, buy_levels, base):
    price_now = vbt.pf_nb.select_nb(c, c.close)
    n_sell = 0
    for lv in sell_levels:
        if price_now >= lv: n_sell += 1
    n_buy = 0
    for lv in buy_levels:
        if price_now <= lv: n_buy += 1
    target = base - n_sell + n_buy
    return vbt.pf_nb.order_nb(
        size=target, size_type=vbt.pf_enums.SizeType.TargetAmount,
        direction=vbt.pf_enums.Direction.LongOnly, fees=0.001,
    )

pf = vbt.Portfolio.from_order_func(
    close, order_func_nb=grid_order_func_nb,
    order_args=(sell_levels, buy_levels, BASE),
    init_cash=1000.0, freq="1d",
)
```

**真实输出（已验证）**：

```
期末资金: 1029.87    总收益: 0.0299    订单数: 60    交易数: 31
```

订单记录节选（`side 0=买 1=卖`，1 手一格交替买卖）：

```
   idx  size       price  side
0    0   3.0  100.845263     0   # 首单建底仓 3 手
1    8   1.0  102.396228     1   # 涨破 102 → 卖 1
2   18   1.0  104.093538     1   # 涨破 104 → 卖 1
3   19   1.0  103.370266     0   # 跌回 102 下方 → 买回 1
...
```

**优点**：无状态、代码最短、天然防重仓（目标仓位是价格的函数）。**缺点**：成交价是收盘价（相当于「收盘时按目标仓位市价撮合」），不是严格意义上的限价成交。

### 4.2 方案 B：OHLC 触及式限价单（有状态，最贴近实盘 grid）【已验证】

**核心思想**：在 `order_func_nb` 里读 `c.high`/`c.low`，只有当 bar 的 low 触及买档、high 触及卖档时才返回订单，`price=档位价`（限价成交）。用 `pre_sim_func_nb` 分配一个 `grid_idx` 状态数组追踪当前档位深度。

```python
@njit
def pre_sim_func_nb(c):
    grid_idx = np.zeros(1, dtype=np.int64)   # 当前额外多单档位深度 0..3
    return (grid_idx,)

@njit
def grid_limit_order_func_nb(c, grid_idx, buy_levels, sell_levels, base):
    if c.i == 0:                             # 首 bar 建底仓
        return vbt.pf_nb.order_nb(size=base, size_type=vbt.pf_enums.SizeType.Amount,
                                  direction=vbt.pf_enums.Direction.LongOnly, fees=0.001)
    low_now  = vbt.pf_nb.select_nb(c, c.low)
    high_now = vbt.pf_nb.select_nb(c, c.high)
    gi = grid_idx[0]
    if gi < buy_levels.shape[0] and low_now <= buy_levels[gi]:      # 跌到下一买档
        grid_idx[0] = gi + 1
        return vbt.pf_nb.order_nb(size=1.0, size_type=vbt.pf_enums.SizeType.Amount,
                                  direction=vbt.pf_enums.Direction.LongOnly,
                                  price=buy_levels[gi], fees=0.001) # 限价 = 买档价
    if gi > 0 and high_now >= sell_levels[gi - 1]:                  # 涨到上一卖档
        grid_idx[0] = gi - 1
        return vbt.pf_nb.order_nb(size=-1.0, size_type=vbt.pf_enums.SizeType.Amount,
                                  direction=vbt.pf_enums.Direction.LongOnly,
                                  price=sell_levels[gi - 1], fees=0.001)
    return vbt.pf_enums.NoOrder

pf = vbt.Portfolio.from_order_func(
    close, order_func_nb=grid_limit_order_func_nb,
    order_args=(buy_levels, sell_levels, BASE),
    pre_sim_func_nb=pre_sim_func_nb,
    open=open_, high=high_, low=low_,        # 必须传 OHLC
    init_cash=1000.0, freq="1d",
)
```

**真实输出（已验证）**——注意 `price` 精确落在档位价 98/96/94/106/104/102：

```
期末资金: 1040.64    总收益: 0.0406    订单数: 7    交易数: 4

   idx  size     price  side
0    0   3.0  100.67621     0   # 底仓
1   83   1.0   98.00000     0   # 跌到 98 → 买
2   93   1.0   96.00000     0   # 跌到 96 → 买
3  103   1.0   94.00000     0   # 跌到 94 → 买
4  181   1.0  106.00000     1   # 涨到 106 → 卖
5  182   1.0  104.00000     1
6  183   1.0  102.00000     1
```

**优点**：成交价精确 = 档位价，最贴近真实网格限价单。**缺点**：需手动维护状态数组 + 传 OHLC。

### 4.3 关键坑（grid 篇）

1. **没有原生限价单**：不要期待 `from_order_func` 有 `limit_price` 参数。限价单 = 你自己在 `order_func_nb` 里判断 `low/high` 触及 + 设 `price`。
2. **`price_area_vio_mode="error"` 会直接抛异常终止**（已实测：`price=95` 而首根 bar `low=99` 时报 `ValueError: Adjusted order price is below the low price`）。它不是「未触及就跳过」，而是「越界就报错」。要「未触及跳过」，必须在 `order_func_nb` 里自己判断，别依赖 `price_area_vio_mode`。
3. **跳空多档的成交假设**：方案 B 里若一根 bar 跳空穿过多个档位，每个档位都会在当根/紧邻 bar 按档位价成交（乐观假设，等价于 `LimitOrderPrice.HardLimit`）。更保守的做法是成交价用 close 或 open（方案 A 就是 close 口径）。
4. **`from_order_func` 没有顶层 `fees` 参数**（已实测报 `Portfolio doesn't expect arguments ['fees']`）——手续费必须在 `order_nb(fees=...)` 里逐单设置。`from_orders`/`from_signals` 才有顶层 `fees`。

---

## 五、DCA 分批建仓实战（含"关全部仓位"的正确写法）【已验证】

### 5.1 分批建仓 + 正确全平

```python
@njit
def dca_order_func_nb(c, invest, every, exit_bar):
    if c.i == exit_bar:
        # 全平：close_position_nb 内部 = size=0, size_type=TargetAmount
        return vbt.pf_nb.close_position_nb(fees=0.001)
    if c.i < exit_bar and c.i % every == 0:
        # 每 every 根 bar 投入固定金额（SizeType.Value = 金额口径）
        return vbt.pf_nb.order_nb(
            size=invest, size_type=vbt.pf_enums.SizeType.Value,
            direction=vbt.pf_enums.Direction.LongOnly, fees=0.001,
        )
    return vbt.pf_enums.NoOrder

pf = vbt.Portfolio.from_order_func(
    close, order_func_nb=dca_order_func_nb, order_args=(100.0, 10, 140),
    init_cash=10000.0, freq="1d",
)
```

**真实输出（已验证）**——13 笔买入（每 10 根投 100 美元）+ 1 笔卖出（第 140 根全平 16.23 手）：

```
建仓笔数(买): 13    平仓笔数(卖): 1
最终持仓: 0.0        最终资金: 9851.14
持仓曲线(每 20 根): 0 → 1.95 → 3.93 → 6.14 → 8.78 → 11.77 → 14.80 → 0
最后一条卖出订单: idx=140, size=16.23, side=1   # 一次性卖光全部累计仓位
```

`SizeType.Value` 语义已验证：价格越低，同样 100 美元买到的数量越多（下单量从 0.98 手一路涨到 1.53 手）——这就是 DCA 摊低成本的效果。

### 5.2 坑：固定负 size 只关一部分

社区提到的坑「exits 只关第一笔交易的 size，要关全部仓位得显式处理 size」。在 `from_order_func` 里的等价复现（已实测）：

```python
# 错误写法：第 140 根固定卖 10 单位
return vbt.pf_nb.order_nb(size=-10.0, size_type=vbt.pf_enums.SizeType.Amount,
                          direction=vbt.pf_enums.Direction.LongOnly, fees=0.001)
```

**结果对比（已验证）**：

| 写法 | 最终持仓 | 说明 |
|---|---|---|
| `close_position_nb()`（= `size=0, size_type=TargetAmount`） | **0.0** ✅ | 全部平掉 |
| `order_nb(size=-10.0, size_type=Amount)` | **6.23** ❌ | 只卖 10 手，剩 6.23 手没平 |

**正确关全部仓位的三种等价写法**（任选其一）：

```python
vbt.pf_nb.close_position_nb(fees=0.001)                       # ① 专用助手（最语义化）
vbt.pf_nb.order_nb(size=0.0, size_type=vbt.pf_enums.SizeType.TargetAmount)  # ② 目标仓位=0
vbt.pf_nb.order_nb(size=-np.inf)                              # ③ size=-inf（direction=Both/LongOnly 时卖光）
```

> 根因：`SizeType.Amount` 的 size 是「下单数量」，`-10` 就是卖 10 手；而 `TargetAmount` 的 size 是「目标持仓数量」，`0` 表示平到 0，VBT 自动算 delta（卖出现有全部）。**「关全部」必须用目标口径或 `-inf`，绝不能用固定负数。**

---

## 六、进阶要点备忘

1. **回调栈与参数传递**：`pre_sim_func_nb` 的返回值（元组）会被默认回调一路转发，最终追加到 `order_func_nb` 的 `c` 之后、`order_args` 之前。所以状态数组的标准写法是 `pre_sim_func_nb` 分配 → 自动下传 → `order_func_nb(c, 状态数组, *order_args)`。
2. **`vbt.Rep` 模板 + `broadcast_named_args`**：当 `order_args` 里的参数需要「先广播再传给回调」时（如多资产、参数网格），用 `vbt.Rep('name')` 占位 + `broadcast_named_args=dict(name=...)`，这是官方等权再平衡示例的标准写法（见 `base.py` 的 `from_order_func` 文档示例）。
3. **`flex_order_func_nb`**：`order_func_nb` 一次返回一个订单；`flex_order_func_nb` 返回 `(col, order)` 元组，可在同组内**任意指定列**下单（`-1` 表示本 segment 结束）。多列网格、跨资产配对时用它。
4. **`row_wise=True`**：默认按列/组迭代；设 `row_wise=True` 改成按行迭代（回调换成 `pre_row_func_nb`/`post_row_func_nb`）。
5. **成交价口径**（Discord 作者反复强调）：逻辑只用 `close` 或只用 `open`，别混用；`close` 用于估值分析，`price` 才是下单成交价。grid 方案 A 全用 close，方案 B 用 low/high 判断 + 档位价成交，都保持一致口径。
6. **性能**：`from_order_func` 里的 `order_func_nb` 是 numba 编译的纯循环，网格/参数扫描都很快；若追求极限性能，作者建议「只保留所需特性的自定义 for-loop 模拟器」，但 `from_order_func` 已足够覆盖绝大多数 grid/DCA 需求。

---

## 七、结论（TL;DR）

- 三层抽象：`from_signals`（布尔信号）→ `from_orders`（显式订单数组）→ `from_order_func`（自定义下单函数）。grid/DCA 必须走 `from_order_func`。
- 订单只有 18 个 `Order` 字段，**没有 `type`/`limit_price`/`stop_price`**；`type`/`stop_type` 只存在于 `from_signals` 的记录里。PRO 没有原生限价单，限价 = 自己在 `order_func_nb` 判断触及 + 设 `price`。
- `order_nb` 无状态（纯构造 `Order`），`buy_nb`/`sell_nb`/`execute_order_nb` 有状态（内部执行用）；用户只写 `order_nb`。
- grid 两种打法：无状态目标仓位阶梯（`TargetAmount`，最简单），或有状态 OHLC 触及式限价单（`pre_sim_func_nb` 分配状态数组 + 读 `c.high`/`c.low`）。
- DCA 用 `SizeType.Value` 分批投入；「关全部仓位」用 `close_position_nb()`（=`size=0, size_type=TargetAmount`），**不能用固定负 size**（那只关固定数量）。
