# VBT Rust 后端加速实测（Numba vs Rust）

> 实测环境：Windows 11 x64 / Python 3.12.10 / vectorbtpro 2026.6.27（**editable 安装，源码在 `data/develop-src/`，即 develop 分支**）
> 被测 Rust：GitHub release 官方 `vectorbtpro_rust-2026.6.27-cp311-abi3-win_amd64.whl`
> 结论一句话：**release 版 Rust wheel 能装上、能被检测到，但与 develop 分支的 editable Python 存在版本漂移，装完后会炸掉全局 auto 模式；且实测 Rust 并非普遍更快。**

---

## 一、安装步骤（成功，但踩了两个坑 + 发现一个致命兼容问题）

### 1.1 下载（成功）

私有仓库 release 资产必须走 API 端点（`browser_download_url` 对私有仓库返回 Not Found）：

```bash
# ① 拿 asset 列表
curl -s -H "Authorization: Bearer <token>" \
  "https://api.github.com/repos/polakowo/vectorbt.pro/releases/tags/v2026.6.27" -o release.json

# ② 找到 Windows wheel 的 asset_id = 465799710
#    name = vectorbtpro_rust-2026.6.27-cp311-abi3-win_amd64.whl (9,044,265 bytes)

# ③ 走 asset API 下载（不能直接用 browser_download_url）
curl -L -s -H "Accept: application/octet-stream" -H "Authorization: Bearer <token>" \
  -o vectorbtpro_rust-2026.6.27-cp311-abi3-win_amd64.whl \
  "https://api.github.com/repos/polakowo/vectorbt.pro/releases/assets/465799710"
```

### 1.2 安装（坑 1：wheel 文件名必须合法）

```bash
.venv/Scripts/python.exe -m pip install vectorbtpro_rust-2026.6.27-cp311-abi3-win_amd64.whl
# Successfully installed vectorbtpro-rust-2026.6.27
```

**坑**：如果下载时随便起名 `rust.whl`，pip 会报 `ERROR: Invalid wheel filename (wrong number of parts): 'rust'`。pip 会校验 wheel 文件名编码（name-version-pythonTag-abiTag-platform），必须用原始文件名 `vectorbtpro_rust-2026.6.27-cp311-abi3-win_amd64.whl`。

**`cp311-abi3` 装到 Python 3.12 完全没问题**：abi3 是稳定 ABI（向前兼容），这是它能在 3.12 上装成功的原因（无需重新编译，纯二进制 wheel）。

### 1.3 验证检测（坑 2：版本检测通过 ≠ 真正可用）

```python
import vectorbtpro_rust
print(vectorbtpro_rust.__version__)          # 2026.6.27
import vectorbtpro as vbt
print(vbt.is_rust_available())               # True  ← 只做字符串相等判断
```

`is_rust_available()` 的实现（develop 分支源码）只检查**版本字符串相等**：

```python
return vectorbtpro_rust.__version__ == __version__
```

这一步返回 True 有**误导性** —— 真正的兼容性问题藏在更深的 `is_auto_eligible()` 里（见下）。

---

## 二、Rust 后端启用验证（部分成功 + 致命发现）

### 2.1 注册层面：成功

装好后，jitting 注册表里出现了 `rs` 这个 jitter：

```python
from vectorbtpro._settings import settings
print(list(settings["jitting"]["jitters"].keys()))   # ['nb', 'np', 'rs']  ← rs 出现了
```

底层 kernel 也能通过 `jit_reg.resolve(func, jitter="rs")` 正常解析并跑通（Numba CPUDispatcher vs Rust 原生函数）：

```python
from vectorbtpro.benchmarks import bench_engine
from vectorbtpro.returns.nb import sharpe_ratio_1d_nb
jit_reg = bench_engine.jit_reg
fn_nb = jit_reg.resolve(sharpe_ratio_1d_nb, jitter="nb", disable_auto_backend=True)  # CPUDispatcher
fn_rs = jit_reg.resolve(sharpe_ratio_1d_nb, jitter="rs", disable_auto_backend=True)  # <function sharpe_ratio_1d_rs>
```

### 2.2 致命发现：release wheel 与 develop editable Python 版本漂移

本机 `vectorbtpro` 是 **editable 安装、源码指向 `data/develop-src/`（develop 分支）**，虽然 `__version__` 也标 `2026.6.27`，但 develop 分支已经**前进过**了。release 的 Rust wheel 是冻结在 2026.6.27 release 点的，两边对不上。具体两个错位：

**错位 A —— `from_signals` 签名多了 2 个参数（develop Python 新增，release Rust 没有）**

develop 分支的 `from_signals_nb` / `from_basic_signals_nb` 新增了两个参数 `row_offset` 和 `init_temp_records`，而 release Rust 的 `from_signals_rs` / `from_basic_signals_rs` 还是旧签名。实测：

```python
# 简单多头回测（走 basic 快路径）
vbt.Portfolio.from_signals(..., jitted="rs")
# TypeError: from_basic_signals_rs() takes from 2 to 55 positional arguments but 57 were given

# 带止损（走完整路径）
vbt.Portfolio.from_signals(..., jitted="rs", sl_stop=0.05)
# TypeError: from_signals_rs() takes from 2 to 84 positional arguments but 86 were given
```

（两边都缺 2 个参数：Numba 版有 `row_offset` + `init_temp_records`，Rust 版没有。）

**错位 B —— develop 的 `rust.py` 后端期望 `__build_profile__`，release wheel 没有**

develop 分支 `jitting/backends/rust.py` 的 `is_auto_eligible()`：

```python
def is_auto_eligible(self) -> bool:
    if not self.is_available():      # is_available 只查版本字符串，返回 True
        return False
    import vectorbtpro_rust
    if vectorbtpro_rust.__build_profile__ == "release" or os.environ.get("VBT_RUST_DEV_AUTO") == "1":
        return True
```

release wheel 只导出 `__version__`，**没有 `__build_profile__`**（这是 develop 分支新增的 dev/release profile 检测）。结果：

```python
vbt.MA.run(r, 10)          # AttributeError: module 'vectorbtpro_rust' has no attribute '__build_profile__'
vbt.Portfolio.from_signals(...)  # 同上（默认 jitted=None 走 auto 分发时）
```

**后果：装上 release Rust wheel 后，全局 auto 模式（默认 `jitted=None`）直接崩溃** —— 包括最常用的 `vbt.MA.run`、`vbt.Portfolio.from_signals` 全都炸。而 `jitted="nb"` 显式指定 Numba 仍正常。

### 2.3 环境恢复

卸载 mismatched wheel 后 auto 模式立即恢复：

```bash
.venv/Scripts/python.exe -m pip uninstall -y vectorbtpro-rust
# is_rust_available() → False；MA.run / from_signals 的 auto 模式恢复正常
```

**正确修复路径**（本机没有 cargo/rustc/maturin，未执行）：
- develop 分支源码 `data/develop-src/rust/` 里**已有** `__build_profile__`（`src/lib.rs:46`）、`row_offset`（`from_signals.rs`）、`init_temp_records`（`core.rs`）——即 develop Rust 源码是与 develop Python 对齐的。
- 应从 develop 源码重新编译 wheel（`python -m maturin build --manifest-path data/develop-src/rust/Cargo.toml --release`），而不是装 release 的 9MB wheel。
- 或：把 Python 也换成 release 版 wheel（`vectorbtpro-2026.6.27-py3-none-any.whl`），与 release Rust wheel 对齐。

---

## 三、Numba vs Rust 实测耗时

> 底层 kernel（sharpe/sortino/rolling 等）的签名恰好没变，所以这些 benchmark 数字是有效的 apples-to-apples（develop Numba vs release Rust）。`from_signals` 因签名错位无法测 Rust。

### 3.1 单 kernel 三种规模（`bench_rust.py`，median）

| kernel | 规模 | nb (ms) | rs (ms) | 加速比 | 谁赢 |
|---|---|---|---|---|---|
| sharpe_ratio_1d | 100 | 0.001 | 0.002 | **0.26x** | Numba（Rust 慢 ~4x）|
| sharpe_ratio_1d | 10K | 0.019 | 0.016 | 1.16x | Rust |
| sharpe_ratio_1d | 1M | 2.07 | 1.45 | **1.43x** | Rust |
| rolling_mean_1d | 10K | 0.011 | 0.016 | 0.72x | Numba |
| rolling_mean_1d | 100K | 0.11 | 0.14 | 0.84x | Numba |
| rolling_mean_1d | 1M | 2.06 | 2.47 | 0.83x | Numba |

### 3.2 kernel 横扫 @ 1M float64（`bench_rust2.py`，median，12 个 kernel）

| kernel | nb (ms) | rs (ms) | 加速比 | 谁赢 |
|---|---|---|---|---|
| **rolling_std** | 8.674 | 3.893 | **2.23x** | **Rust（最大胜出）** |
| sharpe_ratio_1d | 1.887 | 1.408 | 1.34x | Rust |
| sortino_ratio_1d | 4.075 | 3.214 | 1.27x | Rust |
| ffill_1d | 1.447 | 1.384 | 1.05x | Rust（微弱）|
| total_return_1d | 1.804 | 1.815 | 0.99x | 平手 |
| max_drawdown_1d | 3.023 | 3.146 | 0.96x | 平手 |
| cumulative_returns_1d | 2.892 | 3.220 | 0.90x | Numba |
| ma (moving avg) | 2.079 | 2.363 | 0.88x | Numba |
| drawdown_1d | 2.028 | 2.241 | 0.91x | Numba |
| nanstd_1d | 1.689 | 1.839 | 0.92x | Numba |
| crossed_above_1d | 4.308 | 4.665 | 0.92x | Numba |
| rolling_mean | 2.258 | 2.729 | 0.83x | Numba |

### 3.3 关键读数

1. **Rust 并不普遍更快**。12 个 kernel 里 Rust 明确赢的只有 4 个（rolling_std 2.23x、sharpe 1.34x、sortino 1.27x、ffill 1.05x），Numba 赢 6 个（0.83~0.92x），平手 2 个。中位数加速比 ≈ 0.9~1.0x —— **整体看是打平，不是宣传的「稳定 1.3~2x」**。

2. **最大的 Rust 胜利是 `rolling_std`（2.23x），但同族的 `rolling_mean` 反而是 Numba 快（0.83x）**。说明 Rust 后端对「标准差」这类 kernel 有专门优化，但均值没做好。**逐 kernel 差异极大，不能按「模块」或「类别」套用结论，必须实测到具体函数。**

3. **tiny kernel（100 元素）Rust 慢 4 倍（0.26x）**：PyO3 桥接开销（参数转 Rust 原生类型 + 释放/重拿 GIL + 返回 NumPy）在微小输入上主导。这跟官方 benchmark 文档「rs_raw 在 tiny kernel 快 100x+」的结论**不矛盾** —— 因为 `rs_raw` 测的是剥离 Python wrapper 后的裸 Rust kernel，而我测的 `rs` 是真实调用路径（含 wrapper）。**真实调用中 Rust 的桥接开销在 <1K 元素时是负资产。**

4. **跟官方 benchmark（Apple M3 / rustc 1.94.1）数字对不上**：官方 `rs_raw vs nb` 中位数 1.3~1.6x，本机实测 `rs vs nb` 中位数 ~1.0x。原因有三：(a) 平台差异（Windows x64 vs Apple M3，LLVM 生成代码对 Numba 的竞争力不同）；(b) 我测 `rs` 含 wrapper，官方测 `rs_raw` 不含；(c) 本机是 develop 分支的 Numba（可能比 release 更优化），而 Rust 是冻结的 release。

---

## 四、结论：什么时候值得用 Rust 后端

### 4.1 先决条件（本机现状）

**当前环境不要装 release 版 Rust wheel**。develop editable Python + release Rust wheel = 版本漂移，装完炸掉全局 auto 模式（`__build_profile__` 缺失 + `from_signals` 签名错位）。要启用 Rust 必须二选一：

- 用 develop 源码重新编译 Rust wheel（`maturin build --release`，需 cargo/maturin，编译 ~9 万行 + `lto=true` 较慢），与 develop Python 对齐；
- 或 Python 退回 release wheel，与 release Rust wheel 对齐。

### 4.2 什么场景值得用 Rust（在对齐版本的前提下）

| 场景 | 建议 |
|---|---|
| **大数组（≥100K~1M）的 std/sharpe/sortino 类 kernel** | 值得。实测 rolling_std 2.23x、sharpe 1.34x、sortino 1.27x |
| **tiny / 小数组（<1K）高频调用** | 不值得。PyO3 桥接开销让 Rust 慢 4x（0.26x）|
| **rolling_mean / cumulative_returns / drawdown / crossed_above 等** | 不值得。实测 Numba 反而快 8~17% |
| **`Portfolio.from_signals` 回测主循环** | 本机无法实测（签名错位）。理论上回测是大 loop、Rust 应受益，但需先解决版本对齐；且从其他 kernel 看收益不是普适的 |
| **想「不纠结、让框架自己挑」** | 用官方 AutoBench（`jitted="ab"/"abm"`），但注意 auto 模式本身在版本错位时直接崩 |

### 4.3 三个反直觉的实测结论

1. **Rust 不是「一键全局提速」**：加速比是逐 kernel、逐平台、逐规模波动的，中位数约打平。必须对具体 kernel 实测，不能套官方 benchmark 的「1.3~2x」。
2. **Python↔Rust 桥接是有固定成本的**：小输入下 Rust 更慢；只有 kernel 计算量足够大（≥100K 元素）时 AOT + GIL 释放的优势才能覆盖桥接成本。
3. **版本对齐比「装没装」更重要**：`is_rust_available()` 只做版本字符串相等判断，掩盖了 develop/release 之间的 ABI/签名漂移。装 Rust 后要立刻跑 `vbt.MA.run` 这类 auto 路径做冒烟测试，而不是只看 `is_rust_available()==True`。

---

## 附：复现材料

- 下载/安装命令：见 §1.1/1.2（token 在 `httpsvectorbt.propvt_ff8edc14.txt` 第二行，asset_id `465799710`）
- 基准脚本：项目根 `bench_rust.py`（三规模）、`bench_rust2.py`（12 kernel 横扫 @1M）。二者依赖 `jit_reg.resolve(..., jitter="rs")`，仅在 Rust wheel 已装时可用。
- 关键源码对照：develop `data/develop-src/rust/src/lib.rs:46`（`__build_profile__`）、`.../portfolio/from_signals.rs`（`row_offset`）、`.../portfolio/core.rs`（`init_temp_records`）—— 证明 develop Rust 源码已对齐 develop Python，缺的是「从源码重新编译」这一步。
