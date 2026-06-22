"""FastAPI 用户侧 API 包（端口 8766）。

与现有 NoneBot2 8082 进程物理隔离（S6）：
  - 8082: NoneBot2 + web_admin（ADMIN_API_KEY 认证，开发者监控）
  - 8766: 独立 FastAPI（JWT 认证，用户侧 REST + WS）

模块：
  - server.py: FastAPI app + 路由注册 + 启动入口
  - auth.py: JWT 双 Token + bcrypt + SMS 验证码 + auth 端点
  - deps.py: 依赖注入（get_current_user / require_admin / ownership 校验）
  - bots.py: Bot CRUD 端点
  - chat.py: WebSocket 聊天 + 消息历史端点
  - templates.py: 人格模板 API（6 套预设，Task 1.5 ✅ 2026-06-22）
  - abilities.py: 能力配置 API（Task 1.7 ✅ 2026-06-22）
  - quota.py: 额度管理 API（Task 1.8 ✅ 2026-06-22）
  - dashboard.py: 仪表盘聚合 API（Task 1.9 ✅ 2026-06-22）
  - notifications.py: 通知 API（Task 1.10 ✅ 2026-06-22）
  - sensitive_filter.py: 敏感词过滤引擎（Task 1.15 ✅ 2026-06-22）
  - stats.py: 统计聚合 API（Task 1.16 ✅ 2026-06-22）
  - admin.py: 开发者面板 API + 监控端点（Task 1.11 + 1.13 ✅ 2026-06-22）
  - chat_media.py: 语音/图片 API（Task 1.14 ✅ 2026-06-22）
  - channels.py: 通道管理 API（Task 1.17 ✅ 2026-06-22）
  - kms.py: API Key 加密存储 AES-256-GCM + KMS 升级接口（Task 1.3 ✅ 2026-06-22）
  - api_keys.py: API Key 管理 API（Task 1.3 ✅ 2026-06-22）

Phase 1 完成度：**17/17 Task 完成**（2026-06-22 收官：1.3 KMS + 1.12 运维落地）
"""
