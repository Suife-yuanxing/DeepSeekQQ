#!/bin/bash
set -euo pipefail

# ============================================================
# DeepSeekQQ 部署脚本
# 用法: ./deploy.sh [--no-restart]
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

SERVICE_NAME="deepseek-bot"
BACKUP_BASE="/tmp/deepseek_backups"
NO_RESTART=false

for arg in "$@"; do
    case "$arg" in
        --no-restart) NO_RESTART=true ;;
    esac
done

echo "🔄 开始部署..."

# 1. 备份当前版本（放到项目外部）
BACKUP_DIR="$BACKUP_BASE/deepseek_$(date +%m%d_%H%M)"
if [ -d plugins/deepseek ]; then
    mkdir -p "$BACKUP_BASE"
    cp -r plugins/deepseek "$BACKUP_DIR"
    echo "✅ 已备份到 $BACKUP_DIR"
fi

# 2. 拉取最新代码
echo "📥 拉取最新代码..."
if ! git pull origin main; then
    echo "❌ git pull 失败，中止部署"
    exit 1
fi

# 3. 安装所有依赖
echo "📦 安装依赖..."
if [ -f pyproject.toml ]; then
    pip install -e . -q 2>/dev/null || pip install -e .
elif [ -f requirements.txt ]; then
    pip install -r requirements.txt -q 2>/dev/null || pip install -r requirements.txt
else
    echo "⚠️ 未找到依赖文件，跳过"
fi

# 4. 数据库迁移（如果有）
if [ -f plugins/deepseek/migrations.py ]; then
    echo "🗃️ 检查数据库迁移..."
    python -c "from plugins.deepseek.migrations import run_migrations; import asyncio; asyncio.run(run_migrations())" 2>/dev/null || echo "⚠️ 迁移检查跳过"
fi

# 5. 重启服务
if [ "$NO_RESTART" = false ]; then
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        echo "🔄 重启服务..."
        sudo systemctl restart "$SERVICE_NAME"
        sleep 2
        if systemctl is-active --quiet "$SERVICE_NAME"; then
            echo "✅ 服务已重启"
        else
            echo "❌ 服务重启失败，请检查日志: journalctl -u $SERVICE_NAME -n 20"
            exit 1
        fi
    else
        echo "⚠️ 服务 $SERVICE_NAME 未运行，跳过重启"
        echo "   启动命令: sudo systemctl start $SERVICE_NAME"
    fi
fi

# 6. 健康检查
echo "🔍 健康检查..."
sleep 3
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    echo "✅ 部署完成，服务运行中"
else
    echo "⚠️ 部署完成，但服务状态未知"
fi

# 7. 清理旧备份（保留最近 5 个）
if [ -d "$BACKUP_BASE" ]; then
    ls -dt "$BACKUP_BASE"/deepseek_* 2>/dev/null | tail -n +6 | xargs rm -rf 2>/dev/null || true
fi
