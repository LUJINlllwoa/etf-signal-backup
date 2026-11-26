#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按「触发后最高价有没有利润」为主口径，对比五档阈值。
主指标：max_gain（触发后 1 分 K 最高收盘相对触发价）、达标率、最高有利润占比；
尾盘收盘盈亏仅作参考。好单 = 摸到目标 或 盘中最高 > 触发价；坏单 = 触发后最高价都没超过触发价。"""
import statistics, json
import backtest_filter as bf

rows = bf.load_samples()
envs = {d: bf.market_env(d) for d in sorted(set(r['date'] for r in rows))}

def is_good_hi(r):
    return r.get('hit_hi') or r.get('hit_lo') or (r.get('max_gain') is not None and r['max_gain'] > 0)

def blocked(r, wp, pp):
    e = envs[r['date']]
    if not e: return False
    down = (e['med_close'] <= wp) or (e['pull_back'] <= pp)
    return bf.hk_index_tied(r['code']) and r['kind'] in bf.BLOCK_KINDS and down

def hi_stats(rs):
    n = len(rs)
    if n == 0: return None
    hi = [r['max_gain'] for r in rs if r.get('max_gain') is not None]
    cl = [r['close_gain'] for r in rs if r.get('close_gain') is not None]
    win = sum(1 for r in rs if r.get('hit_hi'))
    bot = sum(1 for r in rs if r.get('hit_lo'))
    pos = sum(1 for x in hi if x > 0)
    neg = sum(1 for x in hi if x <= 0)
    return {'n': n, 'win': round(win/n*100,1), 'bot': round(bot/n*100,1),
            'hi_pos': round(pos/len(hi)*100,1) if hi else 0,
            'hi_neg': round(neg/len(hi)*100,1) if hi else 0,
            'avg_hi': round(statistics.mean(hi),2) if hi else 0,
            'med_hi': round(statistics.median(hi),2) if hi else 0,
            'avg_cl': round(statistics.mean(cl),2) if cl else 0}

def evaluate(wp, pp):
    kept = [r for r in rows if not blocked(r, wp, pp)]
    blk = [r for r in rows if blocked(r, wp, pp)]
    sk, sb = hi_stats(kept), hi_stats(blk)
    kill = [r for r in blk if is_good_hi(r)]
    bad = [r for r in blk if not is_good_hi(r)]
    return {'kept': sk, 'blk': sb,
            'kill': len(kill), 'bad': len(bad),
            'kill_rate': round(len(kill)/len(blk)*100,1) if blk else 0}

TIERS = [('宽松 -1.5%', -1.5, -1.5), ('当前 -1.0%', -1.0, -1.0), ('收紧 -0.5%', -0.5, -0.5),
         ('弱-1.0/回落-1.5', -1.0, -1.5), ('弱-1.5/回落-1.0', -1.5, -1.0)]

print('主口径：触发后最高价 max_gain（%）；尾盘收盘仅参考')
print(f"{'档位':<14} {'拦':>3} {'放':>3} | 放行均最高 中位最高 达标 最高有利润 | 拦截均最高 中位最高 最高无利润 | 误杀 拦对 误杀率")
res = []
for name, wp, pp in TIERS:
    e = evaluate(wp, pp); res.append((name, e))
    k, b = e['kept'], e['blk']
    print(f"{name:<14} {b['n']:>3} {k['n']:>3} | "
          f"{k['avg_hi']:>+8.2f} {k['med_hi']:>+7.2f} {k['win']:>5.1f} {k['hi_pos']:>7.1f} | "
          f"{b['avg_hi']:>+8.2f} {b['med_hi']:>+7.2f} {b['hi_neg']:>8.1f} | "
          f"{e['kill']:>4} {e['bad']:>4} {e['kill_rate']:>5.1f}%")

print()
print('=== 拦截组明细（按触发后最高 max_gain 升序，越靠前越该拦）===')
_, cur = res[1]
for r in sorted([r for r in rows if blocked(r, -1.0, -1.0)], key=lambda x: x.get('max_gain') or 0):
    flag = '误杀' if is_good_hi(r) else '拦对'
    print(f"  {r['date']} {r['code']} {r['kind']:4s} 最高{r.get('max_gain'):+.2f}% 收盘{r.get('close_gain'):+.2f}% "
          f"主目标={r.get('hit_hi')} [{flag}]")

print()
print('=== 分类型（当前 -1.0/-1.0，最高价口径）===')
for kd in sorted(set(r['kind'] for r in rows)):
    sub = [r for r in rows if r['kind'] == kd]
    s_all = hi_stats(sub)
    sub_b = [r for r in sub if blocked(r, -1.0, -1.0)]
    sub_k = [r for r in sub if not blocked(r, -1.0, -1.0)]
    print(f"【{kd}】旧 n={s_all['n']} 均最高{s_all['avg_hi']:+.2f} 中位{s_all['med_hi']:+.2f} 达标{s_all['win']}% 最高有利润{s_all['hi_pos']}%")
    if sub_b:
        sb = hi_stats(sub_b)
        print(f"      拦截{sb['n']} 均最高{sb['avg_hi']:+.2f} 中位{sb['med_hi']:+.2f} 最高无利润{sb['hi_neg']}%")
    if sub_k:
        sk = hi_stats(sub_k)
        print(f"      放行{sk['n']} 均最高{sk['avg_hi']:+.2f} 中位{sk['med_hi']:+.2f} 达标{sk['win']}% 最高有利润{sk['hi_pos']}%")

json.dump([{'tier': n, **{k: v for k, v in e.items() if k in ('kill','bad','kill_rate')},
            'kept': e['kept'], 'blk': e['blk']} for n, e in res],
          open(f'{bf.BASE}/backtest_thresholds_hi.json', 'w'), ensure_ascii=False, indent=1)
print('\n已存 backtest_thresholds_hi.json')
