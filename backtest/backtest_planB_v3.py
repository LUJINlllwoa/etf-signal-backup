#!/usr/bin/env python3
# 方案B v3: 坑底区域容错埋伏(允许二次探底/小幅假跌破)
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

def simulate(bars, NEAR, SL_BUF, TAKE, LOW_AGE, EXHAUST):
    if len(bars) < 10:
        return None
    op = bars[0]["o"]
    for i in range(LOW_AGE, len(bars)):
        if hm(bars[i]["t"]) > "1000":
            break
        c = bars[i]["c"]
        if c >= op:
            continue
        prior = bars[:i+1]
        low_i = min(range(len(prior)), key=lambda k: prior[k]["l"])
        if i - low_i < LOW_AGE:      # 坑底太新,可能还在急跌
            continue
        pit = prior[low_i]["l"]
        if (c/pit - 1)*100 > NEAR:   # 不在坑底区域,不追
            continue
        # 动能衰竭: 最近3根最低都在坑底EXHAUST容差内,没有再往下打空间
        last3 = bars[max(0, i-2):i+1]
        if min(x["l"] for x in last3) < pit*(1-EXHAUST/100):
            continue
        entry = c
        sl = pit*(1-SL_BUF/100)
        for x in bars[i+1:]:
            tp = entry*(1+TAKE/100)
            if x["h"] >= tp and x["l"] <= sl:
                return round((sl/entry-1)*100, 3)   # 同根保守按止损
            if x["h"] >= tp:
                return TAKE
            if x["l"] <= sl:
                return round((sl/entry-1)*100, 3)
            if hm(x["t"]) >= "1450":
                return round((x["c"]/entry-1)*100, 3)
    return None

def run(NEAR, SL_BUF, TAKE, LOW_AGE, EXHAUST):
    rs = []
    for d in DAYS:
        for f in glob.glob(f"{ARCH}/{d}/m1/*.json"):
            r = simulate(load(f), NEAR, SL_BUF, TAKE, LOW_AGE, EXHAUST)
            if r is not None:
                rs.append(r)
    return rs

if __name__ == "__main__":
    print(f"{'贴底':>4} {'止损缓冲':>6} {'止盈':>4} {'坑龄':>4} || {'信号':>4} {'胜率%':>6} {'均单':>7} {'累计':>8}")
    best = None
    for NEAR, SL_BUF, TAKE, LOW_AGE, EXHAUST in itertools.product(
            [0.3, 0.4, 0.5], [0.3, 0.4, 0.5], [0.6], [5], [0.2]):
        rs = run(NEAR, SL_BUF, TAKE, LOW_AGE, EXHAUST)
        if not rs:
            continue
        n = len(rs); w = sum(1 for r in rs if r > 0)
        mean = sum(rs)/n; tot = sum(rs)
        print(f"{NEAR:>4} {SL_BUF:>6} {TAKE:>4} {LOW_AGE:>4} || {n:>4} {w/n*100:>6.1f} {mean:>7.3f} {tot:>8.2f}")
        if best is None or tot > best[0]:
            best = (tot, (NEAR, SL_BUF, TAKE, LOW_AGE, EXHAUST), rs)
    print("\n最优:", best[1], "累计", round(best[0], 2))
