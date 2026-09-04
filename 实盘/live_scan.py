#!/usr/bin/env python3
"""蜂王震荡套利 —— 多币实时盯盘 + 到点自动下单（直连总部执行接口）。

规律：冲高失败=阻力（涨到阻力做空）、探底失败=支撑（跌到支撑做多）。
扫描全部候选币，贴近阻力做空 / 贴近支撑做多，查持仓避免重复开仓。

跑法：
  python live_scan.py          # dry-run：只扫信号不下单
  python live_scan.py --live   # 实盘：到点自动下单
"""
import json, time, argparse, urllib.request, urllib.error, gzip, io
from datetime import datetime
from pathlib import Path

EXEC_URL = "http://154.64.254.42:3001"
EXEC_TOKEN = "bee-exec-2024-secret"
EXEC_HDR = {"x-api-token": EXEC_TOKEN, "Content-Type": "application/json"}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

TP_MARGIN_PCT = 50   # 止盈 = 保证金 50%
SL_MARGIN_PCT = 30   # 止损 = 保证金 30%
TOUCH = 0.005        # 贴近 0.5% 触发
LEVERAGE = 10        # 杠杆 10x
MARGIN_PER_POS = 10  # 每仓保证金 10U
NOTIONAL = MARGIN_PER_POS * LEVERAGE  # 每仓名义 = 100U
INTERVAL = 5         # 轮询间隔秒

ROOT = Path(__file__).resolve().parent
CTVAL_CACHE = {}  # 币种 → 合约面值（ctVal×ctMult）


def now():
    return datetime.now().strftime("%H:%M:%S")


def load_coins():
    p = ROOT / "output" / "level_coins.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def get_prices():
    """批量拉所有 USDT 永续现价。"""
    req = urllib.request.Request("https://www.okx.com/api/v5/market/tickers?instType=SWAP",
                                 headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    raw = urllib.request.urlopen(req, timeout=20).read()
    try:
        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    except Exception:
        pass
    data = json.loads(raw.decode("utf-8", "replace")).get("data", [])
    return {t["instId"].split("-")[0]: float(t["last"]) for t in data if t["instId"].endswith("-USDT-SWAP")}


def get_positions():
    req = urllib.request.Request(f"{EXEC_URL}/api/exec/position", headers={"x-api-token": EXEC_TOKEN})
    return json.loads(urllib.request.urlopen(req, timeout=15).read()).get("data", [])


def get_ctval(base):
    """查合约面值（ctVal × ctMult），缓存。"""
    if base in CTVAL_CACHE:
        return CTVAL_CACHE[base]
    url = f"https://www.okx.com/api/v5/public/instruments?instType=SWAP&instId={base}-USDT-SWAP"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    raw = urllib.request.urlopen(req, timeout=15).read()
    try:
        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    except Exception:
        pass
    inst = json.loads(raw.decode("utf-8", "replace"))["data"][0]
    ct = float(inst["ctVal"]) * float(inst["ctMult"])
    CTVAL_CACHE[base] = ct
    return ct


def place_order(base, direction, price):
    ct = get_ctval(base)
    qty = max(1, int(NOTIONAL / (ct * price)))  # 名义 100U → 张数（含面值）
    tp_pct = TP_MARGIN_PCT / LEVERAGE  # 止盈价格% = 保证金50% ÷ 杠杆10 = 5%
    sl_pct = SL_MARGIN_PCT / LEVERAGE  # 止损价格% = 保证金30% ÷ 杠杆10 = 3%
    if direction == "short":
        sl = round(price * (1 + sl_pct / 100), 6)
        tp = round(price * (1 - tp_pct / 100), 6)
    else:
        sl = round(price * (1 - sl_pct / 100), 6)
        tp = round(price * (1 + tp_pct / 100), 6)
    payload = {"instId": f"{base}-USDT-SWAP", "direction": direction, "quantity": qty,
               "stop_loss": sl, "take_profit": tp, "leverage": LEVERAGE}
    req = urllib.request.Request(f"{EXEC_URL}/api/exec/order", data=json.dumps(payload).encode(),
                                 headers=EXEC_HDR, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, f"EXC {type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    mode = "实盘" if args.live else "dry-run 只扫"
    # 查余额算最多能开几个仓（每仓保证金 10U，扫完余额）
    bal_req = urllib.request.Request(f"{EXEC_URL}/api/exec/balance", headers={"x-api-token": EXEC_TOKEN})
    bal = json.loads(urllib.request.urlopen(bal_req, timeout=15).read())
    total_eq = 0
    for d in bal.get("data", []):
        for det in d.get("details", []):
            total_eq += float(det.get("eq", 0) or 0)
    max_pos = max(1, int(total_eq / MARGIN_PER_POS))
    print(f"[{now()}] 启动 {mode} 多币盯盘 | 名义{NOTIONAL}U 杠杆{LEVERAGE}x 每仓保证金{MARGIN_PER_POS}U 最多{max_pos}仓 | 余额{total_eq:.0f}U")

    while True:
        try:
            coins = load_coins()
            prices = get_prices()
            positions = get_positions()
            held = {p["instId"].split("-")[0] for p in positions if p.get("instId")}

            # 报告当前持仓
            if positions:
                for p in positions:
                    print(f"[{now()}] 持仓 {p['instId']} 均价{p.get('avgPx')} 现价{p.get('last','')} upl={p.get('upl','')}", flush=True)

            # 扫候选币找信号
            for c in coins:
                base, res, sup = c.get("base"), c.get("res"), c.get("sup")
                if not base or not res or not sup:
                    continue
                if base in held:
                    continue  # 已持仓跳过
                last = prices.get(base)
                if not last:
                    continue
                if last < 0.0001:
                    continue  # 价格太小的币，止盈止损精度丢失，跳过（如 PEPE 0.0000036）
                res_f, sup_f = float(res), float(sup)
                if last >= res_f * (1 - TOUCH) and len(held) < max_pos:
                    if args.live:
                        st, txt = place_order(base, "short", last)
                        print(f"[{now()}] {base} 贴近阻力{res_f} 做空 @ {last} → HTTP{st} {txt[:120]}", flush=True)
                        held.add(base)
                    else:
                        print(f"[{now()}] [dry] {base} 贴近阻力{res_f} → 做空 @ {last}", flush=True)
                elif last <= sup_f * (1 + TOUCH) and len(held) < max_pos:
                    if args.live:
                        st, txt = place_order(base, "long", last)
                        print(f"[{now()}] {base} 贴近支撑{sup_f} 做多 @ {last} → HTTP{st} {txt[:120]}", flush=True)
                        held.add(base)
                    else:
                        print(f"[{now()}] [dry] {base} 贴近支撑{sup_f} → 做多 @ {last}", flush=True)
        except Exception as e:
            print(f"[{now()}] 异常 {type(e).__name__}: {e}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
