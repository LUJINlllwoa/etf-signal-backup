# -*- coding: utf-8 -*-
"""
全量形态回放回测：用本地 6 天 m1 归档，复刻 scan_pm 的纯价格/量能形态触发，
统计触发后轨迹与止盈/止损参数敏感性。

口径边界（务必注意）：
  - 可复刻：实体阳线、量比、坑底、W底、回踩、时间窗口、触发后轨迹。
  - 不可复刻（历史取不到）：成分股红率/加权 w、美股溢价安全门、大盘弱市/熔断、对昨收 gap。
    因此回放触发点比实盘宽松，胜率是"形态宽松版"参考，不等于实盘成交。
用法: python3 replay_backtest.py
"""
import json, os, glob, statistics as st
import scan_pm as sp

BT = os.path.dirname(os.path.abspath(__file__))
M1 = '/home/user/Doubao/chats/38445260152008706/etf-rank-board/archive/intraday'

AM_START, AM_END, AM_CLOSE, PM_START, PM_END = 570, 600, 690, 780, 890
KIND_ORDER = ['早盘', '下午', '反转', '二次探底', '美股']

# 线上目标(%) / 止损(%)
PARAM = {
    '早盘':   {'lo': 0.65, 'hi': 0.65, 'stop': 0.25},
    '下午':   {'lo': 0.20, 'hi': 0.30, 'stop': 0.25},
    '反转':   {'lo': None, 'hi': None, 'stop': 0.40},
    '美股':   {'lo': 0.30, 'hi': 0.80, 'stop': 0.40},
    '二次探底': {'lo': 0.65, 'hi': 0.65, 'stop': 0.40},
}


def bar_min(b):
    return int(b[0][8:10]) * 60 + int(b[0][10:12])


def in_trade_window(t):
    return AM_START <= t < AM_CLOSE or PM_START <= t <= PM_END


def build_qt(bars, i):
    open0 = float(bars[0][1])
    now = float(bars[i][2])
    high = max(float(b[3]) for b in bars[:i + 1])
    return {'now': now, 'open': open0, 'high': high,
            'rt_open': (now / open0 - 1) * 100 if open0 > 0 else 0,
            'gap': 0.0}  # 回放取不到昨收 gap，仅占位；形态判定不依赖其数值


def replay_day(day_dir, day):
    """返回当天所有形态触发记录（同票同形态只记第一次）。"""
    recs = []
    seen = set()
    m1dir = os.path.join(day_dir, 'm1')
    if not os.path.isdir(m1dir):
        return recs
    for f in sorted(glob.glob(os.path.join(m1dir, '*.json'))):
        code = os.path.basename(f)[:-5]
        if code in sp.ZOMBIES:
            continue
        bars = list(json.load(open(f, encoding='utf-8')).values())
        bars.sort(key=lambda b: b[0])
        if len(bars) < 20:
            continue
        open0 = float(bars[0][1])
        day_close = float(bars[-1][2])
        for i in range(20, len(bars)):
            t = bar_min(bars[i])
            if not in_trade_window(t):
                continue
            qt = build_qt(bars, i)
            prefix = bars[:i + 1]
            trig = float(bars[i][2])

            # 判定形态（按优先级去重到票）
            kind = None
            if code in sp.US_CODES:
                cand = sp.reversal_metrics(qt, prefix)
                if cand:
                    kind = '美股'
            elif code in sp.COMMODITY_CODES:
                cand = sp.candidate_metrics(qt, prefix)
                if cand:
                    kind = '早盘' if t <= AM_END else '下午'
            else:
                # 二次探底优先（盘中双底拉起）
                sb = sp.second_bottom_metrics(qt, prefix)
                if sb:
                    kind = '二次探底'
                else:
                    rev = sp.reversal_metrics(qt, prefix)
                    if rev:
                        kind = '反转'
                    else:
                        cand = sp.candidate_metrics(qt, prefix)
                        if cand:
                            kind = '早盘' if t <= AM_END else '下午'
            if not kind:
                continue
            key = (code, kind)
            if key in seen:
                continue
            seen.add(key)

            after = bars[i + 1:]
            if not after:
                continue
            prm = PARAM[kind]
            tgt_hi = open0 if kind == '反转' else trig * (1 + prm['hi'] / 100)
            tgt_lo = open0 if kind == '反转' else trig * (1 + prm['lo'] / 100)
            stop_px = trig * (1 - prm['stop'] / 100)

            max_close, min_low = -1e9, 1e9
            hit_hi = hit_lo = stop_first = False
            for b in after:
                close, low = float(b[2]), float(b[4])
                max_close = max(max_close, close)
                min_low = min(min_low, low)
                if not hit_hi and close >= tgt_hi:
                    hit_hi = True
                if not hit_lo and close >= tgt_lo:
                    hit_lo = True
                if not hit_lo and low <= stop_px:
                    stop_first = True
            recs.append({
                'code': code, 'kind': kind, 'day': day, 'time': bars[i][0][8:10] + ':' + bars[i][0][10:12],
                'trig': trig, 'open0': open0, 'day_close': day_close,
                'max_gain': round((max_close / trig - 1) * 100, 2),
                'max_dd': round((min_low / trig - 1) * 100, 2),
                'close_gain': round((day_close / trig - 1) * 100, 2),
                'hit_hi': hit_hi, 'hit_lo': hit_lo, 'stop_first': stop_first,
            })
    return recs


def agg(rs):
    n = len(rs)
    if not n:
        return None
    hi = sum(r['hit_hi'] for r in rs)
    lo = sum(r['hit_lo'] for r in rs)
    mg = [r['max_gain'] for r in rs]
    cg = [r['close_gain'] for r in rs]
    dd = [r['max_dd'] for r in rs]
    cg_win = [x for x in cg if x > 0.05]
    cg_lose = [x for x in cg if x <= -0.05]
    return {'n': n, 'hi': hi, 'lo': lo,
            'hi_rate': round(hi / n * 100, 1), 'lo_rate': round(lo / n * 100, 1),
            'mg_avg': round(st.mean(mg), 2), 'mg_med': round(st.median(mg), 2),
            'cg_avg': round(st.mean(cg), 2), 'cg_med': round(st.median(cg), 2),
            'dd_avg': round(st.mean(dd), 2), 'dd_min': round(min(dd), 2),
            'cg_neg': round(sum(1 for x in cg if x <= -0.05) / n * 100, 1),
            'stop_first': sum(r['stop_first'] for r in rs),
            'stop_rate': round(sum(r['stop_first'] for r in rs) / n * 100, 1),
            'pl_ratio': round(st.mean(cg_win) / abs(st.mean(cg_lose)), 2) if cg_win and cg_lose else None}


def main():
    days = sorted(os.listdir(M1))
    all_recs = []
    for d in days:
        recs = replay_day(os.path.join(M1, d), d)
        all_recs += recs
        print(f'{d}: 形态触发 {len(recs)} 笔')
    print(f'\n合计 {len(all_recs)} 笔（宽松形态口径，未过红率/溢价/大盘闸门）')

    print('\n== 按类型（线上参数）==')
    for k in KIND_ORDER:
        a = agg([r for r in all_recs if r['kind'] == k])
        if a:
            print(f"{k}: n={a['n']} 主目标 {a['hi_rate']}% 保底 {a['lo_rate']}% "
                  f"最高 {a['mg_avg']}%(中位{a['mg_med']}%) 收盘 {a['cg_avg']}%(中位{a['cg_med']}%) "
                  f"回撤 {a['dd_avg']}%(最深{a['dd_min']}%) 先破止损 {a['stop_rate']}% "
                  f"收盘负 {a['cg_neg']}% 盈亏比 {a['pl_ratio']}")

    print('\n== 按日期 ==')
    for d in days:
        a = agg([r for r in all_recs if r['day'] == d])
        if a:
            print(f"{d}: n={a['n']} 主目标 {a['hi_rate']}% 保底 {a['lo_rate']}% 收盘 {a['cg_avg']}% 回撤 {a['dd_avg']}% 先破止损 {a['stop_rate']}%")

    # 止盈档扫描：以触发后最高收盘能否摸到目标，统计各目标档达标率与平均最高
    print('\n== 止盈档扫描（触发后最高收盘触达率 %）==')
    for tgt in [0.2, 0.3, 0.45, 0.65, 0.8]:
        reach = sum(1 for r in all_recs if r['max_gain'] >= tgt)
        print(f"  目标 +{tgt}%: 触达 {reach}/{len(all_recs)} = {reach/len(all_recs)*100:.1f}%")

    # 止损档扫描：触发后最低跌破各止损线的比例
    print('\n== 止损档扫描（触发后盘中最低跌破率 %）==')
    for stp in [0.25, 0.4, 0.6, 1.0]:
        brk = sum(1 for r in all_recs if r['max_dd'] <= -stp)
        print(f"  止损 -{stp}%: 跌破 {brk}/{len(all_recs)} = {brk/len(all_recs)*100:.1f}%")

    json.dump(all_recs, open(os.path.join(BT, 'replay_records.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('\n已写入 replay_records.json')


if __name__ == '__main__':
    main()
