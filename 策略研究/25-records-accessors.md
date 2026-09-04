# VectorBT PRO — Records 结构化数组 与 Accessors 访问器

> 研究方式：本地源码 `vectorbt.pro-main/vectorbtpro/`（`records/base.py`、`accessors.py`、`base/accessors.py`）+ 本地实跑验证（vectorbtpro 2026.6.27）。
> 回测前统一加 `vbt.settings['jitting']['backends']['auto_mode'] = False` 规避 Rust 坑。

---

## 一、Records 机制总览

### 1.1 定位

VBT 有两种数据表示：**矩阵（matrix）** 与 **记录（records）**。矩阵是「一列一个属性」的 2D 数组；当数据稀疏、或一个时间点要存多条信息（如同一时刻多笔订单）、或一个元素要携带异构信息时，矩阵变低效，改用**记录**。

Records = **固定 schema 的结构化 NumPy 数组**（`np.ndarray`，带 `dtype.fields`）。本质上是「每行一条记录、每列一个属性」的 DataFrame 等价物，但比 DataFrame 轻（无索引开销、纯列式字节布局、可走 Numba/Rust 零拷贝）。

`Records` 类继承链（源码 `records/base.py` 已确认）：

```
Records (Analyzable, Wrapping)
 ├─ PriceRecords → Orders
 └─ Ranges → Drawdowns / Trades(ExitTrades) → Positions
```

`Portfolio` 直接暴露的入口：
```python
pf.orders      # FSOrders（Records 子类）
pf.trades      # ExitTrades
pf.positions   # Positions
pf.drawdowns   # Drawdowns
pf.order_records   # 结构化 ndarray（等价 pf.orders.records_arr）
```

### 1.2 三层表示（核心结论）

每个 Records 对象有三种读法，**列名互不相同**（本机实跑验证）：

| 层 | 访问方式 | 返回类型 | 列名/字段 | 是否缓存 |
|----|---------|---------|----------|---------|
| 原始 DataFrame | `rec.records` | `pd.DataFrame` | **原始字段名**（`entry_idx`、`pnl`…） | ❌ 每次 `pd.DataFrame.from_records` 新建 |
| 结构化数组 | `rec.records_arr`（别名 `rec.values`） | `np.ndarray`（结构化 dtype） | **原始字段名** | ✅ 同一内部对象 |
| 可读 DataFrame | `rec.records_readable`（别名 `rec.readable`） | `pd.DataFrame` | **title 名**（`Entry Index`、`PnL`…），idx/col 已转成时间/列标签 | 每次新建 |

实跑对照（`pf.trades`）：

```python
trades.records.columns
# ['id','col','size','entry_order_id','entry_idx','entry_price','entry_fees',
#  'exit_order_id','exit_idx','exit_price','exit_fees','pnl','return',
#  'direction','status','parent_id']

trades.records_readable.columns
# ['Exit Trade Id','Column','Size','Entry Order Id','Entry Index','Avg Entry Price',
#  'Entry Fees','Exit Order Id','Exit Index','Avg Exit Price','Exit Fees',
#  'PnL','Return','Direction','Status','Position Id']
```

`records` / `records_readable` **都不是缓存属性**，每次访问都是新对象；`records_arr` 是内部 `self._records_arr` 的同一对象（`trades.records_arr is trades.records_arr` → `True`）。

另有两个辅助视图：
- `rec.recarray`：`self.values.view(np.recarray)`，支持 `ra.pnl` 属性式字段访问。
- `rec.field_names`：`list(self.values.dtype.fields.keys())`。

### 1.3 dtype 字段说明

`pf.trades.records_arr.dtype`（16 字段，`itemsize=128`，`aligned=True`，即每字段 8 字节对齐）：

| 字段 | 类型 | 含义 | readable 转换 |
|------|------|------|--------------|
| `id` | i8 | 交易 id（列内递增） | `mapping='ids'` → id 重映射 |
| `col` | i8 | 列号（0-based，对应 wrapper 列） | `mapping='columns'` → 列标签 `(14,'SYM1')` |
| `size` | f8 | 成交数量（持仓大小） | — |
| `entry_order_id` | i8 | 开仓订单 id | `'ids'` |
| `entry_idx` | i8 | **开仓行号（位置索引，非时间）** | `mapping='index'` → 实际时间戳 |
| `entry_price` | f8 | 开仓均价 | — |
| `entry_fees` | f8 | 开仓手续费 | — |
| `exit_order_id` | i8 | 平仓订单 id | `'ids'` |
| `exit_idx` | i8 | **平仓行号** | `'index'` → 时间戳 |
| `exit_price` | f8 | 平仓均价 | — |
| `exit_fees` | f8 | 平仓手续费 | — |
| `pnl` | f8 | 已实现盈亏（含费） | — |
| `return` | f8 | 收益率（相对投入） | — |
| `direction` | i8 | 方向枚举 | `TradeDirectionT(Long=0, Short=1)` |
| `status` | i8 | 状态枚举 | `TradeStatusT(Open=0, Closed=1)` |
| `parent_id` | i8 | 所属持仓 id | `'ids'` |

`pf.orders.records_arr.dtype` 字段（11 个）：`id, col, signal_idx, creation_idx, idx, size, price, fees, side, type, stop_type`。其中 `idx` 是成交行号、`creation_idx` 是订单创建行号、`signal_idx` 是触发信号行号；`side`/`type`/`stop_type` 是 `Side`/`OrderType`/`StopType` 枚举的 int。

`pf.drawdowns.records_arr` 字段（9 个）：`id, col, start_idx, valley_idx, end_idx, start_val, valley_val, end_val, status`。

`pf.positions.records_arr` 字段与 `trades` 完全相同（16 个）。

### 1.4 field_config —— 三层转换的总开关

Records 的「原始 ↔ 可读」转换完全由 `field_config` 驱动（`records/base.py`）。基类默认 config：

```python
_field_config = HybridConfig(dict(
    dtype=None,
    settings=dict(
        id=dict(name="id", title="Id", mapping="ids"),
        col=dict(name="col", title="Column", mapping="columns", as_customdata=False),
        idx=dict(name="idx", title="Index", mapping="index"),
    ),
))
```

每个字段的 `settings` 条目含义：

- `name`：结构化数组里的**真实字段名**。
- `title`：readable DataFrame 的**列名**。
- `mapping`：可选转换，四种取值：
  - `'index'` → 位置索引 → `wrapper.index[idx]` 实际时间戳（走 `get_map_field_to_index`）；
  - `'columns'` → 列号 → 列标签（走 `get_map_field_to_columns`）；
  - `'ids'` → id 重映射（保证子集/row_stack 后 id 仍连续）；
  - **枚举类型**（`TradeStatusT` / `TradeDirectionT` 等 NamedTuple 风格枚举）→ int → 字符串名。
- `as_customdata`：plotly 图的 hovertemplate 用。

`Trades`（ExitTrades）通过 `@override_field_config` 装饰器**继承并覆盖**基类 config：把基类 `idx` 改名为 `exit_idx`，新增 `start_idx`（`entry_idx`）与 `end_idx`（`exit_idx`）。实跑 dump `trades.field_config['settings']` 可见完整映射表，其中 `direction -> TradeDirectionT(Long=0, Short=1)`、`status -> TradeStatusT(Open=0, Closed=1)`。

**转换核心在 `Records.to_readable()`**：遍历 `field_names`，命中 `settings` 的字段按 `name`→`title` 改名、按 `mapping` 做值映射，再用 `pd.concat(..., axis=1)` 拼成 DataFrame。枚举映射用 `get_apply_mapping_arr`，索引映射用 `get_map_field_to_index`。

---

## 二、Records 进阶分析实战（已验证）

数据：4 符号 GBM、RSI 14 交叉、`direction='both'`，1000 行，得到 **27 笔交易**（13 long + 14 short）。

### 2.1 按持仓时长分桶统计收益

```python
arr = pf.trades.records_arr
duration = arr['exit_idx'] - arr['entry_idx']          # 行号差 = 持仓时长（1d 频率下即天数）
g = pd.DataFrame({'duration': duration, 'pnl': arr['pnl'], 'direction': arr['direction']})
d = pd.cut(duration, bins=[0, 30, 60, 100, 200, 99999],
           labels=['<30', '30-60', '60-100', '100-200', '>200'])
g.groupby(d, observed=True).agg(笔数=('pnl','size'), 平均PnL=('pnl','mean'), 总PnL=('pnl','sum'))
```

结果（本机）：

| 持仓时长 | 笔数 | 平均PnL | 总PnL |
|---------|------|---------|-------|
| <30 | 2 | +44.49 | +88.98 |
| 30-60 | 3 | +103.48 | +310.45 |
| 60-100 | 4 | +42.37 | +169.48 |
| 100-200 | 13 | +6.52 | +84.80 |
| >200 | 5 | -206.43 | -1032.16 |

**洞察**：这套 RSI 策略的亏损集中在超长持仓（>200 天），短持仓普遍盈利——持仓越久越亏。

### 2.2 按方向（多/空）统计

```python
g['dir'] = g['direction'].map({0:'long', 1:'short'})
g.groupby('dir').agg(笔数=('pnl','size'), 总PnL=('pnl','sum'),
                     平均收益率=('return','mean'), 胜率=('pnl', lambda x:(x>0).mean()))
```

| 方向 | 笔数 | 总PnL | 平均收益率 | 胜率 |
|------|------|-------|-----------|------|
| long | 13 | +102.35 | +0.0057 | 0.538 |
| short | 14 | -480.80 | -0.0238 | **0.643** |

**洞察**：做空胜率反而更高（64%），但总亏损更大——典型的「高胜率小赚、低胜率大亏」结构（short 的亏损单幅度远大于盈利单）。这正是 records 直接暴露原始数组才能一眼看出的结构。

### 2.3 找最大单笔亏损

```python
i = arr['pnl'].argmin()
# raw: entry_idx=667 exit_idx=916 pnl=-531.27
row = pf.trades.records_readable.iloc[i]
# readable: Entry Index=1971-10-30  Exit Index=1972-07-05
```

同时验证 `price.index[arr['entry_idx'][i]]` == readable 的时间戳（完全一致）。

### 2.4 交易 PnL 分布

```python
np.histogram(arr['pnl'], bins=10)   # 或 pd.Series(arr['pnl']).plot.hist()
```

4 符号下 27 笔交易直方图呈「两头重」：少数大盈 + 少数大亏 + 中间稀疏，符合趋势策略特性。

### 2.5 进阶方法（Records 原生 MapReduce）

```python
trades.map_field('pnl')          # → MappedArray，.values 平铺数组，.mean() 按列 reduce 成 Series
trades.map_field('pnl').mean()   # Series，index 为 (rsi_window, symbol) 完整列
trades.apply_mask(arr['pnl'] > 0)   # 过滤只留盈利单，返回新 Records
trades.recarray                  # np.recarray 视图，ra.pnl 属性访问
trades.regroup(...)              # 重分组后 reduce
```

`map_field` 是 Records 的 MapReduce 核心：把某字段映射成 `MappedArray`，可链式 `.mean()/.sum()/.min()/.max()` 且支持 `group_by`，全程不转矩阵、省内存（源码 `records/base.py` 文档重点强调）。

---

## 三、Accessors 机制

### 3.1 一句话

Accessor 给 Pandas 对象（`pd.Series` / `pd.DataFrame` / `pd.Index`）注册一个**额外命名空间** `.vbt`，实现 `df.vbt.xxx` 链式魔法。VBT 的所有功能入口都挂在这上面。

### 3.2 注册机制（源码 `accessors.py`）

**核心三函数**（本质都调 `register_accessor`）：

```python
register_index_accessor("vbt")     # → pd.Index.vbt
register_series_accessor("vbt")    # → pd.Series.vbt
register_dataframe_accessor("vbt") # → pd.DataFrame.vbt
```

`register_accessor(name, cls)` 返回装饰器，做四件事：
1. 若 `cls` 已有同名属性，发 warning（覆盖风险）。
2. 按 `settings.caching.use_cached_accessors` 选择 `CachedAccessor` 还是 `Accessor` 描述符，`setattr(cls, name, descriptor)`。
3. `ensure_own_accessors(cls)`：保证 `cls._accessors` 是自有集合。
4. `cls._accessors.add(name)` 登记。

**描述符分派是灵魂**（`Accessor.__get__`）：

```python
def __get__(self, obj, cls):
    if obj is None:                              # 类级访问 → 返回 accessor 类本身
        return self._accessor
    if isinstance(obj, (pd.Index, pd.Series, pd.DataFrame)):  # Pandas 对象 → accessor(obj)
        accessor_obj = self._accessor(obj)
    elif issubclass(self._accessor, type(obj)):  # accessor 本身 → replace(cls_=accessor) 换壳
        accessor_obj = obj.replace(cls_=self._accessor)
    else:                                        # 其它 accessor → 用其 wrapper+obj 重建
        accessor_obj = self._accessor(obj.wrapper, obj=obj._obj)
    return accessor_obj
```

- `CachedAccessor` 额外执行 `object.__setattr__(obj, self._name, accessor_obj)` 把结果缓存到实例上（VBT 默认 `use_cached_accessors=False`，即 `df.vbt` 每次访问都会新建 `Vbt_DFAccessor`）。
- `DirNamesMixin`（来自 `pandas.core.accessor`）负责让 `dir(df.vbt)` 列出子 accessor：`_dir_additions()` 返回 `collect_accessor_names(type(self))`，后者扫 `cls.__mro__` 里所有 `Accessor`/`CachedAccessor` 描述符名。

### 3.3 子访问器注册（`.vbt.returns` / `.vbt.signals` …）

`accessors.py` 提供四个「挂在 accessor 之上」的装饰器，本质仍是 `register_accessor(name, parent)`：

```python
register_vbt_accessor(name, parent=Vbt_Accessor)      # 通用
register_idx_vbt_accessor(name, parent=Vbt_IDXAccessor)
register_sr_vbt_accessor(name, parent=Vbt_SRAccessor) # Series 专属
register_df_vbt_accessor(name, parent=Vbt_DFAccessor) # DataFrame 专属
```

各模块在文件底部用装饰器把子 accessor 挂到 `Vbt_*Accessor` 上（本机 grep 确认）：

| 子 accessor | 注册位置 | 装饰器 |
|------------|---------|--------|
| `.vbt.returns` | `returns/accessors.py:160` (通用) + `:4224`(sr) + `:4269`(df) | `@register_vbt_accessor` / `@register_sr_vbt_accessor` / `@register_df_vbt_accessor` |
| `.vbt.signals` | `signals/accessors.py:203 / 3904 / 3931` | 同上 |
| `.vbt.ohlcv` | `ohlcv/accessors.py:121` | `@register_df_vbt_accessor` |
| `.vbt.px` | `px/accessors.py:36 / 68 / 93` | 同上 |

**继承链**（`accessors.py` 模块 docstring 明示）：

```
BaseIDXAccessor
BaseSR/DFAccessor
  → GenericSR/DFAccessor
      → SignalsSR/DFAccessor
      → ReturnsSR/DFAccessor
      → OHLCVDFAccessor
  → PXSR/DFAccessor
```

所以 `pd.Series.vbt.to_2d_array` 与 `pd.Series.vbt.returns.to_2d_array` 都可用——子 accessor 继承父 accessor 的全部方法。

顶层入口类：`Vbt_Accessor(DirNamesMixin, GenericAccessor)`、`Vbt_SRAccessor(...GenericSRAccessor)`、`Vbt_DFAccessor(...GenericDFAccessor)`、`Vbt_IDXAccessor(...BaseIDXAccessor)`；快捷方式 `pd_acc`/`sr_acc`/`df_acc`/`idx_acc`。

**调用链 `df.vbt.returns.total()` 的分派**：
1. `df.vbt` → `Vbt_DFAccessor` 实例（`register_dataframe_accessor("vbt")` 的 `Accessor.__get__`）。
2. `.returns` → 命中 `Vbt_DFAccessor` 上的 `Accessor` 描述符（`@register_df_vbt_accessor("returns")` 挂的），`__get__` 判定 `obj` 是 accessor → 走 `obj.replace(cls_=ReturnsDFAccessor)` 换壳得到 `ReturnsDFAccessor` 实例。
3. `.total()` → `ReturnsDFAccessor` 继承自 `GenericDFAccessor`/`ReturnsAccessor` 的方法。

---

## 四、自定义 accessor 最小示例（已验证）

给 DataFrame 加 `.vbt.my_indicator` 命名空间，两个方法 `rolling_zscore` / `pct_of_max`：

```python
from vectorbtpro.accessors import Vbt_DFAccessor, register_df_vbt_accessor

@register_df_vbt_accessor("my_indicator")
class MyIndicatorAccessor(Vbt_DFAccessor):
    def __init__(self, wrapper, obj=None, **kwargs):
        super().__init__(wrapper, obj=obj, **kwargs)
        self._obj = obj

    def rolling_zscore(self, window=20):
        df = self.obj
        return (df - df.rolling(window).mean()) / df.rolling(window).std()

    def pct_of_max(self):
        return self.obj / self.obj.max()

price = vbt.GBMData.fetch(["SYM1", "SYM2"], seed=1).get().iloc[:500]
z = price.vbt.my_indicator.rolling_zscore(window=20)   # DataFrame
p = price.vbt.my_indicator.pct_of_max()                # DataFrame
```

实跑结果：
- `price.vbt.my_indicator` 类型 = `MyIndicatorAccessor`；
- `Vbt_DFAccessor._accessors` = `['my_indicator', 'ohlcv', 'px', 'returns', 'signals']`（自定义项已登记）；
- `'my_indicator' in dir(price.vbt)` → `True`（`DirNamesMixin` 生效）。

**扩展性结论**：只要继承对应 `Vbt_*Accessor` 并用 `@register_*_vbt_accessor` 装饰，即可无限扩展 `.vbt.*` 命名空间，且自动出现在 `dir()` 与 IDE 补全里。写自定义指标（`.vbt.my_indicator`）不需要走 `IndicatorFactory` 那一套（那是给 `Indicator` 输出对象用的），直接 accessor 即可。

---

## 五、关键坑

1. **`entry_idx`/`exit_idx` 是「位置行号」，不是时间戳**。raw records 里 `entry_idx=667` 对应 `wrapper.index[667]`（本机验证 `price.index[667] == readable 的 Entry Index`）。要拿真实时间必须 `records_readable` 或 `trades.get_map_field_to_index('exit_idx')`。跨 DataFrame 子集（如 `iloc` 切片后）行号不再对应原价格矩阵。

2. **readable 列名是 `title`，不是字段名**。`records` 用 `entry_idx`，`records_readable` 用 `Entry Index`。用 `.map()`/`.groupby()` 处理时优先用 raw `records_arr` 的字段名（稳定、无歧义），展示时才用 readable。

3. **`records`/`records_readable` 不缓存，`records_arr` 缓存**。循环里反复访问 `trades.records` 会重复 `pd.DataFrame.from_records`（轻量但非同一对象）；要判等/复用请抓 `records_arr`。

4. **枚举 int → 名称靠 `field_config` 的 `mapping` 枚举**。`direction` 字段里 `0=Long / 1=Short`（注意：这是 `TradeDirectionT`，与 `Portfolio.from_signals` 的 `direction='longonly'/'shortonly'/'both'`（`Direction` 枚举 0/1/2）是两套！`direction='both'` 的组合会产生 0 和 1 的混合交易记录，不会出现 2）。`status` 里 `0=Open / 1=Closed`。别把两套枚举搞混。

5. **Accessor 默认不缓存**（`use_cached_accessors=False`）。`df.vbt` 每次访问新建 accessor 实例，开销极小但若在热循环里高频访问可开启缓存。

6. **自定义 accessor 的 `__init__` 要 `super().__init__(wrapper, obj=obj)` 并保存 `self._obj`**；`self.obj` 属性会做 wrapper 对齐校验，直接 `self._obj` 拿原始对象更稳。

7. **Records 索引语义 = Series 而非 DataFrame**：`records['a']` 按列取子集（返回 Records），不能改时间轴；索引转发依赖 `ArrayWrapper` 的 `group_select`。

---

## 六、一句话结论

Records 是「固定 schema 的结构化 NumPy 数组」+ `field_config` 驱动的三层视图（`records` 原始字段名 / `records_arr` 底层数组 / `records_readable` title+映射转换），字段级 `mapping`（index/columns/ids/枚举）完成 idx→时间、col→标签、int→名称的全部转换；Accessors 是「`register_accessor` + 描述符 `__get__` + `DirNamesMixin`」三位一体的 Pandas 命名空间扩展机制，`@register_*_vbt_accessor` 让 `.vbt.returns`/`.vbt.signals`/自定义 `.vbt.my_indicator` 无限可扩。吃透这两套，就能同时理解 VBT 的「数据底层（列式记录）」与「API 表层（链式魔法）」。
