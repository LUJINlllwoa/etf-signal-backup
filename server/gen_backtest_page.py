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


def main():
    rv = json.load(open(os.path.join(BT, 'backtest_review.json'), encoding='utf-8'))
    tr = json.load(open(os.path.join(BT, 'trajectory_20261125.json'), encoding='utf-8'))
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

    advices = [
        ('二次探底模式：建议暂停或大幅收紧', '4 天 5 只 0 达标、收盘全负 −2.16%；今日 5 只 100% 达标前先破止损。样本虽小但方向一致，先关推送观察，不要继续接飞刀。'),
        ('下午单：收紧进场门槛', '56 只里 41% 摸不到 0.2%；今日 81% 达标前先破止损、平均回撤 −2.85% 最深。假突破偏多，可上调量比/红率门槛，弱市日直接不发下午单。'),
        ('止盈偏快，不拿尾盘', '整体平均回吐 1.42%，今日达标后平均回吐 2.71%；最高能赚中位仅 0.37%，收盘中位 −0.46%。触保底/主目标分批落袋，尾盘前走，早盘/下午/反转别拿到收盘。'),
        ('弱市环境过滤要加强', '11-23 强市收盘 +0.52%、盈亏比 2.78；11-25 弱市 −2.00%、97.9% 收盘负。策略高度依赖大盘，弱市（恒生/恒科跌≥1%）应整体降仓、减少开档单；暴跌 −3% 熔断保留。'),
        ('反转（坑反弹）：摸到就走别贪', '达标率 85.7% 最高，但收盘盈亏比 0.38、回吐 1.3%~1.38%。反弹到目标就落袋，不追求拿住。'),
        ('美股池：相对最抗跌，保留', '平均收盘 −0.12%、触发后回撤最浅，可作为相对主力池；白天闭市只做溢价、快进快出口径不变。'),
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
<div class="sub">2026-11-25 · 4 天 130 只已确认信号 + 今日 47 只逐分钟轨迹</div>

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
<div class="st">优化建议（按证据强弱）</div>
{adv_html}
</div>

<div class="note">局限：样本仅 4 天 130 只；11-23/11-24 最高能赚为旧口径（含触发前高点），偏乐观；二次探底仅 5 只；今日为弱市日，轨迹止损比例不能外推为常态。建议再积累 1~2 周数据复核后再动参数。</div>
</body></html>'''
    open(OUT, 'w', encoding='utf-8').write(html)
    print('DELIVER:' + OUT)


if __name__ == '__main__':
    main()
