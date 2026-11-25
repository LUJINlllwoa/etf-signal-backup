# -*- coding: utf-8 -*-
"""生成策略回测复盘手机端网页：4天结果汇总 + 今日轨迹 + 优化建议。"""
import json, os

BT = os.path.dirname(os.path.abspath(__file__))
OUT = '/home/user/Doubao/chats/38445260152008706/策略回测复盘_20261125.html'

BLUE, RED, GREEN, GREY, BG = '#2563EB', '#EA6668', '#059669', '#6B7280', '#F4F3EE'


def c(v):
    if v is None:
        return GREY
    return RED if v >= 0.05 else (GREEN if v <= -0.05 else GREY)


def pct(v, signed=True):
    if v is None:
        return '—'
    return ('%+.2f%%' % v) if signed else ('%.2f%%' % v)


def r_agg(rs):
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
    return {'n': n, 'hi_rate': round(hi / n * 100, 1), 'lo_rate': round(lo / n * 100, 1),
            'mg_avg': round(sum(mg) / n, 2), 'mg_med': round(sorted(mg)[n // 2], 2),
            'cg_avg': round(sum(cg) / n, 2), 'dd_avg': round(sum(dd) / n, 2),
            'stop_rate': round(sum(r['stop_first'] for r in rs) / n * 100, 1),
            'cg_neg': round(sum(1 for x in cg if x <= -0.05) / n * 100, 1),
            'pl_ratio': round(sum(cg_win) / len(cg_win) / abs(sum(cg_lose) / len(cg_lose)), 2) if cg_win and cg_lose else None}


def main():
    rv = json.load(open(os.path.join(BT, 'backtest_review.json'), encoding='utf-8'))
    tr = json.load(open(os.path.join(BT, 'trajectory_20261125.json'), encoding='utf-8'))
    rep = json.load(open(os.path.join(BT, 'replay_records.json'), encoding='utf-8'))
    tot = rv['total']
    kinds = ['早盘', '下午', '反转', '二次探底', '美股']

    # 4天按类型表
    kind_rows = ''
    for k in kinds:
        a = rv['by_kind'].get(k)
        if not a:
            continue
        pl = a['pl_ratio'] if a['pl_ratio'] is not None else '—'
        kind_rows += (
            f'<tr><td><b>{k}</b></td><td>{a["n"]}</td>'
            f'<td>{a["hi_rate"]}%</td><td>{a["lo_rate"]}%</td>'
            f'<td style="color:{c(a["mg_avg"])}">{pct(a["mg_avg"])}</td>'
            f'<td style="color:{c(a["cg_avg"])}">{pct(a["cg_avg"])}</td>'
            f'<td>{a["cg_neg"]}%</td><td>{pl}</td></tr>')

    # 今日轨迹表
    tagg = {}
    for x in tr:
        tagg.setdefault(x['kind'], []).append(x)
    traj_rows = ''
    for k in kinds:
        rs = tagg.get(k)
        if not rs:
            continue
        n = len(rs)
        hi = sum(x['hit_hi'] for x in rs)
        lo = sum(x['hit_lo'] for x in rs)
        sf = sum(x['stopped_first'] for x in rs)
        dd = sum(x['max_dd'] for x in rs) / n
        gv = [x['giveback'] for x in rs if x['giveback'] is not None]
        gv_avg = sum(gv) / len(gv) if gv else None
        neg = sum(1 for x in rs if x['close_gain'] <= -0.05) / n * 100
        traj_rows += (
            f'<tr><td><b>{k}</b></td><td>{n}</td>'
            f'<td>{lo}（{lo/n*100:.0f}%）</td><td>{hi}（{hi/n*100:.0f}%）</td>'
            f'<td style="color:{RED}">{sf}（{sf/n*100:.0f}%）</td>'
            f'<td style="color:{c(dd)}">{dd:.2f}%</td>'
            f'<td style="color:{GREY}">{pct(gv_avg)}</td><td>{neg:.0f}%</td></tr>')

    # 按日期
    day_rows = ''
    for day, a in rv['by_day'].items():
        dd = day.replace('-', '')
        pl = a['pl_ratio'] if a['pl_ratio'] is not None else '—'
        day_rows += (
            f'<tr><td>{dd[4:6]}-{dd[6:8]}</td><td>{a["n"]}</td>'
            f'<td>{a["hi_rate"]}%</td><td>{a["lo_rate"]}%</td>'
            f'<td style="color:{c(a["mg_avg"])}">{pct(a["mg_avg"])}</td>'
            f'<td style="color:{c(a["cg_avg"])}">{pct(a["cg_avg"])}</td>'
            f'<td>{a["pull_avg"]}%</td><td>{pl}</td></tr>')

    # 6 天全量形态回放
    rep_n = len(rep)
    rep_kind_rows = ''
    for k in kinds:
        a = r_agg([r for r in rep if r['kind'] == k])
        if not a:
            continue
        pl = a['pl_ratio'] if a['pl_ratio'] is not None else '—'
        rep_kind_rows += (
            f'<tr><td><b>{k}</b></td><td>{a["n"]}</td>'
            f'<td>{a["hi_rate"]}%</td><td>{a["lo_rate"]}%</td>'
            f'<td style="color:{c(a["mg_avg"])}">{pct(a["mg_avg"])}</td>'
            f'<td style="color:{c(a["cg_avg"])}">{pct(a["cg_avg"])}</td>'
            f'<td style="color:{c(a["dd_avg"])}">{pct(a["dd_avg"])}</td>'
            f'<td>{a["stop_rate"]}%</td><td>{a["cg_neg"]}%</td><td>{pl}</td></tr>')
    take_rows = ''
    for tgt in [0.2, 0.3, 0.45, 0.65, 0.8]:
        reach = sum(1 for r in rep if r['max_gain'] >= tgt)
        take_rows += f'<tr><td>+{tgt}%</td><td>{reach}/{rep_n}</td><td>{reach/rep_n*100:.1f}%</td></tr>'
    stop_rows = ''
    for stp in [0.25, 0.4, 0.6, 1.0]:
        brk = sum(1 for r in rep if r['max_dd'] <= -stp)
        stop_rows += f'<tr><td>−{stp}%</td><td>{brk}/{rep_n}</td><td>{brk/rep_n*100:.1f}%</td></tr>'

    advices = [
        ('二次探底模式：建议暂停或大幅收紧', '4 天 5 只 0 达标、收盘全负 −2.16%；今日 5 只 100% 达标前先破止损。样本虽小但方向一致，先关推送观察，不要继续接飞刀。'),
        ('下午单：收紧进场门槛', '56 只里 41% 摸不到 0.2%；今日 81% 达标前先破止损、平均回撤 −2.85% 最深。假突破偏多，可上调量比/红率门槛，弱市日直接不发下午单。'),
        ('止盈偏快，不拿尾盘', '整体平均回吐 1.42%，今日达标后平均回吐 2.71%；最高能赚中位仅 0.37%，收盘中位 −0.46%。触保底/主目标分批落袋，尾盘前走，早盘/下午/反转别拿到收盘。'),
        ('弱市环境过滤要加强', '11-23 强市收盘 +0.52%、盈亏比 2.78；11-25 弱市 −2.00%、97.9% 收盘负。策略高度依赖大盘，弱市（恒生/恒科跌≥1%）应整体降仓、减少开档单；暴跌 −3% 熔断保留。'),
        ('反转（坑反弹）：摸到就走别贪', '达标率 85.7% 最高，但收盘盈亏比 0.38、回吐 1.3%~1.38%。反弹到目标就落袋，不追求拿住。'),
        ('美股池：相对最抗跌，保留', '台账平均收盘 −0.12%、回放盈亏比 2.16、触发后回撤 −0.60% 最浅，可作为相对主力池；白天闭市只做溢价、快进快出口径不变。'),
        ('止损位评估放宽，别被插针扫出', '全量回放里 86.1% 的单盘中最低会下探破 −0.25%，78.6% 破 −0.4%；现早盘/下午止损 0.25% 偏紧，容易被上下影插针洗出。可评估放宽到 −0.4%，或改成“下一根收盘站稳破位”再止损，而非盘中一碰就走。'),
        ('早盘单：形态期望偏弱，建议收紧', '回放早盘 134 笔平均收盘 −1.29%、回撤 −2.17% 最深、80.6% 收盘负，形态期望在五类里最弱；可上调早盘实体/量比门槛，或降低早盘仓位，弱市日少开早盘单。'),
    ]
    adv_html = ''
    for i, (t, d) in enumerate(advices, 1):
        adv_html += (f'<div style="background:#fff;border-radius:14px;padding:14px 16px;margin-bottom:10px;'
                     f'border:0.5px solid rgba(0,0,0,0.08);">'
                     f'<div style="font-size:16px;font-weight:700;color:{BLUE};margin-bottom:6px;">{i}. {t}</div>'
                     f'<div style="font-size:14px;color:#1A1B1C;line-height:1.7;">{d}</div></div>')

    html = f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1,maximum-scale=1,user-scalable=no">
<title>策略回测复盘 2026-11-25</title><style>
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:{BG};font-family:'PingFang SC','Roboto',sans-serif;color:#1A1B1C;padding:16px;}}
h1{{font-size:20px;font-weight:800;}}
.sub{{font-size:13px;color:{GREY};margin-top:4px;}}
.section{{background:#fff;border-radius:14px;padding:16px;margin-top:16px;}}
.st{{font-size:16px;font-weight:700;margin-bottom:10px;}}
.cards{{display:flex;flex-wrap:wrap;gap:10px;}}
.card{{flex:1 1 42%;background:{BG};border-radius:12px;padding:12px;}}
.card .num{{font-size:19px;font-weight:800;}}
.card .lab{{font-size:12px;color:{GREY};margin-top:3px;}}
table{{width:100%;border-collapse:collapse;font-size:13px;}}
th,td{{padding:8px 6px;text-align:center;border-bottom:1px solid #E4E3DD;}}
th{{color:{GREY};font-weight:600;font-size:12px;}}
td:first-child,th:first-child{{text-align:left;}}
.note{{font-size:12px;color:{GREY};line-height:1.7;margin-top:10px;}}
</style></head><body>
<h1>策略回测复盘</h1>
<div class="sub">2026-11-25 · 4 天 130 只已确认信号 + 今日 47 只轨迹 + 6 天全量回放 {rep_n} 笔</div>

<div class="section">
<div class="st">整体总览（4 天合计）</div>
<div class="cards">
<div class="card"><div class="num">{tot['n']}</div><div class="lab">信号样本</div></div>
<div class="card"><div class="num" style="color:{RED}">{tot['hi_rate']}%</div><div class="lab">主目标达成率</div></div>
<div class="card"><div class="num" style="color:{RED}">{tot['lo_rate']}%</div><div class="lab">保底达成率</div></div>
<div class="card"><div class="num" style="color:{c(tot['mg_avg'])}">{pct(tot['mg_avg'])}</div><div class="lab">平均最高能赚（中位 {pct(tot['mg_med'])}）</div></div>
<div class="card"><div class="num" style="color:{c(tot['cg_avg'])}">{pct(tot['cg_avg'])}</div><div class="lab">平均收盘（中位 {pct(tot['cg_med'])}）</div></div>
<div class="card"><div class="num" style="color:{GREY}">{tot['cg_neg']}%</div><div class="lab">收盘为负占比</div></div>
<div class="card"><div class="num" style="color:{GREY}">{tot['pull_avg']}%</div><div class="lab">平均回吐</div></div>
<div class="card"><div class="num" style="color:{GREY}">{tot['pl_ratio']}</div><div class="lab">收盘盈亏比（盈/亏）</div></div>
</div>
</div>

<div class="section">
<div class="st">按类型 · 4 天结果</div>
<table><tr><th>类型</th><th>样本</th><th>主目标</th><th>保底</th><th>平均最高</th><th>平均收盘</th><th>收盘负</th><th>盈亏比</th></tr>
{kind_rows}</table>
</div>

<div class="section">
<div class="st">今日 47 只 · 触发后轨迹</div>
<table><tr><th>类型</th><th>样本</th><th>触保底</th><th>触主目标</th><th>达标前先破止损</th><th>回撤均值</th><th>达标后回吐</th><th>收盘负</th></tr>
{traj_rows}</table>
<div class="note">回撤＝触发后盘中最低价相对触发价；“先破止损”指在触保底/主目标之前盘中先碰止损线（早盘/下午 −0.25%，反转/二次探底/美股 −0.4%）。</div>
</div>

<div class="section">
<div class="st">按日期 · 强弱对比</div>
<table><tr><th>日期</th><th>样本</th><th>主目标</th><th>保底</th><th>平均最高</th><th>平均收盘</th><th>回吐</th><th>盈亏比</th></tr>
{day_rows}</table>
</div>

<div class="section">
<div class="st">6 天全量形态回放（宽松口径 {rep_n} 笔）</div>
<table><tr><th>类型</th><th>样本</th><th>主目标</th><th>保底</th><th>平均最高</th><th>平均收盘</th><th>平均回撤</th><th>先破止损</th><th>收盘负</th><th>盈亏比</th></tr>
{rep_kind_rows}</table>
<div class="note">回放只复刻价格/实体/量能/坑底/W底形态，未过成分红率、加权、美股溢价与大盘闸门，样本被放大，绝对胜率不等于实盘，仅看形态方向。</div>
</div>

<div class="section">
<div class="st">止盈档 · 触发后最高触达率</div>
<table><tr><th>目标</th><th>触达</th><th>比例</th></tr>
{take_rows}</table>
</div>

<div class="section">
<div class="st">止损档 · 触发后盘中最低跌破率</div>
<table><tr><th>止损</th><th>跌破</th><th>比例</th></tr>
{stop_rows}</table>
<div class="note">跌破按盘中最低价（含插针）统计；实盘按收盘站稳判定会更宽松。</div>
</div>

<div class="section">
<div class="st">优化建议（按证据强弱）</div>
{adv_html}
</div>

<div class="note">局限：台账样本仅 4 天 130 只；11-23/11-24 最高能赚为旧口径（含触发前高点），偏乐观；二次探底仅 5 只；今日为弱市日。全量回放未过红率/加权/溢价/大盘闸门，形态样本被放大，止损跌破率含插针。建议再积累 1~2 周数据复核后再动参数。</div>
</body></html>'''
    open(OUT, 'w', encoding='utf-8').write(html)
    print('DELIVER:' + OUT)


if __name__ == '__main__':
    main()
