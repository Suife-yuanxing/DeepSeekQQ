#!/bin/bash
set -e
cd /home/ubuntu/DeepSeekQQ
[ -d plugins/deepseek ] && cp -r plugins/deepseek plugins/deepseek_backup_$(date +%m%d_%H%M)
find plugins/deepseek -maxdepth 1 -type f -name "*.py" -delete 2>/dev/null || true
pip install -q aiofiles 2>/dev/null || pip install aiofiles
echo "✅ 部署完成，请执行: python bot.py"
