#!/usr/bin/env python3
# 严谨最终版: 贴坑底 + 固定止损 + 个股过滤 + regime开关
import json, glob

ARCH = "/home/user/Doubao/chats/38445260152008706/etf-rank-board/archive"
DAYS = ["20261118", "20261119", "20261120"]
REGIME_CP, NL_THRESH = 5, 50
NEAR = 0.4        # 进场收盘距坑底<=0.4%
TAKE, STOP = 0.6, 0.4

def hm(t): return t[8:12]

def load(f):
    raw = json.load(open(f))
    if isinstance(raw, dict):
        raw = [raw[k] for k in sorted(raw)]
    return sorted([{"t": r[0], "o": float(r[1]), "c": float(r[2]),
                    "h": float(r[3]), "l": float(r[4]), "v": float(r[5])}
                   for r in raw if hm(r[0]) >= "0930"], key=lambda x: x["t"])

def regime(day_bars):
    nl = n = 0
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
    if not (5 <= age < 15 or depth >= 2):    # 个股过滤
        return None
    if (entry/pit-1)*100 > NEAR:             # 收盘没贴坑底,不进
        return None
    sl = entry*(1-STOP/100); tp = entry*(1+TAKE/100)
    for x in b[pi+1:]:
        if x["h"] >= tp and x["l"] <= sl:
            return -STOP
        if x["h"] >= tp:
            return TAKE
        if x["l"] <= sl:
            return -STOP
        if hm(x["t"]) >= "1450":
            return round((x["c"]/entry-1)*100, 3)
    return None  # 未收盘且未触发=持仓中

def day_report(d, day_bars):
    nl = regime(day_bars)
    if nl >= NL_THRESH:
        print(f"{d}: 创新低{nl:4.0f}% => 关闭坑反弹"); return 0
    rets = [r for b in day_bars.values() if (r := trade(b)) is not None]
    if not rets:
        print(f"{d}: 无信号"); return 0
    n = len(rets); w = sum(1 for r in rets if r > 0)
    print(f"{d}: 创新低{nl:4.0f}% n={n:3d} 胜率={w/n*100:5.1f}% "
          f"均单={sum(rets)/n:+.3f} 累计={sum(rets):+.2f}")
    return sum(rets)

if __name__ == "__main__":
    print("=== 历史3天(样本内) ===")
    tot = 0
    for d in DAYS:
        bars = {f.split("/")[-1][:-5]: load(f)
                for f in glob.glob(f"{ARCH}/{d}/m1/*.json")}
        tot += day_report(d, bars)
    print("样本内合计:", round(tot, 2))
    print("\n=== 今天11/23(样本外, 未收盘) ===")
    today = json.load(open("/home/user/.doubao/agent_mode/workspace/today_oos.json"))
    day_report("20261123", today)
