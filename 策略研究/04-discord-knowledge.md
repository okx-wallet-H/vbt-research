# 04 — Discord 社区知识挖掘（作者 Oleg Polakow 的权威用法指导）

> 来源：vectorbt.pro 官方 Discord（guild `918629562441695344`），通过 vectorbt-pro MCP 工具检索。
> 说明：消息作者在 MCP 导出中被匿名化为 `@maintainer`（= Oleg Polakow，官方唯一维护者）、`@user` / `@user_1` 等。本文区分「作者原话」与「社区用户经验」。

---

## 一、性能优化

### 要点
1. **自定义模拟器 > `from_signals`**。默认 VBT 模拟器为了通用性，代码库庞大，即使你用不到全部特性，也会拖慢执行和 LLVM 优化。作者明确建议：用 VBT 底层 API 写一个只含所需特性的简单 for-loop，就能远快于 `from_signals`。这也是他在 64M 参数组合场景下给用户的最终答案——"要严肃用 vbt 回测，建议自己写模拟器"。
2. **累加器（accumulator）不是万能药**。开发耗时，且无法为每个指标都做。多数指标可预计算，累加器的必要性通常被高估。
3. **硬件升级收益巨大**（社区验证：i9-14900K 相对旧机器有数量级提升，且比 Threadripper 便宜）；但作者本人至今用当年开发 vbt 的旧 MacBook（仅 2-4GB 空闲内存），靠代码优化弥补硬件。
4. **循环 vs 向量化**：先按列、再按行循环；numpy 函数略快于 numba 循环，但把多个操作合并进一个循环可反超 numpy。作者偏好循环。
5. **Numba 压倒 PyPy**：作者实测，100 万元素算百分比变化，PyPy 2 秒、Numba 1 毫秒（约 2000x）。PyPy 不能跑 numba / PyTorch / TensorFlow，不适用于科学计算。
6. **`takes_1d` 对速度影响极小**；最佳写法是「在循环外预分配所有数组 + 单 for-loop」。社区实测：把 pandas-ta 指标用 `IndicatorFactory` + numba 重写，从 42.5ms 降到 2.64ms（约 16x）。
7. **Numba 优化是试错**。合并多个循环有时反而更慢（作者：numba 内部优化问题难以预判，只能逐个拆/加组件定位瓶颈）。
8. **Rust 迁移是长期方向**，但作者强调"vbt 应先有成熟的高层接口"。jitting 特性允许为任意内置函数挂多种实现（numpy/numba/rust），随时切换。
9. **benchmark 要公平**：作者反驳某文章"比 vbt 快 1000x"——那只是单资产 1 年日线（约 365 点），vbt 的毫秒级固定开销（输入转换等 setup）在百万级数据点下可忽略；应按数据规模比。

### 作者关键原话
- "Building a custom simulator can greatly boost performance. The default VBT simulator is very flexible with many features, but its large codebase slows down execution and LLVM optimization... A custom simulator—just a simple for-loop calling VBT's low-level API with only needed features—can be much faster than `from_signals`."
  — https://discord.com/channels/918629562441695344/918630948248125512/1209864010652983347
- "Numpy functions are slightly faster than numba loops. You can combine many operations into a single loop, making it faster than NumPy."
  — https://discord.com/channels/918629562441695344/918630948248125512/1059181233181052948
- "To backtest seriously with vbt, I suggest writing your own simulator... You can stop the simulation early when desired, saving a lot of time."
  — https://discord.com/channels/918629562441695344/918630948248125512/1106637922800779455
- "A loop calculating percentage change on 1 million elements takes 2 seconds with PyPy and 1 millisecond with Numba—a 2000x speedup."
  — https://discord.com/channels/918629562441695344/918629563469295628/981240552378994718

---

## 二、Portfolio / Signal 最佳实践

### 要点
1. **逻辑只用 `open` 或只用 `close`，不要混用**，否则实盘会出问题（作者强烈建议）。`close` 用于分析（equity/returns/unrealized PnL），`price` 才是下单成交价——两者通常相同，但做 bid/ask 价差时应改 `price` 而非 `close`。
2. **多资产 = 多列**。每个资产一列；`close` 可以比 `price` 列少，但必须可对齐（见 fundamentals 广播文档）。hedge 模式（同时多空）用同一资产的两列反方向实现。
3. **扩展 VBT 类用继承，不要 monkey-patch**。`class Portfolio(vbt.Portfolio): ...` 后 `MyPortfolio.from_signals(...)` 会返回 `MyPortfolio` 实例（`@classmethod` 自动绑定）。
4. **`init_cash="auto"`** = 无限现金，用于绕过"下单不能超过可用现金"限制；但此时目标百分比、依赖 portfolio value 的指标会失效（value 变无穷）。
5. **融资成本**：杠杆/借贷成本用负 `cash_earning`（或负 dividend）；借入资金可用 `vbt.pf_nb.get_debt_nb(c)` 获取；需要在 `from_signals` 或 `from_order_func` 内访问，`from_orders` 不行。
6. **止损价差**：只有 stop（无常规 exit）时，成交在 stop 价，与 bid/ask 无关；要自定义 stop 成交价用 `stop_exit_price` 参数，或在 adjust/signal 函数里动态设。
7. **参数网格优先 `vbt.Param` 直传 `from_signals`**（快但更耗内存），或用 `@parameterized` 装饰器；多级参数用 `vbt.combine_params` 合并（`vbt.Param` 互不知晓对方，做 `SL < TP` 这类条件必须 `combine_params`）。
8. **多时间框架**：先 resample 数据/指标，不要每个 timeframe 各自 fetch。
9. **多核分发**：多列时 `chunked="dask"` 或换执行引擎（`vbt.utils.execution`）；整条流水线分发用 chunking 或 parameterization（见 pairs trading 教程）。

### 作者关键原话
- "Do all your logic using either `open` or `close`, not both, to avoid issues when going live."
  — https://discord.com/channels/918629562441695344/918630948248125512/1025869364035072020
- "It's better to modify the `price` argument instead of `close`, since `close` is used for analysis, while `price` is used for ordering."
  — https://discord.com/channels/918629562441695344/918630948248125512/1257718744902271076
- "It's better to use subclassing instead of adding methods this way." / "Running `MyPortfolio.from_signals` returns a `MyPortfolio` instance."
  — https://discord.com/channels/918629562441695344/918630948248125512/1065443552097738783
- "You can set `init_cash="auto"` for infinite cash, but then target percentages and metrics relying on portfolio value won't work."
  — https://discord.com/channels/918629562441695344/918630948248125512/976960270121468025

---

## 三、常见报错和坑（Top 10）

1. **`ValueError: Cannot find common levels to align indexes`** — 传入的 DataFrame 列索引不一致（如 entry 与 SL/TP stop DataFrame 列不匹配）。解法：手动对齐，或列无关时用 cross-product（`vbt.pd_acc.cross`）；有共同层级（如 "symbol"）会自动对齐。
2. **`ValueError: Could not broadcast shapes / shape mismatch`** — 广播失败。所有参数的**最终 shape 必须一致**，输入 shape 可不同（按 NumPy 广播规则）。作者反复强调：**传 pandas 对象（Series/DataFrame）而非裸 numpy 数组**，让 vbt 按 index 对齐而非按 shape。
3. **Numba `TypingError: setitem(array(float64,1d,C), int64, array(float64,2d,F))`** — 在 `adjust_func_nb`/`signal_func_nb` 里给 1D 数组的某个位置赋值了 2D 数组。维度不匹配；ChatGPT 可辅助解读 numba 报错。
4. **`NameError: name 'TP' is not defined`** — 在 `vbt.Param(..., condition="SL < TP")` 里引用其他参数。`vbt.Param` 互不知晓对方，需 `vbt.combine_params` 先合并参数。
5. **`bm_close` 广播 shape 不匹配**（`operands could not be broadcast together`）— 组合 portfolio 时 benchmark 数组 shape 不对。看 `combined_pf.wrapper.shape` 手动广播。
6. **`vbt.Param` 传列表报 shape mismatch** — 参数优化必须用 `vbt.Param([...])` 而不是裸 list。
7. **`td_stop` 传列表报错** — 止损多个值用 `vbt.Param([0.05, 0.1])`，阶梯止损用 stop laddering 特性。
8. **Pandas `Can only compare identically-labeled DataFrame objects`** — 两个 DataFrame 列标签不同却直接 `>` 比较（常发生在自己拼的信号 DataFrame），先对齐或转成统一结构。
9. **大参数网格 OOM**：`vbt.combine_params` 生成 `param_product` + `param_index`，64M 组合各占 ~500MB RAM。解法：见"高级用法"的 unlimited search / 自写 numba loop。
10. **`TypeError: plot_func must be callable` / Plotly 6 `heatmapgl` 报错** — 版本兼容问题：升级到最新 VBT（已支持 Plotly 6）；Plotly 6 下若报 heatmapgl，可在 import vectorbtpro 前设 `plotly.io.templates.default = "plotly_white"`。

---

## 四、交叉验证 / 防过拟合

### 要点
1. **Sharpe Ratio 不可靠、不预测未来**（社区资深用户反复强调）；"total return 在样本外优于 Sharpe"（经验之谈）。没有对所有资产/时间都适用的"圣杯"特征。
2. **单一 walk-forward 路径不够**。要用 Monte Carlo、CPCV（Combinatorial Purged Cross-Validation）、System Parameter Permutation 等稳健性测试组合。
3. **发现过拟合是好事**——说明你可以转向下一个想法；避免过拟合是系统化交易的核心难题，无简单解。
4. **VBT 的工具链**：`@cv_split` 装饰器（在训练集跑参数化测试、在对应测试集验证）；`splitter` 做 walk-forward；跨验证教程覆盖不同场景（是否 numba 编译决定实现差异，没有一刀切模板）。
5. **warmup 问题**：希望"一次建好含所有参数组合的 indicator 并复用"，用 `@cv_split`——回调只拿到一个参数组合，从 indicator 里取对应数据做测试。
6. **ablation study（消融）**：核心信号 + 逐层加 filter，用 `BaseAccessor.combine` 组合布尔列；filter 写成 indicator 可用 indicator expressions 定义可变逻辑。
7. **参数选择哲学**（社区）：10-15 个参数若高度相关则对冲意义小；若互不相关可能等于两边下注。没有标准答案，多个流派并存。

### 作者关键原话
- "There isn't a one-size-fits-all template in VBT... That's why the full cross-validation tutorial covers different cases."
  — https://discord.com/channels/918629562441695344/918630037626961961/1179518830935023637
- "Have you tried the `@cv_split` decorator? It lets you run parameterized tests on training sets and validates them on corresponding test sets... With `@cv_split`, only one parameter combination is passed to the callback."
  — https://discord.com/channels/918629562441695344/918630948248125512/1086690257203900477

> 注：本节多数深水区观点（Sharpe 不可靠、Monte Carlo、CPCV）来自社区资深用户，非作者本人；作者主要提供工具使用指导。

---

## 五、数据获取

### 要点
1. **Binance 全市场下载 + 本地缓存**是社区标准范式：`vbt.BinanceData.fetch(symbols=..., skip_on_error=True)`，然后用 `file_exists`/`load`/`save` 缓存到磁盘，避免重复下载。缓存文件名用 start/end/timeframe + symbols 的 md5 拼。
2. **yfinance 已开始限流/订阅化**（Yahoo 政策变化）。免费替代：Alpha Vantage（作者推荐）。巴西 B3 股票要用 `.SA` 后缀（如 `COGN3.SA`）。
3. **yfinance `on_bad_lines` 报错**：不是 vbt 问题，是 pandas 版本太旧，升级 pandas（或 yfinance）。
4. **多资产下载**：`fetch(symbols=[...], skip_on_error=True)` 跳过坏标的；delisted 会报 "possibly delisted; no timezone found"，升级 yfinance 可解。
5. **VBT 的 MCP / `quick_search` 首次运行慢**是正常的——资产（如 Discord messages）需预处理 + 缓存到磁盘，之后复用；若每次都重复，是缓存路径问题。

### 作者关键原话
- "Yahoo now has rate limiting and a subscription model" → 推荐 "Alpha Vantage" 作为免费替代。
  — https://discord.com/channels/918629562441695344/918629563469295628/1352130999185834056
- "It's not vbt related"（yfinance/pandas 报错时，作者常提醒这是上游库问题）
  — https://discord.com/channels/918629562441695344/918629995415502888/1064857994170486824

---

## 六、高级用法

### 要点
1. **自定义下单用 `order_nb`（无状态）**，不要用 `nb_buy`/`nb_sell`（它们需要 account_state 参数）。参照 `Portfolio.from_order_func` 的例子。
2. **Grid / DCA / 分批限价单**：`from_signals` 目前做不了"一次信号下多档限价单"，需用 `from_order_func` + `flex_order_func_nb`，或自定义模拟器；在 `signal_func_nb`/`order_func_nb` 里用 memory 数组追踪价位层级。gridbot 同理——作者："VBT supports all strategies, including grid trading."
3. **止损阶梯（stop laddering）**：TP/SL 阶梯已支持（`stop_ladder="uniform"` + `tp_stop=vbt.Param([[..],[..]], keys=[...])`）；但**入场没有阶梯**（entries 由用户自己管理）。
4. **超参优化**：`hyperopt` 或 `optuna` 都行，作者明确"没有魔法——写一个处理单参数组合的函数，任何优化框架都能用"。参数就是普通整数参数。
5. **百万级参数组合（unlimited search）**：避免用 `combine_params` 物化整个网格（内存爆炸），改用 numba for-loop 逐组合处理（内存低），或用 chunked + `FlexArraySlicer`（见 pairs trading 教程 "half a trillion parameter combinations" 处）。
6. **低内存取交易信息**：用 `trades[0]["entry_idx"]` / `trades[-1]["exit_idx"]` 而非 `pf.trades.readable.loc[...]`。
7. **组合 IS/OOS portfolio**：用 row stacking 而非 column stacking（cookbook/portfolio/#stacking）；需要先 rename columns / tile 对齐 group。
8. **拿分组信息**：`pf.wrapper.get_columns()` 或 `pf.wrapper.grouper.get_index()`。
9. **绘图**：`plot_trades()` 无论哪种下单方式画法一致；exit trade 合并其关联的所有 entry orders。

### 作者关键原话
- "Use only `order_nb`; it's stateless. See examples under `Portfolio.from_order_func`."
  — https://discord.com/channels/918629562441695344/918630948248125512/1049183350591586345
- "You can't do this with `from_signals` currently... use a flexible order function (`Portfolio.from_order_func` with `flex_order_func_nb`) or create a custom simulator."
  — https://discord.com/channels/918629562441695344/918630948248125512/1092270332448084009
- "There's no magic—just write a function to process a parameter combination. You can use any framework for optimization."
  — https://discord.com/channels/918629562441695344/918630948248125512/1106637922800779455
- "VBT supports all strategies, including grid trading. Track levels in `signal_func_nb` (or `order_func_nb`) and return a signal or order when a level is reached."
  — https://discord.com/channels/918629562441695344/918630948248125512/1351250498358874155

---

## 七、学习路径（作者 + 社区共识）

1. **先学 Python / pandas / numpy 再上 vbt**，作者反复强调这是硬前提："vbtpro isn't easy without them"。
2. **vbt 核心概念极简**（作者原话）："它接收多个数组 → 广播到单一 shape → 逐行（timestamp）迭代计算 → 得到 time-series 结果。有几百个这样的函数。"理解核心就能写几乎任何东西。
3. **ChatGPT 不知道 vbt**（作者明确），但对 pandas/numpy/numba 很有用，可作为结对编程。
4. **时间投入**（社区）：零基础 → 能独立做回测/优化约 6 个月全职（300-500 小时）；有交易经验者 6-12 个月练交易心理再上 bot。
5. **学习路径建议**：先学生态和方法，性能优化后置；先通读 tutorials（尤其 getting started、cross-validation），再套自己的场景。numba 可以晚点碰。
6. **IDE**：作者推荐 PyCharm（VBT 大量动态定义，PyCharm 动态分析更友好；VSCode/Pylance 仅静态分析）。
7. **"vbt 相当于一门数据科学/编程综合课程"**（作者），学到的知识是最大回报。

### 作者关键原话
- "VectorBT is simple: it takes multiple arrays, broadcasts them to a single shape, then iterates over each row (timestamp) to perform calculations, resulting in a time-series like returns. There are hundreds of such functions."
  — https://discord.com/channels/918629562441695344/918629563469295628/991624302745112697
- "For many, vbt serves as a comprehensive course in data science and programming—the knowledge gained is the greatest reward."
  — https://discord.com/channels/918629562441695344/918630948248125512/1085290452695134358

---

## 八、Discord 消息 URL 列表（便于追溯）

### 性能优化
- https://discord.com/channels/918629562441695344/918630948248125512/1209864010652983347
- https://discord.com/channels/918629562441695344/968453568703111198/1051578001034313808
- https://discord.com/channels/918629562441695344/918629563469295628/1083851960605753414
- https://discord.com/channels/918629562441695344/968453568703111198/1320335026050695200
- https://discord.com/channels/918629562441695344/918629563469295628/1134598912766902362
- https://discord.com/channels/918629562441695344/918630948248125512/1059181233181052948
- https://discord.com/channels/918629562441695344/918629563469295628/981240552378994718
- https://discord.com/channels/918629562441695344/918629563469295628/1108179185882640475
- https://discord.com/channels/918629562441695344/1212414569612443688/1212425923132129383
- https://discord.com/channels/918629562441695344/918629563469295628/1205548300120490064

### Portfolio / Signal 最佳实践
- https://discord.com/channels/918629562441695344/918630948248125512/1354472985964056700
- https://discord.com/channels/918629562441695344/918630948248125512/1065443552097738783
- https://discord.com/channels/918629562441695344/918630948248125512/976960270121468025
- https://discord.com/channels/918629562441695344/918629563469295628/1426227349728268329
- https://discord.com/channels/918629562441695344/918630948248125512/1025869364035072020
- https://discord.com/channels/918629562441695344/918630948248125512/1379929982633246730
- https://discord.com/channels/918629562441695344/918630948248125512/1257718744902271076
- https://discord.com/channels/918629562441695344/918629563469295628/1164014122376110090
- https://discord.com/channels/918629562441695344/918629563469295628/1026209896548548782
- https://discord.com/channels/918629562441695344/918630948248125512/1010130570044518401

### 常见报错和坑
- https://discord.com/channels/918629562441695344/918630948248125512/1321896198067585055
- https://discord.com/channels/918629562441695344/918630948248125512/1303026081577701377
- https://discord.com/channels/918629562441695344/918629995415502888/1113190663866040390
- https://discord.com/channels/918629562441695344/918630948248125512/1336544855568027708
- https://discord.com/channels/918629562441695344/918630948248125512/1030644269251313756
- https://discord.com/channels/918629562441695344/918630948248125512/1049699409916600330
- https://discord.com/channels/918629562441695344/918630948248125512/1026490339000848436
- https://discord.com/channels/918629562441695344/918630948248125512/1455645921151946812

### 交叉验证 / 防过拟合
- https://discord.com/channels/918629562441695344/918630037626961961/1179518830935023637
- https://discord.com/channels/918629562441695344/918630948248125512/1291066816646615130
- https://discord.com/channels/918629562441695344/918630948248125512/1372490789434949754
- https://discord.com/channels/918629562441695344/918630948248125512/1086690257203900477
- https://discord.com/channels/918629562441695344/918629563469295628/1138834020176773171
- https://discord.com/channels/918629562441695344/918629563469295628/1184079544190259240
- https://discord.com/channels/918629562441695344/962063560664563722/1132009576783429702

### 数据获取
- https://discord.com/channels/918629562441695344/918630948248125512/1359301608650965246
- https://discord.com/channels/918629562441695344/918629995415502888/1064857994170486824
- https://discord.com/channels/918629562441695344/918629563469295628/1352130999185834056
- https://discord.com/channels/918629562441695344/918630948248125512/1419780578299089047

### 高级用法
- https://discord.com/channels/918629562441695344/918630948248125512/1092270332448084009
- https://discord.com/channels/918629562441695344/918630948248125512/1173941327742902272
- https://discord.com/channels/918629562441695344/918630948248125512/1329548609443729420
- https://discord.com/channels/918629562441695344/918630948248125512/1317188136832335983
- https://discord.com/channels/918629562441695344/918630948248125512/1049183350591586345
- https://discord.com/channels/918629562441695344/918630948248125512/1351250498358874155
- https://discord.com/channels/918629562441695344/918629995415502888/1372261711972663438
- https://discord.com/channels/918629562441695344/918630948248125512/1106637922800779455
- https://discord.com/channels/918629562441695344/918630948248125512/1238510440565178449
- https://discord.com/channels/918629562441695344/918629563469295628/1363451278872543362

### 学习路径
- https://discord.com/channels/918629562441695344/918630948248125512/1132680834814591047
- https://discord.com/channels/918629562441695344/918629563469295628/991624302745112697
- https://discord.com/channels/918629562441695344/918629563469295628/1175180513498107994
- https://discord.com/channels/918629562441695344/962063560664563722/1156971120445108355
- https://discord.com/channels/918629562441695344/918630948248125512/1085290452695134358
- https://discord.com/channels/918629562441695344/918630948248125512/1075491243032850454
