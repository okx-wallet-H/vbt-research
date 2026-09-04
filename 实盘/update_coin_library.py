# ⚠️ 存档副本，实际运行脚本在 backtest/，别用这个跑
#!/usr/bin/env python3
"""每日更新币库：扫 OKX 全量 USDT 永续，记录每个币的现价/面值/最大杠杆/24h成交额。

跑法：python update_coin_library.py
输出：backtest/output/coin_library.json
"""
import json, urllib.request, gzip, io
from datetime import datetime, timezone
from pathlib import Path

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
OUT = Path(__file__).resolve().parent / "output" / "coin_library.json"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    raw = urllib.request.urlopen(req, timeout=30).read()
    try:
        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    except Exception:
        pass
    return json.loads(raw.decode("utf-8", "replace"))


def main():
    # 全量 SWAP instruments（含最大杠杆 lever、面值 ctVal）
    insts = get("https://www.okx.com/api/v5/public/instruments?instType=SWAP")
    usdt_swap = [i for i in insts.get("data", []) if i["instId"].endswith("-USDT-SWAP")]

    # 全量 tickers（现价 + 24h 成交额）
    ticks = get("https://www.okx.com/api/v5/market/tickers?instType=SWAP")
    tick_map = {t["instId"]: t for t in ticks.get("data", []) if t["instId"].endswith("-USDT-SWAP")}

    library = {}
    for inst in usdt_swap:
        instId = inst["instId"]
        base = instId.split("-")[0]
        t = tick_map.get(instId, {})
        library[base] = {
            "instId": instId,
            "max_lever": inst.get("lever"),      # 最大杠杆倍数
            "ct_val": inst.get("ctVal"),          # 合约面值（币）
            "ct_mult": inst.get("ctMult"),
            "last": t.get("last"),
            "vol_24h_usdt": t.get("volCcy24h"),   # 24h 成交额（USDT）
        }

    out = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(library),
        "coins": library,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 币库已更新：{len(library)} 个 USDT 永续 → {OUT}")


if __name__ == "__main__":
    main()
