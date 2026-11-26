# -*- coding: utf-8 -*-
"""独立守护 watcher：与 scan_pm 主进程解耦，专门负责"扫描进程死了就拉起"。

为什么需要独立进程：
  scan_pm --loop 一旦崩溃/被回收，它内部的线程会一起死，无法自救。
  本 watcher 是另一个常驻进程，独立探活、独立订阅 ntfy 指令：
    1. 主线程周期探活 scan_pm --loop：进程不在 / 心跳陈旧(卡死) 就调用
       scan_pm.py --ensure 复用既有互斥与拉起链路（不重复造拉起逻辑）。
    2. 后台线程订阅 ntfy 私有 topic，收到外部指令秒级动作：
         status / 状态  -> 回推扫描进程存活状态到 ntfy
         recover / 拉起 / 重启扫描 -> 探活并拉起，回推结果
  3. watcher 自身心跳写 watcher.heartbeat，状态写 watcher_status.json，供外部/兜底判断。

边界（与 scan_pm.py 一致）：
  - 本 watcher 不抓行情、不算信号、不生成网页；只做守护与指令应答。
  - 容器重启后 watcher 自身也会消失，靠平台 cron 的 --ensure 唤醒容器后由
    用户态 supervisord（conf autostart）一并拉起 scan_pm 与本 watcher。

用法：
  python3 scan_watcher.py --loop      常驻守护（由 supervisord 托管）
  python3 scan_watcher.py --ensure   看门狗：watcher 不在则后台拉起（单实例不双开）
"""
import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

PY = sys.executable
WATCHER_PID = os.path.join(BASE_DIR, 'watcher.pid')
WATCHER_HB = os.path.join(BASE_DIR, 'watcher.heartbeat')
WATCHER_LOCK = os.path.join(BASE_DIR, 'watcher.lock')
STATUS_JSON = os.path.join(BASE_DIR, 'watcher_status.json')
SCAN_PID = os.path.join(BASE_DIR, 'scan_pm.pid')
SCAN_HB = os.path.join(BASE_DIR, 'scan_pm.heartbeat')

PROBE_INTERVAL = 15          # 主线程探活间隔（秒）
HEARTBEAT_STALE = 180        # 与 scan_pm 一致：心跳超过该秒数判卡死
NTFY_RECONNECT_MIN = 3
NTFY_RECONNECT_MAX = 30

STATUS_CMDS = {'status', '状态', '健康', '心跳', 'state'}
RECOVER_CMDS = {'recover', '拉起', '重启扫描', '恢复', 'restart', '启动扫描'}


# ----------------------------- 配置读取 -----------------------------

def _load_cfg():
    try:
        import notify_push
        return notify_push.load_config()
    except Exception:
        return {}


def _ntfy_cfg(cfg):
    c = (cfg or {}).get('ntfy') or {}
    server = (c.get('server') or 'https://ntfy.sh').rstrip('/')
    topic = c.get('topic') or ''
    return server, topic


def push_ntfy(title, message, tags=None):
    """只回推到 ntfy（与订阅同源），不打扰微信。失败不抛异常。"""
    try:
        import notify_push
        cfg = notify_push.load_config()
        return notify_push._send_ntfy(cfg, title, message, tags or [])
    except Exception as e:
        print('watcher ntfy 回推失败: %s' % e, flush=True)
        return {'ok': False, 'detail': str(e)}


# ----------------------------- 探活判定 -----------------------------

def _pgrep_loop():
    """返回 scan_pm --loop 进程 pid 列表（空=不在）。"""
    try:
        out = subprocess.run(['pgrep', '-f', 'python3 scan_pm.py --loop'],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return [p for p in out.splitlines() if p.strip()]
    except Exception:
        return []


def loop_state():
    """返回 dict：loop 是否在跑、pid、心跳差、是否卡死。"""
    pids = _pgrep_loop()
    state = {'running': bool(pids), 'pid': pids[0] if pids else None,
             'age_sec': None, 'stale': False}
    if not pids:
        return state
    try:
        hb = float(open(SCAN_HB).read().strip())
        age = time.time() - hb
        state['age_sec'] = round(age, 1)
        state['stale'] = age > HEARTBEAT_STALE
    except Exception:
        state['age_sec'] = None
        state['stale'] = True  # 心跳文件读不到，按卡死处理
    return state


def _kill_stale_loop(state):
    """进程在但心跳陈旧：杀掉残留，再走 ensure 拉起。"""
    pid = state.get('pid')
    if pid:
        try:
            os.kill(int(pid), 9)
            print('watcher: 扫描进程心跳陈旧，已杀残留 pid=%s' % pid, flush=True)
        except Exception:
            pass
    try:
        os.remove(SCAN_PID)
    except OSError:
        pass


SUP_CONF = os.path.join(BASE_DIR, 'scan_pm_supervisord.conf')


def run_ensure():
    """复用 scan_pm.py --ensure 既有互斥/拉起链路；并兜底 start scan_pm program。

    覆盖场景：兜底 supervisord 在跑、但 scan_pm program 处于 STOPPED/FATAL，
    ensure_daemon 见 supervisord 存活会直接返回，不会主动 start；这里补一刀。
    已在跑时 supervisorctl start 报 already started，无妨，幂等不双开。
    """
    try:
        subprocess.run(['supervisorctl', '-c', SUP_CONF, 'start', 'scan_pm'],
                       cwd=BASE_DIR, capture_output=True, text=True, timeout=15)
    except Exception:
        pass
    try:
        r = subprocess.run([PY, os.path.join(BASE_DIR, 'scan_pm.py'), '--ensure'],
                           cwd=BASE_DIR, capture_output=True, text=True, timeout=30)
        out = (r.stdout or '') + (r.stderr or '')
        return r.returncode == 0, out.strip()
    except Exception as e:
        return False, 'ensure 异常: %s' % e


def write_status(state, note=''):
    try:
        doc = {
            'watcher_pid': os.getpid(),
            'watcher_heartbeat': round(time.time()),
            'loop_running': state.get('running'),
            'loop_pid': state.get('pid'),
            'loop_age_sec': state.get('age_sec'),
            'loop_stale': state.get('stale'),
            'note': note,
            'checked_at': time.strftime('%F %T'),
        }
        with open(STATUS_JSON, 'w', encoding='utf-8') as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print('watcher: 状态文件写入失败: %s' % e, flush=True)


def write_watcher_hb():
    with open(WATCHER_HB, 'w') as f:
        f.write(str(round(time.time())))


def ensure_loop_if_needed():
    """探活一次；不健康就拉起。返回 (state, action)。"""
    state = loop_state()
    action = 'none'
    if not state['running']:
        ok, out = run_ensure()
        action = 'ensure(进程不在)'
        print('watcher: 扫描进程不在 -> ensure: %s' % out, flush=True)
    elif state['stale']:
        _kill_stale_loop(state)
        ok, out = run_ensure()
        action = 'ensure(心跳陈旧已杀残留)'
        print('watcher: 扫描进程卡死 -> 杀残留后 ensure: %s' % out, flush=True)
    else:
        write_status(state, '正常')
    return state, action


# ----------------------------- ntfy 指令订阅 -----------------------------

def _subscribe_loop(cfg):
    """后台线程：长连订阅 ntfy topic，断线退避重连。"""
    server, topic = _ntfy_cfg(cfg)
    if not topic:
        print('watcher: ntfy topic 未配置，指令订阅不启动', flush=True)
        return
    url = '%s/%s/json' % (server, topic)
    backoff = NTFY_RECONNECT_MIN
    while True:
        try:
            req = urllib.request.Request(url, headers={'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                backoff = NTFY_RECONNECT_MIN
                for raw in resp:
                    line = raw.decode('utf-8', 'ignore').strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except Exception:
                        continue
                    if msg.get('event') != 'message':
                        continue
                    text = (msg.get('message') or '').strip()
                    if not text:
                        continue
                    handle_command(text)
        except Exception as e:
            print('watcher: ntfy 订阅断线(%s)，%d 秒后重连' % (e, backoff), flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, NTFY_RECONNECT_MAX)


def handle_command(text):
    cmd = text.strip().lower()
    if cmd in STATUS_CMDS:
        state = loop_state()
        if state['running']:
            msg = '扫描进程在跑 pid=%s，心跳差 %s 秒' % (
                state['pid'], state['age_sec'] if state['age_sec'] is not None else '?')
            if state['stale']:
                msg += '（心跳陈旧，判卡死）'
        else:
            msg = '扫描进程不在'
        push_ntfy('watcher 状态', msg, ['clipboard'])
        print('watcher: 收到 status 指令 -> %s' % msg, flush=True)
    elif cmd in RECOVER_CMDS:
        state, action = ensure_loop_if_needed()
        time.sleep(2)
        state2 = loop_state()
        if state2['running']:
            msg = '已拉起/在跑 pid=%s，心跳差 %s 秒（动作: %s）' % (
                state2['pid'], state2['age_sec'], action)
        else:
            msg = '拉起后仍未检测到进程（动作: %s）' % action
        push_ntfy('watcher 拉起结果', msg, ['wrench'])
        print('watcher: 收到 recover 指令 -> %s' % msg, flush=True)
    # 其余普通消息（信号推送/状态回推）不响应，避免回环。


# ----------------------------- 主循环 -----------------------------

def _sigterm(signum, frame):
    raise SystemExit('watcher 收到 SIGTERM，优雅退出')


def main_loop():
    signal.signal(signal.SIGTERM, _sigterm)
    with open(WATCHER_PID, 'w') as f:
        f.write(str(os.getpid()))
    write_watcher_hb()
    cfg = _load_cfg()
    threading.Thread(target=_subscribe_loop, args=(cfg,), daemon=True).start()
    print('watcher 常驻启动 pid=%d，探活间隔 %d 秒' % (os.getpid(), PROBE_INTERVAL), flush=True)
    try:
        while True:
            ensure_loop_if_needed()
            write_watcher_hb()
            time.sleep(PROBE_INTERVAL)
    finally:
        try:
            os.remove(WATCHER_PID)
        except OSError:
            pass


def watcher_running():
    try:
        out = subprocess.run(['pgrep', '-f', 'python3 scan_watcher.py --loop'],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return bool(out)
    except Exception:
        return False


def ensure_watcher():
    if watcher_running():
        print('watcher 已在跑，无需拉起（不双开）', flush=True)
        return
    # 残留 pid 清理
    for f in (WATCHER_PID,):
        try:
            os.remove(f)
        except OSError:
            pass
    log_fp = open(os.path.join(BASE_DIR, 'watcher.log'), 'a', buffering=1)
    subprocess.Popen([PY, os.path.join(BASE_DIR, 'scan_watcher.py'), '--loop'],
                     cwd=BASE_DIR,
                     stdout=log_fp, stderr=subprocess.STDOUT,
                     start_new_session=True)
    print('watcher 未运行，已后台拉起', flush=True)


def main():
    # 单实例锁：防止 supervisord 与手动/ensure 重复拉起
    lock_fp = open(WATCHER_LOCK, 'w')
    try:
        fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('watcher 上一轮仍在运行，本轮跳过')
        return
    if '--ensure' in sys.argv:
        ensure_watcher()
        return
    main_loop()


if __name__ == '__main__':
    main()
