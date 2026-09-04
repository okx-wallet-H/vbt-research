#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
订单簿碰墙事件研究 — 实时监控价格触碰大挂单墙，判定反弹/穿透。
只读公共行情（OKX public WS：books + trades），不下单不交易。

用法：
  python orderbook_wall_event.py [RUN_SECONDS]
  环境变量 RUN_SECONDS 亦可（默认 3300 = 55 分钟）

产出（与脚本同目录）：
  wall_events.jsonl   —— 每个碰墙事件的完整记录（touch + 1/5/15min 采样 + 判定）
  wall_run.log        —— 运行日志（心跳、事件、最终统计）
  wall_summary.json   —— 最终统计汇总（命中率等）
"""

import json
import os
import sys
import time
import threading

import websocket

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
INSTS = ["DOT-USDT-SWAP", "SOL-USDT-SWAP"]
TICKS = {"DOT-USDT-SWAP": 0.0001, "SOL-USDT-SWAP": 0.01}

# 任务给定的固定墙（之前 61 号报告实测的真实价位）
TASK_WALLS = {
    "DOT-USDT-SWAP": [
        {"price": 0.8785, "side": "res", "label": "DOT阻力墙0.8785"},
        {"price": 0.8731, "side": "sup", "label": "DOT支撑墙0.8731"},
    ],
    "SOL-USDT-SWAP": [
        {"price": 102.00, "side": "sup", "label": "SOL支撑墙102.00"},
        {"price": 103.88, "side": "res", "label": "SOL阻力墙103.88"},
    ],
}

TOUCH_BAND = 0.0005      # 碰墙判定 ±0.05%
RESET_BAND = 0.0015      # 价格离开 0.15% 后重新武装
SAMPLE_HORIZONS = [60, 300, 900]   # 1 / 5 / 15 分钟
RESUB_INTERVAL = 300     # 每 5 分钟重订阅 books 强制刷新快照（防 checksum 漂移）
DYNAMIC_INTERVAL = 60    # 每 1 分钟重探测动态墙
HEARTBEAT_INTERVAL = 60

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
JSONL_PATH = os.path.join(OUT_DIR, "wall_events.jsonl")
LOG_PATH = os.path.join(OUT_DIR, "wall_run.log")
SUMMARY_PATH = os.path.join(OUT_DIR, "wall_summary.json")

RUN_SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("RUN_SECONDS", "3300"))

# ---- 全局状态 ----
books = {i: {"bids": {}, "asks": {}, "ready": False} for i in INSTS}
last_trade = {i: {"px": None, "ts": 0} for i in INSTS}

walls = []           # 墙注册表（task + dynamic）
wall_state = {}      # wall_id -> "armed" / "fired"
active_events = []   # 已碰墙、尚未完成 15min 采样的
completed = []       # 已采样完成的
lock = threading.Lock()
CURRENT_WS = {"ws": None}


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def fmt_price(inst, p):
    return ("%.4f" if TICKS[inst] <= 0.0001 else "%.2f") % p


def add_wall(inst, price, side, label, wall_type):
    wid = "%s_%s_%s" % (wall_type, inst, label)
    now = time.time()
    for w in walls:
        if w["id"] == wid:
            w["price"] = price  # 动态墙价位漂移时刷新
            w["last_seen"] = now
            return w
    w = {"id": wid, "inst": inst, "price": price, "side": side,
         "label": label, "wall_type": wall_type, "last_seen": now}
    walls.append(w)
    wall_state[wid] = "armed"
    return w


def book_side(inst, side):
    # res=阻力=卖单墙在 asks；sup=支撑=买单墙在 bids
    return books[inst]["asks"] if side == "res" else books[inst]["bids"]


def wall_qty(inst, price, side):
    """墙价位 ±3 tick 内、对应侧的挂单量合计（墙可能跨 2-3 档）。"""
    b = book_side(inst, side)
    tick = TICKS[inst]
    total = 0.0
    for p, sz in b.items():
        try:
            pf = float(p)
        except ValueError:
            continue
        if abs(pf - price) <= 3 * tick:
            total += sz
    return round(total, 2)


def best_px(inst, which):
    d = books[inst]["bids"] if which == "bid" else books[inst]["asks"]
    if not d:
        return None
    vals = sorted(float(p) for p in d.keys())
    return vals[-1] if which == "bid" else vals[0]


def mid_px(inst):
    bid = best_px(inst, "bid")
    ask = best_px(inst, "ask")
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


# ---- 事件判定 ----
def judge(side, wall_px, px):
    if px is None:
        return "unknown"
    if side == "res":
        # 阻力墙：反弹=回落到墙下方；穿透=站上墙上方
        return "breakthrough" if px >= wall_px else "bounce"
    else:
        # 支撑墙：反弹=回升到墙上方；穿透=跌破墙下方
        return "breakthrough" if px <= wall_px else "bounce"


def fire_event(w, px):
    now_ms = int(time.time() * 1000)
    wq = wall_qty(w["inst"], w["price"], w["side"])
    ev = {
        "id": "ev_%d" % now_ms,
        "inst": w["inst"],
        "label": w["label"],
        "side": w["side"],
        "wall_type": w["wall_type"],
        "wall_price": w["price"],
        "touch_ts_ms": now_ms,
        "touch_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ms / 1000)),
        "touch_px": px,
        "approach": "below" if px < w["price"] else "above",
        "wall_qty_touch": wq,
        "samples": {},
    }
    with lock:
        active_events.append(ev)
    log("碰墙 %s | %s | 墙 %.5f 现价 %.5f | 墙量 %.0f | %s" % (
        ev["touch_time"], w["label"], w["price"], px, wq,
        "从下方" if ev["approach"] == "below" else "从上方"))


# ---- WS 回调 ----
def on_open(ws):
    CURRENT_WS["ws"] = ws
    args = []
    for i in INSTS:
        args.append({"channel": "books", "instId": i})
        args.append({"channel": "trades", "instId": i})
    ws.send(json.dumps({"op": "subscribe", "args": args}))
    log("WS 已连接并订阅 books + trades")


def on_message(ws, message):
    if message == "ping":
        ws.send("pong")
        return
    try:
        obj = json.loads(message)
    except Exception:
        return
    arg = obj.get("arg", {})
    ch = arg.get("channel")
    inst = arg.get("instId")
    if inst not in INSTS:
        return
    if ch == "books":
        handle_books(inst, obj)
    elif ch == "trades":
        handle_trades(inst, obj)


def handle_books(inst, obj):
    action = obj.get("action", "snapshot")
    b = books[inst]
    for d in obj.get("data", []):
        if action == "snapshot":
            b["bids"] = {p: float(sz) for p, sz, *_ in d["bids"]}
            b["asks"] = {p: float(sz) for p, sz, *_ in d["asks"]}
            b["ready"] = True
        else:
            for p, sz, *_ in d.get("bids", []):
                sz = float(sz)
                if sz == 0:
                    b["bids"].pop(p, None)
                else:
                    b["bids"][p] = sz
            for p, sz, *_ in d.get("asks", []):
                sz = float(sz)
                if sz == 0:
                    b["asks"].pop(p, None)
                else:
                    b["asks"][p] = sz


def handle_trades(inst, obj):
    for d in obj.get("data", []):
        try:
            px = float(d["px"])
        except (KeyError, ValueError):
            continue
        last_trade[inst]["px"] = px
        last_trade[inst]["ts"] = int(d.get("ts", 0))
        check_touch(inst, px)


def check_touch(inst, px):
    for w in walls:
        if w["inst"] != inst:
            continue
        wid = w["id"]
        st = wall_state.get(wid, "armed")
        dist = (px - w["price"]) / w["price"]
        if st == "armed":
            if abs(dist) <= TOUCH_BAND:
                fire_event(w, px)
                wall_state[wid] = "fired"
        else:
            if abs(dist) > RESET_BAND:
                wall_state[wid] = "armed"


# ---- 采样与动态墙探测 ----
def sample_active():
    now_ms = int(time.time() * 1000)
    done = []
    with lock:
        for ev in active_events:
            for h in SAMPLE_HORIZONS:
                key = str(h)
                if key in ev["samples"]:
                    continue
                if now_ms >= ev["touch_ts_ms"] + h * 1000:
                    px = last_trade[ev["inst"]]["px"]
                    wq = wall_qty(ev["inst"], ev["wall_price"], ev["side"])
                    ev["samples"][key] = {
                        "ts": time.strftime("%H:%M:%S"),
                        "px": px,
                        "wall_qty": wq,
                        "result": judge(ev["side"], ev["wall_price"], px),
                        "wall_eaten": (wq < 0.5 * ev["wall_qty_touch"]) if ev["wall_qty_touch"] > 0 else None,
                    }
            if all(str(h) in ev["samples"] for h in SAMPLE_HORIZONS):
                done.append(ev)
        for ev in done:
            active_events.remove(ev)
            completed.append(ev)
            try:
                with open(JSONL_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            except Exception:
                pass


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return 0.0
    if n % 2:
        return xs[n // 2]
    return (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def prune_dynamic():
    """删除超过 5 分钟未被重新确认的动态墙，防止墙表无限膨胀。"""
    now = time.time()
    with lock:
        stale = [w for w in walls
                 if w["wall_type"] == "dynamic" and now - w["last_seen"] > 300]
        for w in stale:
            walls.remove(w)
            wall_state.pop(w["id"], None)
        if stale:
            log("清理 %d 堵过期动态墙" % len(stale))


def redetect_dynamic():
    for inst in INSTS:
        if not books[inst]["ready"]:
            continue
        mid = mid_px(inst)
        if mid is None:
            continue
        asks = books[inst]["asks"]
        bids = books[inst]["bids"]
        med_ask = _median(asks.values())
        med_bid = _median(bids.values())
        best_ask_wall = None
        for p, sz in asks.items():
            pf = float(p)
            if mid <= pf <= mid * 1.01 and med_ask > 0 and sz >= 5 * med_ask:
                if best_ask_wall is None or sz > best_ask_wall[1]:
                    best_ask_wall = (pf, sz)
        if best_ask_wall:
            add_wall(inst, best_ask_wall[0], "res",
                     "动态卖墙%s" % fmt_price(inst, best_ask_wall[0]), "dynamic")
        best_bid_wall = None
        for p, sz in bids.items():
            pf = float(p)
            if mid * 0.99 <= pf <= mid and med_bid > 0 and sz >= 5 * med_bid:
                if best_bid_wall is None or sz > best_bid_wall[1]:
                    best_bid_wall = (pf, sz)
        if best_bid_wall:
            add_wall(inst, best_bid_wall[0], "sup",
                     "动态买墙%s" % fmt_price(inst, best_bid_wall[0]), "dynamic")


def heartbeat():
    now = time.time()
    parts = []
    for inst in INSTS:
        mid = mid_px(inst)
        lp = last_trade[inst]["px"]
        parts.append("%s mid=%s last=%s" % (
            inst, ("%.5f" % mid) if mid is not None else "NA",
            ("%.5f" % lp) if lp is not None else "NA"))
    for w in walls:
        if w["wall_type"] == "dynamic" and now - w["last_seen"] > 120:
            continue  # 心跳只打印近期活跃的动态墙
        wq = wall_qty(w["inst"], w["price"], w["side"])
        parts.append("%s qty=%.0f %s" % (w["label"], wq, wall_state.get(w["id"], "?")))
    log("心跳 | " + " | ".join(parts))


def force_resub_books():
    ws = CURRENT_WS.get("ws")
    if ws is None:
        return
    args = [{"channel": "books", "instId": i} for i in INSTS]
    try:
        ws.send(json.dumps({"op": "unsubscribe", "args": args}))
        time.sleep(1)
        ws.send(json.dumps({"op": "subscribe", "args": args}))
        log("已重订阅 books（强制刷新快照）")
    except Exception as e:
        log("重订阅失败: %s" % e)


# ---- 汇总 ----
def finalize_and_summary():
    with lock:
        for ev in list(active_events):
            for h in SAMPLE_HORIZONS:
                key = str(h)
                if key not in ev["samples"]:
                    px = last_trade[ev["inst"]]["px"]
                    ev["samples"][key] = {
                        "ts": time.strftime("%H:%M:%S"), "px": px,
                        "wall_qty": wall_qty(ev["inst"], ev["wall_price"], ev["side"]),
                        "result": judge(ev["side"], ev["wall_price"], px),
                        "final": True,
                        "wall_eaten": None,
                    }
            try:
                with open(JSONL_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            except Exception:
                pass
            completed.append(ev)
        active_events.clear()

    log("=" * 70)
    log("运行结束。事件总数: %d" % len(completed))

    summary = {"total_events": len(completed), "horizons": {}, "walls": {}}
    for h in SAMPLE_HORIZONS:
        b = t = 0
        for ev in completed:
            s = ev["samples"].get(str(h))
            if s and s["result"] in ("bounce", "breakthrough"):
                if s["result"] == "bounce":
                    b += 1
                else:
                    t += 1
        tot = b + t
        rate = (b / tot * 100) if tot else 0.0
        log("  %2dm 判定：反弹 %d / 穿透 %d / 未定 %d / 命中率(反弹) %.1f%%" % (
            h // 60, b, t, len(completed) - tot, rate))
        summary["horizons"][str(h)] = {"bounce": b, "breakthrough": t,
                                       "total": tot, "bounce_rate": round(rate, 2)}

    for w in walls:
        b = t = 0
        for ev in completed:
            if ev["wall_id"] != w["id"]:
                continue
            s = ev["samples"].get("900")
            if s and s["result"] in ("bounce", "breakthrough"):
                if s["result"] == "bounce":
                    b += 1
                else:
                    t += 1
        log("  墙 %s (%s, %.5f)：反弹 %d / 穿透 %d" % (
            w["label"], "阻力" if w["side"] == "res" else "支撑", w["price"], b, t))
        summary["walls"][w["label"]] = {
            "side": w["side"], "price": w["price"],
            "bounce": b, "breakthrough": t,
        }

    try:
        with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    log("统计已写入 %s" % SUMMARY_PATH)


def start_ws():
    def on_error(ws, e):
        log("WS error: %s" % e)

    def on_close(ws, code, msg):
        log("WS closed: %s %s" % (code, msg))

    ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message,
                                on_error=on_error, on_close=on_close)
    t = threading.Thread(target=ws.run_forever,
                         kwargs={"ping_interval": 20, "ping_timeout": 10})
    t.daemon = True
    t.start()
    return t


def main():
    log("启动碰墙事件研究。RUN_SECONDS=%d (%.1f 分钟)" % (RUN_SECONDS, RUN_SECONDS / 60))
    log("监控墙：")
    for inst, ws_list in TASK_WALLS.items():
        for w in ws_list:
            add_wall(inst, w["price"], w["side"], w["label"], "task")
            log("  [task] %s %s %.5f (%s)" % (inst, "阻力" if w["side"] == "res" else "支撑",
                                                w["price"], w["label"]))

    start_time = time.time()
    ws_thread = start_ws()
    last_dynamic = time.time() - DYNAMIC_INTERVAL + 5   # 启动后 5s 先探测一次
    last_resub = time.time() - RESUB_INTERVAL + 60      # 启动后 60s 第一次重订阅
    last_hb = 0.0

    try:
        while time.time() - start_time < RUN_SECONDS:
            now = time.time()
            if not ws_thread.is_alive():
                log("WS 线程退出，重连...")
                ws_thread = start_ws()
            sample_active()
            if now - last_dynamic >= DYNAMIC_INTERVAL:
                prune_dynamic()
                redetect_dynamic()
                last_dynamic = now
            if now - last_resub >= RESUB_INTERVAL:
                force_resub_books()
                last_resub = now
            if now - last_hb >= HEARTBEAT_INTERVAL:
                heartbeat()
                last_hb = now
            time.sleep(5)
    except KeyboardInterrupt:
        log("收到中断，收尾...")
    finally:
        finalize_and_summary()


if __name__ == "__main__":
    main()
