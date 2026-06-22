#!/bin/bash
# Phase 0.0.5: 全量数据库备份脚本
# 备份所有 SQLite DB，sha256 校验，保留最近 5 次
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${PROJECT_DIR}/data/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

echo "=== 念念 Bot 数据库备份 $TIMESTAMP ==="

# 备份文件列表
DBS=(
    "${PROJECT_DIR}/data/chat_memory.db"
    "${PROJECT_DIR}/data/chat_memory.db-shm"
    "${PROJECT_DIR}/data/chat_memory.db-wal"
)

BACKUP_FILE="${BACKUP_DIR}/backup_${TIMESTAMP}.tar.gz"
CHECKSUM_FILE="${BACKUP_DIR}/backup_${TIMESTAMP}.sha256"

# 打包并校验
tar -czf "$BACKUP_FILE" "${DBS[@]}" 2>/dev/null || {
    # 如果 tar 失败（某些 DB 文件可能不存在），逐个备份
    for db in "${DBS[@]}"; do
        if [ -f "$db" ]; then
            cp "$db" "${BACKUP_DIR}/$(basename $db)_${TIMESTAMP}"
        fi
    done
    echo "[警告] tar 打包失败，已逐个复制"
}

# sha256
if command -v sha256sum &>/dev/null; then
    sha256sum "$BACKUP_FILE" > "$CHECKSUM_FILE" 2>/dev/null || true
elif command -v shasum &>/dev/null; then
    shasum -a 256 "$BACKUP_FILE" > "$CHECKSUM_FILE" 2>/dev/null || true
fi

# 保留最近 5 次备份
ls -t "${BACKUP_DIR}"/backup_*.tar.gz 2>/dev/null | tail -n +6 | xargs rm -f 2>/dev/null || true
ls -t "${BACKUP_DIR}"/backup_*.sha256 2>/dev/null | tail -n +6 | xargs rm -f 2>/dev/null || true

echo "备份完成: $BACKUP_FILE"
echo "校验文件: $CHECKSUM_FILE"
echo "当前备份数: $(ls ${BACKUP_DIR}/backup_*.tar.gz 2>/dev/null | wc -l)"
