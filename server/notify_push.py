# -*- coding: utf-8 -*-
"""统一消息推送：出信号时同时发到「手机系统通知(ntfy)」和「微信(Server酱 / PushPlus)」。

凭证全部读同目录 notify_config.json，不写死在代码里；某个通道没填就自动跳过，
某个通道报错不影响其他通道。仅用标准库，无第三方依赖。

用法：
  python3 notify_push.py "标题" "正文"     # 命令行测试，向所有已配置通道发
  from notify_push import push
  push("标题", "正文", tags=["bell"])       # 脚本内调用
"""
import json
import os
import time
import urllib.request
import urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(BASE, 'notify_config.json')
TIMEOUT = 10

DEFAULT_CONFIG = {
    'ntfy': {'server': 'https://ntfy.sh', 'topic': '', 'priority': 4, 'tags': []},
    'wxpusher': {'spt': ''},
    'serverchan': {'sendkey': ''},
    'pushplus': {'token': ''},
}


def load_config():
    """读配置；不存在则生成一份空模板（各通道留空，等用户填凭证）。"""
    if not os.path.exists(CONFIG):
        save_config(DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        d = json.load(open(CONFIG, encoding='utf-8'))
    except Exception:
        d = {}
    # 合并缺省字段，保证结构完整
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in d.items():
        if isinstance(v, dict) and k in merged:
            merged[k].update({kk: vv for kk, vv in v.items() if vv is not None})
        else:
            merged[k] = v
    return merged


def save_config(cfg):
    with open(CONFIG, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _http(url, data=None, headers=None, raw=False):
    """发 HTTP 请求，返回响应文本；失败抛异常由调用方捕获。"""
    if data is not None and not isinstance(data, bytes):
        data = urllib.parse.urlencode(data).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode('utf-8', 'ignore')


def _send_ntfy(cfg, title, message, tags, click=None):
    c = cfg.get('ntfy') or {}
    server = (c.get('server') or 'https://ntfy.sh').rstrip('/')
    topic = c.get('topic')
    if not topic:
        return {'ok': False, 'skip': True, 'detail': '未配置 ntfy topic'}
    body = {
        'topic': topic,
        'title': title,
        'message': message,
        'priority': int(c.get('priority') or 4),
    }
    if click:
        body['click'] = click
    t = list(c.get('tags') or [])
    if tags:
        t += list(tags)
    if t:
        body['tags'] = t
    data = json.dumps(body, ensure_ascii=False).encode('utf-8')
    last = None
    for attempt in range(3):  # 偶发握手超时自动重试，最多3次，避免漏推
        try:
            txt = _http(server, data=data, headers={'Content-Type': 'application/json'})
            d = json.loads(txt)
            return {'ok': True, 'detail': 'ntfy id=%s' % d.get('id')}
        except Exception as e:
            last = e
            time.sleep(1)
    raise last


def _send_serverchan(cfg, title, message):
    c = cfg.get('serverchan') or {}
    key = (c.get('sendkey') or '').strip()
    if not key:
        return {'ok': False, 'skip': True, 'detail': '未配置 Server酱 sendkey'}
    url = 'https://sctapi.ftqq.com/%s.send' % key
    txt = _http(url, data={'title': title, 'desp': message})
    d = json.loads(txt)
    ok = d.get('code') == 0
    return {'ok': ok, 'detail': 'Server酱 code=%s %s' % (d.get('code'), d.get('message', ''))}


def _send_pushplus(cfg, title, message):
    c = cfg.get('pushplus') or {}
    token = (c.get('token') or '').strip()
    if not token:
        return {'ok': False, 'skip': True, 'detail': '未配置 PushPlus token'}
    body = {'token': token, 'title': title, 'content': message, 'template': 'txt'}
    data = json.dumps(body, ensure_ascii=False).encode('utf-8')
    txt = _http('https://www.pushplus.plus/send', data=data,
                headers={'Content-Type': 'application/json'})
    d = json.loads(txt)
    ok = d.get('code') == 200
    return {'ok': ok, 'detail': 'PushPlus code=%s %s' % (d.get('code'), d.get('msg', ''))}


def _send_wxpusher(cfg, title, message, html=None, url=None):
    """WxPusher 极简推送：只需个人 SPT，走国内厂商系统推送通道。

    传 html 则用 HTML 消息（contentType=2，可带一键复制按钮），否则纯文字。
    传 url 则点消息可跳转（支持自定义 URL Scheme 拉起看板自动复制代码）。
    """
    c = cfg.get('wxpusher') or {}
    spt = (c.get('spt') or '').strip()
    if not spt:
        return {'ok': False, 'skip': True, 'detail': '未配置 WxPusher spt'}
    body = {
        'spt': spt,
        'summary': title,
        'content': html if html else message,
        'contentType': 2 if html else 1,
    }
    if url:
        body['url'] = url
    data = json.dumps(body, ensure_ascii=False).encode('utf-8')
    txt = _http('https://wxpusher.zjiecode.com/api/send/message/simple-push',
                data=data, headers={'Content-Type': 'application/json'})
    d = json.loads(txt)
    ok = d.get('code') == 1000
    return {'ok': ok, 'detail': 'WxPusher code=%s %s' % (d.get('code'), d.get('msg', ''))}


def push(title, message, tags=None, html=None, url=None, click=None):
    """向所有已配置通道发送。返回 {通道: {ok, detail}}，不抛异常。

    html 仅对 WxPusher 生效（HTML 消息 + 一键复制按钮），其余通道仍发纯文字。
    url 仅对 WxPusher 生效（点击跳转/自定义 Scheme）。
    click 仅对 ntfy 生效（通知点击打开的链接）。
    """
    cfg = load_config()
    result = {}
    for name, fn in [('ntfy', _send_ntfy),
                     ('wxpusher', _send_wxpusher),
                     ('serverchan', _send_serverchan),
                     ('pushplus', _send_pushplus)]:
        try:
            if name == 'ntfy':
                result[name] = fn(cfg, title, message, tags, click)
            elif name == 'wxpusher':
                result[name] = fn(cfg, title, message, html, url)
            else:
                result[name] = fn(cfg, title, message)
        except Exception as e:
            result[name] = {'ok': False, 'detail': '%s 异常: %s' % (name, e)}
    return result


def configured_channels():
    """返回当前已填凭证、会实际发送的通道名列表。"""
    cfg = load_config()
    ch = []
    if (cfg.get('ntfy') or {}).get('topic'):
        ch.append('ntfy')
    if (cfg.get('wxpusher') or {}).get('spt'):
        ch.append('wxpusher')
    if (cfg.get('serverchan') or {}).get('sendkey'):
        ch.append('serverchan')
    if (cfg.get('pushplus') or {}).get('token'):
        ch.append('pushplus')
    return ch


def push_wxpusher(title, message, html=None):
    """只走 WxPusher（微信）单通道，当前主推送方式，不触发 ntfy 等其他通道。

    传 html 则带一键复制按钮；不抛异常，失败返回 {ok:False, detail}。
    """
    try:
        return _send_wxpusher(load_config(), title, message, html=html)
    except Exception as e:
        return {'ok': False, 'detail': 'wxpusher 异常: %s' % e}


if __name__ == '__main__':
    import sys
    if len(sys.argv) >= 3:
        t, m = sys.argv[1], sys.argv[2]
    else:
        t, m = '推送测试', '这是一条来自港股ETF看板的测试消息。'
    print('已配置通道:', configured_channels())
    print(json.dumps(push(t, m, tags=['bell']), ensure_ascii=False, indent=1))
