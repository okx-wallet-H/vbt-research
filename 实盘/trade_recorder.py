# ⚠️ 存档副本，实际运行脚本在 backtest/，别用这个跑
#!/usr/bin/env python3
"""实盘平仓记录器：查 OKX 历史持仓（完整平仓，不拆单），追加到 trade_log.json，统计止盈/止损胜率。

跑法：python trade_recorder.py
输出：backtest/output/trade_log.json + 控制台统计
"""
import json, hmac, hashlib, base64, urllib.request, urllib.error, gzip, io
from datetime import datetime, timezone
from pathlib import Path

cred = json.load(open(r"C:\Users\CF\Desktop\VBT研究站\data\okx_readonly_credentials.json", encoding="utf-8"))
API_KEY, SECRET, PASS = cred["api_key"], cred["secret"], cred["passphrase"]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
OUT = Path(__file__).resolve().parent / "output" / "trade_log.json"
# 震荡套利实盘起始时间（北京时间 2026-09-04 11:42 = UTC 03:42）
START_MS = 1788411732000  # 2026-09-04T03:42:00Z


def sign(ts, method, path, body=""):
    return base64.b64encode(hmac.new(SECRET.encode(), (ts+method+path+body).encode(), hashlib.sha256).digest()).decode()


def api(method, path):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    req = urllib.request.Request("https://www.okx.com" + path, method=method, headers={
        "OK-ACCESS-KEY": API_KEY, "OK-ACCESS-SIGN": sign(ts, method, path),
        "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": PASS,
        "Content-Type": "application/json", "User-Agent": UA, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            if r.headers.get('Content-Encoding') == 'gzip':
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            return json.loads(raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read()
        if e.headers.get('Content-Encoding') == 'gzip':
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        return json.loads(raw.decode("utf-8", "replace"))


def load_log():
    if OUT.exists():
        return json.loads(OUT.read_text(encoding="utf-8"))
    return {"trades": [], "seen_pos_ids": []}


def main():
    log = load_log()
    seen = set(log.get("seen_pos_ids", []))

    # 历史持仓（完整平仓，每个 posId 是一笔完整交易）
    d = api("GET", "/api/v5/account/positions-history?instType=SWAP&limit=100")
    positions = d.get("data", [])
    if not positions:
        print("历史持仓为空")
        return

    new_trades = 0
    for p in positions:
        pos_id = p.get("posId", "")
        ctime = int(p.get("cTime", 0) or 0)
        if ctime < START_MS or pos_id in seen:
            continue  # 起始时间之前 + 已记录过
        seen.add(pos_id)
        pnl = float(p.get("realizedPnl", 0) or 0)
        trade = {
            "pos_id": pos_id,
            "inst": p.get("instId", "").split("-")[0],
            "side": "多" if p.get("posSide") == "long" else "空",
            "pnl": round(pnl, 4),
            "open_px": p.get("avgPx"),
            "close_px": p.get("closeAvgPx"),
            "close_ts": datetime.fromtimestamp(ctime/1000, tz=timezone.utc).strftime("%m-%d %H:%M:%S"),
            "result": "止盈" if pnl > 0 else "止损",
        }
        log["trades"].append(trade)
        new_trades += 1

    log["seen_pos_ids"] = sorted(seen)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")

    # 统计
    trades = log["trades"]
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    total = sum(t["pnl"] for t in trades)
    win_rate = len(wins) / len(trades) * 100 if trades else 0

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 新增 {new_trades} 笔完整平仓")
    print(f"累计完整平仓 {len(trades)} 笔")
    print(f"  止盈 {len(wins)} 笔（+{sum(t['pnl'] for t in wins):.2f}U）")
    print(f"  止损 {len(losses)} 笔（{sum(t['pnl'] for t in losses):.2f}U）")
    print(f"  胜率 {win_rate:.1f}%  |  净已实现 {total:+.2f}U")


if __name__ == "__main__":
    main()
