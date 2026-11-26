#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回测：新大盘过滤规则（weak/回落段暂停港股 下午/反转/二次探底；商品/美股/早盘豁免）
对比旧规则（全推）与新规则（剔弱市/回落段港股三类）的达标率、亏损率、误杀/拦对。

口径限制（务必知悉）：
- 历史指数分时接口当前取不到，大盘环境用「当天港股 ETF 池 1 分钟 K 截面中位数」代理：
  med(t)=池内各票 close/open-1 的中位数；weak=收盘 med<=-1%；pullback=收盘 med 距盘中 med 高点 <=-1%。
- verify 台账无触发时点，大盘标签按「日级」分层，而非信号时点；属近似回测。
- 结果字段沿用 verify 已按新口径算好的 max_gain(触发后最高收盘%)、close_gain(收盘盈亏%)、
  hit_hi(吃到主目标)、hit_lo(摸到保底)、broke(破止损)。
"""
import json, os, statistics, glob, re

BASE = '/home/user/.doubao/agent_mode/workspace/bt_data'
ARCH = '/home/user/Doubao/chats/38445260152008706/etf-rank-board/archive'

COMMODITY_CODES = {'sz159980', 'sz159981', 'sz159985'}
US_CODES = {
    'sh513500','sz159612','sz159655','sh513650','sz159501','sh513100','sz159696','sz159941',
    'sz159659','sh513300','sh513870','sh513390','sz159660','sz159632','sh513110','sz159513',
    'sz159509','sz159577','sh513850','sh513290','sz159502','sz159529','sh513400','sh513350','sz159518'}
ZOMBIES = {'sh513680','sh513790','sh513960','sz159799','sz159963'}

BLOCK_KINDS = {'下午', '反转', '二次探底'}
WEAK_PCT = -1.0
PULL_PCT = -1.0

def is_us(c): return c in US_CODES
def is_comm(c): return c in COMMODITY_CODES
def hk_index_tied(c): return c not in COMMODITY_CODES and c not in US_CODES

def load_1126():
    """从 signal_state 持仓台账 + 当日 m1 重算触发后结果（新口径），转成 verify 同构 row。"""
    p = f'{BASE}/signal_state.json'
    if not os.path.exists(p): return []
    d = json.load(open(p))
    m1dir = f'{ARCH}/intraday/20261126/m1'
    rows = []
    for code, v in d.get('position', {}).items():
        fp = f'{m1dir}/{code}.json'
        if not os.path.exists(fp): continue
        dct = json.load(open(fp))
        bars = [x for _, x in sorted(dct.items())]
        if not bars: continue
        trig = v['trig']; target = v.get('target'); stop = v.get('stop')
        bt = v.get('trig_bt')
        idx = next((i for i, b in enumerate(bars) if b[0] == bt), None)
        if idx is None:
            idx = next((i for i, b in enumerate(bars) if float(b[2]) >= trig), 0)
        after = bars[idx:]
        real_high = max(float(b[2]) for b in after)
        close = float(bars[-1][2])
        max_gain = round((real_high / trig - 1) * 100, 2) if trig else None
        close_gain = round((close / trig - 1) * 100, 2) if trig else None
        hit_hi = bool(target and real_high >= target)
        stopped_after = bool(stop and any(float(b[4]) <= stop for b in after))
        rows.append({'code': code, 'kind': v['kind'], 'trig_price': trig,
                     'target': target, 'stop': stop, 'hit_hi': hit_hi, 'hit_lo': hit_hi,
                     'max_gain': max_gain, 'close_gain': close_gain, 'broke': False,
                     '_stopped_after': stopped_after, 'date': '20261126'})
    return rows

def load_samples():
    rows = []
    for d in ['20261120','20261123','20261124','20261125']:
        p = f'{BASE}/verify_{d}.json'
        if not os.path.exists(p): continue
        x = json.load(open(p))
        for r in x['rows']:
            r['date'] = d
            rows.append(r)
    rows += load_1126()
    return rows

def m1_dir(d):
    for cand in [f'{ARCH}/intraday/{d}/m1', f'{ARCH}/{d}/m1']:
        if os.path.isdir(cand): return cand
    return None

def market_env(d):
    """日级截面大盘代理。返回 dict(med_close, med_hi, weak, pullback, down_env, n_pool)"""
    dd = m1_dir(d)
    if not dd: return None
    # 逐票时点序列
    series = {}  # code -> {ts: close/open-1}
    opens = {}
    for f in glob.glob(f'{dd}/*.json'):
        code = os.path.basename(f)[:-5]
        if code in COMMODITY_CODES or code in US_CODES or code in ZOMBIES: continue
        try:
            dct = json.load(open(f))
        except Exception: continue
        if not dct: continue
        items = sorted(dct.items())
        first = items[0][1]
        op = float(first[1])
        if op <= 0: continue
        opens[code] = op
        series[code] = {ts: float(v[2])/op - 1 for ts, v in items}
    if not series: return None
    times = sorted(set(ts for s in series.values() for ts in s))
    med = {}
    for t in times:
        vals = [s[t] for s in series.values() if t in s]
        if vals: med[t] = statistics.median(vals)
    med_close = med[times[-1]] * 100
    med_hi = max(med.values()) * 100
    weak = med_close <= WEAK_PCT
    # 回落幅度须把百分比还原成指数再比：close_idx/hi_idx-1（不能直接拿百分比数值相除）
    close_idx, hi_idx = 1 + med_close / 100, 1 + med_hi / 100
    pullback = (close_idx / hi_idx - 1) * 100 <= PULL_PCT
    return {'med_close': round(med_close,2), 'med_hi': round(med_hi,2),
            'pull_back': round((close_idx/hi_idx-1)*100,2), 'weak': weak,
            'pullback': pullback, 'down_env': weak or pullback, 'n_pool': len(series)}

def stats(rows):
    n = len(rows)
    if n == 0: return None
    win = sum(1 for r in rows if r.get('hit_hi'))
    bot = sum(1 for r in rows if r.get('hit_lo'))
    loss = sum(1 for r in rows if (r.get('close_gain') or 0) < 0)
    win_close = sum(1 for r in rows if (r.get('close_gain') or 0) > 0)
    avg_cl = statistics.mean([r.get('close_gain') or 0 for r in rows])
    avg_hi = statistics.mean([r.get('max_gain') or 0 for r in rows])
    return {'n': n, 'win': win, 'win_rate': round(win/n*100,1),
            'bot': bot, 'bot_rate': round(bot/n*100,1),
            'loss': loss, 'loss_rate': round(loss/n*100,1),
            'win_close': win_close, 'win_close_rate': round(win_close/n*100,1),
            'avg_cl': round(avg_cl,2), 'avg_hi': round(avg_hi,2)}

def main():
    rows = load_samples()
    envs = {d: market_env(d) for d in sorted(set(r['date'] for r in rows))}

    print('='*70)
    print('一、每日大盘环境（港股池截面代理，日级）')
    print('='*70)
    for d, e in envs.items():
        if not e: print(d, '无 m1 归档'); continue
        print(f"{d} 池内{e['n_pool']:3d}只 收盘中位 {e['med_close']:+.2f}% "
              f"盘中中位高 {e['med_hi']:+.2f}% 回落 {e['pull_back']:+.2f}% "
              f"| weak={e['weak']} pullback={e['pullback']} down_env={e['down_env']}")

    # 标记每条样本是否会被新规则拦截
    for r in rows:
        e = envs[r['date']]
        tied = hk_index_tied(r['code'])
        blocked_target = tied and r['kind'] in BLOCK_KINDS
        down = bool(e and e['down_env'])
        r['_tied'] = tied
        r['_blocked_by_new'] = blocked_target and down
        r['_env'] = e

    print()
    print('='*70)
    print('二、旧规则（全推） vs 新规则（剔弱市/回落段港股三类）')
    print('='*70)
    old = stats(rows)
    kept = [r for r in rows if not r['_blocked_by_new']]
    blocked = [r for r in rows if r['_blocked_by_new']]
    new = stats(kept)

    def line(tag, s):
        if not s: print(f'{tag}: 无样本'); return
        print(f"{tag}: n={s['n']} 主目标达标 {s['win']}({s['win_rate']}%) "
              f"保底 {s['bot']}({s['bot_rate']}%) 收盘为正 {s['win_close']}({s['win_close_rate']}%) "
              f"亏损 {s['loss']}({s['loss_rate']}%) 平均收盘 {s['avg_cl']:+.2f}% "
              f"平均最高 {s['avg_hi']:+.2f}%")
    line('旧规则全推 ', old)
    line('新规则放行 ', new)
    line('新规则拦截 ', stats(blocked))

    print()
    print('='*70)
    print('三、被拦截样本拆解：误杀（实际达标） vs 拦对（实际亏损/止损）')
    print('='*70)
    if blocked:
        def is_good(r): return r.get('hit_hi') or (r.get('close_gain') or 0) > 0
        kill = [r for r in blocked if is_good(r)]              # 误杀：达标或收盘为正
        bottom_only = [r for r in blocked if r.get('hit_lo') and not r.get('hit_hi')]
        bad = [r for r in blocked if not is_good(r)]          # 拦对：未达标且收盘亏
        print(f'拦截总数 {len(blocked)}')
        print(f'  误杀·好单(达标或收盘为正) {len(kill)} ({len(kill)/len(blocked)*100:.1f}%)')
        print(f'  其中仅摸到保底未主目标 {len(bottom_only)}')
        print(f'  拦对·坏单(未达标且收盘亏) {len(bad)} ({len(bad)/len(blocked)*100:.1f}%)')
        print(f'  拦截样本平均收盘 {statistics.mean([r.get("close_gain") or 0 for r in blocked]):+.2f}%')
        print()
        print('被拦截明细：')
        for r in sorted(blocked, key=lambda x: x.get('close_gain') or 0):
            flag = '误杀' if is_good(r) else '拦对'
            print(f"  {r['date']} {r['code']} {r['kind']:4s} 触发{r['trig_price']} "
                  f"最高{r.get('max_gain'):+.2f}% 收盘{r.get('close_gain'):+.2f}% "
                  f"保底={r.get('hit_lo')} 主目标={r.get('hit_hi')} [{flag}]")

    print()
    print('='*70)
    print('四、分类型 × 新规则放行/拦截 表现')
    print('='*70)
    kinds = sorted(set(r['kind'] for r in rows))
    for k in kinds:
        sub = [r for r in rows if r['kind'] == k]
        s_old = stats(sub)
        sub_k = [r for r in sub if not r['_blocked_by_new']]
        s_new = stats(sub_k)
        sub_b = [r for r in sub if r['_blocked_by_new']]
        print(f'【{k}】旧: n={s_old["n"]} 达标{s_old["win_rate"]}% 收盘为正{s_old["win_close_rate"]}% 亏损{s_old["loss_rate"]}% 收盘{s_old["avg_cl"]:+.2f}%')
        if sub_b:
            sb = stats(sub_b)
            print(f'      拦截 {sb["n"]} 只: 达标{sb["win_rate"]}% 收盘为正{sb["win_close_rate"]}% 亏损{sb["loss_rate"]}% 收盘{sb["avg_cl"]:+.2f}%')
        if s_new:
            print(f'      放行 n={s_new["n"]} 达标{s_new["win_rate"]}% 收盘为正{s_new["win_close_rate"]}% 亏损{s_new["loss_rate"]}% 收盘{s_new["avg_cl"]:+.2f}%')

    # 落盘明细
    out = {'env': {d: e for d, e in envs.items()},
           'old': old, 'new': new, 'blocked': stats(blocked),
           'blocked_detail': [{k: r[k] for k in ('date','code','kind','trig_price','max_gain','close_gain','hit_hi','hit_lo','broke')} for r in blocked]}
    json.dump(out, open(f'{BASE}/backtest_filter_result.json','w'), ensure_ascii=False, indent=1)
    print('\n明细已存 backtest_filter_result.json')

if __name__ == '__main__':
    main()
