#!/bin/bash
# scan_pm 系统托管自举脚本（由系统级 supervisord 拉起）。
# 幂等：同一时刻只允许一个 scan_pm --loop，避免系统托管与用户态兜底双开。
cd /home/user/.doubao/agent_mode/workspace/bt_data || exit 1

# 已有常驻 python 进程在跑则不再重复拉起（只匹配 python 命令行，排除本脚本自身）
existing=$(pgrep -f "python3 scan_pm.py --loop" | grep -v $$ || true)
if [ -n "$existing" ]; then
    echo "[$(date '+%F %T')] scan_pm --loop 已在运行 pid=$existing，本实例退出" >> scan_pm_system.log
    exit 0
fi

exec /opt/python3.12/bin/python3 scan_pm.py --loop
