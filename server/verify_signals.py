# -*- coding: utf-8 -*-
"""每日信号核对：收盘后用真实日K核对 scan_pm 当天信号，累计命中率。

输入：
  day_signals.json        当天 scan_pm 发出的信号（code/kind/trig_price/confirm_bt）
输出：
  verify_YYYYMMDD.json    当天逐条核对 + 汇总
  signal_hitrate.json     跨天累计命中率（每天覆盖追加）
  并通过 WxPusher 推送一条 HTML 核对结果（手机上直接看）
用法：
  python3 verify_signals.py            核对当天
  python3 verify_signals.py 2026-11-19 # 指定日期
"""
import json
import os
import sys
import time

import requests

import scan_pm as sp

OUT_DIR = '/home/user/Doubao/chats/38445260152008706'
H = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'}


FQ_HOSTS = [
    'https://proxy.finance.qq.com/ifzqgtimg/appstock',
    'https://web.ifzq.gtimg.cn/appstock',
    'https://ifzq.gtimg.cn/appstock',
]


def _fetch_day_once(code, date, n):
    """取日K单轮，返回 date 当天标准化 {date,open,high,low,close}；腾讯 fqkline 优先，新浪兜底。"""
    for pre in FQ_HOSTS:
        try:
            u = '%s/app/fqkline/get?param=%s,day,,%d,qfq' % (pre, code, n)
            node = requests.get(u, headers=H, timeout=8).json()['data'][code]
            arr = node.get('qfqday') or node.get('day')
            for row in reversed(arr):
                if row[0][:10] == date:
                    return {'date': date, 'open': float(row[1]),
                            'close': float(row[2]), 'high': float(row[3]),
                            'low': float(row[4])}
        except Exception:
            time.sleep(0.3)
    # 新浪兜底
    try:
        u = ('https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.'
             'getKLineData?symbol=%s&scale=240&ma=no&datalen=%d' % (code, n))
        with __import__('urllib.request', fromlist=['urlopen']).urlopen(
                __import__('urllib.request', fromlist=['Request']).Request(u, headers=H),
                timeout=15) as r:
            arr = json.loads(r.read().decode('utf-8', 'ignore'))
        for q in reversed(arr):
            if q['day'][:10] == date:
                return {'date': date, 'open': float(q['open']),
                        'close': float(q['close']), 'high': float(q['high']),
                        'low': float(q['low'])}
    except Exception:
        pass
    return None


def fetch_day(code, date, n=12):
    """日K获取，最多重试3轮，避免接口限流导致样本缺失。"""
    for _ in range(3):
        d = _fetch_day_once(code, date, n)
        if d:
            return d
        time.sleep(0.8)
    return None


def fetch_m1_day(code, date):
    """取 date 当天1分K（接口只保留最近约2天，更早返回None）。"""
    try:
        bars = sp.fetch_kline(code, 'm1', 320)
        key = date.replace('-', '')
        day_bars = [b for b in bars if b[0].startswith(key)]
        return day_bars or None
    except Exception:
        return None


def verify_one(sig, day, m1_bars=None):
    code, kind, trig = sig['code'], sig['kind'], float(sig['trig_price'])
    confirm_ts = sig.get('confirm_bt')
    time_txt = (confirm_ts[8:10] + ':' + confirm_ts[10:12]) if confirm_ts and len(confirm_ts) >= 12 else None
    out = {'code': code, 'kind': kind, 'time': time_txt, 'trig_price': round(trig, 3), 'day': day}
    if not day:
        out.update(tgt_lo=None, tgt_hi=None, result='无数据', hit_lo=False, hit_hi=False,
                   max_gain=None, close_gain=None)
        return out
    high, low, close = day['high'], day['low'], day['close']
    # 四类信号各自的目标价口径
    if kind == '早盘':
        tgt_lo = tgt_hi = round(trig * (1 + sp.AM_TAKE_LO / 100), 3)
    elif kind == '下午':
        tgt_lo = round(trig * (1 + sp.PM_TAKE_LO / 100), 3)
        tgt_hi = round(trig * (1 + sp.PM_TAKE_HI / 100), 3)
    elif kind == '反转':
        # 坑反弹目标＝回到当天开盘价（不是固定百分比）
        tgt_lo = tgt_hi = round(day['open'], 3)
    elif kind == '美股':
        tgt_lo = round(trig * (1 + sp.US_TAKE / 100), 3)
        tgt_hi = round(trig * (1 + sp.US_TAKE_HI / 100), 3)
    elif kind == '二次探底':
        # 二次探底目标 +0.65%（与推送 SB_TAKE 同口径），单目标无保底/主看之分
        tgt_lo = tgt_hi = round(trig * (1 + sp.SB_TAKE / 100), 3)
    else:
        tgt_lo = tgt_hi = None
    out.update(tgt_lo=tgt_lo, tgt_hi=tgt_hi)
    raw_high = high
    # 只取触发（确认）时刻及之后的1分K：触发前的冲高不计入触发后利润，
    # 否则会把早盘/上午的高点错算成本次信号能吃到的空间。
    if m1_bars and sig.get('confirm_bt'):
        m1_bars = [b for b in m1_bars if b[0] >= sig['confirm_bt']]
    # 真实站稳价：有1分K用收盘价最高（挤掉插针），取不到1分K回退日K最高
    if m1_bars:
        real_high = max(float(b[2]) for b in m1_bars)
        m1_filtered = True
    else:
        real_high = high
        m1_filtered = False
    spike_water = round((raw_high / real_high - 1) * 100, 2) if real_high else None
    if tgt_lo is not None:
        hit_lo, hit_hi = real_high >= tgt_lo, real_high >= tgt_hi
        max_gain = round((real_high / trig - 1) * 100, 2)
    else:
        hit_lo = hit_hi = False
        max_gain = None
    close_gain = round((close / trig - 1) * 100, 2)
    result = '吃到主目标' if hit_hi else ('吃到保底' if hit_lo else '没到目标')
    out.update(result=result, hit_lo=hit_lo, hit_hi=hit_hi,
               max_gain=max_gain, close_gain=close_gain, broke=low < trig,
               raw_high=raw_high, real_high=round(real_high, 3),
               spike_water=spike_water, m1_filtered=m1_filtered)
    return out


def load_day_signals(date):
    try:
        doc = json.load(open('day_signals.json', encoding='utf-8'))
        if doc.get('date') == date:
            return doc.get('signals') or []
    except Exception:
        pass
    return []


def update_hitrate(date, rows):
    """累计命中率：有效（有日K）信号才统计。"""
    fn = 'signal_hitrate.json'
    try:
        hr = json.load(open(fn, encoding='utf-8'))
    except Exception:
        hr = {'days': []}
    hr['days'] = [d for d in hr.get('days', []) if d.get('date') != date]
    valid = [r for r in rows if r.get('day')]
    n = len(valid)
    mg = [r['max_gain'] for r in valid if r['max_gain'] is not None]
    cg = [r['close_gain'] for r in valid if r['close_gain'] is not None]
    day_doc = {
        'date': date, 'n': n,
        'hit_hi': sum(1 for r in valid if r['hit_hi']),
        'hit_lo': sum(1 for r in valid if r['hit_lo']),
        'miss': sum(1 for r in valid if not r['hit_lo']),
        'max_gain_avg': round(sum(mg) / len(mg), 2) if mg else None,
        'close_gain_avg': round(sum(cg) / len(cg), 2) if cg else None}
    hr['days'].append(day_doc)
    hr['days'].sort(key=lambda x: x['date'])
    allv = [d for d in hr['days'] if d['n']]
    tot = sum(d['n'] for d in allv)
    hr['cumulative'] = {
        'n_signal': tot,
        'hit_hi_rate': round(sum(d['hit_hi'] for d in allv) / tot * 100, 1) if tot else None,
        'hit_lo_rate': round(sum(d['hit_lo'] for d in allv) / tot * 100, 1) if tot else None,
        'close_gain_avg': round(
            sum(d['close_gain_avg'] * d['n'] for d in allv
                if d['close_gain_avg'] is not None) / tot, 2) if tot else None}
    json.dump(hr, open(fn, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return hr, day_doc


def build_push_html(rows, day_doc, cum):
    name_map = dict(sp.HK_ETF_LIST)
    name_map.update(dict(sp.US_ETF))
    name_map.update(dict(sp.COMMODITY_FUT))

    def pf(v):
        if v is None:
            return '—', '#6B7280'
        c = '#EA6668' if v >= 0.05 else ('#059669' if v <= -0.05 else '#6B7280')
        return ('%+.2f%%' % v), c

    def line(r):
        nm = name_map.get(r['code'], r['code'])
        if not r.get('day'):
            return '<div style="padding:8px 0;">%s %s（%s）：日K没取到，数据故障</div>' % (
                nm, sp.pure_code(r['code']), r['kind'])
        res_color = ('#EA6668' if r['result'] == '吃到主目标'
                     else '#2563EB' if r['result'] == '吃到保底' else '#6B7280')
        mg_txt, mg_c = pf(r['max_gain'])
        cg_txt, cg_c = pf(r['close_gain'])
        t = r.get('time') or '—'
        return ('<div style="padding:9px 0;border-bottom:1px solid #eee;">'
                '<b>%s</b> <span style="color:%s;">%s</span><br>'
                '<span style="font-size:13px;color:#6B7280;">%s ｜ %s ｜ 触发 %s</span><br>'
                '<span style="font-size:14px;color:#1f2937;">触发价 %s ｜ 最高 %s ｜ 尾盘 %s</span><br>'
                '<span style="font-size:14px;">最高能赚 <span style="color:%s;">%s</span> ｜ '
                '收盘盈亏 <span style="color:%s;">%s</span></span></div>') % (
            nm, res_color, r['result'],
            sp.pure_code(r['code']), r['kind'], t,
            r['trig_price'], r['real_high'], r['day']['close'],
            mg_c, mg_txt, cg_c, cg_txt)
    body = ''.join(line(r) for r in rows) or '<div>当天没有发出信号</div>'
    if cum and cum.get('n_signal'):
        foot = ('<div style="margin-top:12px;padding:10px;background:#f0f5ff;border-radius:8px;'
                'font-size:15px;">累计 %d 个信号：主目标达成率 <b>%s%%</b>，'
                '保底达成率 <b>%s%%</b>，平均收盘 %+.2f%%</div>') % (
            cum['n_signal'], cum['hit_hi_rate'], cum['hit_lo_rate'],
            cum['close_gain_avg'] or 0)
    else:
        foot = ''
    return ('<div style="font-size:16px;line-height:1.6;color:#1f2937;">'
            '<div style="font-size:18px;font-weight:600;margin-bottom:8px;">'
            '信号核对 %s</div>%s%s</div>') % (day_doc['date'], body, foot)


def build_review_html(date, rows, day_doc, cum):
    """手机端信号利润复盘页：当天全部实际信号逐只利润 + 分类汇总 + 跨天累计。"""
    name_map = dict(sp.HK_ETF_LIST)
    name_map.update(dict(sp.US_ETF))
    name_map.update(dict(sp.COMMODITY_FUT))
    BLUE, RED, GREEN, GREY = '#2563EB', '#EA6668', '#059669', '#6B7280'
    KIND_ORDER = ['早盘', '下午', '反转', '二次探底', '美股']

    def card(r):
        nm = name_map.get(r['code'], r['code'])
        if not r.get('day'):
            return ('<div style="background:#fff;border-radius:14px;padding:14px;margin-bottom:10px;'
                    'border:0.5px solid rgba(0,0,0,0.08);font-size:15px;color:%s;">%s（%s）'
                    '日K未取到，属数据故障，不计入胜率</div>' % (GREY, nm, r['kind']))
        def _color(v):
            if v is None:
                return GREY
            return RED if v >= 0.05 else (GREEN if v <= -0.05 else GREY)
        max_color = _color(r['max_gain'])
        mg_txt = ('%+.2f%%' % r['max_gain']) if r['max_gain'] is not None else '—'
        cg_txt = ('%+.2f%%' % r['close_gain']) if r['close_gain'] is not None else '—'
        close_color = _color(r['close_gain'])
        res_color = (RED if r['result'] == '吃到主目标'
                     else BLUE if r['result'] == '吃到保底' else GREY)
        t = r.get('time') or '—'
        if r['tgt_lo'] == r['tgt_hi']:
            tgt_txt = '目标价 <b>%s</b>' % r['tgt_lo']
        else:
            tgt_txt = '保底 <b>%s</b> / 主看 <b>%s</b>' % (r['tgt_lo'], r['tgt_hi'])
        if r.get('m1_filtered') and r.get('spike_water') and r['spike_water'] > 0.05:
            spike_txt = '插针水分 <b style="color:%s;">%+.2f%%</b>（已挤掉）' % (GREY, r['spike_water'])
        elif r.get('m1_filtered'):
            spike_txt = '已按1分K过滤插针'
        else:
            spike_txt = '历史日无1分K，未过滤插针'
        return ('<div style="background:#fff;border-radius:14px;padding:14px 16px;margin-bottom:10px;'
                'border:0.5px solid rgba(0,0,0,0.08);">'
                '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">'
                '<span style="flex:1;min-width:0;font-size:16px;font-weight:700;color:#1A1B1C;">%s</span>'
                '<span style="flex-shrink:0;background:%s;color:#fff;font-size:14px;font-weight:700;padding:3px 11px;'
                'border-radius:999px;">%s</span></div>'
                '<div style="font-size:14px;color:%s;line-height:1.7;margin-top:6px;">'
                '触发时间 <b style="color:%s;">%s</b> ｜ %s ｜ %s<br>'
                '触发价 <b style="color:#1A1B1C;">%s</b> ｜ 最高价 <b style="color:#1A1B1C;">%s</b>'
                ' ｜ 尾盘价 <b style="color:#1A1B1C;">%s</b><br>'
                '最高能赚 <b style="color:%s;">%s</b> ｜ 收盘盈亏 <b style="color:%s;">%s</b><br>'
                '%s<br>%s'
                '</div></div>' % (
                    nm, res_color, r['result'], GREY, BLUE, t,
                    sp.pure_code(r['code']), r['kind'],
                    r['trig_price'], r['real_high'], r['day']['close'],
                    max_color, mg_txt, close_color, cg_txt,
                    tgt_txt, spike_txt))

    blocks = []
    for kind in KIND_ORDER:
        grp = [r for r in rows if r['kind'] == kind]
        if not grp:
            continue
        blocks.append(
            '<div style="font-size:17px;font-weight:800;color:%s;margin:18px 0 8px;">%s（%d 只）</div>%s'
            % (BLUE, kind, len(grp), ''.join(card(r) for r in grp)))

    n = day_doc['n']
    if n:
        summary = (
            '<div style="background:%s;border-radius:14px;padding:16px;color:#fff;">'
            '<div style="font-size:16px;font-weight:700;">当天信号 %d 只</div>'
            '<div style="font-size:15px;line-height:1.8;margin-top:6px;opacity:.95;">'
            '主目标达成 <b>%d</b> ｜ 保底达成 <b>%d</b> ｜ 没到 <b>%d</b><br>'
            '平均最高能赚 <b>%s%%</b> ｜ 平均收盘 <b>%s%%</b></div></div>'
            % (BLUE, n, day_doc['hit_hi'], day_doc['hit_lo'], day_doc['miss'],
               day_doc['max_gain_avg'], day_doc['close_gain_avg']))
    else:
        summary = ('<div style="background:#fff;border-radius:14px;padding:16px;font-size:15px;color:%s;'
                   'border:0.5px solid rgba(0,0,0,0.08);">当天没有发出有效信号（熔断或无确认），无利润样本</div>'
                   % GREY)

    cum_html = ''
    if cum and cum.get('n_signal'):
        cum_html = (
            '<div style="font-size:14px;color:%s;line-height:1.8;background:#fff;border-radius:12px;'
            'padding:14px;margin-top:14px;">跨天累计 <b style="color:#1A1B1C;">%d</b> 个信号：'
            '主目标达成率 <b style="color:%s;">%s%%</b>，保底达成率 <b style="color:%s;">%s%%</b>，'
            '平均收盘 <b>%s%%</b></div>'
            % (GREY, cum['n_signal'], GREEN, cum['hit_hi_rate'], GREEN, cum['hit_lo_rate'],
               cum['close_gain_avg']))

    return ('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1,maximum-scale=1,'
            'user-scalable=no"><title>信号利润复盘 %s</title></head>'
            '<body style="margin:0;padding:0;background:#F4F3EE;box-sizing:border-box;'
            "font-family:'PingFang SC','Roboto',Arial,sans-serif;'>"
            '<div style="max-width:560px;margin:0 auto;padding:18px 16px 32px;box-sizing:border-box;overflow-x:hidden;">'
            '<div style="font-size:22px;font-weight:800;color:#1A1B1C;">信号利润复盘</div>'
            '<div style="font-size:15px;color:%s;margin-top:4px;">%s · 收盘真实日K核对</div>'
            '%s%s%s'
            '<div style="font-size:13px;color:%s;line-height:1.8;margin-top:16px;">'
            '口径：利润均相对触发价；最高能赚＝1分K收盘价最高（真实站稳、可成交），已挤掉上影插针；'
            '收盘盈亏＝收盘价/触发价；反转目标为当天开盘价，美股为实时IOPV口径；ETF无印花税。</div>'
            '</div></body></html>'
            % (date, GREY, date, summary, ''.join(blocks), cum_html, GREY))


def main(date=None):
    if date is None:
        date = sys.argv[1] if len(sys.argv) > 1 else time.strftime('%Y-%m-%d')
    date = date if '-' in date else '%s-%s-%s' % (date[:4], date[4:6], date[6:8])
    sigs = load_day_signals(date)
    print('核对日期 %s，当天信号 %d 条' % (date, len(sigs)))
    rows = []
    skipped = 0
    for sig in sigs:
        # 低开高走早期记录未建仓、无触发价，不参与利润核对
        if not sig.get('trig_price'):
            skipped += 1; continue
        day = fetch_day(sig['code'], date)
        m1_bars = fetch_m1_day(sig['code'], date)
        rows.append(verify_one(sig, day, m1_bars))
    print('参与核对 %d 只，跳过未建仓 %d 只' % (len(rows), skipped))
    hr, day_doc = update_hitrate(date, rows)
    cum = hr.get('cumulative')
    json.dump({'date': date, 'day': day_doc, 'rows': rows, 'cumulative': cum},
              open('verify_%s.json' % date.replace('-', ''), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    # 手机端信号利润复盘网页（逐只利润 + 汇总），写到项目目录并打印 DELIVER
    page_html = build_review_html(date, rows, day_doc, cum)
    fn_html = os.path.join(OUT_DIR, '信号利润复盘_%s.html' % date.replace('-', ''))
    with open(fn_html, 'w', encoding='utf-8') as f:
        f.write(page_html)
    print('DELIVER:%s' % fn_html)
    if sp.notify_push:
        h = build_push_html(rows, day_doc, cum)
        sp.notify_push.push('信号核对·%s' % date, '信号核对', html=h)
    print(json.dumps(day_doc, ensure_ascii=False))
    print('累计:', json.dumps(cum, ensure_ascii=False))


if __name__ == '__main__':
    main()
