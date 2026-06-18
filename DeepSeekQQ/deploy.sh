#!/bin/bash
set -euo pipefail

# ============================================================
# DeepSeekQQ 部署脚本
# 用法: ./deploy.sh [--no-restart] [--skip-syntax]
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

SERVICE_NAME="deepseek-bot"
BACKUP_BASE="$HOME/backups/deepseek"
NO_RESTART=false
SKIP_SYNTAX=false
PORTS=(8082 8765)

# 探测 Python：优先使用 venv，其次系统 python3/python
if [ -f "$SCRIPT_DIR/.venv/bin/python" ]; then
    PYTHON="$SCRIPT_DIR/.venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
elif command -v python &>/dev/null; then
    PYTHON="python"
else
    echo "❌ 找不到 Python，请确保已安装或 venv 已创建"
    exit 1
fi
echo "🐍 Python: $PYTHON"

for arg in "$@"; do
    case "$arg" in
        --no-restart) NO_RESTART=true ;;
        --skip-syntax) SKIP_SYNTAX=true ;;
    esac
done

# ============================================================
# 工具函数
# ============================================================

# 等待端口释放（最多等30秒）
wait_port_release() {
    local port=$1
    local max_wait=${2:-30}
    local waited=0
    while ss -tlnp 2>/dev/null | grep -q ":${port} "; do
        if [ $waited -ge $max_wait ]; then
            echo "⚠️ 端口 $port 等待超时（${max_wait}s），强制继续"
            return 1
        fi
        sleep 1
        waited=$((waited + 1))
    done
    echo "✅ 端口 $port 已释放（等待 ${waited}s）"
    return 0
}

# 检查所有端口是否被当前 bot 进程占用
check_ports_clean() {
    for port in "${PORTS[@]}"; do
        if ss -tlnp 2>/dev/null | grep -q ":${port} "; then
            return 1
        fi
    done
    return 0
}

# 回滚到备份
rollback() {
    local backup_dir=$1
    if [ -d "$backup_dir" ]; then
        echo "⏪ 正在回滚到 $backup_dir ..."
        rm -rf plugins/deepseek
        cp -r "$backup_dir/plugins/deepseek" plugins/
        cp "$backup_dir"/bot.py "$backup_dir"/pyproject.toml "$backup_dir"/requirements.txt . 2>/dev/null || true
        echo "✅ 已回滚"
    fi
}

# ============================================================
# 主流程
# ============================================================

echo "🔄 开始部署..."

# 1. 备份当前版本（放到项目外部）
BACKUP_DIR="$BACKUP_BASE/deepseek_$(date +%m%d_%H%M)"
if [ -d plugins/deepseek ]; then
    mkdir -p "$BACKUP_BASE"
    # 备份核心文件
    mkdir -p "$BACKUP_DIR/plugins/deepseek"
    cp -r plugins/deepseek "$BACKUP_DIR/plugins/"
    cp bot.py pyproject.toml requirements.txt "$BACKUP_DIR/" 2>/dev/null || true
    echo "✅ 已备份到 $BACKUP_DIR"
fi

# 2. 拉取最新代码
echo "📥 拉取最新代码..."
if ! git pull origin master; then
    echo "❌ git pull 失败，中止部署"
    exit 1
fi

# 3. 语法检查（部署前拦截语法错误，避免端口冲突洪水）
if [ "$SKIP_SYNTAX" = false ]; then
    echo "🔍 语法检查..."
    SYNTAX_OK=true
    while IFS= read -r pyfile; do
        if ! $PYTHON -c "import py_compile; py_compile.compile('$pyfile', doraise=True)" 2>/dev/null; then
            # py_compile 静默失败，用 compile() 内置函数再试一次以获取错误详情
            ERROR_MSG=$($PYTHON -c "
import sys
try:
    with open('$pyfile') as f:
        compile(f.read(), '$pyfile', 'exec')
except SyntaxError as e:
    print(f'SyntaxError: {e.msg} at line {e.lineno}')
    sys.exit(1)
" 2>&1) || true
            if [ -n "$ERROR_MSG" ]; then
                echo "❌ $pyfile: $ERROR_MSG"
                SYNTAX_OK=false
            fi
        fi
    done < <(find plugins/deepseek -name "*.py" -type f; for f in bot.py config.py; do [ -f "$f" ] && echo "$f"; done)

    if [ "$SYNTAX_OK" = false ]; then
        echo "❌ 语法检查失败，中止部署（未影响线上服务）"
        echo "   修复后重新部署，或使用 --skip-syntax 跳过检查"
        exit 1
    fi
    echo "✅ 语法检查通过"
else
    echo "⚠️ 跳过语法检查"
fi

# 4. 安装所有依赖（优先 requirements.txt 精确版本，避免 pyproject.toml >= 拉取破坏性更新）
echo "📦 安装依赖..."
if [ -f requirements.txt ]; then
    $PYTHON -m pip install -r requirements.txt || { echo "❌ 依赖安装失败，中止部署"; exit 1; }
elif [ -f pyproject.toml ]; then
    $PYTHON -m pip install -e . || { echo "❌ 依赖安装失败，中止部署"; exit 1; }
else
    echo "⚠️ 未找到依赖文件，跳过"
fi

# 5. 数据库迁移（如果有）
if [ -f plugins/deepseek/migrations.py ]; then
    echo "🗃️ 检查数据库迁移..."
    $PYTHON -c "from plugins.deepseek.migrations import run_migrations; import asyncio; asyncio.run(run_migrations())" || { echo "❌ 数据库迁移失败，中止部署"; exit 1; }
fi

# 6. 重启服务（安全模式：stop → 等端口释放 → start）
if [ "$NO_RESTART" = false ]; then
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        echo "🛑 停止服务..."
        sudo systemctl stop "$SERVICE_NAME"

        # 等待所有端口释放
        echo "⏳ 等待端口释放..."
        for port in "${PORTS[@]}"; do
            wait_port_release "$port" 30 || true
        done

        echo "🚀 启动服务..."
        sudo systemctl start "$SERVICE_NAME"
        sleep 3

        if systemctl is-active --quiet "$SERVICE_NAME"; then
            echo "✅ 服务已重启"
        else
            echo "❌ 服务启动失败！"
            echo "   查看日志: journalctl -u $SERVICE_NAME -n 30"
            echo "   尝试回滚..."
            rollback "$BACKUP_DIR"
            echo "🔄 用旧版本启动..."
            sudo systemctl start "$SERVICE_NAME"
            sleep 3
            if systemctl is-active --quiet "$SERVICE_NAME"; then
                echo "✅ 已回滚并恢复运行"
            else
                echo "❌ 回滚后仍无法启动，请手动排查"
                exit 1
            fi
        fi
    else
        echo "⚠️ 服务 $SERVICE_NAME 未运行"
        echo "🚀 启动服务..."
        sudo systemctl start "$SERVICE_NAME"
        sleep 3
        if systemctl is-active --quiet "$SERVICE_NAME"; then
            echo "✅ 服务已启动"
        else
            echo "❌ 服务启动失败，请检查日志: journalctl -u $SERVICE_NAME -n 30"
            exit 1
        fi
    fi
else
    echo "⚠️ 跳过服务重启（--no-restart）"
fi

# 7. 健康检查
echo "🔍 健康检查..."
sleep 2
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    echo "✅ 部署完成，服务运行中"
    # 快速端口检查
    for port in "${PORTS[@]}"; do
        if ss -tlnp 2>/dev/null | grep -q ":${port} "; then
            echo "   📡 端口 $port: 监听中"
        else
            echo "   ⚠️ 端口 $port: 未监听"
        fi
    done
else
    echo "⚠️ 部署完成，但服务状态未知"
fi

# 8. 清理旧备份（保留最近 5 个）
if [ -d "$BACKUP_BASE" ]; then
    ls -dt "$BACKUP_BASE"/deepseek_* 2>/dev/null | tail -n +6 | xargs rm -rf 2>/dev/null || true
fi
