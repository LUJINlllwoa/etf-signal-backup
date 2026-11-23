# -*- coding: utf-8 -*-
"""
共用行情模块：三源行情 + 重试 + 陈旧识别 + 延时重取。

背景：早盘开盘头几分钟，免费行情接口可能返回“现价=开盘价”的旧帧，
若不识别，会把“没刷新”当成“真平盘 0%”，红率/涨幅全部失真。
本模块统一做：腾讯主源 → 延时重取一次 → 新浪港股兜底；
并返回 stale 标记，调用方见到 stale=True 必须把红率标“暂缺”，不得当 0% 用。

标准行情 dict 字段：
  code / name / now / prev / open / high / amount / rt_open(现价较开盘%) / gap(开盘较昨收%)
"""
import time
import requests

HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'}
BATCH = 60

# 陈旧判定：有效行情中，现价仍贴在开盘价上的比例超过该值，即判旧帧
STALE_MOVE_RATIO = 0.3
RETRY_WAIT = 12  # 秒，延时重取间隔


def _num(s):
    """安全转float：清理末尾可能残留的引号/分号（港股末字段会带 '";' 尾巴），失败返回None。"""
    try:
        return float(s)
    except Exception:
        try:
            return float(str(s).strip().strip('";').strip())
        except Exception:
            return None


def _tencent(codes):
    """腾讯批量行情（A股/港股通用，每批≤60）"""
    out = {}
    codes = [c for c in codes if c]
    if not codes:
        return out
    for i in range(0, len(codes), BATCH):
        batch = codes[i:i + BATCH]
        try:
            url = 'https://qt.gtimg.cn/q=' + ','.join(batch)
            r = requests.get(url, timeout=8, headers={'User-Agent': 'Mozilla/5.0'})
            r.encoding = 'gbk'
            for line in r.text.strip().split('\n'):
                if '=' not in line:
                    continue
                code = line.split('=')[0].split('_')[-1].strip()
                p = line.split('~')
                if len(p) <= 37:
                    continue
                now = _num(p[3]); prev = _num(p[4]); op = _num(p[5])
                hi = _num(p[33]); lo = _num(p[34]); amt = _num(p[37])
                if not op or op <= 0 or now is None:
                    continue
                # 可选字段：取不到只是None，绝不拖垮整条核心数据
                prem = _num(p[77]) if len(p) > 77 else None
                nav = _num(p[78]) if len(p) > 78 else None
                out[code] = {
                    'code': code, 'name': p[1], 'now': now, 'prev': prev,
                    'open': op, 'high': hi, 'low': lo, 'amount': amt,
                    'rt_open': (now / op - 1) * 100,
                    'gap': (op / prev - 1) * 100 if prev and prev > 0 else 0,
                    'prem_live': prem,
                    'nav_live': nav}
        except Exception:
            pass
        time.sleep(0.2)
    return out


def _sina_hk(codes):
    """新浪港股兜底（hq.sinajs.cn 批量）。仅港股个股；A股ETF 不在此兜底。
    字段: 0英文 1中文 2开盘 3昨收 4最高 5最低 6现价 ..."""
    out = {}
    hk = [c for c in codes if c.startswith('hk')]
    if not hk:
        return out
    try:
        url = 'https://hq.sinajs.cn/list=' + ','.join(hk)
        r = requests.get(url, headers=HEADERS, timeout=8)
        r.encoding = 'gbk'
        for line in r.text.strip().split('\n'):
            if '=' not in line:
                continue
            code = line.split('=')[0].split('_')[-1].strip()
            val = line.split('"')[1] if '"' in line else ''
            a = val.split(',')
            if len(a) < 7:
                continue
            try:
                op = float(a[2]); prev = float(a[3]); hi = float(a[4]); now = float(a[6])
                if op <= 0 or now <= 0:
                    continue
                out[code] = {
                    'code': code, 'name': a[1], 'now': now, 'prev': prev,
                    'open': op, 'high': hi, 'amount': 0,
                    'rt_open': (now / op - 1) * 100,
                    'gap': (op / prev - 1) * 100 if prev > 0 else 0,
                }
            except Exception:
                pass
    except Exception:
        pass
    return out


def stale_of(q, codes):
    """返回 (stale, moved, valid)。
    valid: 有有效开盘/现价的只数；moved: 现价已偏离开盘价的只数。
    全部或绝大多数贴在开盘价上 → 旧帧。"""
    valid = [c for c in codes if q.get(c) and q[c]['open'] > 0 and q[c]['now'] > 0]
    if not valid:
        return True, 0, 0
    moved = [c for c in valid if abs(q[c]['now'] - q[c]['open']) > 1e-9]
    if not moved:
        return True, 0, len(valid)
    if len(moved) < max(1, len(valid) * STALE_MOVE_RATIO):
        return True, len(moved), len(valid)
    return False, len(moved), len(valid)


def fetch_safe(codes, retry_wait=RETRY_WAIT):
    """主入口：腾讯 → 陈旧则延时重取一次 → 仍陈旧用新浪兜底。
    返回 (quotes, stale, source)。
    source ∈ tencent / tencent-retry / sina / mixed；stale=True 时数据不可用于判涨跌。"""
    codes = [c for c in codes if c]
    q = _tencent(codes)
    stale, moved, valid = stale_of(q, codes)
    source = 'tencent'

    if stale:
        time.sleep(retry_wait)  # 等第一笔成交把现价刷下来
        q2 = _tencent(codes)
        s2, m2, v2 = stale_of(q2, codes)
        if not s2:
            return q2, False, 'tencent-retry'
        # 新浪兜底
        qs = _sina_hk(codes)
        s3, _, _ = stale_of(qs, codes)
        if not s3:
            return qs, False, 'sina'
        # 两源都旧：合并已有字段，强标陈旧
        merged = dict(q)
        merged.update(qs)
        return merged, True, 'mixed'

    return q, False, source


def fetch_etf(codes):
    """ETF 实时行情（主源腾讯，不做成分股那套陈旧兜底；调用方自行判断现价=昨收）。"""
    return _tencent(codes)
