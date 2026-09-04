# 62 — OKX 官方 API 落地 + 总部 exec 切换方案

> api-researcher · 任务 #4 · 2026-09-04
> 一句话：**官方 API 已有一条能跑的路（`backtest/trade_bot.py` 直连 OKX REST + 交易 key + attached TP/SL），切换不是从零；先修 posMode/posSide 冲突 + live_trade.py 张数算错两个 bug，再补批量下单、账户级止盈、动态改止损。**

---

## 1. 现状：三条执行链路并存

| 链路 | 文件 | 执行方式 | 止盈止损 |
|---|---|---|---|
| ① 总部 exec | `backtest/live_trade.py` | POST `http://154.64.254.42:3001`（5 接口） | 服务端自动 OCO |
| ② HVIP 网关 | `_gateway/run_xrp.py` | WS `gateway.hvip.one` 发信号 | `takeProfit/stopLoss pct`（网关执行，记忆记录有止损/fill_px bug） |
| ③ OKX 官方 REST | `backtest/trade_bot.py` | 直连 `www.okx.com` | attached TP/SL（下单自带 tpTriggerPx/slTriggerPx） |

**切换 = 把 ①② 收敛到 ③**。trade_bot.py 已写好 HMAC 签名 + 下单 + attached TP/SL，直接复用它的 `_sign/_headers/_req`。

### 凭据分布（下单必须用对 key）

| key | 权限 | 在哪 |
|---|---|---|
| `f74899e5-...` | **只读**（下单会 50116 无权限） | `data/okx_readonly_credentials.json` + `~/.okx/config.toml` `okx-prod` |
| `cf713acd-...` | **交易** | `trade_bot.py` 硬编码 + `~/.okx/config.toml` `live`（MCP 默认 profile） |

---

## 2. 三个能力落地

### 2.1 attached TP/SL（✅ 已实现，补防插针开关）
trade_bot.py 已用对：市价单 + 下单自带止盈止损（一个请求搞定，平仓后自动撤 attached 单）。补一个开关防插针：

```python
body["tpTriggerPxType"] = "mark"   # 触发价用标记价
body["slTriggerPxType"] = "mark"   # 止损用标记价（防插针）
```

动态改止损（总部 exec 没有）：改触发价 → `POST /api/v5/trade/amend-algos`；跟踪止损 → `POST /api/v5/trade/order-algo` `ordType:"move_order_stop"`。

### 2.2 批量下单（❌ 未实现）
`POST /api/v5/trade/batch-orders`，一次 ≤20 个订单对象（MCP 里是 `swap_batch_orders`）。把循环下单改成一个数组；返回是数组，逐条看 `sCode=="0"`，不是整体成败；候选 >20 分批。

### 2.3 账户级止盈（❌ 未实现，官方无现成，自写循环）
官方无「总权益 +5% 自动全平」服务端触发，拼装：

```python
entry_eq = get_total_eq()          # GET /api/v5/account/balance → data[0]["totalEq"]
while True:
    if get_total_eq() >= entry_eq * 1.05:
        close_all(); break
def close_all():
    # GET /api/v5/account/positions?instType=SWAP → 枚举 {instId, posSide}
    # 逐个 POST /api/v5/trade/close-position {instId, mgnMode:"isolated",
    #        posSide, autoCxl: True}   # autoCxl 自动撤该币挂的 TP/SL 单
```

`autoCxl: True` 是关键：全平同时撤挂单，不留幽灵单（总部 exec 没有）。部分平仓用 reduceOnly 市价单 + sz（close-position 是全平）。

---

## 3. 总部 exec → 官方 API 逐接口映射

| 总部 exec | 官方 REST | 差异 |
|---|---|---|
| POST `/api/exec/order` | POST `/api/v5/trade/order` / `/batch-orders` | 官方多 ordType、attached TP/SL、mark 价止损 |
| POST `/api/exec/close` | POST `/api/v5/trade/close-position`（全平 autoCxl）/ reduceOnly 市价单（部分平） | 官方平仓更干净 |
| GET `/api/exec/position` | GET `/api/v5/account/positions?instType=SWAP` | 等价 |
| GET `/api/exec/balance` | GET `/api/v5/account/balance` | 官方多 totalEq 总权益 |
| GET `/api/exec/ping` | GET `/api/v5/public/time`（免鉴权） | 等价 |

切换步骤：定凭据(cf713acd) → 修 posMode/posSide → 换下单 → 换平仓/查持仓/查余额 → 加账户级止盈循环。切换后保留总部 exec 当降级兜底一周。

---

## 4. 坑清单（按优先级）

1. **【实锤】posSide vs posMode 冲突（最高优先）**：`account_get_config` 实测 `posMode:"long_short_mode"`（对冲）；trade_bot.py 下单写死 `posSide:"net"`，对冲模式下必报错。佐证：trade_recorder.py 按 `posSide=="long"/"short"` 分多空。修法：`POST /api/v5/account/set-position-mode` 切 net_mode，或下单统一传 long/short。
2. **【实锤】live_trade.py 张数算错（~100 倍超额）**：`qty=int(50/price)` 算出 35 张 XRP=4900U，注释却写「名义 50U」。正确是 trade_bot.py 的 `sz=int(notional/(ct_val*last))`。
3. **【实锤】凭据分散**：只读/交易 key 混在 config.toml，下单误用只读 key 会无权限。
4. **【待确认】保证金币种**：`settleCcy:"USDC"`（USDS 保证金），trade_bot.py 用 `-USDT-SWAP` + isolated，下单前确认合约保证金币种与余额匹配。
5. **【已知】HVIP 网关止损 bug**（记忆 hvip-gateway.md）：改走官方 attached TP/SL 的原因。
6. 批量 20 上限、下单传 clOrdId 幂等防重复。

## 5. 待拍板
posMode 用 net 还是 hedge；USDT 还是 USDS 保证金；是否保留总部 exec 兜底。
