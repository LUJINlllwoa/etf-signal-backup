# -*- coding: utf-8 -*-
"""
逐分钟轨迹回测：基于当日 verify 台账 + 本地 m1 归档，
逐只计算触发后的最大回撤、先触止损/先达标、达标后回吐。
用法: python3 backtest_trajectory.py [YYYYMMDD]
"""
import json, os, sys, statistics as st

BT = os.path.dirname(os.path.abspath(__file__))
M1 = '/home/user/Doubao/chats/38445260152008706/etf-rank-board/archive/intraday'

# 各类型 目标(%) 与 止损(%)
PARAM = {
    '早盘':   {'lo': 0.65, 'hi': 0.65, 'stop': 0.25},
    '下午':   {'lo': 0.20, 'hi': 0.30, 'stop': 0.25},
    '反转':   {'lo': None, 'hi': None, 'stop': 0.40},   # 目标=回开盘价
    '美股':   {'lo': 0.30, 'hi': 0.80, 'stop': 0.40},
    '二次探底': {'lo': 0.65, 'hi': 0.65, 'stop': 0.40},
}
KIND_ORDER = ['早盘', '下午', '反转', '二次探底', '美股']


def analyze(r, m1dir):
    code = r['code']
    trig = r['trig_price']
    t = r.get('time')
    day = r.get('day') or {}
    if not t or not day:
        return None
    confirm_ts = day['date'].replace('-', '') + t.replace(':', '')
    p = os.path.join(m1dir, code + '.json')
    if not os.path.exists(p):
        return None
    bars = json.load(open(p, encoding='utf-8'))
    after = [b for b in bars.values() if b[0] >= confirm_ts]
    if not after:
        return None
    after.sort(key=lambda b: b[0])
    prm = PARAM[r['kind']]
    tgt_hi = day['open'] if r['kind'] == '反转' else trig * (1 + prm['hi'] / 100)
    tgt_lo = day['open'] if r['kind'] == '反转' else trig * (1 + prm['lo'] / 100)
    stop_px = trig * (1 - prm['stop'] / 100)

    max_close, min_low = -1e9, 1e9
    hit_hi = hit_lo = stopped_first = False
    hit_lo_idx = None
    for i, b in enumerate(after):
        close, low = float(b[2]), float(b[4])
        max_close = max(max_close, close)
        min_low = min(min_low, low)
        if not hit_hi and close >= tgt_hi:
            hit_hi = True
        if not hit_lo and close >= tgt_lo:
            hit_lo = True
            if hit_lo_idx is None:
                hit_lo_idx = i
        if not hit_lo and low <= stop_px:
            stopped_first = True
    max_gain = (max_close / trig - 1) * 100
    max_dd = (min_low / trig - 1) * 100
    close_gain = (day['close'] / trig - 1) * 100
    giveback = (max_close / day['close'] - 1) * 100 if hit_lo_idx is not None else None
    return {'code': code, 'kind': r['kind'], 'time': t, 'trig': trig,
            'max_gain': round(max_gain, 2), 'max_dd': round(max_dd, 2),
            'close_gain': round(close_gain, 2), 'hit_hi': hit_hi, 'hit_lo': hit_lo,
            'stopped_first': stopped_first, 'giveback': round(giveback, 2) if giveback is not None else None}


def agg(rs):
    n = len(rs)
    if not n:
        return None
    dd = [x['max_dd'] for x in rs]
    gv = [x['giveback'] for x in rs if x['giveback'] is not None]
    return {
        'n': n,
        'hi': sum(x['hit_hi'] for x in rs),
        'lo': sum(x['hit_lo'] for x in rs),
        'stop_first': sum(x['stopped_first'] for x in rs),
        'dd_avg': round(st.mean(dd), 2),
        'dd_min': round(min(dd), 2),
        'giveback_avg': round(st.mean(gv), 2) if gv else None,
        'cg_neg': round(sum(1 for x in rs if x['close_gain'] <= -0.05) / n * 100, 1),
    }


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else '20261125'
    d = json.load(open(os.path.join(BT, f'verify_{day}.json'), encoding='utf-8'))
    rows = d['rows'] if isinstance(d, dict) and 'rows' in d else d
    m1dir = os.path.join(M1, day, 'm1')
    res = [x for x in (analyze(r, m1dir) for r in rows) if x]
    print(f'日期 {day}  轨迹样本 {len(res)}')
    all_a = agg(res)
    print(f"\n== 全部 ==\n  先触保底 {all_a['lo']}({all_a['lo']/all_a['n']*100:.1f}%) | 先触主目标 {all_a['hi']}({all_a['hi']/all_a['n']*100:.1f}%) | 达标前先破止损 {all_a['stop_first']}({all_a['stop_first']/all_a['n']*100:.1f}%)")
    print(f"  触发后最大回撤 均值 {all_a['dd_avg']}% / 最深 {all_a['dd_min']}% | 达标后平均回吐 {all_a['giveback_avg']}% | 收盘负 {all_a['cg_neg']}%")
    for k in KIND_ORDER:
        a = agg([x for x in res if x['kind'] == k])
        if a:
            print(f"\n== {k}（{a['n']}只）==  保底 {a['lo']}({a['lo']/a['n']*100:.0f}%) 主目标 {a['hi']}({a['hi']/a['n']*100:.0f}%) 达标前先破止损 {a['stop_first']}({a['stop_first']/a['n']*100:.0f}%) 回撤均值 {a['dd_avg']}% 最深 {a['dd_min']}% 达标后回吐 {a['giveback_avg']}% 收盘负 {a['cg_neg']}%")
    json.dump(res, open(os.path.join(BT, f'trajectory_{day}.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('\n已写入 trajectory_%s.json' % day)


if __name__ == '__main__':
    main()
