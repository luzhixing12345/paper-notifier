#!/usr/bin/env bash

# ===== 配置 =====
SCRIPT_PATH="$(pwd)/build-cache.py"
PYTHON_PATH="/home/lzx/.venv/bin/python"

# 北京时间 = UTC+8
# cron 默认使用系统时区，这里强制指定 TZ
CRON_JOB="0 8 * * * TZ=Asia/Shanghai $PYTHON_PATH $SCRIPT_PATH > $SCRIPT_PATH.log 2>&1"

# ===== 添加任务（避免重复）=====
(crontab -l 2>/dev/null | grep -v "$SCRIPT_PATH"; echo "$CRON_JOB") | crontab -

echo "✅ 定时任务已添加：每天北京时间 8 点运行 $SCRIPT_PATH"
