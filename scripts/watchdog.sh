#!/bin/bash
# 进程守护：QQ 崩溃后自动重启

while true; do
    # 检查 QQ 进程
    if ! pgrep -f "qq --no-sandbox" > /dev/null; then
        echo "$(date): QQ 进程不存在，正在重启..."
        tmux kill-session -t napcat 2>/dev/null
        tmux new-session -d -s napcat "xvfb-run -a qq --no-sandbox"
        echo "$(date): QQ 已重启"
    fi
    
    # 检查 NoneBot 进程
    if ! pgrep -f "nb run" > /dev/null; then
        echo "$(date): NoneBot 进程不存在，正在重启..."
        tmux kill-session -t nonebot 2>/dev/null
        cd /home/ubuntu/DeepSeekQQ && tmux new-session -d -s nonebot "nb run"
        echo "$(date): NoneBot 已重启"
    fi
    
    sleep 30
done
