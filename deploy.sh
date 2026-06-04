#!/bin/bash
set -e
cd /home/ubuntu/DeepSeekQQ

echo "🔄 开始部署..."

# 备份当前版本
BACKUP_DIR="plugins/deepseek_backup_$(date +%m%d_%H%M)"
[ -d plugins/deepseek ] && cp -r plugins/deepseek "$BACKUP_DIR" && echo "✅ 已备份到 $BACKUP_DIR"

# 从 git 拉取最新代码（替代暴力删除）
git pull origin main 2>/dev/null || echo "⚠️ git pull 失败，跳过"

# 安装依赖
pip install -q aiofiles 2>/dev/null || pip install aiofiles

echo "✅ 部署完成，请执行: python bot.py"
