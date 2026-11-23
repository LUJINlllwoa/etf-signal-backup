#!/usr/bin/env python3
# 方案B v2: 贴坑底横盘企稳埋伏 —— 参数扫描回测
import json, glob, os, itertools

ARCH = "/home/user/Doubao/chats/38445260152008706/etf-rank-board/archive"
DAYS = ["20261118", "20261119", "20261120"]

def hhmm(t): return t[8:12]

def load_bars(path):
    raw = json.load(open(path))
    if isinstance(raw, dict):
        raw = [raw[k] for k in sorted(raw)]
    bars = []
    for r in raw:
        if hhmm(r[0]) < "0930":
            continue
        bars.append({"t": r[0], "o": float(r[1]), "c": float(r[2]),
                     "h": float(r[3]), "l": float(r[4]), "v": float(r[5])})
    return sorted(bars, key=lambda x: x["t"])

def simulate(bars, p):
    LOW_BACK, FLAT_N, FLAT_RANGE, NEAR_LOW, TAKE, STOP, BREAK_LOW = p
    if len(bars) < FLAT_N + 2:
        return None
    day_open = bars[0]["o"]
    for i in range(FLAT_N, len(bars)):
        if hhmm(bars[i]["t"]) > "1000":
            break
        c = bars[i]["c"]
        if c >= day_open:
            continue
        prior = bars[:i+1]
        low_i = min(range(len(prior)), key=lambda k: prior[k]["l"])
        if i - low_i < LOW_BACK:
            continue
        pit = prior[low_i]["l"]
        win = bars[i-FLAT_N+1:i+1]
        if min(b["l"] for b in win) <= pit:          # 窗口内创新低
            continue
        wh = max(b["h"] for b in win); wl = min(b["l"] for b in win)
        if (wh/wl - 1)*100 > FLAT_RANGE:             # 横盘振幅过大
            continue
        if (c/pit - 1)*100 > NEAR_LOW:               # 现价离坑底太远,不追
            continue
        entry = c
        # 止损价: 固定-0.35% 与 跌破坑底 取更紧(更高)者
        sl_fixed = entry*(1-STOP/100)
        sl_break = pit*(1-BREAK_LOW/100)
        sl = max(sl_fixed, sl_break)
        for b in bars[i+1:]:
            tp = entry*(1+TAKE/100)
            if b["h"] >= tp and b["l"] <= sl:
                return -STOP
            if b["h"] >= tp:
                return TAKE
            if b["l"] <= sl:
                return round((sl/entry-1)*100, 3)
            if hhmm(b["t"]) >= "1450":
                return round((b["c"]/entry-1)*100, 3)
    return None

def evaluate(params):
    rets = []
    for d in DAYS:
        for f in glob.glob(f"{ARCH}/{d}/m1/*.json"):
            bars = load_bars(f)
            r = simulate(bars, params)
            if r is not None:
                rets.append(r)
    return rets

if __name__ == "__main__":
    grid = list(itertools.product(
        [5],            # LOW_BACK
        [6, 8, 10],     # FLAT_N
        [0.4, 0.5, 0.6],# FLAT_RANGE
        [0.3, 0.4],     # NEAR_LOW
        [0.6],          # TAKE
        [0.35],         # STOP
        [0.15, 0.25],   # BREAK_LOW
    ))
    print(f"{'N':>2} {'振幅':>4} {'贴底':>4} {'破底':>4} || {'信号':>4} {'胜率%':>6} {'均单':>7} {'累计':>8}")
    best = None
    for p in grid:
        _, n, fr, nl, _, _, bl = p
        rets = evaluate(p)
        if not rets:
            continue
        cnt = len(rets); win = sum(1 for r in rets if r > 0)
        mean = sum(rets)/cnt; tot = sum(rets)
        print(f"{n:>2} {fr:>4} {nl:>4} {bl:>4} || {cnt:>4} {win/cnt*100:>6.1f} {mean:>7.3f} {tot:>8.2f}")
        if best is None or tot > best[0]:
            best = (tot, p, rets)
    print("\n最优参数:", best[1], "累计", round(best[0], 2))
