# -*- coding: utf-8 -*-
"""
基于已确认信号台账(verify_*.json)的事后结果回测，用于研判策略优化方向。
口径：
  max_gain = 触发后最高能赚(%)；close_gain = 收盘/触发价(%)；回吐 = max_gain-close_gain。
  注意：11-23/11-24 为旧口径(含触发前高点)，max_gain 偏乐观；close_gain 口径各天一致。
"""
import json, glob, os, statistics as st

BT = os.path.dirname(os.path.abspath(__file__))
KIND_ORDER = ['早盘', '下午', '反转', '二次探底', '美股']


def load_rows():
    rows = []
    for f in sorted(glob.glob(os.path.join(BT, 'verify_2026112*.json'))):
        d = json.load(open(f, encoding='utf-8'))
        rr = d['rows'] if isinstance(d, dict) and 'rows' in d else d
        day = d.get('date') or os.path.basename(f)[7:15]
        for r in rr:
            r['_day'] = day
            rows.append(r)
    return rows


def agg(rs):
    n = len(rs)
    if not n:
        return None
    hi = sum(1 for r in rs if r.get('hit_hi'))
    lo = sum(1 for r in rs if r.get('hit_lo'))
    mg = [r['max_gain'] for r in rs if r.get('max_gain') is not None]
    cg = [r['close_gain'] for r in rs if r.get('close_gain') is not None]
    pull = [r['max_gain'] - r['close_gain'] for r in rs
            if r.get('max_gain') is not None and r.get('close_gain') is not None]
    cg_win = [x for x in cg if x > 0.05]
    cg_lose = [x for x in cg if x <= -0.05]
    # 最高能赚分档
    bins = {'<0.2': 0, '0.2~0.3': 0, '0.3~0.65': 0, '>=0.65': 0}
    for x in mg:
        if x < 0.2: bins['<0.2'] += 1
        elif x < 0.3: bins['0.2~0.3'] += 1
        elif x < 0.65: bins['0.3~0.65'] += 1
        else: bins['>=0.65'] += 1
    return {
        'n': n,
        'hi': hi, 'lo': lo,
        'hi_rate': round(hi / n * 100, 1),
        'lo_rate': round(lo / n * 100, 1),
        'mg_avg': round(st.mean(mg), 2) if mg else None,
        'mg_med': round(st.median(mg), 2) if mg else None,
        'cg_avg': round(st.mean(cg), 2) if cg else None,
        'cg_med': round(st.median(cg), 2) if cg else None,
        'cg_neg': round(sum(1 for x in cg if x <= -0.05) / len(cg) * 100, 1) if cg else None,
        'pull_avg': round(st.mean(pull), 2) if pull else None,
        'pl_ratio': round(st.mean(cg_win) / abs(st.mean(cg_lose)), 2) if cg_win and cg_lose else None,
        'bins': bins,
    }


def show(title, a):
    if not a:
        print(f'{title}: 无样本'); return
    print(f"\n== {title} ==")
    print(f"  样本 {a['n']} | 主目标 {a['hi']}({a['hi_rate']}%) | 保底 {a['lo']}({a['lo_rate']}%)")
    print(f"  最高能赚 均值 {a['mg_avg']}% / 中位 {a['mg_med']}% | 收盘 均值 {a['cg_avg']}% / 中位 {a['cg_med']}% | 收盘负占比 {a['cg_neg']}%")
    print(f"  平均回吐 {a['pull_avg']}% | 收盘盈亏比(盈/亏) {a['pl_ratio']}")
    b = a['bins']
    tot = sum(b.values())
    if tot:
        print(f"  摸高分档: <0.2 {b['<0.2']}({b['<0.2']/tot*100:.0f}%) | 0.2~0.3 {b['0.2~0.3']}({b['0.2~0.3']/tot*100:.0f}%) | 0.3~0.65 {b['0.3~0.65']}({b['0.3~0.65']/tot*100:.0f}%) | >=0.65 {b['>=0.65']}({b['>=0.65']/tot*100:.0f}%)")


def main():
    rows = load_rows()
    show('全部 4 天合计', agg(rows))
    for k in KIND_ORDER:
        show(k, agg([r for r in rows if r['kind'] == k]))
    print('\n== 按日期 ==')
    for day in sorted({r['_day'] for r in rows}):
        show(day, agg([r for r in rows if r['_day'] == day]))
    print('\n== 日期 × 类型（平均收盘% / 样本）==')
    kinds = [k for k in KIND_ORDER if any(r['kind'] == k for r in rows)]
    print('日期'.ljust(12), *[k.ljust(14) for k in kinds])
    for day in sorted({r['_day'] for r in rows}):
        cells = []
        for k in kinds:
            a = agg([r for r in rows if r['_day'] == day and r['kind'] == k])
            cells.append(f"{a['cg_avg']}%/{a['n']}" if a else '—')
        print(day.ljust(12), *[c.ljust(14) for c in cells])
    # 落盘
    out = {'total': agg(rows),
           'by_kind': {k: agg([r for r in rows if r['kind'] == k]) for k in KIND_ORDER},
           'by_day': {day: agg([r for r in rows if r['_day'] == day]) for day in sorted({r['_day'] for r in rows})}}
    json.dump(out, open(os.path.join(BT, 'backtest_review.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('\n已写入 backtest_review.json')


if __name__ == '__main__':
    main()
