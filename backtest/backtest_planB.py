#!/usr/bin/env python3
# 方案B(企稳提前) vs 方案A(放量突破) 坑反弹回测
# 数据: etf-rank-board/archive/YYYYMMDD/m1/*.json
import json, glob, os

ARCH = "/home/user/Doubao/chats/38445260152008706/etf-rank-board/archive"
DAYS = ["20261118", "20261119", "20261120"]

# 参数(%)
LOW_BACK = 5      # 坑底至少在5根前
BOX_N = 8         # 企稳观察窗口
RV_MIN = 1.5      # 方案A放量门槛
TAKE = 0.6        # 止盈 +0.6%
STOP = 0.35       # 止损 -0.35%
FORCE_END = "1450"  # 尾盘强平
AM_END = "1000"   # 10点前

def hhmm(t): return t[8:12]

def load_bars(path):
    raw = json.load(open(path))
    bars = []
    for r in raw:
        t = r[0]
        if hhmm(t) < "0930":  # 只取连续竞价
            continue
        bars.append({"t": t, "o": float(r[1]), "c": float(r[2]),
                     "h": float(r[3]), "l": float(r[4]), "v": float(r[5])})
    bars.sort(key=lambda x: x["t"])
    return bars

def avg_vol(bars, end, n):
    seg = [b["v"] for b in bars[max(0, end-n):end] if b["v"] > 0]
    return sum(seg)/len(seg) if seg else 0.0

def simulate(bars, plan):
    """返回该标的当日的交易列表(理论上每天最多1个坑反弹)。"""
    if len(bars) < BOX_N + 2:
        return None
    day_open = bars[0]["o"]
    entry_i = None
    for i in range(BOX_N, len(bars)):
        t = hhmm(bars[i]["t"])
        if t > AM_END:
            break
        c = bars[i]["c"]
        if c >= day_open:        # 必须还在坑里
            continue
        # 坑底: 截至i的最低价
        prior = bars[:i+1]
        low_i = min(range(len(prior)), key=lambda k: prior[k]["l"])
        if i - low_i < LOW_BACK:  # 坑底不够早
            continue
        pit_low = prior[low_i]["l"]
        box = bars[i-BOX_N+1:i+1]
        if min(b["l"] for b in box) <= pit_low:  # 最近8根创新低,未企稳
            continue
        # 重心抬升: 收盘 > 最近5根均价
        ma5 = sum(b["c"] for b in bars[i-4:i+1]) / 5
        if not (c > ma5):
            continue
        if plan == "A":
            rv = bars[i]["v"] / avg_vol(bars, i, 8) if avg_vol(bars, i, 8) > 0 else 0
            box_high = max(b["h"] for b in bars[i-BOX_N:i])
            if rv < RV_MIN or c <= box_high:   # 放量+突破
                continue
        else:  # 方案B不要求放量,但要有量
            if bars[i]["v"] <= 0:
                continue
        entry_i = i
        break
    if entry_i is None:
        return None
    entry = bars[entry_i]["c"]
    # 出场
    for j in range(entry_i+1, len(bars)):
        b = bars[j]
        tp = entry * (1 + TAKE/100)
        sl = entry * (1 - STOP/100)
        hit_tp = b["h"] >= tp
        hit_sl = b["l"] <= sl
        if hit_tp and hit_sl:
            # 同根都碰,保守按止损
            return {"entry_t": bars[entry_i]["t"], "entry": entry, "exit_t": b["t"],
                    "ret": -STOP, "out": "同根保守算止损"}
        if hit_tp:
            return {"entry_t": bars[entry_i]["t"], "entry": entry, "exit_t": b["t"],
                    "ret": TAKE, "out": "止盈"}
        if hit_sl:
            return {"entry_t": bars[entry_i]["t"], "entry": entry, "exit_t": b["t"],
                    "ret": -STOP, "out": "止损"}
        if hhmm(b["t"]) >= FORCE_END:
            ret = (b["c"]/entry - 1)*100
            return {"entry_t": bars[entry_i]["t"], "entry": entry, "exit_t": b["t"],
                    "ret": round(ret, 3), "out": "尾盘平仓"}
    return None

def run(plan):
    trades = []
    for d in DAYS:
        for f in sorted(glob.glob(f"{ARCH}/{d}/m1/*.json")):
            code = os.path.basename(f)[:-5]
            bars = load_bars(f)
            tr = simulate(bars, plan)
            if tr:
                tr.update({"day": d, "code": code})
                trades.append(tr)
    return trades

def summarize(trades, name):
    n = len(trades)
    if not n:
        print(f"\n[{name}] 无信号"); return
    wins = [t for t in trades if t["ret"] > 0]
    losses = [t for t in trades if t["ret"] <= 0]
    tp = [t for t in trades if t["out"] == "止盈"]
    sl = [t for t in trades if t["out"] in ("止损", "同根保守算止损")]
    force = [t for t in trades if t["out"] == "尾盘平仓"]
    mean = sum(t["ret"] for t in trades)/n
    total = sum(t["ret"] for t in trades)
    print(f"\n===== {name} =====")
    print(f"信号数={n}  止盈={len(tp)}  止损={len(sl)}  尾盘平仓={len(force)}")
    print(f"胜率={len(wins)/n*100:.1f}%  平均每单={mean:.3f}%  累计={total:.2f}%")
    print(f"盈亏比(单笔)={TAKE/STOP:.2f}  期望(胜%*{TAKE}-败%*{STOP})="
          f"{len(wins)/n*TAKE - len(losses)/n*STOP:.3f}%")
    if force:
        fr = [t['ret'] for t in force]
        print(f"尾盘平仓均值={sum(fr)/len(fr):.3f}%  最好={max(fr):.2f}%  最差={min(fr):.2f}%")

if __name__ == "__main__":
    for plan in ["B", "A"]:
        trades = run(plan)
        summarize(trades, "方案B 企稳提前" if plan == "B" else "方案A 放量突破")
        if plan == "B":
            print("\n--- 方案B 明细 ---")
            for t in trades:
                print(f"{t['day']} {t['code']} 进{t['entry_t'][-4:]}@{t['entry']:.3f} "
                      f"出{t['exit_t'][-4:]} {t['ret']:+.3f}% [{t['out']}]")
