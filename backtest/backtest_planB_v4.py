#!/usr/bin/env python3
# 方案B v4: 坑底区域埋伏(坑深确认+不接飞刀, 去坑龄)
import json, glob, itertools

ARCH = "/home/user/Doubao/chats/38445260152008706/etf-rank-board/archive"
DAYS = ["20261118", "20261119", "20261120"]

def hm(t): return t[8:12]

def load(f):
    raw = json.load(open(f))
    if isinstance(raw, dict):
        raw = [raw[k] for k in sorted(raw)]
    return sorted([{"t": r[0], "o": float(r[1]), "c": float(r[2]),
                    "h": float(r[3]), "l": float(r[4]), "v": float(r[5])}
                   for r in raw if hm(r[0]) >= "0930"], key=lambda x: x["t"])

def simulate(bars, NEAR, SL_BUF, TAKE, PIT_DEPTH, WICK):
    if len(bars) < 10:
        return None
    op = bars[0]["o"]
    for i in range(1, len(bars)):
        if hm(bars[i]["t"]) > "1000":
            break
        c = bars[i]["c"]
        if c >= op:
            continue
        pit = min(x["l"] for x in bars[:i+1])
        if (op/pit - 1)*100 < PIT_DEPTH:        # 坑不够深
            continue
        if (c/pit - 1)*100 > NEAR:              # 离坑底太远
            continue
        if c < pit*(1-WICK/100):                # 收盘还在坑底下方,接飞刀
            continue
        entry = c
        sl = pit*(1-SL_BUF/100)
        for x in bars[i+1:]:
            tp = entry*(1+TAKE/100)
            if x["h"] >= tp and x["l"] <= sl:
                return round((sl/entry-1)*100, 3)
            if x["h"] >= tp:
                return TAKE
            if x["l"] <= sl:
                return round((sl/entry-1)*100, 3)
            if hm(x["t"]) >= "1450":
                return round((x["c"]/entry-1)*100, 3)
    return None

def run(NEAR, SL_BUF, TAKE, PIT_DEPTH, WICK):
    rs = []
    for d in DAYS:
        for f in glob.glob(f"{ARCH}/{d}/m1/*.json"):
            r = simulate(load(f), NEAR, SL_BUF, TAKE, PIT_DEPTH, WICK)
            if r is not None:
                rs.append(r)
    return rs

if __name__ == "__main__":
    print(f"{'贴底':>4} {'缓冲':>4} {'坑深':>4} || {'信号':>4} {'胜率%':>6} {'均单':>7} {'累计':>8}")
    best = None
    for NEAR, SL_BUF, PIT_DEPTH in itertools.product(
            [0.3, 0.4, 0.5], [0.3, 0.4, 0.5], [0.5, 0.8, 1.0]):
        rs = run(NEAR, SL_BUF, 0.6, PIT_DEPTH, 0.1)
        if not rs:
            continue
        n = len(rs); w = sum(1 for r in rs if r > 0)
        mean = sum(rs)/n; tot = sum(rs)
        print(f"{NEAR:>4} {SL_BUF:>4} {PIT_DEPTH:>4} || {n:>4} {w/n*100:>6.1f} {mean:>7.3f} {tot:>8.2f}")
        if best is None or tot > best[0]:
            best = (tot, (NEAR, SL_BUF, PIT_DEPTH), rs)
    print("\n最优:", best[1], "累计", round(best[0], 2))
