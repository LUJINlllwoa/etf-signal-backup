#!/usr/bin/env python3
# 整合回测: 个股过滤 + 市场环境开关(regime)
import json, glob

ARCH = "/home/user/Doubao/chats/38445260152008706/etf-rank-board/archive"
DAYS = ["20261118", "20261119", "20261120"]
REGIME_CP = 5      # 第5根(9:34)判定环境
NL_THRESH = 50     # 创新低占比>=50% => 普跌日, 关闭坑反弹
TAKE = 0.6; SL_BUF = 0.5

def hm(t): return t[8:12]

def load(f):
    raw = json.load(open(f))
    if isinstance(raw, dict):
        raw = [raw[k] for k in sorted(raw)]
    return sorted([{"t": r[0], "o": float(r[1]), "c": float(r[2]),
                    "h": float(r[3]), "l": float(r[4]), "v": float(r[5])}
                   for r in raw if hm(r[0]) >= "0930"], key=lambda x: x["t"])

def regime(day_bars):
    nl = 0; n = 0
    for b in day_bars.values():
        if len(b) <= REGIME_CP:
            continue
        n += 1
        if b[REGIME_CP]["l"] <= min(x["l"] for x in b[:REGIME_CP+1]):
            nl += 1
    return nl/n*100 if n else 0

def trade(b):
    if len(b) < 30:
        return None
    op = b[0]["o"]
    cand = [(i, x) for i, x in enumerate(b) if hm(x["t"]) <= "1000" and x["l"] < op]
    if not cand:
        return None
    pi, px = min(cand, key=lambda t: t[1]["l"])
    pit = px["l"]; entry = px["c"]
    if entry >= op:
        return None
    depth = (op/pit-1)*100; age = pi
    # 个股过滤
    if not (5 <= age < 15 or depth >= 2):
        return None
    sl = pit*0.995; tp = entry*1.006
    for x in b[pi+1:]:
        if x["h"] >= tp:
            return 0.6
        if x["l"] <= sl:
            return round((sl/entry-1)*100, 3)
    return round((b[-1]["c"]/entry-1)*100, 3)

def report(title, use_regime):
    print(f"\n===== {title} =====")
    tot_all = 0
    for d in DAYS:
        day_bars = {f.split("/")[-1][:-5]: load(f)
                    for f in glob.glob(f"{ARCH}/{d}/m1/*.json")}
        nl = regime(day_bars)
        blocked = use_regime and nl >= NL_THRESH
        rets = []
        if not blocked:
            for b in day_bars.values():
                r = trade(b)
                if r is not None:
                    rets.append(r)
        if rets:
            n = len(rets); w = sum(1 for r in rets if r > 0)
            print(f"{d}: 创新低占比={nl:4.0f}% n={n:3d} 胜率={w/n*100:5.1f}% "
                  f"均单={sum(rets)/n:+.3f} 累计={sum(rets):+.2f}")
            tot_all += sum(rets)
        else:
            print(f"{d}: 创新低占比={nl:4.0f}% => 当日关闭坑反弹")
    print(f"三天合计(信号收益累加): {tot_all:+.2f}%")

if __name__ == "__main__":
    report("无环境开关(仅个股过滤)", False)
    report("加市场环境开关", True)
