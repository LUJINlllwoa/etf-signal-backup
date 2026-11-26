#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阈值松紧对比：weak(对开盘) 与 pullback(距高点回落) 各取三档，
在同一批 183 笔样本上比较放行组质量、拦截组质量、误杀/拦对。"""
import statistics
import backtest_filter as bf

rows = bf.load_samples()
envs = {d: bf.market_env(d) for d in sorted(set(r['date'] for r in rows))}

def is_good(r): return r.get('hit_hi') or (r.get('close_gain') or 0) > 0

def blocked(r, wp, pp):
    e = envs[r['date']]
    if not e: return False
    down = (e['med_close'] <= wp) or (e['pull_back'] <= pp)
    return bf.hk_index_tied(r['code']) and r['kind'] in bf.BLOCK_KINDS and down

def evaluate(wp, pp):
    kept = [r for r in rows if not blocked(r, wp, pp)]
    blk = [r for r in rows if blocked(r, wp, pp)]
    sk, sb = bf.stats(kept), bf.stats(blk)
    kill = [r for r in blk if is_good(r)]
    bad = [r for r in blk if not is_good(r)]
    return {
        'wp': wp, 'pp': pp,
        'kept_n': sk['n'], 'kept_win': sk['win_rate'], 'kept_pos': sk['win_close_rate'],
        'kept_loss': sk['loss_rate'], 'kept_cl': sk['avg_cl'], 'kept_hi': sk['avg_hi'],
        'blk_n': sb['n'] if sb else 0, 'blk_win': sb['win_rate'] if sb else 0,
        'blk_pos': sb['win_close_rate'] if sb else 0, 'blk_loss': sb['loss_rate'] if sb else 0,
        'blk_cl': sb['avg_cl'] if sb else 0,
        'kill': len(kill), 'bad': len(bad),
        'kill_rate': round(len(kill)/len(blk)*100,1) if blk else 0,
    }

TIERS = [('宽松 -1.5%', -1.5, -1.5), ('当前 -1.0%', -1.0, -1.0), ('收紧 -0.5%', -0.5, -0.5),
         ('弱-1.0/回落-1.5', -1.0, -1.5), ('弱-1.5/回落-1.0', -1.5, -1.0)]

print(f"{'档位':<12} {'拦截':>4} {'放行':>4} | 放行达标 放行收正 放行亏损 放行均收 | 拦截达标 拦截收正 拦截亏损 拦截均收 | 误杀 拦对 误杀率")
res = []
for name, wp, pp in TIERS:
    e = evaluate(wp, pp)
    res.append((name, e))
    print(f"{name:<12} {e['blk_n']:>4} {e['kept_n']:>4} | "
          f"{e['kept_win']:>6.1f} {e['kept_pos']:>6.1f} {e['kept_loss']:>6.1f} {e['kept_cl']:>+7.2f} | "
          f"{e['blk_win']:>6.1f} {e['blk_pos']:>6.1f} {e['blk_loss']:>6.1f} {e['blk_cl']:>+7.2f} | "
          f"{e['kill']:>4} {e['bad']:>4} {e['kill_rate']:>5.1f}%")

print()
print('=== 每日环境在各档下是否拦截（down_env）===')
print(f"{'日期':<10} {'收盘中位':>8} {'距高点':>8} | 宽松  当前  收紧")
for d, e in envs.items():
    if not e: continue
    flags = []
    for _, wp, pp in TIERS:
        down = (e['med_close'] <= wp) or (e['pull_back'] <= pp)
        flags.append('拦' if down else '放')
    print(f"{d:<10} {e['med_close']:>+8.2f} {e['pull_back']:>+8.2f} | {flags[0]:>4} {flags[1]:>4} {flags[2]:>4}")

# 保存
import json
json.dump([{'tier': n, **e} for n, e in res],
          open(f'{bf.BASE}/backtest_thresholds.json', 'w'), ensure_ascii=False, indent=1)
print('\n已存 backtest_thresholds.json')
