# DeepSeek 用户侧 API（8766）部署指南

> Task 1.12 运维落地。FastAPI 进程独立于 NoneBot2 8082 bot，通过 systemd 管理。

## 架构

```
端口 8082: deepseek-bot.service   (NoneBot2 + QQ bot，ADMIN_API_KEY 认证)
端口 8766: deepseek-api.service   (FastAPI 用户侧 API，JWT 认证) ← 本文档
共享:     /home/ubuntu/DeepSeekQQ/DeepSeekQQ/data/chat_memory.db (SQLite WAL)
```

两进程物理隔离，互不依赖，可独立重启。共享同一 SQLite（WAL 模式支持并发读写）。

## 部署步骤

### 1. 生成密钥并创建 .api.env

```bash
cd /home/ubuntu/DeepSeekQQ/DeepSeekQQ
cp deploy/api.env.example .api.env

# 生成三个强随机密钥并填入 .api.env
JWT_SECRET=$(openssl rand -hex 32)
PHONE_KEY=$(openssl rand -hex 32)
APIKEY_KEY=$(openssl rand -hex 32)

sed -i "s|^PLATFORM_JWT_SECRET=.*|PLATFORM_JWT_SECRET=$JWT_SECRET|" .api.env
sed -i "s|^PLATFORM_PHONE_AES_KEY=.*|PLATFORM_PHONE_AES_KEY=$PHONE_KEY|" .api.env
sed -i "s|^PLATFORM_APIKEY_AES_KEY=.*|PLATFORM_APIKEY_AES_KEY=$APIKEY_KEY|" .api.env

chmod 600 .api.env   # 仅 owner 可读（含密钥）
```

> ⚠️ **备份 .api.env！** 三个密钥丢失会导致：JWT 全失效（用户需重新登录）+ 手机号密文不可逆 + API Key 密文不可逆。

### 2. 安装 systemd service

```bash
sudo cp deploy/deepseek-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable deepseek-api
```

### 3. 开放防火墙 8766 端口

腾讯云轻量服务器通过控制台防火墙规则开放（不用 ufw）。用 lighthouse MCP：

```
mcp__lighthouse__create_firewall_rules(
  Region="ap-shanghai",
  InstanceId="lhins-n2eeuw4m",
  FirewallRules=[{Protocol:"TCP", Port:"8766", CidrBlock:"0.0.0.0/0",
                  FirewallRuleDescription:"用户侧 API"}]
)
```

或控制台手动添加：TCP 8766，来源 0.0.0.0/0。

### 4. 启动并验证

```bash
sudo systemctl start deepseek-api
sudo systemctl status deepseek-api          # 应为 active (running)
curl http://127.0.0.1:8766/api/v1/health    # 应返回 {"ok":true,"version":"1.0.0",...}
journalctl -u deepseek-api -n 30 --output=cat   # 看启动日志无报错
```

## 常用运维命令

```bash
sudo systemctl restart deepseek-api         # 重启（改代码后）
sudo systemctl stop deepseek-api            # 停止
journalctl -u deepseek-api -f --output=cat  # 实时日志
curl http://127.0.0.1:8766/api/docs         # Swagger 文档（生产可关）
```

> ❌ **绝对不要** `nohup python -m plugins.deepseek.api_platform.server &` ——会和 systemd 抢 8766 端口导致循环崩溃（同 8082 bot 的教训，见 CLAUDE.md）。

## SSL / HTTPS

当前跑 HTTP，生产对外暴露需 HTTPS。**Phase 5 打包发布前**用 Caddy 反代 + Let's Encrypt 落地：

```bash
# Caddyfile 示例（Phase 5 补）
api.your-domain.com {
    reverse_proxy 127.0.0.1:8766
}
```

本轮不强行上 SSL，避免过度设计。App 直连公网 IP + 8766 仅用于内部测试。

## 回滚

```bash
sudo systemctl stop deepseek-api
sudo rm /etc/systemd/system/deepseek-api.service
sudo systemctl daemon-reload
```

删除 service 不影响 8082 bot。`.api.env` 保留以备重装。

## 验证清单

- [ ] `systemctl status deepseek-api` = active (running)
- [ ] `curl /api/v1/health` 返回 `{"ok":true}`
- [ ] `curl /api/v1/auth/sms` 返回 200（端点可达）
- [ ] `journalctl -u deepseek-api` 无 ImportError / 端口占用报错
- [ ] `systemctl status deepseek-bot` 仍 active（8082 未受影响）
- [ ] 防火墙 8766 已开（腾讯云控制台或 MCP 确认）
