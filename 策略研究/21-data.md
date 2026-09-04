# 21 — 数据获取（Data 模块）

> 研究目标：吃透 VBT 的 Data 数据层 —— 多数据源、Data 对象层级、多资产对齐、本地缓存、持久化。
> 研究方式：本地权威源码 `data/develop-src/vectorbtpro/data/`（editable install 指向，非 `vectorbt.pro-main/` 副本）+ 真实联网取数实跑验证。
> 版本：vectorbtpro 2026.6.27。**本报告所有「已验证」片段均在本机实跑通过（含 Yahoo Finance 真实联网拉取）。**

---

## 〇、重要前置说明

1. **权威源码位置是 `C:\Users\CF\Desktop\VBT研究站\data\develop-src\vectorbtpro\`**，不是 `vectorbt.pro-main/`（那是 zip 解压副本）。editable install 的 `vectorbtpro.__file__` 指向 develop-src。
2. **联网结论**：本机环境 Yahoo Finance **可直连，无需代理**。`vbt.YFData.pull` 真实拉取成功（AAPL/MSFT 均返回数据）。
3. **Rust 漂移坑只影响回测 jitting**，数据拉取/处理本身不触发 Numba/Rust 编译，无需先设 `auto_mode=False`；但同一进程要接着跑 `Portfolio` 回测前再设。

---

## 一、Data 类层级与各数据源

### 1.1 继承关系（源码 `data/base.py`）

```
Base (base 基类)
 └─ BaseDataMixin          # feature/symbol 通用接口（features/symbols/get_feature/get_symbol…）
     └─ OHLCDataMixin      # OHLC 便捷属性（open/high/low/close/volume/vwap/hlc3/ohlc4/ohlc/ohlcv）
         └─ Data(Analyzable, OHLCDataMixin, metaclass=MetaData)   # 主类
```

各数据源**不在 base.py 里**，而在 `data/custom/` 目录（每个源一个文件），按 `RemoteData`/`LocalData`/`SyntheticData` 三个基类分组：

| 分组 | 基类 | 数据源类（`vbt.` 前缀） |
|------|------|------------------------|
| 远程 | `RemoteData`(remote.py) | `YFData`(yf)、`BinanceData`(binance)、`CCXTData`(ccxt)、`TVData`(tv)、`AVData`(av)、`PolygonData`(polygon)、`AlpacaData`(alpaca)、`FinPyData`(finpy)、`NDLData`(ndl)、`BentoData`(bento) |
| 本地 | `LocalData`(local.py) | `HDFData`(hdf)、`CSVData`(csv)、`ParquetData`(parquet)、`FileData`(file)、`FeatherData`(feather)、`SQLData`(sql)、`DuckDBData`(duckdb)、`ArcticDBData`(arcticdb) |
| 合成 | `SyntheticData`(synthetic.py) | `GBMData`(gbm)、`GBMOHLCData`(gbm_ohlc)、`RandomData`(random)、`RandomOHLCData`(random_ohlc) |

- `DataSaver(DataUpdater)` 另在 `data/saver.py`/`data/updater.py`：`CSVDataSaver`、`HDFDataSaver`、`SQLDataSaver`、`DuckDBDataSaver`、`ArcticDBDataSaver`（定时增量更新+落盘）。
- `Data` 顶层用 `metaclass=MetaData` 把 `feature_config` 做成类级 Config（YFData 里配了 `Dividends`/`Stock Splits`/`Capital Gains` 的 resample 函数）。

### 1.2 Data 核心 API（`vbt.Data` 属性清单精选）

| 类别 | API |
|------|-----|
| 拉取 | `pull`（classmethod 主入口）、`download`/`fetch`（=pull 向后兼容别名）、`from_data`、`from_data_str` |
| 取数 | `get`（feature/symbol/per/as_dict）、`data`（KeyDict）、`items`、`select`/`select_keys`/`select_symbols`/`select_features` |
| OHLC | `open` `high` `low` `close` `volume` `trade_count` `vwap` `hlc3` `ohlc4` `ohlc` `ohlcv` |
| 缓存 | `key_cache_set`/`key_cache_get`/`key_cache_clear`（LMDB） |
| 持久化 | `to_csv`/`from_csv`、`to_parquet`/`from_parquet`、`to_hdf`/`from_hdf`、`to_feather`/`from_feather`、`to_sql`/`from_sql`、`to_duckdb`/`from_duckdb`、`to_arcticdb`/`from_arcticdb`、`save`/`load` |
| 对齐/拼接 | `merge`、`concat`、`column_stack`、`row_stack`、`align_index`、`align_columns`、`realign`、`resample` |
| 取向 | `feature_oriented`/`symbol_oriented`、`invert`、`to_feature_oriented`/`to_symbol_oriented` |
| 元信息 | `ndim` `shape` `shape_2d` `columns` `index` `freq` `features` `symbols` `keys` `level_name` `single_key`/`single_feature`/`single_symbol` |
| 增量 | `update`/`update_symbol`/`update_feature`、`last_index`、`delisted` |
| 分析 | `returns`/`log_returns`/`daily_returns`/`drawdowns`（OHLCDataMixin 自带） |

### 1.3 `pull` 签名要点（源码 base.py 4262 行）

```python
Data.pull(keys=None, *, keys_are_features=None, features=None, symbols=None,
          classes=None, level_name=None, tz_localize=None, tz_convert=None,
          missing_index=None, missing_columns=None, wrapper_kwargs=None,
          skip_on_error=None, silence_warnings=None, execute_kwargs=None,
          cache=None, refresh_cache=False, clear_cache=False, cache_kwargs=None,
          split_seed=True, return_raw=False, **kwargs)
```

- `keys` 默认当 **symbol**（`keys_are_features` 默认 False）。字典形式 `{symbol: fetch_kwargs}` 可给每个 symbol 单独传参。
- `execute_kwargs` 走 `vectorbtpro.utils.execution.execute`，多 symbol 逐个拉取（带进度条，可按 `execute_kwargs` 配置并行化）。
- `cache` 缓存、`tz_convert` 统一时区、`missing_index/missing_columns` 处理缺失。

---

## 二、取数实战

### 2.1 真实数据（Yahoo Finance，已验证直连成功）

```python
import vectorbtpro as vbt

data = vbt.YFData.pull(["AAPL", "MSFT"], start="2024-01-01", end="2024-01-10", timeframe="1 day")
```

实测输出：

```
type: <class 'vectorbtpro.data.custom.yf.YFData'>
features: ['Open', 'High', 'Low', 'Close', 'Volume', 'Dividends', 'Stock Splits']
symbols: ['AAPL', 'MSFT']
feature_oriented: False   symbol_oriented: True
freq: 1 days 00:00:00
index tz: America/New_York
```

**关键点**：
- `pull(keys)` 的 keys 默认是 **symbol**，所以拉多 symbol 时得到 `symbol_oriented=True` 的 Data。
- `data.data` 是 `symbol_dict`（key=symbol），每个 value 是一个含 7 个 feature 列的 DataFrame。
- **`data.close` 直接返回对齐好的宽表 DataFrame**（列=symbol，行=时间）—— 这是喂给 `Portfolio` 的标准入口。

```python
data.close                 # DataFrame(6,2)，列=['AAPL','MSFT']
data.get(feature="Close")  # 同上，DataFrame(6,2)
data.get(symbol="AAPL")    # DataFrame(6,7)，AAPL 的全部 7 个 feature
data.get()                 # tuple，7 个 DataFrame（每个 feature 一个，列=symbol）
data.get(per="symbol", as_dict=True)  # dict，key=symbol
data.ohlcv                 # 只有 OHLCV 的 Data（去掉 Dividends/Stock Splits）
```

### 2.2 `get()` 返回值规则（易踩坑）

`get()` 的返回类型取决于 `single_feature`/`single_symbol` 和 `per` 参数：
- 单 feature + 多 symbol → 单个 DataFrame
- 多 feature + 单 symbol → 单个 DataFrame
- **多 feature + 多 symbol → tuple of DataFrame**（默认 `per="feature"`，每个 feature 一个）
- 用 `feature=`/`symbol=` 指定单个时返回单个 DataFrame；`as_dict=True` 返回 dict。

### 2.3 合成数据 fallback（离线可验证核心机制）

```python
g = vbt.GBMData.fetch(["S1", "S2", "S3"], seed=1)
g.features   # [0]  ← 整数 0，不是 "Close"！
g.symbols    # ['S1', 'S2', 'S3']
g.close      # None  ← GBMData 非 OHLC，没有 close 属性
g.get()      # DataFrame(20693,3)，列=['S1','S2','S3']，index 从 1970-01-01 起（默认 start）

o = vbt.GBMOHLCData.fetch("S1", seed=2)
o.features   # ['Open', 'High', 'Low', 'Close']  ← 才有 OHLC

r = vbt.RandomData.fetch("S1", seed=3)
r.features   # [0]
```

**坑**：`GBMData`/`RandomData` 是「单特征价格数据」，feature 名是整数 `0`，没有 `close` 属性，取数用 `get()`。要合成 OHLC 用 `GBMOHLCData`/`RandomOHLCData`。

---

## 三、Data.from_data 构造（wrapper / feature / symbol 机制）

`from_data(data, columns_are_symbols=False, invert_data=False, ...)` —— **`columns_are_symbols` 是决定 DataFrame 列语义的开关**：

```python
# 单 symbol 多 feature（OHLCV DataFrame）
df = pd.DataFrame({"Open":[10,11], "High":[12,13], "Low":[9,10],
                   "Close":[11,12], "Volume":[100,110]}, index=idx)
d = vbt.Data.from_data(df)
d.symbols   # ['symbol']（默认名）
d.features  # ['Open','High','Low','Close','Volume']
d.close     # Series（单 symbol 自动 squeeze！）

# 多 symbol 单 feature（价格 DataFrame，列是股票）
df2 = pd.DataFrame({"AAPL":[180,181], "MSFT":[360,362]}, index=idx)
vbt.Data.from_data(df2)                        # 默认：列当 feature → features=['AAPL','MSFT'], symbols=['symbol']（错！）
vbt.Data.from_data(df2, columns_are_symbols=True)  # 正确：列当 symbol → symbols=['AAPL','MSFT'], features=['feature']

# 多 symbol 多 feature：用 dict {symbol: DataFrame(feature列)}
d3 = vbt.Data.from_data({"AAPL": df_aapl, "MSFT": df_msft})
d3.symbols  # ['AAPL','MSFT']
d3.features # ['Open','Close']
d3.close    # DataFrame(2,2)，列=['AAPL','MSFT']
```

**三条关键结论**：
1. **`from_data(df)` 默认把列当 feature**（`columns_are_symbols=False`），单 symbol 名为 `"symbol"`。想让列=资产必须显式 `columns_are_symbols=True`。
2. **多 symbol 多 feature 的正确构造是传 dict** `{symbol: DataFrame}`。
3. **`close`/`open` 等属性在单 symbol 时 squeeze 成 Series，多 symbol 时才返回 DataFrame**。

`from_data_str("YFData:AAPL")` 可直接解析 "类名:symbol" 字符串（默认 "symbol" 无前缀时用 YFData），实测返回 YFData 实例并联网拉取。

---

## 四、本地缓存机制（LMDB）

### 4.1 `pull(cache=True)` 的缓存

```python
import tempfile
tmp = tempfile.mkdtemp(prefix="vbt_cache_")

t0 = time.time()
d1 = vbt.YFData.pull("AAPL", start="2024-01-01", end="2024-01-15",
                     timeframe="1 day", cache=True,
                     cache_kwargs=dict(cache_dir=tmp))   # 1.41s
t0 = time.time()
d2 = vbt.YFData.pull("AAPL", start="2024-01-01", end="2024-01-15",
                     timeframe="1 day", cache=True,
                     cache_kwargs=dict(cache_dir=tmp))   # 0.00s（命中缓存）
d1.close.equals(d2.close)  # True
```

实测输出：`cache 目录内容: ['YFData.lmdb']` —— **缓存是 LMDB 数据库文件，默认文件名 `{类名}.lmdb`**。

### 4.2 缓存实现细节（源码 `key_cache_set`/`key_cache_get`）

- 底层用 `lmdbm.Lmdb`（需 `pip install lmdbm`，已装）。
- **cache key = `blake2b(__version__ + cls + key + fetch_kwargs 的 pickle 序列化, digest_size=16).hexdigest()`** —— 即「版本 + 数据源类 + symbol + 全部 fetch 参数」的哈希，参数一变缓存即失效。
- 存储的是 pickle 序列化的原始 `SymbolData`（`(DataFrame, metadata_dict)` 元组）。
- `key_cache_get` 命中返回 **tuple**（`(DataFrame, {'tz':..., 'freq':...})`），未命中返回 None。

### 4.3 关键配置

- **默认 `vbt.settings["data"]["cache"] = False`** —— 缓存默认关闭，必须显式 `cache=True`。
- `cache_dir`/`db_name` 在 `settings["data"]["cache_kwargs"]` 子配置里，可用 `cache_kwargs=dict(cache_dir=...)` 覆盖。
- `pull` 还支持 `refresh_cache`（忽略旧缓存重新拉）、`clear_cache`（先清空）。

### 4.4 第二种「本地缓存」：DataSaver 增量落盘

```python
saver = vbt.CSVDataSaver(d1, save_kwargs=dict(path_or_buf="saver.csv"))
saver.update()   # 先 data.update() 增量拉新，再 save_data() 追加写盘（mode="a", header=False）
```

- `DataSaver.update_every(...)` 可挂 `ScheduleManager` 做定时增量更新。
- **坑**：`save_kwargs` 直接透传给 `Data.to_csv`/`to_hdf`，参数名是 **`path_or_buf`**（不是 `path`）。实测 `save_kwargs=dict(path=...)` 会报 `TypeError: got an unexpected keyword argument 'path'`。
- 各 Saver 对应：`CSVDataSaver`→csv、`HDFDataSaver`→hdf（需 tables）、`SQLDataSaver`→sql、`DuckDBDataSaver`→duckdb、`ArcticDBDataSaver`→arcticdb。

---

## 五、多资产对齐与拼接

### 5.1 自动对齐（pull 内建）

`pull(多 symbol)` 会**自动把不同资产对齐到统一 DatetimeIndex**。实测 AAPL 与 MSFT 的 index 完全一致：

```
AAPL index == MSFT index: True
AAPL rows: 6  MSFT rows: 6
freq: 1 days 00:00:00
index tz: America/New_York
```

对齐由 `pull` → `from_data` → `align_data` → `align_index`/`align_columns` 完成；`missing_index`/`missing_columns` 参数指定缺失处理策略（如 `"drop"`/`"keep"`）。

### 5.2 拼接三兄弟（语义区别，易混）

| 方法 | 用途 | 实测 |
|------|------|------|
| `merge(*datas)` | **合并多个 Data 实例**（跨 symbol/feature），后者覆盖前者 | `da.merge(db)` → symbols=['AAPL','MSFT']，close DataFrame(6,2) ✅ |
| `concat(keys=None)` | **单个 Data 内部按 key 展开成宽表**（返回 KeyDict，不合并别的 Data） | `da.concat()` → feature_dict，keys=7 个 feature |
| `column_stack` | 按列并排（用于**不同 feature** 的 Data） | 两个同 feature 不同 symbol 的 Data 会报 `Concatenated columns contain duplicates` ❌ |
| `row_stack` | 时间轴拼接（不同时间区间） | `da2.row_stack(db2)` → 两段时间拼成一段 ✅ |

**结论**：多资产合并的正道是 `pull(多 symbol)` 或 `Data.merge(d1, d2)`；`concat` 是「单 Data 内部展开」，`column_stack` 是「不同 feature 并排」，别混用。

---

## 六、持久化（round-trip 无损性验证）

| 方式 | 依赖 | 是否无损 round-trip | 说明 |
|------|------|---------------------|------|
| `save`/`load` | 无（pickle） | ✅ 无损（连 YFData 类名都保留） | `d1.save("d.pkl"); vbt.Data.load("d.pkl")` → `close` 相等，type 仍是 YFData |
| `to_parquet`（**目录模式**）+ `ParquetData.pull` | pyarrow | ✅ 无损 | 每个 key 一个文件 `AAPL.parquet`/`MSFT.parquet`，读回 symbols/features/close 全等 |
| `to_parquet`（**单文件**）+ `from_parquet` | pyarrow | ❌ 丢失 symbol 名 | 实测读回 `symbols=['d1']`（用文件名当 symbol），close 不相等 |
| `to_csv` | 无 | ⚠️ 仅导出 | 多 symbol 会写成多文件/宽表，非 Data 语义 |
| `to_hdf` | **tables（PyTables）** | — | 未装，报 `Please install tables` |
| `to_feather` | pyarrow | — | 需 pyarrow |

**关键结论**：
1. **`save`/`load`（pickle）是唯一零依赖、完全无损的 Data 序列化方式**，跨会话恢复回测数据首选。
2. **`to_parquet` 必须用目录模式**（`path_or_buf` 传目录，每个 key 落一个文件），配 `vbt.ParquetData.pull(目录)` 读回才无损；单文件模式会丢 symbol 语义。
3. 依赖缺口：`to_hdf` 需 `tables`、`to_parquet`/`to_feather` 需 `pyarrow`（本机 pyarrow 已补装 25.0.1，tables 未装）。

---

## 七、关键坑汇总

1. **联网/代理**：本机 Yahoo Finance 直连成功，无需代理。若换网络失败，`skip_on_error=True` 可跳过坏 symbol；或退用合成数据（`GBMData`/`GBMOHLCData`）验证机制。
2. **依赖包**：`yfinance`（YFData 必需）、`lmdbm`（缓存必需）、`pyarrow`（parquet/feather）、`tables`（hdf）都要单独装，VBT 不自动带。
3. **symbol 命名**：YFData 用 Yahoo 命名（`AAPL`/`MSFT`/`BTC-USD`/`^GSPC`）；Binance 用 `BTCUSDT` 等；CCXT 用交易所标准对。`timeframe` 支持 `"1 day"`/`"daily"`/`"15 minutes"`；`start`/`end` 支持 `"2024-01-01"`/`"1 year ago"`/`"now"`。
4. **时区**：YFData 自动 `tz_localize` 到行情原生时区（美股 `America/New_York`），多资产若时区不一致，`pull` 会 warn 并统一到 UTC；可用 `tz_convert="UTC"` 显式统一。
5. **feature vs symbol 取向**：`pull(keys)` 的 keys 默认是 symbol；`from_data(df)` 的列默认是 feature（要列=symbol 必须 `columns_are_symbols=True` 或传 dict）。
6. **`close` 属性 squeeze**：单 symbol 返回 Series、多 symbol 返回 DataFrame，写通用代码注意类型判断。
7. **`GBMData` 非 OHLC**：feature 是整数 `0`，`close=None`，取数用 `get()`。
8. **缓存默认关闭**：`settings["data"]["cache"]=False`，需显式 `cache=True`。
9. **DataSaver 参数名**：`save_kwargs` 用 `path_or_buf`，不是 `path`。
10. **持久化无损性**：只有 `save`/`load` 和 `to_parquet(目录)+ParquetData.pull` 是无损 round-trip；`to_csv`/单文件 parquet 会丢 symbol 语义。

---

## 八、最小可用链路（实盘回测第一步）

```python
import vectorbtpro as vbt
# 真实数据（联网）：拉多资产对齐好的 close
data = vbt.YFData.pull(["AAPL", "MSFT"], start="2023-01-01", end="2024-01-01",
                       timeframe="1 day", tz_convert="UTC")
price = data.close          # DataFrame(约250, 2)，列=AAPL/MSFT

# 本地缓存（离线复跑）：LMDB 命中不重新下载
data2 = vbt.YFData.pull(["AAPL", "MSFT"], start="2023-01-01", end="2024-01-01",
                        timeframe="1 day", cache=True,
                        cache_kwargs=dict(cache_dir="./vbt_cache"))

# 持久化（跨会话无损）
data.save("./data.pkl")                    # 或 data.to_parquet("./pq/") 目录模式
restored = vbt.Data.load("./data.pkl")     # 或 vbt.ParquetData.pull("./pq/")

# 合成数据 fallback（离线验证回测机制）
price_syn = vbt.GBMData.fetch(["S1", "S2"], seed=1).get()  # DataFrame
```
