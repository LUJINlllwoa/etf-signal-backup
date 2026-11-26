#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""港股看板信号云端全天扫描（早盘启动信号 + 下午信号），完整复刻 APP 逻辑。

架构：平台定时任务最小间隔 1 小时，无法每分钟触发，所以每小时的看门狗(--ensure)
负责把常驻进程拉起；常驻进程(--loop)在云端自己每分钟循环，9:30-14:50 按 APP 逻辑扫描，
出 confirm 就推系统通知(ntfy) + 微信(PushPlus/Server酱)，同一只当日不重复。

两套信号（候选门槛完全相同，确认闸不同）：
  早盘启动信号（9:30-10:00，requireRed=false）：只看“下一根站稳触发价”，
       confirm 后低开、高开各锁 score 最高一只（最多2只），目标 0.3~0.65；
  下午信号（10:00-14:50，requireRed=true）：下一根站稳 + 成分红率(按个数)≥60% + 加权就绪，
       每只 confirm 都推，目标 0.2~0.3。
涨幅与红率一律以开盘价为基准；成分股走防陈旧通道，陈旧帧红率标暂缺，不当0%。

用法：
  python3 scan_pm.py --loop     常驻循环（每分钟扫，14:50 自动退出）
  python3 scan_pm.py --ensure   看门狗：常驻不在就后台拉起
  python3 scan_pm.py            单次扫描（手动用，带文件锁）
"""
import fcntl
import html
import json
import os
import signal
import subprocess
import sys
import time
import datetime
from concurrent.futures import ThreadPoolExecutor

import requests

import quote_util as qu

try:
    import notify_push
except Exception:
    notify_push = None

try:
    import huawei_push
except Exception:
    huawei_push = None

try:
    import hm_push
except Exception:
    hm_push = None

# —— 常量（对齐 APP，单位 %）——
START_PCT = 0.15
START_BODY = 0.15
RV_MIN = 1.5
TRIGGER_LOOKBACK = 4   # 触发根回看窗口：单轮扫描最坏85秒，最近4根内的放量阳线都能识别，不再只看最新根
BREAKOUT_CONS = 0.05
BREAKOUT_PULLBACK = 0.99
PM_RED_MIN = 0.6
AM_TARGET = 0.65  # 早盘第一波目标：+0.65% 就走（早盘行情大，拿住吃大肉）
QUICK_TAKE = 0.2   # 下午快进快出目标：+0.2% 就走
QUICK_STOP = 0.25  # 快止损：跌破买入价 0.25% 立刻走，不恋战
PM_RV_MIN = 0.4   # 下午确认根量比门槛：缩量(量比<0.4)的突破多为假突破，回测成功率仅26% vs 有量60%
AM_TAKE_LO = AM_TARGET
AM_TAKE_HI = AM_TARGET
PM_TAKE_LO = QUICK_TAKE
PM_TAKE_HI = 0.3
AM_START = 570   # 9:30
AM_END = 600     # 10:00
AM_CLOSE = 690   # 11:30 A股上午收盘（全部标的均为A股上市ETF）
PM_START = 780   # 13:00 A股下午开盘
PM_END = 890     # 14:50
STALE_KLINE_GAP = 3  # 窗口内最新1分K根落后当前分钟超过该值，判定行情停更
# —— 低开高走早期信号：开盘最早两根5分K（9:30-9:40）低开翻红就微信提醒，不等站稳/量比 ——
LOWUP_EARLY_GAP = -0.05   # 低开门槛(%)：开盘对昨收 < 该值（对齐 APP LOWUP_GAP）
LOWUP_EARLY_UP = 0.05     # 高走门槛(%)：现价对开盘 > 该值（对齐 APP LOWUP_UP）
LOWUP_B2_END = 580        # 第二根5分K结束(9:40)，之后不再推早期信号
FAIL_BUFFER = 0.1  # 下一根轻微回踩缓冲(%)：收盘只低0.1%以内不算破位，继续观察，不被1厘洗出
MAX_RESET = 2      # fail后二次启动最多解锁次数，防止在反复假突破的票上无限打脸
MAX_REENTRY = 1    # 破止损后重新启动最多1次：已确认又被止损出局，若再次放量创新高可重新进场，防止在弱票上反复打脸
REENTRY_ALLOWED = set()  # 本轮已破止损(position stopped)、允许重新启动的票（每轮扫描开始时刷新）
POLL_SLEEP = 4     # 常驻进程轮询间隔（秒）；触发即推，密集轮询让新触发根最快被发现，整体延迟压到约8秒内
HEARTBEAT_STALE = 180  # 心跳超过这个秒数没更新，判定进程卡死，看门狗杀掉重启
IDLE_SLEEP = 30    # 非信号窗口的常驻等待间隔（秒），安静等待不空转
# —— 目标价真实站稳（防插针）：单根上影线超过该比例(%)算插针，达标看1分K收盘价 ——
WICK_MAX = 0.30
# —— 追高保护：确认推送时，若现价相对原始触发根已涨超“一个完整目标空间”，
# 说明等你进场时行情已走完、进场太晚，本单放弃（phase 置 chase 终态，当天不再做）。
# 通过的信号目标/止损一律基于确认时现价（实际可成交价）重算，保证有真实空间。——
CHASE_CAP_RATIO = 1.0
# —— 触发根年龄上限：ready 触发根超过该分钟数仍未 confirm，说明低价窗口已过
# （红率/加权反复不达标或行情已走），直接置 chase 放弃，不再抱着陈旧触发价误导买入。——
MAX_TRIG_AGE = 15

# —— 坑反弹信号（方案B：贴坑底企稳埋伏，回测胜率68.8%）——
REV_BOX_N = 8        # 底部盘整区间回看根数
REV_LOW_BACK = 5     # 兼容旧引用
REV_STOP = 0.4       # 固定止损 -0.4%（基于进场价，单笔风险确定）
REV_TAKE = 0.6       # 止盈 +0.6%
REV_NEAR = 0.4       # 进场收盘必须贴坑底：距坑底≤0.4%，排除长下影线虚高
REV_AGE_LO = 5       # 坑底根龄窗口（9:35–9:45）
REV_AGE_HI = 15
REV_DEPTH = 2.0      # 或当日坑深≥2%（深坑超跌反弹）
# 市场环境开关（普跌日整体关闭坑反弹）
REV_REGIME_THRESH = 45   # 9:35“贴当日最低且在跌”票占比≥45% => 普跌日
REV_NL_TOL = 0.15        # 现价贴当日最低的容差%
# 坑反弹确认门槛（逆势，弱市也抓；比顺势宽松但仍防接飞刀）
REV_RED_MIN = 0.4      # 成分股红率门槛（顺势为 0.6）
REV_W_NORMAL = 0.0     # 正常市：成分加权 w 需为正
REV_W_WEAK = -0.5      # 弱市：允许成分股小跌，但不能深度暴跌（防接飞刀）

# —— 二次探底再拉起（第一波冲高后回踩、W底企稳，捕捉坑底拉起那一段）——
SB_FIRST_PUSH = 1.0     # 第一波冲高最小涨幅%（相对开盘），确认是有力度的真拉升
SB_PULLBACK_GAP = 8     # 第一波高点距当前至少多少根，确认已经历回踩而非正在冲高
SB_NECK_MIN = 0.3       # 两底之间中间反弹的最小幅度%，确认是W底而非单底/下跌中继
SB_PIT_TOL = 0.1        # 第二底相对第一底允许的破位容差%，超过视为破位不接
SB_BODY_MIN = 0.15      # 拉起触发根最小实体阳线%
SB_RV_MIN = 1.5         # 拉起触发根量比门槛，要真放量
SB_LIFT_MIN = 0.1       # 触发根收盘相对坑底最小抬升%
SB_LIFT_MAX = 0.7       # 触发根收盘相对坑底最大抬升%，超过说明已涨高、不追
SB_PB_KEEP = 0.2        # 回踩最多吐回第一波涨幅的(1-该值)，至少保留该比例，维持上升结构
SB_TAKE = 0.65          # 目标 +0.65%
SB_STOP = 0.4           # 止损 -0.4%

# —— 大盘环境开关（弱市自动踩刹车）——
INDEX_CODES = ['r_hkHSI', 'r_hkHSTECH']  # 恒生指数、恒生科技
INDEX_WEAK_PCT = -1.0   # 任一指数跌幅达到/超过 1% 即判弱市
CRASH_PCT = -3.0       # 任一指数跌幅达到/超过 3% 即判暴跌硬熔断：所有信号（含下午单）全部停止，30天5526样本暴跌安全率仅4%
WEAK_PM_W = 0.2         # 弱市下午成分加权门槛抬高：强共振才进
MARKET = {'state': 'normal', 'hsi': None, 'hstech': None}

ZOMBIES = {'sh513680', 'sh513790', 'sh513960', 'sz159799', 'sz159963'}

# —— 商品期货ETF（A股时段交易；跟踪期货合约、无成分股，纯价格/量能确认；信号模式与港股一致）——
COMMODITY_FUT = [('sz159980', '有色ETF大成'),
                 ('sz159981', '能源化工ETF建信'),
                 ('sz159985', '豆粕ETF华夏')]
COMMODITY_CODES = {c for c, _n in COMMODITY_FUT}

# —— 美股QDII（A股时段交易、跟踪隔夜美股；白天美股闭市，无成分股实时红率，
# 用「溢价 + 价格量能」确认；只做坑底反弹，不追开盘）——
US_ETF = [
    ('sh513500', '标普500ETF博时'), ('sz159612', '标普500ETF国泰'),
    ('sz159655', '标普500ETF华夏'), ('sh513650', '标普500ETF南方'),
    ('sz159501', '纳指ETF嘉实'), ('sh513100', '纳指ETF国泰'),
    ('sz159696', '纳指ETF易方达'), ('sz159941', '纳指ETF广发'),
    ('sz159659', '纳斯达克100ETF招商'), ('sh513300', '纳斯达克ETF华夏'),
    ('sh513870', '纳指ETF富国'), ('sh513390', '纳指100ETF博时'),
    ('sz159660', '纳指ETF汇添富'), ('sz159632', '纳斯达克ETF华安'),
    ('sh513110', '纳指ETF华泰柏瑞'), ('sz159513', '纳斯达克100ETF大成'),
    ('sz159509', '纳指科技ETF景顺'), ('sz159577', '美国50ETF汇添富'),
    ('sh513850', '美国50ETF易方达'), ('sh513290', '纳指生物科技ETF汇添富'),
    ('sz159502', '标普生物科技ETF嘉实'), ('sz159529', '标普消费ETF景顺'),
    ('sh513400', '道琼斯ETF鹏华'), ('sh513350', '标普油气ETF富国'),
    ('sz159518', '标普油气ETF嘉实'),
]
US_CODES = {c for c, _n in US_ETF}
US_TAKE = 0.3            # 保底目标（坑底100%有0.3反弹）
US_TAKE_HI = 0.8         # 主看目标（坑底89%能到0.8，喝更多汤）
US_STOP = 0.25           # 快止损
US_HIGH_PREM = 10.0      # 现溢价≥10% 标「高溢价」风险（纳指类常态12~18%）
US_PREM_CONTRACT = 2.0   # 盘中溢价相对昨溢价收敛超过2pt=杀溢价进行时，不进场
US_NAV_CACHE = {}        # 静态净值兜底缓存（优先用腾讯实时净值，缺失时才用）

KLINE_HOSTS = [
    'https://ifzq.gtimg.cn/appstock',
    'https://web.ifzq.gtimg.cn/appstock',
    'https://proxy.finance.qq.com/ifzqgtimg/appstock',
]
H = {'User-Agent': 'Mozilla/5.0'}

TODAY = datetime.date.today().strftime('%Y-%m-%d')

with open('hk_etf_list.json', 'r', encoding='utf-8') as f:
    HK_ETF_LIST = json.load(f)
with open('etf_holdings_weighted.json', 'r', encoding='utf-8') as f:
    HOLDINGS = json.load(f)
with open('commodity_holdings.json', 'r', encoding='utf-8') as f:
    COMMODITY_HOLDINGS = json.load(f)
try:
    PREV_AMT = json.load(open('prev_amt.json', encoding='utf-8'))
except Exception:
    PREV_AMT = {}


def now_min():
    d = datetime.datetime.now()
    return d.hour * 60 + d.minute


def trig_bt_min(trig_bt):
    """触发根时间戳(YYYYMMDDHHMM)转当日分钟数，用于计算触发根年龄。"""
    return int(trig_bt[8:10]) * 60 + int(trig_bt[10:12])


def pure_code(code):
    """去掉 sh/sz 前缀，返回纯数字代码（用于消息展示和复制，同花顺认纯数字）。"""
    return code[2:] if len(code) > 2 and code[:2] in ('sh', 'sz') else code


def visible_codes():
    """活跃池：剔僵尸 + 剔上一交易日成交额<0.3亿（对齐 visibleEtfs）。"""
    out = []
    for c, _n in HK_ETF_LIST:
        if c in ZOMBIES:
            continue
        pa = PREV_AMT.get(c)
        if pa and pa.get('prev_amt_yi') is not None and pa['prev_amt_yi'] < 0.3:
            continue
        out.append(c)
    # 商品期货ETF流动性稳定，不做成交额过滤，直接并入
    out.extend(c for c, _n in COMMODITY_FUT)
    return out


def fetch_market():
    """取恒生指数、恒生科技实时涨跌幅。返回 {'hsi':{name,pct},'hstech':{...}}；
    取不到/旧帧返回 None（保守处理，不因为接口抖动乱停信号）。"""
    try:
        url = 'https://qt.gtimg.cn/q=' + ','.join(INDEX_CODES)
        r = requests.get(url, headers=H, timeout=8)
        r.encoding = 'gbk'
        out = {}
        for line in r.text.strip().split(';'):
            if '~' not in line:
                continue
            p = line.split('~')
            try:
                now, prev = float(p[3]), float(p[4])
            except Exception:
                continue
            if now <= 0 or prev <= 0:
                continue
            pct = round((now / prev - 1) * 100, 2)
            if 'r_hkHSTECH' in line:
                out['hstech'] = {'name': p[1], 'pct': pct}
            elif 'r_hkHSI' in line:
                out['hsi'] = {'name': p[1], 'pct': pct}
        return out or None
    except Exception as e:
        print('大盘指数取数失败，按正常环境处理:', e)
        return None


def market_state():
    """刷新全局 MARKET 并返回三级状态：
    任一指数跌幅达到 CRASH_PCT 判 crash（硬熔断全停）；
    达到 INDEX_WEAK_PCT 判 weak（早盘/反转暂停，下午强共振）；
    取不到数保守判 normal，不阻断信号。"""
    m = fetch_market()
    MARKET['hsi'] = m['hsi'] if m else None
    MARKET['hstech'] = m['hstech'] if m else None
    weak = crash = False
    if m:
        for k in ('hsi', 'hstech'):
            if not m.get(k):
                continue
            if m[k]['pct'] <= CRASH_PCT:
                crash = True
            elif m[k]['pct'] <= INDEX_WEAK_PCT:
                weak = True
    MARKET['state'] = 'crash' if crash else ('weak' if weak else 'normal')
    return MARKET


def is_weak():
    return MARKET.get('state') in ('weak', 'crash')


def is_crash():
    return MARKET.get('state') == 'crash'


def rev_regime_blocked(doc, quotes):
    """方案B 市场环境开关：9:35 后统计“现价贴着当日最低且在跌”的票占比，
    ≥REV_REGIME_THRESH 判定为系统性普跌日，当天坑反弹整体关闭，结果按日期缓存。"""
    key = 'rev_regime_%s' % TODAY
    cached = doc.get(key)
    if cached is not None:
        return bool(cached)
    if now_min() < 575:          # 9:35 前不判定（个股最早也此时才进）
        return False
    nl = n = 0
    for qt in quotes.values():
        low = qt.get('low'); now = qt.get('now'); op = qt.get('open')
        if not low or now is None or not op:
            continue
        n += 1
        if now < op and (now/low - 1)*100 <= REV_NL_TOL:
            nl += 1
    if n == 0:
        return False
    blocked = (nl/n*100) >= REV_REGIME_THRESH
    doc[key] = blocked
    print('坑反弹环境开关 @%d: 贴最低占比%.1f%% => %s'
          % (now_min(), nl/n*100, '普跌日关闭' if blocked else '正常做'))
    return blocked


def fetch_kline(code, period='m1', n=30):
    for pre in KLINE_HOSTS:
        try:
            u = '%s/app/kline/mkline?param=%s,%s,,%d' % (pre, code, period, n)
            j = requests.get(u, headers=H, timeout=8).json()
            arr = j['data'][code][period]
            if arr:
                return arr
        except Exception:
            time.sleep(0.3)
    return fetch_kline_em(code, period, n)


EM_KLT = {'m1': 1, 'm5': 5, 'm15': 15, 'm30': 30, 'm60': 60, 'day': 101}


def fetch_kline_em(code, period='m1', n=30):
    """东财 K 线备份：腾讯 K 线整体故障（控制器错误/停滞）时使用。
    返回与腾讯同构的 bars [ts, open, close, high, low, vol]。"""
    secid = ('1.' if code.startswith('sh') else '0.') + code[2:]
    klt = EM_KLT.get(period, 1)
    u = ('https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s'
         '&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56'
         '&klt=%d&fqt=1&end=20500101&lmt=%d') % (secid, klt, n)
    try:
        j = requests.get(u, headers=H, timeout=8).json()
        data = j.get('data')
        if not data or not data.get('klines'):
            return []
        out = []
        for line in data['klines']:
            ts_s, rest = line.split(',', 1)
            p = rest.split(',')
            ts = ts_s.replace('-', '').replace(' ', '').replace(':', '')
            out.append([ts, p[0], p[1], p[2], p[3], p[4]])
        return out
    except Exception:
        return []


def is_spike_bar(bar):
    """单根K线是否为上影插针：上影线(高- max(开,收))占现价比 > WICK_MAX。"""
    try:
        o, c, h = float(bar[1]), float(bar[2]), float(bar[3])
    except Exception:
        return False
    if h <= 0:
        return False
    return (h - max(o, c)) / h * 100 > WICK_MAX


def target_real_hit(code, target, qt, bars=None, trig_bt=None):
    """目标价是否真实站稳（真能卖掉）：
    只看触发(trig_bt)之后的1分K——有收盘价 >= 目标，或当前实时价(now) >= 目标；
    买入前的K线不算；仅最高价碰一下、收盘掉回的插针 → False。"""
    if bars is None:
        bars = fetch_kline(code, 'm1', 30)
    for b in bars:
        if trig_bt is not None and b[0] < trig_bt:
            continue
        try:
            if float(b[2]) >= target:
                return True
        except Exception:
            continue
    if qt and qt.get('now') is not None and qt['now'] >= target:
        return True
    return False


def latest_bar_rv(bars):
    """最新根量比 = 最新根量 / 前19根均量（与 candidate_metrics 口径一致）。"""
    if not bars:
        return None
    vols = [float(b[5] or 0) for b in bars]
    prior = vols[:-1][-19:]
    base_v = sum(prior) / len(prior) if prior else 0
    return vols[-1] / base_v if base_v > 0 else None


def bar_rv(vols, i):
    """第 i 根的量比 = 该根量 / 它之前19根均量（任意根口径，与原最新根口径一致）。"""
    prior = vols[max(0, i - 19):i]
    base = sum(prior) / len(prior) if prior else 0
    return vols[i] / base if base > 0 else None


def candidate_metrics(qt, bars):
    """候选门槛（早盘/下午共用），达标返回 dict，否则 None。
    触发根在最近 TRIGGER_LOOKBACK 根里从新往回找，不再只看最新根，
    避免服务端单轮扫描慢、轮询时刻错过放量阳线那根。"""
    lift = qt['rt_open']
    if lift < START_PCT:
        return None
    if not bars:
        return None
    vols = [float(b[5] or 0) for b in bars]
    trig = None
    start_i = max(0, len(bars) - TRIGGER_LOOKBACK)
    for i in range(len(bars) - 1, start_i - 1, -1):
        b = bars[i]
        try:
            o, c = float(b[1]), float(b[2])
        except Exception:
            continue
        if o <= 0:
            continue
        body = (c / o - 1) * 100
        if body < START_BODY:
            continue
        if vols[i] <= 0:
            continue
        rv = bar_rv(vols, i)
        if rv is not None and rv < RV_MIN:
            continue
        trig = (b, body, rv)
        break
    if not trig:
        return None
    lb, body, rv = trig
    hi = qt['high']
    pull = qt['now'] / hi if hi and hi > 0 else None
    if pull is not None and pull < BREAKOUT_PULLBACK:
        return None
    gap = qt['gap']
    low = gap is not None and gap < 0
    score = (rv or 1) + lift * 0.5
    return {'lift': lift, 'lb': lb,
            'rv': round(rv, 2) if rv is not None else None,
            'body': round(body, 2), 'gap': gap, 'low': low,
            'score': round(score, 3)}


def m5_current_agg(bars, t):
    """从当日1分K聚合当前所在5分K根（仅早盘前两根），返回 {idx,o,c,body}，取不到 None。
    idx=0 第一根(9:30-9:35)、idx=1 第二根(9:35-9:40)；口径与 APP barSelf 对齐。"""
    if t < AM_START:
        return None
    g_now = (t - AM_START) // 5
    open_o = None
    members = []
    for b in bars or []:
        try:
            bm = int(b[0][-4:-2]) * 60 + int(b[0][-2:])
            o, c = float(b[1]), float(b[2])
        except Exception:
            continue
        g = (bm - AM_START) // 5 if bm >= AM_START else -1
        if g != g_now or bm > t:   # 只聚合当前组且不晚于当前时间的根，防“未来根”
            continue
        if open_o is None:
            open_o = o
        members.append(c)
    if open_o is None or not members:
        return None
    close_c = members[-1]
    return {'idx': g_now, 'o': open_o, 'c': close_c,
            'body': (close_c / open_o - 1) * 100}


def reversal_metrics(qt, bars):
    """方案B 坑反弹：贴坑底企稳埋伏（不追涨、不强制放量）。
    在最近 TRIGGER_LOOKBACK 根里从新往回找触发根，每根用截至该根的口径判断：
    坑底在第5-15根（约9:35-9:45）或当日坑深≥2%，且触发根收盘贴坑底（≤0.4%）。
    返回与 gate_eval 兼容的 cand dict，否则 None。"""
    if not qt or not bars:
        return None
    gap = qt.get('gap')
    open_px = qt.get('open')
    now_px = qt.get('now')
    if gap is None:
        return None
    if not open_px or open_px <= 0 or now_px is None or now_px >= open_px:
        return None                        # 现价仍在开盘价下方（坑里）
    if len(bars) < 10:
        return None
    vols = [float(b[5] or 0) for b in bars]
    start_i = max(0, len(bars) - TRIGGER_LOOKBACK)
    for i in range(len(bars) - 1, start_i - 1, -1):
        b = bars[i]
        try:
            o, c = float(b[1]), float(b[2])
        except Exception:
            continue
        if o <= 0 or c <= 0:
            continue
        seg_lows = [float(x[4]) for x in bars[:i+1]]
        pit = min(seg_lows)
        li = max(j for j in range(len(seg_lows)) if seg_lows[j] == pit)
        age = i - li
        depth = (open_px/pit - 1) * 100
        if not (REV_AGE_LO <= age < REV_AGE_HI or depth >= REV_DEPTH):
            continue                       # 个股过滤：坑底时机或深坑
        if (c/pit - 1) * 100 > REV_NEAR:
            continue                       # 收盘没贴坑底，不进
        if vols[i] <= 0:
            continue                       # 缩量允许，但要有量
        rv = bar_rv(vols, i)
        body = (c/o - 1) * 100
        lift = (c/open_px - 1) * 100
        score = (rv or 1) + depth * 0.5    # 坑越深、有量，分越高
        return {'lift': round(lift, 3), 'lb': b,
                'rv': round(rv, 2) if rv is not None else None,
                'body': round(body, 2), 'gap': gap, 'low': gap < 0,
                'score': round(score, 3)}
    return None


def second_bottom_metrics(qt, bars):
    """二次探底再拉起：第一波冲高后回踩，出现两次探底、第二底不破前低（企稳），
    再放量阳线拉起时触发。返回与 gate_eval 兼容的 cand dict，否则 None。

    与 reversal_metrics 的区别：反转只认开盘价下方、9:35-9:45 的深坑贴底；
    本函数覆盖盘中（含开盘价上方的浅坑）回踩双底后的拉起，不要求创新高。
    """
    if not qt or not bars or len(bars) < 20:
        return None
    open_px = qt.get('open')
    now_px = qt.get('now')
    if not open_px or open_px <= 0 or now_px is None:
        return None
    # 1) 第一波冲高：当日已有相对开盘达标的高点，且高点不在最近几根（已回踩）
    highs = [float(b[3]) for b in bars]
    hi_val = max(highs)
    hi_idx = highs.index(hi_val)
    if (hi_val / open_px - 1) * 100 < SB_FIRST_PUSH:
        return None                        # 没有像样的第一波拉升
    if hi_idx > len(bars) - SB_PULLBACK_GAP:
        return None                        # 高点太近，还没经历回踩
    # 2) W底识别：第一底＝第一波高点之后的主低点；中间须有一次明显反弹；
    #    第二底＝反弹高点之后的次低点，且不低于第一底（底部抬高）
    seg = [(hi_idx + j, float(bars[hi_idx + j][4]))
           for j in range(len(bars) - hi_idx)]
    pit1 = min(v for _, v in seg)
    pit1_i = next(i for i, v in seg if v == pit1)
    if pit1_i >= len(bars) - 2:
        return None
    # 中间反弹高点（第一底之后的最高 high）
    neck = max(float(bars[i][3]) for i in range(pit1_i + 1, len(bars)))
    neck_i = next(i for i in range(pit1_i + 1, len(bars))
                  if float(bars[i][3]) == neck)
    if (neck / pit1 - 1) * 100 < SB_NECK_MIN:
        return None                        # 中间没有像样反弹，不构成W底
    p2seg = [(i, float(bars[i][4])) for i in range(neck_i + 1, len(bars))]
    if not p2seg:
        return None
    pit2 = min(v for _, v in p2seg)
    pit2_i = next(i for i, v in p2seg if v == pit2)
    # 第二底不破第一底（容差内）；pit2 明显低于 pit1 视为破位，不接
    if (pit1 / pit2 - 1) * 100 > SB_PIT_TOL:
        return None
    # 回踩不能把第一波涨幅全吐回去：第二底至少保留第一波涨幅的 SB_PB_KEEP，维持上升结构
    first_push_pct = (hi_val / open_px - 1) * 100
    pit2_pct = (pit2 / open_px - 1) * 100
    if pit2_pct < first_push_pct * SB_PB_KEEP:
        return None
    # 3) 最近窗口内、第二底之后的放量阳线拉起
    vols = [float(b[5] or 0) for b in bars]
    start_i = max(0, len(bars) - TRIGGER_LOOKBACK)
    for i in range(len(bars) - 1, start_i - 1, -1):
        b = bars[i]
        try:
            o, c = float(b[1]), float(b[2])
        except Exception:
            continue
        if o <= 0 or c <= 0:
            continue
        if i <= pit2_i:
            continue                       # 触发根必须在第二底之后
        body = (c / o - 1) * 100
        if body < SB_BODY_MIN:
            continue
        if vols[i] <= 0:
            continue
        rv = bar_rv(vols, i)
        if rv is not None and rv < SB_RV_MIN:
            continue
        lift = (c / pit2 - 1) * 100        # 收盘相对坑底抬升
        if not (SB_LIFT_MIN <= lift <= SB_LIFT_MAX):
            continue
        gap = qt.get('gap')
        score = (rv or 1) + body
        return {'lift': round((c / open_px - 1) * 100, 3), 'lb': b,
                'rv': round(rv, 2) if rv is not None else None,
                'body': round(body, 2), 'gap': gap,
                'low': gap is not None and gap < 0,
                'score': round(score, 3)}
    return None


def comp_metrics(code):
    """成分股：红率(按个数) + 加权 w（对齐 compOpenRedRate / compOpenAvg）。
    完全取不到数据(n=0)属网络抖动，内部重试一次，避免误判 fail。"""
    raw = HOLDINGS.get(code)
    if not raw:
        return None
    stocks = raw['stocks'] if isinstance(raw, dict) else raw
    codes = [s.get('code') or s.get('stock_code') for s in stocks]
    q, stale, _src = qu.fetch_safe(codes)
    red = n = 0
    wv = ww = 0.0
    for _retry in range(2):
        red = n = 0
        wv = ww = 0.0
        for s in stocks:
            sc = s.get('code') or s.get('stock_code')
            d = q.get(sc)
            if not d:
                continue
            rt = d['rt_open']
            n += 1
            if rt > 0:
                red += 1
            w = float(s.get('weight', 0) or 0)
            if w > 0:
                wv += rt * w
                ww += w
        if n > 0 or _retry == 1:
            break
        time.sleep(1)  # 网络抖动，等1秒重取
        q, stale, _src = qu.fetch_safe(codes)
    return {'red_rate': round(red / n, 3) if n else None,
            'w': round(wv / ww, 3) if ww else None,
            'n': n, 'stale': stale}


def fetch_futures(secids):
    """东财单个 stock/get 取期货主连涨跌幅（批量端点不支持主连），并发取，返回 {secid: 涨跌幅%}。"""
    def one(secid):
        try:
            u = ('https://push2.eastmoney.com/api/qt/stock/get?secid=%s'
                 '&fields=f43,f58,f60,f170') % secid
            d = requests.get(u, headers=H, timeout=8).json().get('data')
            if d and d.get('f170') is not None:
                return secid, d['f170'] / 100.0
        except Exception:
            pass
        return secid, None
    with ThreadPoolExecutor(max_workers=5) as ex:
        return dict(ex.map(one, secids))


def commodity_comp_metrics(code):
    """商品期货ETF成分：成分是期货合约，涨跌幅以昨结为基准；红率按品种个数、加权按权重。"""
    meta = COMMODITY_HOLDINGS.get(code)
    if not meta:
        return None
    secids = [s['secid'] for s in meta['stocks']]
    q = fetch_futures(secids)
    red = n = 0
    wv = ww = 0.0
    for s in meta['stocks']:
        rt = q.get(s['secid'])
        if rt is None:
            continue
        n += 1
        if rt > 0:
            red += 1
        w = s['w']
        wv += rt * w
        ww += w
    return {'red_rate': round(red / n, 3) if n else None,
            'w': round(wv / ww, 3) if ww else None, 'n': n}


def load_us_nav():
    """读 cache_pre.json 里的美股静态净值/昨收（每日预热更新），不临时估算。"""
    try:
        d = json.load(open('cache_pre.json', encoding='utf-8'))
        return d.get('us_etf') or {}
    except Exception:
        return {}


def us_premium_metrics(code, now_px, live=None):
    """美股溢价安全门：返回现/昨溢价、收敛幅度、高溢价标记、是否放行。
    白天美股闭市，净值优先用腾讯实时 IOPV（字段78），现溢价优先用官方实时值（字段77）；
    静态 cache_pre 仅作兜底。净值缺失（数据故障）返回 None，调用方按未就绪处理，不强行确认。"""
    pre = US_NAV_CACHE.get(code) or {}
    nav_live = live.get('nav_live') if live else None
    prem_live = live.get('prem_live') if live else None
    prev_live = live.get('prev') if live else None
    nav = float(nav_live) if nav_live else pre.get('nav')
    if not nav:
        return None
    prev_close = prev_live if prev_live else pre.get('prev_close')
    if prem_live is not None:
        prem_now = float(prem_live)
    elif now_px:
        prem_now = (float(now_px) / nav - 1) * 100
    else:
        return None
    prem_prev = (float(prev_close) / nav - 1) * 100 if prev_close else prem_now
    contract = prem_now - prem_prev          # 负=收敛(杀溢价)，正=扩张
    return {
        'prem_prev': round(prem_prev, 2), 'prem_now': round(prem_now, 2),
        'contract': round(contract, 2),
        'high_prem': prem_now >= US_HIGH_PREM,
        'prem_ok': contract > -US_PREM_CONTRACT,   # 收敛未超2pt才放行
    }


def load_state():
    default = {'date': TODAY, 'am': {}, 'pm': {}, 'rev': {}, 'us': {}, 'sb': {},
               'fired_am': [], 'fired_pm': [], 'fired_rev': [], 'fired_us': [],
               'fired_sb': [], 'fired_lowup': [],
               'position': {},
               'market': {'state': 'normal', 'hsi': None, 'hstech': None}}
    try:
        d = json.load(open('signal_state.json', encoding='utf-8'))
        if d.get('date') == TODAY:
            for k, v in default.items():
                d.setdefault(k, v)
            return d
    except Exception:
        pass
    return default


def gate_eval(states, code, cand, bars, qt, require_red, is_rev=False):
    """触发即推闸门：放量阳线触发根已收盘，实时价仍在触发价上方、当轮红率/加权/
    量比达标，立即 confirm（不再等待站稳），抢低价。

    下一根K线若已走完且收盘破位，则判 fail（保留原假信号过滤）。
    is_rev=True 时低开反转用专用门槛（弱市也能抓坑反弹，但仍防接飞刀）。
    """
    st = states.get(code)
    if not st:
        if not cand:
            return None
        st = states[code] = {
            'trig_bt': cand['lb'][0], 'trig_price': float(cand['lb'][2]),
            'phase': 'ready', 'gap': cand['gap'], 'low': cand['low'],
            'score': cand['score'], 'lift': cand['lift']}
    if st.get('phase') == 'confirm':
        # 破止损后重新启动：已被止损出局 + 重新放量(cand非None)创新高 + 未超上限 -> 重置重来
        rc = st.get('reentry_count', 0)
        if (cand and code in REENTRY_ALLOWED and rc < MAX_REENTRY
                and float(cand['lb'][2]) > st['trig_price']):
            st['trig_bt'] = cand['lb'][0]
            st['trig_price'] = float(cand['lb'][2])
            st['phase'] = 'ready'
            st['score'] = cand['score']
            st['lift'] = cand['lift']
            st['reentry_count'] = rc + 1
            st.pop('why', None)
        else:
            return st
    if st.get('phase') == 'fail':
        # 二次启动：重新放量(cand非None)且严格突破原触发价创新高，且未超解锁上限 -> 重来
        rc = st.get('reset_count', 0)
        if cand and rc < MAX_RESET and float(cand['lb'][2]) > st['trig_price']:
            st['trig_bt'] = cand['lb'][0]
            st['trig_price'] = float(cand['lb'][2])
            st['phase'] = 'ready'
            st['score'] = cand['score']
            st['lift'] = cand['lift']
            st['reset_count'] = rc + 1
            st.pop('why', None)
        else:
            return st
    if st.get('phase') == 'chase':
        # 追高放弃的单当天锁定，不再重新评估、不再推送
        return st

    # 触发根年龄上限：ready 根太老仍未 confirm，低价窗口已过，置 chase 放弃
    try:
        trig_age = now_min() - trig_bt_min(st['trig_bt'])
        if trig_age > MAX_TRIG_AGE:
            st['phase'] = 'chase'
            st['why'] = '触发根已过 %d 分钟，低价错过' % trig_age
            print('追高放弃 %s（触发根陈旧 %d 分钟）@%d' % (code, trig_age, now_min()))
            return st
    except (ValueError, TypeError):
        pass  # 非标准时间戳无法算年龄，跳过该检查

    now_price = qt['now'] if qt else None
    stand = now_price is not None and now_price >= st['trig_price']

    red_ok = w_ready = w_gate = True
    cm = None  # 成分指标，美股分支用 um，先初始化避免失败文案引用未定义变量
    um = None
    if require_red:
        if code in US_CODES:
            # 美股QDII：白天美股闭市、无成分股实时红率，用溢价安全门确认（实时净值）
            um = us_premium_metrics(code, qt['now'], qt) if qt else None
            st['prem_now'] = um['prem_now'] if um else None
            st['contract'] = um['contract'] if um else None
            red_ok = bool(um) and um['prem_ok']
            w_ready = bool(um)
            w_gate = bool(um) and um['prem_ok']
        elif code in COMMODITY_CODES:
            # 商品期货ETF成分是期货合约，走期货红率/加权门槛
            cm = commodity_comp_metrics(code)
            st['red_rate'] = cm['red_rate'] if cm else None
            st['w'] = cm['w'] if cm else None
            if is_rev:
                red_min = REV_RED_MIN
                w_min = REV_W_WEAK if is_weak() else REV_W_NORMAL
            else:
                red_min = PM_RED_MIN
                w_min = WEAK_PM_W if is_weak() else BREAKOUT_CONS
            red_ok = bool(cm) and cm['red_rate'] is not None and cm['red_rate'] >= red_min
            w_ready = bool(cm) and cm['w'] is not None
            w_gate = (not cm) or (cm['w'] is None) or (cm['w'] > w_min)
        else:
            cm = comp_metrics(code)
            st['red_rate'] = cm['red_rate'] if cm else None
            st['w'] = cm['w'] if cm else None
            if is_rev:
                # 低开反转：弱市也能抓，红率放宽到 40%，加权只防深度暴跌
                red_min = REV_RED_MIN
                w_min = REV_W_WEAK if is_weak() else REV_W_NORMAL
            else:
                red_min = PM_RED_MIN
                w_min = WEAK_PM_W if is_weak() else BREAKOUT_CONS  # 弱市要求强共振
            red_ok = bool(cm) and cm['red_rate'] is not None and cm['red_rate'] >= red_min
            w_ready = bool(cm) and cm['w'] is not None
            w_gate = (not cm) or (cm['w'] is None) or (cm['w'] > w_min)

    ti = next((j for j, b in enumerate(bars) if b[0] == st['trig_bt']), -1)
    has_next = ti >= 0 and ti + 1 < len(bars)
    nxt = bars[ti + 1] if has_next else None

    if stand:
        # 触发即推：放量阳线触发根已收盘（实体/量比客观门槛已满足），实时价仍在
        # 触发价上方且红率/加权/量比当轮达标，立即 confirm，不再等待15秒，抢低价
        rv_now = latest_bar_rv(bars)
        pm_rv_ok = (not require_red or is_rev
                    or rv_now is None or rv_now >= PM_RV_MIN)
        if red_ok and w_ready and w_gate and pm_rv_ok:
            st['phase'] = 'confirm'
            st['confirm_bt'] = cand['lb'][0] if cand else st['trig_bt']
            st['confirm_ts'] = int(time.time())
            return st
        # 下一根K线已走完、价格站稳但红率/加权不达标 -> fail
        if has_next and require_red and not (red_ok and w_ready and w_gate):
            st['phase'] = 'fail'
            if not red_ok:
                if code in US_CODES:
                    st['why'] = '溢价安全门未通过'
                elif cm and cm['red_rate'] is not None:
                    st['why'] = '红率不足(%.0f%%)' % (cm['red_rate'] * 100)
                else:
                    st['why'] = '成分加权未就绪'
            elif cm and cm['w'] is not None:
                st['why'] = '成分共振不足(w=%.2f)' % cm['w']
            else:
                st['why'] = '成分加权未就绪'
        return st

    # 实时价跌破触发价：下一根K线已走完且收盘明显破位（超过缓冲）才 fail
    if has_next and float(nxt[2]) < st['trig_price']:
        drop_pct = (float(nxt[2]) / st['trig_price'] - 1) * 100
        if drop_pct < -FAIL_BUFFER:
            st['phase'] = 'fail'
            st['why'] = '下一根破位'
        # 缓冲内的轻微回踩：不 fail，等下一根重新站稳
    return st


def entry_levels(s, qt, take_pct, stop_pct):
    """触发即推：目标/止损基于触发价（用户争取买入的低价）。

    触发价 trig_price 是放量阳线触发根的收盘价，目标/止损直接基于它，用户收到
    后第一时间下单、成交价接近触发价。若识别时现价已涨完一个完整目标空间
    （触发根被延迟发现、低价已错过），返回 None 不推，避免误导。
    返回 {'trig','target','stop'}，或 None（过期/无行情）。
    """
    trig = float(s['trig_price'])
    target = round(trig * (1 + take_pct / 100), 3)
    stop = round(trig * (1 - stop_pct / 100), 3)
    if qt and qt.get('now') is not None:
        chase = round((float(qt['now']) / trig - 1) * 100, 4)
        if chase >= take_pct * CHASE_CAP_RATIO:
            return None
    return {'trig': trig, 'target': target, 'stop': stop}


def build_html(msg, code):
    """构造带一键复制按钮的 HTML 消息（WxPusher copy 标签），复制纯数字代码。"""
    pc = pure_code(code)
    safe = html.escape(msg).replace('\n', '<br>')
    return (
        '<div style="font-size:17px;line-height:1.7;color:#1f2937;">'
        '<div style="margin-bottom:14px;">%s</div>'
        '<copy data-clipboard-text="%s" '
        'style="background:#2563EB;color:#ffffff;border:none;border-radius:10px;'
        'padding:14px 22px;font-size:19px;">点我复制代码 %s</copy>'
        '</div>') % (safe, html.escape(pc), html.escape(pc))


def do_push(title, msg, code=None):
    if not notify_push:
        return {'skip': 'no module'}
    try:
        # 当前主通道：WxPusher（微信，有声音有横幅，带一键复制按钮）。
        # 华为静默通道、旧安卓通道、ntfy 已关闭，避免一条信号重复提醒。
        h = build_html(msg, code) if code else None
        r = {'wxpusher': notify_push.push_wxpusher(title, msg, html=h)}
        print('推送:', json.dumps(r, ensure_ascii=False))
        return r
    except Exception as e:
        print('推送失败不阻断: %s' % e)
        return {'error': str(e)}


def push_lowup_early(doc, quotes, codes):
    """低开高走早期信号：开盘最早两根5分K（9:30-9:40），低开后对开盘翻红、当前根走强即推。
    每票当日只推一次；暴跌硬熔断不推（弱市照常）；仅第一/第二根窗口，过后不再补、不拿冻结快照。"""
    t = now_min()
    if t < AM_START or t >= LOWUP_B2_END:
        doc['fired_lowup'] = sorted(set(doc.get('fired_lowup', [])))
        return []
    if is_crash():
        print('暴跌硬熔断，低开高走早期信号暂停（不接飞刀）@%d' % t)
        return []
    if is_weak():
        print('弱市低开高走早期信号照常推送 @%d' % t)
    fired = set(doc.get('fired_lowup', []))
    new = []
    for code in codes:
        if code in fired:
            continue
        qt = quotes.get(code)
        if not qt or qt.get('gap') is None:
            continue
        if qt['gap'] >= LOWUP_EARLY_GAP:        # 需严格低开（gap < -0.05）
            continue
        if qt['rt_open'] <= LOWUP_EARLY_UP:     # 需对开盘严格翻红（rt > 0.05）
            continue
        bars = fetch_kline(code, 'm1', 10)
        agg = m5_current_agg(bars, t)
        if not agg or agg['body'] is None or agg['body'] <= 0:  # 当前5分根需实体阳线
            continue
        root_txt = '第一根' if agg['idx'] == 0 else '第二根'
        title = '低开高走·%s·%s' % (root_txt, qt['name'])
        msg = ('%s %s（%s）\n开盘对昨收 %s（低开）\n现价 %s  对开盘 %s\n'
               '%s 5分K走强（实体 %+.2f%%）\n早期信号，按战法介入，带好止损不恋战') % (
            pure_code(code), qt['name'], root_txt,
            '%+.2f%%' % qt['gap'], '%.3f' % qt['now'],
            '%+.2f%%' % qt['rt_open'], root_txt, agg['body'])
        do_push(title, msg, code)
        fired.add(code)
        new.append({'code': code, 'kind': '低开高走早期', 'root': agg['idx']})
    doc['fired_lowup'] = sorted(fired)
    return new


def push_am_locked(doc, quotes, states):
    """早盘：confirm 后低开/高开各锁 score 最高一只，只推锁定票。
    暴跌硬熔断全停；弱市在整个早盘窗口(9:30-10:00)每一分钟都照常扫描推送，不观望。"""
    if is_crash():
        print('暴跌硬熔断，早盘信号全部暂停（不接飞刀，等大盘企稳）')
        return []
    if is_weak():
        print('弱市早盘照常扫描推送（9:30-10:00 每一分钟都看，不观望）@%d' % now_min())
    fired = set(doc['fired_am'])
    conf = [c for c in states if states[c].get('phase') == 'confirm']
    lock_low = lock_high = None
    for c in conf:
        s = states[c]
        if s.get('low'):
            if not lock_low or s['score'] > states[lock_low]['score']:
                lock_low = c
        else:
            if not lock_high or s['score'] > states[lock_high]['score']:
                lock_high = c
    new = []
    for code in [x for x in (lock_low, lock_high) if x]:
        if code in fired:
            continue
        s = states[code]
        qt = quotes.get(code)
        cm = comp_metrics(code)  # 仅锁定票拉一次，用于展示
        lv = entry_levels(s, qt, AM_TARGET, QUICK_STOP)
        if lv is None:
            # 追高/已涨完目标空间，放弃当天不再做
            s['phase'] = 'chase'
            print('追高放弃 %s（早盘现价相对触发根已涨完目标空间）@%d' % (code, now_min()))
            continue
        take_px, stop_px, trig_px = lv['target'], lv['stop'], lv['trig']
        red_txt = ('%.0f%%' % (cm['red_rate'] * 100)
                   if cm and cm['red_rate'] is not None else '暂缺')
        title = ('弱市开盘信号·%s' if is_weak() else '早盘启动信号·%s') % (qt['name'] if qt else code)
        msg = ('%s %s（%s）\n买入参考价 %.3f（触发价，收到赶紧下单）\n'
               '对开盘 %s  红率 %s  现价 %s\n'
               '早盘拿住：到 %s（+0.65%%）就走\n跌破 %s 立刻止损，不恋战') % (
            pure_code(code), qt['name'] if qt else code,
            '低开' if s.get('low') else '高开', trig_px,
            ('%+.2f%%' % qt['rt_open']) if qt else '—', red_txt,
            ('%.3f' % qt['now']) if qt else '—',
            take_px, stop_px)
        do_push(title, msg, code)
        fired.add(code)
        doc.setdefault('position', {})[code] = {
            'kind': '早盘', 'trig': s['trig_price'], 'entry': trig_px,
            'trig_bt': s.get('trig_bt'),
            'target': take_px,
            'stop': stop_px, 'status': 'holding', 'miss_notified': False}
        new.append({'code': code, 'kind': '早盘', 'trig_price': s['trig_price'],
                    'red_rate': cm['red_rate'] if cm else None,
                    'confirm_bt': s.get('confirm_bt')})
    doc['fired_am'] = sorted(fired)
    return new


def push_pm(doc, quotes, states):
    """下午：每只 confirm 都推。暴跌硬熔断时全部不推（30天样本暴跌安全率仅4%）。"""
    if is_crash():
        print('暴跌硬熔断，下午信号全部暂停（不新开，等大盘企稳）')
        doc['fired_pm'] = sorted(set(doc['fired_pm']))
        return []
    # 当日已推过的票（含早盘）不重复推，避免覆盖早盘持仓记录；仅破止损后的合法重进例外
    fired = set(doc['fired_pm']) | set(doc.get('fired_am', []))
    new = []
    for code, s in states.items():
        if s.get('phase') != 'confirm':
            continue
        is_reentry = s.get('reentry_count', 0) > 0
        pos_p = doc.get('position', {}).get(code)
        if is_reentry:
            # 重新启动票：必须确实已破止损，且本次重启还没推过
            if (not pos_p or pos_p.get('status') != 'stopped'
                    or pos_p.get('reentry_seen') == s['reentry_count']):
                continue
        elif code in fired:
            continue
        qt = quotes.get(code)
        lv = entry_levels(s, qt, QUICK_TAKE, QUICK_STOP)
        if lv is None:
            # 现价相对原始触发根已涨完一个完整目标空间，追高放弃，当天不再做
            s['phase'] = 'chase'
            print('追高放弃 %s（现价相对触发根已涨完目标空间）@%d' % (code, now_min()))
            continue
        take_lo, stop_px, trig_px = lv['target'], lv['stop'], lv['trig']
        title = '下午信号·%s' % (qt['name'] if qt else code)
        msg = ('%s %s\n买入参考价 %.3f（触发价，收到赶紧下单）\n'
               '对开盘 %s  红率 %s  现价 %s\n'
               '快进快出：到 %s（+0.2%%）就走\n跌破 %s 立刻止损，不拖尾盘') % (
            pure_code(code), qt['name'] if qt else code, trig_px,
            ('%+.2f%%' % qt['rt_open']) if qt else '—',
            ('%.0f%%' % (s['red_rate'] * 100)) if s.get('red_rate') is not None else '暂缺',
            ('%.3f' % qt['now']) if qt else '—',
            take_lo, stop_px)
        do_push(title, msg, code)
        fired.add(code)
        rec = {
            'kind': '下午', 'trig': s['trig_price'], 'entry': trig_px,
            'trig_bt': s.get('trig_bt'),
            'target': take_lo,
            'stop': stop_px, 'status': 'holding', 'miss_notified': False,
            'reentry_count': s.get('reentry_count', 0)}
        if is_reentry:
            rec['reentry_seen'] = s['reentry_count']  # 标记本次重启已推，避免重复
        doc.setdefault('position', {})[code] = rec
        new.append({'code': code, 'kind': '下午', 'trig_price': s['trig_price'],
                    'red_rate': s.get('red_rate'), 'confirm_bt': s.get('confirm_bt')})
    doc['fired_pm'] = sorted(fired)
    return new


def push_rev(doc, quotes, states):
    """方案B 坑反弹：confirm 后只推 score 最强一只（逆势单精挑），同票当日不重复。
    暴跌硬熔断全停；普跌日环境开关整体关闭；其余（含弱市）照常推送。"""
    if is_crash():
        print('暴跌硬熔断，坑反弹暂停（不接飞刀，等大盘企稳）')
        return []
    if rev_regime_blocked(doc, quotes):
        print('普跌日，坑反弹整体关闭 @%d' % now_min())
        return []
    if is_weak():
        print('弱市坑反弹照常推送 @%d' % now_min())
    fired = set(doc['fired_rev'])
    already = set(doc['fired_am']) | set(doc['fired_pm']) | fired
    conf = [(c, states[c]) for c in states
            if states[c].get('phase') == 'confirm' and c not in already]
    if not conf:
        doc['fired_rev'] = sorted(fired)
        return []
    conf.sort(key=lambda kv: kv[1]['score'], reverse=True)
    picked = None
    for code, s in conf:
        qt = quotes.get(code)
        lv = entry_levels(s, qt, REV_TAKE, REV_STOP)
        if lv is None:
            # 最强票追高则放弃并顺延看次强，置 chase 当天锁定
            s['phase'] = 'chase'
            print('追高放弃 %s（坑反弹空间不足）@%d' % (code, now_min()))
            continue
        picked = (code, s, qt, lv)
        break
    if not picked:
        doc['fired_rev'] = sorted(fired)
        return []
    code, s, qt, lv = picked
    trig = lv['trig']
    take_px, stop_px = lv['target'], lv['stop']
    gap_tag = '低开' if s.get('low') else '高开'
    title = '坑反弹信号·%s' % (qt['name'] if qt else code)
    msg = ('%s %s（%s坑反弹）\n买入参考价 %.3f（触发价，收到赶紧下单）\n'
           '对开盘 %s  红率 %s  现价 %s\n'
           '贴坑底埋伏：到 %s（+0.6%%）止盈走；跌破 %s（-0.4%%）立刻止损，不恋战') % (
        pure_code(code), qt['name'] if qt else code, gap_tag, trig,
        ('%+.2f%%' % qt['rt_open']) if qt else '—',
        ('%.0f%%' % (s['red_rate'] * 100)) if s.get('red_rate') is not None else '暂缺',
        ('%.3f' % qt['now']) if qt else '—',
        take_px, stop_px)
    do_push(title, msg, code)
    fired.add(code)
    doc.setdefault('position', {})[code] = {
        'kind': '反转', 'trig': s['trig_price'], 'entry': trig,
        'trig_bt': s.get('trig_bt'),
        'target': take_px,
        'stop': stop_px, 'status': 'holding', 'miss_notified': False}
    doc['fired_rev'] = sorted(fired)
    return [{'code': code, 'kind': '反转', 'trig_price': trig,
             'red_rate': s.get('red_rate'), 'confirm_bt': s.get('confirm_bt')}]


def push_sb(doc, quotes, states):
    """二次探底再拉起：confirm 后推送，目标 +0.65%、止损 -0.4%。
    暴跌硬熔断全停；已被其他类型推过的票不重复推。"""
    if is_crash():
        print('暴跌硬熔断，二次探底信号暂停（不接飞刀）')
        doc['fired_sb'] = sorted(set(doc.get('fired_sb', [])))
        return []
    if is_weak():
        # 回测：普跌环境 W 底达标率仅 3.8%、止损率 92%，弱市默认不推，避免在下跌中继接飞刀
        print('弱市二次探底默认不推（回测普跌日达标率仅3.8%%）@%d' % now_min())
        doc['fired_sb'] = sorted(set(doc.get('fired_sb', [])))
        return []
    already = (set(doc['fired_am']) | set(doc['fired_pm'])
               | set(doc['fired_rev']) | set(doc['fired_us']))
    fired = set(doc.get('fired_sb', []))
    new = []
    for code, s in states.items():
        if s.get('phase') != 'confirm' or code in fired or code in already:
            continue
        qt = quotes.get(code)
        lv = entry_levels(s, qt, SB_TAKE, SB_STOP)
        if lv is None:
            # 现价已涨完目标空间，追高放弃，当天锁定
            s['phase'] = 'chase'
            print('追高放弃 %s（二次探底拉起空间不足）@%d' % (code, now_min()))
            continue
        trig_px, take_px, stop_px = lv['trig'], lv['target'], lv['stop']
        title = '二次探底信号·%s' % (qt['name'] if qt else code)
        msg = ('%s %s（二次探底再拉起）\n买入参考价 %.3f（触发价，收到赶紧下单）\n'
               '对开盘 %s  红率 %s  现价 %s\n'
               '双底企稳拉起：到 %s（+0.65%%）就走\n跌破 %s（-0.4%%）立刻止损，不恋战') % (
            pure_code(code), qt['name'] if qt else code, trig_px,
            ('%+.2f%%' % qt['rt_open']) if qt else '—',
            ('%.0f%%' % (s['red_rate'] * 100)) if s.get('red_rate') is not None else '暂缺',
            ('%.3f' % qt['now']) if qt else '—',
            take_px, stop_px)
        do_push(title, msg, code)
        fired.add(code)
        doc.setdefault('position', {})[code] = {
            'kind': '二次探底', 'trig': s['trig_price'], 'entry': trig_px,
            'trig_bt': s.get('trig_bt'),
            'target': take_px,
            'stop': stop_px, 'status': 'holding', 'miss_notified': False}
        new.append({'code': code, 'kind': '二次探底', 'trig_price': s['trig_price'],
                    'red_rate': s.get('red_rate'), 'confirm_bt': s.get('confirm_bt')})
    doc['fired_sb'] = sorted(fired)
    return new


def push_us(doc, quotes, states):
    """美股坑底反弹：confirm 后推送，两档目标（保底0.3、主看0.8），不拿到收盘。
    暴跌硬熔断全停；弱市只放低溢价票（高溢价纳指杀溢价风险大）。"""
    if is_crash():
        print('暴跌硬熔断，美股信号全部暂停（不接飞刀）')
        doc['fired_us'] = sorted(set(doc['fired_us']))
        return []
    fired = set(doc['fired_us'])
    new = []
    for code, s in states.items():
        if s.get('phase') != 'confirm' or code in fired:
            continue
        qt = quotes.get(code)
        # 实时算当前溢价（实时净值，不依赖 state 里可能陈旧的值），弱市高溢价票拦截
        um = us_premium_metrics(code, qt['now'], qt) if qt else None
        high_prem = bool(um) and um['high_prem']
        if is_weak() and high_prem:
            print('弱市高溢价票暂停 %s（防杀溢价）' % code)
            continue
        lv = entry_levels(s, qt, US_TAKE, US_STOP)
        if lv is None:
            # 追高/保底空间不足，放弃当天锁定
            s['phase'] = 'chase'
            print('追高放弃 %s（美股坑底空间不足）@%d' % (code, now_min()))
            continue
        entry = lv['trig']
        take_lo = lv['target']
        take_hi = round(entry * (1 + US_TAKE_HI / 100), 3)
        stop_px = lv['stop']
        prem_txt = ('%+.2f%%' % s['prem_now']) if s.get('prem_now') is not None else '暂缺'
        title = '美股坑底信号·%s' % (qt['name'] if qt else code)
        msg = ('%s %s（美股坑底）\n买入参考价 %.3f（触发价，收到赶紧下单）\n'
               '对开盘 %s  现溢价 %s  现价 %s\n'
               '快进快出：保底到 %s（+0.3%%）可先走、稳；主看 %s（+0.8%%）\n'
               '跌破 %s 立刻止损，不拿到收盘') % (
            pure_code(code), qt['name'] if qt else code, entry,
            ('%+.2f%%' % qt['rt_open']) if qt else '—', prem_txt,
            ('%.3f' % qt['now']) if qt else '—',
            take_lo, take_hi, stop_px)
        do_push(title, msg, code)
        fired.add(code)
        doc.setdefault('position', {})[code] = {
            'kind': '美股', 'trig': s['trig_price'], 'entry': entry,
            'trig_bt': s.get('trig_bt'),
            'target': take_lo,
            'stop': stop_px, 'status': 'holding', 'miss_notified': False}
        new.append({'code': code, 'kind': '美股', 'trig_price': s['trig_price'],
                    'prem_now': s.get('prem_now'), 'confirm_bt': s.get('confirm_bt')})
    doc['fired_us'] = sorted(fired)
    return new


def track_positions(doc, quotes, t):
    """跟踪已推信号：更新 reached/stopped 状态；窗口最后一轮（14:50）仍未到目标，
    推一次“目标未到达”，不让用户傻等。"""
    pos = doc.setdefault('position', {})
    for code, p in list(pos.items()):
        if p.get('status') in ('reached', 'stopped', 'miss'):
            continue
        qt = quotes.get(code)
        if qt:
            if target_real_hit(code, p['target'], qt, trig_bt=p.get('trig_bt')):
                p['status'] = 'reached'
                do_push('已到目标价·%s' % (qt['name'] if qt else code),
                        ('%s %s信号\n触发价 %.3f 目标 %.3f 止损 %.3f 现价 %s\n'
                         '摸到目标价了，可以止盈走人，别贪；若回落跌破止损价也要立刻卖') % (
                            pure_code(code), p['kind'], p['trig'],
                            p['target'], p['stop'],
                            ('%.3f' % qt['now']) if qt.get('now') else '—'),
                        code)
                continue
            # 止损只看触发之后的现价：日内累计最低(qt['low'])含触发前早盘探底的坑底低点，
            # 拿它判新仓会把“早盘砸过、下午反弹拉起”的票刚触发就误判止损（信号/止损相隔1秒）。
            if qt.get('now') is not None and qt['now'] <= p['stop']:
                p['status'] = 'stopped'
                do_push('跌破止损·%s' % (qt['name'] if qt else code),
                        ('%s %s信号\n触发价 %.3f 目标 %.3f 止损 %.3f 现价 %s\n'
                         '已经跌破止损价，立刻卖，不恋战') % (
                            pure_code(code), p['kind'], p['trig'],
                            p['target'], p['stop'],
                            ('%.3f' % qt['now']) if qt.get('now') else '—'),
                        code)
                continue
        if t >= PM_END and not p.get('miss_notified'):
            now_px = qt['now'] if qt else None
            title = '目标未到达·%s' % (qt['name'] if qt else code)
            msg = ('%s %s信号\n触发价 %.3f 目标 %.3f 现价 %s\n'
                   '到点没摸到目标，别傻等，自己决定走不走\n'
                   '跌破止损 %.3f 仍要立刻卖') % (
                pure_code(code), p['kind'], p['trig'], p['target'],
                ('%.3f' % now_px) if now_px else '—', p['stop'])
            do_push(title, msg, code)
            p['miss_notified'] = True
            p['status'] = 'miss'


def append_day_signals(pushed):
    fn = 'day_signals.json'
    doc = {'date': TODAY, 'signals': []}
    try:
        old = json.load(open(fn, encoding='utf-8'))
        if old.get('date') == TODAY:
            doc = old
    except Exception:
        pass
    have = {x['code'] + x['kind'] for x in doc['signals']}
    for p in pushed:
        key = p['code'] + p['kind']
        if key not in have:
            doc['signals'].append(p)
    json.dump(doc, open(fn, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def feed_fresh(codes):
    """窗口内行情是否实时：抽样活跃票，看最新1分K根时间是否贴近当前。
    午休/接口冻结时K线停更，最新根明显落后，据此拦截陈旧信号，不拿冻结快照补推。"""
    t = now_min()
    fresh = 0
    checked = 0
    for c in codes[:8]:
        bars = fetch_kline(c, 'm1', 5)
        if not bars:
            continue
        checked += 1
        try:
            bt = bars[-1][0]  # YYYYMMDDHHMM
            bar_min = int(bt[-4:-2]) * 60 + int(bt[-2:])
            if t - bar_min <= STALE_KLINE_GAP:
                fresh += 1
        except Exception:
            continue
    if checked == 0:
        return False  # 取不到任何K线，不冒险推送
    return fresh * 2 >= checked  # 半数以上抽样票新鲜才算行情实时


def scan_once(force=False):
    t = now_min()
    if not force and (t < AM_START or t > PM_END):
        print('非信号窗口（9:30-14:50），安静退出 @%d' % t)
        return 0
    doc = load_state()
    mk = market_state()
    doc['market'] = {'state': mk['state'],
                     'hsi': mk['hsi']['pct'] if mk['hsi'] else None,
                     'hstech': mk['hstech']['pct'] if mk['hstech'] else None}
    codes = visible_codes()
    # 刷新美股净值缓存（静态预热，不临时估算）
    US_NAV_CACHE.clear()
    US_NAV_CACHE.update(load_us_nav())
    # 已破止损的票才允许重新启动（每轮按持仓实际状态刷新）
    REENTRY_ALLOWED.clear()
    REENTRY_ALLOWED.update(c for c, p in doc.get('position', {}).items()
                           if p.get('status') == 'stopped')
    quotes = qu.fetch_etf(codes + list(US_CODES))
    if not force and not feed_fresh(codes):
        print('行情停更/陈旧（最新K线落后），本轮不推信号，避免收盘后补推 @%d' % t)
        return 0
    is_am = t <= AM_END
    states = doc['am'] if is_am else doc['pm']
    print('%s扫描 %s  行情 %d 只 @%d  大盘%s' % (
        '早盘' if is_am else '下午', TODAY, len(quotes), t,
        '弱市' if is_weak() else '正常'))

    rev_states = doc['rev']
    # 顺势候选（对开盘涨）+ 各自 ready
    cand_codes = [c for c in codes if quotes.get(c) and quotes[c]['rt_open'] >= START_PCT]
    ready_codes = [c for c in states if states[c].get('phase') == 'ready']
    watch = list(dict.fromkeys(cand_codes + ready_codes))
    # 逆势候选（在坑里：现价低于开盘）+ 反转 ready
    rev_codes = [c for c in codes
                 if quotes.get(c) and quotes[c].get('open') and quotes[c]['now'] < quotes[c]['open']]
    rev_ready = [c for c in rev_states if rev_states[c].get('phase') == 'ready']
    rev_watch = list(dict.fromkeys(rev_codes + rev_ready))
    # 美股坑底候选（独立状态 doc['us']，只做坑里反弹，不追开盘）
    us_states = doc['us']
    us_codes_in = [c for c in US_CODES
                   if quotes.get(c) and quotes[c].get('open') and quotes[c]['now'] < quotes[c]['open']]
    us_ready = [c for c in us_states if us_states[c].get('phase') == 'ready']
    us_watch = list(dict.fromkeys(us_codes_in + us_ready))
    # 二次探底再拉起（独立状态 doc['sb']；不要求现价在开盘下方，覆盖浅坑双底）
    sb_states = doc['sb']
    sb_ready = [c for c in sb_states if sb_states[c].get('phase') == 'ready']
    sb_watch = list(dict.fromkeys(codes + sb_ready))

    # 并发拉取本轮所需全部1分K（网络IO并行，把单轮从~85秒压到几秒），
    # 再串行做候选/闸门纯计算，配合触发即推实现秒级响应
    all_kl = list(dict.fromkeys(watch + rev_watch + us_watch + sb_watch))
    with ThreadPoolExecutor(max_workers=12) as ex:
        kl_cache = dict(zip(all_kl, ex.map(fetch_kline, all_kl)))

    for code in watch:
        qt = quotes.get(code)
        bars = kl_cache.get(code, [])
        cand = candidate_metrics(qt, bars) if qt else None
        gate_eval(states, code, cand, bars, qt, require_red=not is_am)

    # 低开反转（逆势，require_red 恒真）
    for code in rev_watch:
        qt = quotes.get(code)
        bars = kl_cache.get(code, [])
        cand = reversal_metrics(qt, bars)
        gate_eval(rev_states, code, cand, bars, qt, require_red=True, is_rev=True)

    # 美股坑底反弹（独立状态；企稳放量后走溢价安全门）
    for code in us_watch:
        qt = quotes.get(code)
        bars = kl_cache.get(code, [])
        cand = reversal_metrics(qt, bars)
        gate_eval(us_states, code, cand, bars, qt, require_red=True, is_rev=True)

    # 二次探底再拉起（独立状态；双底企稳放量后走红率门）
    for code in sb_watch:
        qt = quotes.get(code)
        bars = kl_cache.get(code, [])
        cand = second_bottom_metrics(qt, bars)
        gate_eval(sb_states, code, cand, bars, qt, require_red=True, is_rev=False)

    pushed = []
    try:
        pushed = push_am_locked(doc, quotes, states) if is_am else push_pm(doc, quotes, states)
        pushed += push_lowup_early(doc, quotes, codes)
        pushed += push_rev(doc, quotes, rev_states)
        pushed += push_us(doc, quotes, us_states)
        pushed += push_sb(doc, quotes, sb_states)
        track_positions(doc, quotes, t)
    except Exception as e:
        # 单条推送/持仓分支异常不得阻断去重状态落盘，否则下一轮会重复推送同一只
        print('推送分支异常（不阻断去重落盘）: %s' % e, flush=True)
        pushed = pushed if isinstance(pushed, list) else []
    json.dump(doc, open('signal_state.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    append_day_signals(pushed)

    n_conf = sum(1 for v in states.values() if v.get('phase') == 'confirm')
    n_fail = sum(1 for v in states.values() if v.get('phase') == 'fail')
    r_conf = sum(1 for v in rev_states.values() if v.get('phase') == 'confirm')
    u_conf = sum(1 for v in us_states.values() if v.get('phase') == 'confirm')
    print('本轮新推 %d；顺势 confirm %d/fail %d；反转 confirm %d；美股 confirm %d'
          % (len(pushed), n_conf, n_fail, r_conf, u_conf))
    return len(pushed)


def write_heartbeat():
    with open('scan_pm.heartbeat', 'w') as f:
        f.write(str(round(time.time())))


def _sigterm(signum, frame):
    raise SystemExit('收到 SIGTERM，优雅退出')


def in_window(t):
    # 全部为 A 股上市 ETF（港股/期货/美股QDII），按 A 股时段：9:30-11:30、13:00-14:50
    # 午休 11:30-13:00 不扫描（K线停更、价格冻结，空扫只会用冻结快照误触发陈旧信号）
    return (AM_START <= t < AM_CLOSE) or (PM_START <= t <= PM_END)


def loop_forever():
    # 单实例锁：任何来源（系统托管/兜底 supervisord/手动）重复拉起第二个 --loop 时直接退出，防双开重复推送
    loop_lock = open('scan_pm.loop.lock', 'w')
    try:
        fcntl.flock(loop_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('已有 scan_pm --loop 常驻在跑，本实例退出（防双开）', flush=True)
        return
    # supervisord 停服时发 SIGTERM，转成异常走 finally 清理 pid
    signal.signal(signal.SIGTERM, _sigterm)
    with open('scan_pm.pid', 'w') as f:
        f.write(str(os.getpid()))
    print('常驻进程启动 pid=%d（全天常驻，窗口内才扫描）' % os.getpid(), flush=True)
    write_heartbeat()
    start_day = datetime.date.today()
    try:
        while True:
            # 跨天：日期变了就重新执行自身，干净重置 TODAY/MARKET 等模块常量
            if datetime.date.today() != start_day:
                print('跨天，重新执行自身', flush=True)
                os.execv(sys.executable, [sys.executable] + sys.argv)
            t = now_min()
            try:
                if in_window(t):
                    scan_once()
                    time.sleep(POLL_SLEEP)
                else:
                    time.sleep(IDLE_SLEEP)  # 非窗口安静等待，不退出、不空扫
            except Exception as e:
                print('本轮异常不退出: %s' % e, flush=True)
                time.sleep(POLL_SLEEP)
            write_heartbeat()  # 每轮报告心跳，看门狗据此判断卡死
    finally:
        try:
            os.remove('scan_pm.pid')
        except OSError:
            pass
    print('常驻进程结束', flush=True)


def daemon_alive():
    """进程在 + 心跳新鲜，才算真存活；进程在但心跳陈旧（卡死）则杀掉，返回 False。"""
    try:
        pid = int(open('scan_pm.pid').read().strip())
        os.kill(pid, 0)
    except Exception:
        return False
    try:
        hb = float(open('scan_pm.heartbeat').read().strip())
    except Exception:
        return False
    if time.time() - hb > HEARTBEAT_STALE:
        print('进程在但心跳陈旧(卡死)，杀掉重启', flush=True)
        try:
            os.kill(pid, 9)
        except Exception:
            pass
        return False
    return True


# —— 用户态 supervisord 保活（秒级崩溃自愈；容器重启后由每小时 cron --ensure 恢复）——
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SUP_BIN = '/usr/local/bin/supervisord'
SUP_CONF = os.path.join(BASE_DIR, 'scan_pm_supervisord.conf')
SUP_PID = os.path.join(BASE_DIR, 'supervisord.pid')
SUP_SOCK = os.path.join(BASE_DIR, 'supervisor.sock')


def supervisord_alive():
    try:
        pid = int(open(SUP_PID).read().strip())
        os.kill(pid, 0)
    except Exception:
        return False
    # 容器重启后 PID 可能被其他进程复用，必须核对 cmdline 确实是本实例 supervisord
    try:
        cmdline = open('/proc/%d/cmdline' % pid, 'rb').read().replace(b'\x00', b' ').decode('utf-8', 'ignore')
        if SUP_CONF not in cmdline:
            return False
    except Exception:
        return False
    return True


def loop_running():
    """是否已有 scan_pm --loop 常驻进程在跑（系统托管或用户态兜底均可），避免双开。"""
    try:
        out = subprocess.run(['pgrep', '-f', 'python3 scan_pm.py --loop'],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return bool(out)
    except Exception:
        return False


def ensure_daemon():
    if loop_running():
        print('已有 scan_pm --loop 常驻在跑，无需拉起（系统托管/兜底均不双开）', flush=True)
        return
    if supervisord_alive():
        print('supervisord 保活实例存活，scan_pm 由其托管（崩溃秒级重启），无需拉起')
        return
    # 异常退出可能残留 pid/sock，先清理再启动，避免 supervisord 误判已在运行
    for f in (SUP_PID, SUP_SOCK):
        try:
            os.remove(f)
        except OSError:
            pass
    subprocess.Popen([SUP_BIN, '-c', SUP_CONF],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    print('supervisord 保活实例未运行，已后台拉起（自动拉起 scan_pm）', flush=True)


def main():
    if '--loop' in sys.argv:
        loop_forever()
        return
    if '--ensure' in sys.argv:
        ensure_daemon()
        return
    lock_fp = open('scan_pm.lock', 'w')
    try:
        fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('上一轮仍在运行，本轮跳过')
        return
    scan_once(os.environ.get('PM_FORCE') == '1')


if __name__ == '__main__':
    main()
