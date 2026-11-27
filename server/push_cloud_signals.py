#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把云端当日信号（正式信号 + 低开高走 + 大盘状态）打包成统一 cloud_signals.json，
推送到 GitHub 仓库固定路径，供手机 APP 通过固定 URL 拉取，实现"手机与云端同步"。

信号来源优先级：
  1. 收盘绩效 today_perf_<date>.json（带触发后最高/收盘/达标状态）——收盘后用；
  2. 盘中兜底：signal_state.json 的 position（当日已推送持仓）+ day_signals.json 台账补全，
     绩效字段留空，不估算、不 0 填充。
大盘状态取自 signal_state.json 的 market 段（state/pullback/rebound/hsi/hstech/...），
并算好前端可直接展示的 tag（暴跌/反弹启动/弱市/回落段/正常）。

用法：
  python3 push_cloud_signals.py            # 默认今天
  python3 push_cloud_signals.py 20261124   # 指定日期
无变化则不产生提交。
"""
import json, os, sys, subprocess, datetime

BT = '/home/user/.doubao/agent_mode/workspace/bt_data'
REPO = '/home/user/.doubao/agent_mode/workspace/gh_backup_repo'
OUT_DIR = os.path.join(REPO, 'signals')
OUT = os.path.join(OUT_DIR, 'cloud_signals.json')


def _load_json(fn, default=None):
    try:
        with open(fn, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def name_map():
    """code(带前缀) -> name；同时建 code6 -> code 反查。"""
    m, m6 = {}, {}
    lst = _load_json(os.path.join(BT, 'hk_etf_list.json'), [])
    if isinstance(lst, list):
        for it in lst:
            if isinstance(it, list) and len(it) >= 2:
                code, name = it[0], it[1]
                m[code] = name
                m6[code[2:] if code[:2] in ('sh', 'sz') else code] = code
    return m, m6


def code6_of(code):
    return code[2:] if code[:2] in ('sh', 'sz') else code


def time_of(bt):
    bt = bt or ''
    return bt[8:10] + ':' + bt[10:12] if len(bt) >= 12 else ''


def market_tag(mk):
    if not mk:
        return '正常'
    if mk.get('state') == 'crash':
        return '暴跌'
    if mk.get('rebound'):
        return '反弹启动'
    if mk.get('state') == 'weak':
        return '弱市'
    if mk.get('pullback'):
        return '回落段'
    return '正常'


def from_perf(rows, nm, m6):
    out = []
    for r in rows or []:
        code6 = r.get('code') or ''
        code = m6.get(code6, code6)
        out.append({
            'code': code, 'code6': code6,
            'name': r.get('name') or nm.get(code) or '',
            'kind': r.get('kind', ''), 'time': r.get('time', ''),
            'trig': r.get('trig'), 'entry': r.get('trig'),
            'target': r.get('target'), 'stop': r.get('stop'),
            'high': r.get('high'), 'close': r.get('close'),
            'hi_pct': r.get('hi_pct'), 'cl_pct': r.get('cl_pct'),
            'dip_pct': r.get('dip_pct'), 'status': r.get('status', ''),
            'source': 'perf',
        })
    return out


def from_position(state, day_signals, nm, state_today, day_today):
    """盘中兜底：position 为主，day_signals 台账补全未覆盖项。

    仅当日台账才取用：跨日开盘后、今日尚未出信号时，昨日残留不得带入云端。
    """
    pos = state.get('position', {}) if (isinstance(state, dict) and state_today) else {}
    day_list = day_signals if day_today else []
    out, seen = [], set()
    for code, p in pos.items():
        key = code + p.get('kind', '')
        seen.add(key)
        out.append({
            'code': code, 'code6': code6_of(code),
            'name': nm.get(code, ''),
            'kind': p.get('kind', ''), 'time': time_of(p.get('trig_bt')),
            'trig': p.get('trig'), 'entry': p.get('entry'),
            'target': p.get('target'), 'stop': p.get('stop'),
            'high': None, 'close': None, 'hi_pct': None, 'cl_pct': None,
            'dip_pct': None, 'status': p.get('status', 'holding'),
            'source': 'live',
        })
    for p in (day_list or []):
        code = p.get('code', '')
        key = code + p.get('kind', '')
        if key in seen:
            continue
        seen.add(key)
        out.append({
            'code': code, 'code6': code6_of(code),
            'name': nm.get(code, ''),
            'kind': p.get('kind', ''), 'time': time_of(p.get('confirm_bt')),
            'trig': p.get('trig_price'), 'entry': p.get('trig_price'),
            'target': None, 'stop': None,
            'high': None, 'close': None, 'hi_pct': None, 'cl_pct': None,
            'dip_pct': None, 'status': p.get('status', 'holding'),
            'source': 'live',
        })
    return out


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.datetime.now().strftime('%Y%m%d')
    perf_f = os.path.join(BT, 'today_perf_%s.json' % date)
    lowup_f = os.path.join(BT, 'lowup_recalc.json')
    state_f = os.path.join(BT, 'signal_state.json')
    day_f = os.path.join(BT, 'day_signals.json')

    nm, m6 = name_map()
    perf = _load_json(perf_f)
    state = _load_json(state_f, {})
    day = _load_json(day_f, {})
    day_signals = day.get('signals', []) if isinstance(day, dict) else []

    # 台账日期是否为今日（兼容 2026-11-26 / 20261126 两种写法）
    def _is_today(v):
        return bool(v) and str(v).replace('-', '') == date
    state_today = _is_today(state.get('date')) if isinstance(state, dict) else False
    day_today = _is_today(day.get('date')) if isinstance(day, dict) else False

    mk = state.get('market', {}) if isinstance(state, dict) else {}
    market = {
        'state': mk.get('state', 'normal'),
        'pullback': mk.get('pullback', False),
        'rebound': mk.get('rebound', False),
        'hsi': mk.get('hsi'), 'hstech': mk.get('hstech'),
        'hsi_pull': mk.get('hsi_pull'), 'hstech_pull': mk.get('hstech_pull'),
        'pull_low': mk.get('pull_low'),
        'tag': market_tag(mk),
    }

    if perf and perf.get('rows'):
        signals = from_perf(perf['rows'], nm, m6)
        summary = perf.get('summary', {})
        source = 'perf'
        # 低开高走为收盘回算归档，仅收盘绩效阶段带出；盘中 live 不沿用历史残留
        lowup = []
        if os.path.exists(lowup_f):
            raw = _load_json(lowup_f, [])
            lowup = raw if isinstance(raw, list) else raw.get('rows', raw.get('records', []))
    else:
        signals = from_position(state, day_signals, nm, state_today, day_today)
        summary = {'total': len(signals), 'signal_count': len(signals)}
        source = 'live'
        lowup = []

    cloud = {
        'date': date,
        'generated_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'schema': 2,
        'source': source,
        'market': market,
        'summary': summary,
        'lowup_count': len(lowup),
        'signal_count': len(signals),
        'lowup': lowup,
        'signals': signals,
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    old = None
    if os.path.exists(OUT):
        old = open(OUT, encoding='utf-8').read()
    new = json.dumps(cloud, ensure_ascii=False, indent=2)
    if old == new:
        print('内容无变化，跳过推送')
        return 0
    open(OUT, 'w', encoding='utf-8').write(new)
    print('已写出: %s  来源 %s  正式%d只 低开高走%d只 大盘[%s]' % (
        OUT, source, cloud['signal_count'], cloud['lowup_count'], market['tag']))

    subprocess.run(['git', 'add', 'signals/cloud_signals.json'], cwd=REPO, check=True)
    if subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=REPO).returncode == 0:
        print('无变化，跳过提交')
        return 0
    msg = '云端信号同步 %s %s' % (date, datetime.datetime.now().strftime('%H:%M'))
    subprocess.run(['git', 'commit', '-q', '-m', msg], cwd=REPO, check=True)
    r = subprocess.run(['git', 'push', 'origin', 'main'], cwd=REPO,
                       capture_output=True, text=True)
    if r.returncode != 0:
        print('推送失败:', r.stderr.strip()[:300])
        return 1
    print('推送成功')

    # 刷新 jsdelivr CDN 缓存，让手机端立即拿到最新
    import urllib.request
    purge_url = ('https://purge.jsdelivr.net/gh/LUJINlllwoa/etf-signal-backup@main'
                 '/signals/cloud_signals.json')
    try:
        with urllib.request.urlopen(purge_url, timeout=20) as resp:
            pr = json.loads(resp.read().decode('utf-8'))
        print('CDN刷新:', pr.get('status', 'unknown'))
    except Exception as e:
        print('CDN刷新失败(不影响raw通道):', str(e)[:120])
    return 0


if __name__ == '__main__':
    sys.exit(main())
