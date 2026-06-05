# 📱 Termux 手机控制代理 - 设置指南

## 前提条件

1. **手机**：Android 7.0+，已开启 USB 调试
2. **服务器**：腾讯云服务器已启动 Worker（端口 8765/8766）
3. **网络**：手机能访问服务器公网 IP

---

## 步骤 1：安装 Termux

⚠️ **必须用 F-Droid 版本**，Play Store 版本已停更且有问题

1. 访问 https://f-droid.org/packages/com.termux/
2. 下载并安装 APK
3. 打开 Termux，等待初始化完成

---

## 步骤 2：安装依赖

在 Termux 中逐行执行：

```bash
# 更新包管理器
pkg update -y

# 安装 Python 和 ADB
pkg install -y python adb

# 安装 Python 依赖
pip install websockets pillow

# 验证安装
python --version
adb version
```

---

## 步骤 3：连接手机 ADB（本机调试）

因为 Termux 运行在手机上，需要连接本机 ADB：

```bash
# 方法 1：使用 Termux ADB（推荐）
# 部分手机可以直接用，不需要额外设置
adb devices

# 如果显示 "unauthorized"，需要：
# 1. 手机开启 USB 调试（设置 → 开发者选项）
# 2. 用 USB 线连接电脑，电脑上运行 adb devices
# 3. 手机上点击 "允许 USB 调试"
# 4. 断开 USB，在 Termux 再试 adb devices

# 方法 2：使用无线 ADB（Android 11+）
# 1. 开发者选项 → 无线调试 → 开启
# 2. 记配对码和端口
# 3. Termux 中运行：
adb pair <IP>:<端口> <配对码>
adb connect <IP>:<端口>
```

---

## 步骤 4：下载脚本

### 方法 A：直接下载（推荐）

```bash
# 创建目录
mkdir -p ~/phone-agent
cd ~/phone-agent

# 下载脚本（替换为你的服务器地址）
curl -o phone-agent.py http://你的服务器IP:8082/scripts/termux-phone-agent.py

# 或者用 scp（如果你电脑有脚本）
scp scripts/termux-phone-agent.py 手机IP:~/phone-agent/
```

### 方法 B：手动复制

1. 电脑上打开 `scripts/termux-phone-agent.py`
2. 复制内容
3. Termux 中运行：
   ```bash
   mkdir -p ~/phone-agent
   cd ~/phone-agent
   nano phone-agent.py
   # 粘贴内容，Ctrl+O 保存，Ctrl+X 退出
   ```

---

## 步骤 5：配置脚本

编辑脚本开头的配置：

```bash
cd ~/phone-agent
nano phone-agent.py
```

修改这几行：

```python
SERVER_URL = "wss://你的服务器公网IP:8766"  # WSS 地址
API_KEY = "你的SCREENMCP_API_KEY"  # 从 config.py 获取
DEVICE_ID = "my-phone"  # 设备标识，随便起名
```

---

## 步骤 6：测试运行

```bash
cd ~/phone-agent
python phone-agent.py
```

应该看到：

```
==================================================
📱 Termux 手机控制代理
==================================================
服务器: wss://xxx:8766
设备ID: my-phone

✅ ADB 已安装: Android Debug Bridge version 1.0.41
✅ 设备已连接
🔌 连接服务器: wss://xxx:8766
✅ 已连接到服务器，等待命令...
```

---

## 步骤 7：后台运行（保持后台）

### 方法 1：Termux 后台

```bash
# 按 Home 键退出 Termux（不要输入 exit）
# Termux 会在后台继续运行
```

### 方法 2：使用 tmux（推荐）

```bash
# 安装 tmux
pkg install tmux

# 创建会话
tmux new -s phone-agent

# 运行脚本
cd ~/phone-agent
python phone-agent.py

# 按 Ctrl+B，然后按 D 脱离会话

# 重新连接会话
tmux attach -t phone-agent
```

### 方法 3：使用 nohup

```bash
cd ~/phone-agent
nohup python phone-agent.py > agent.log 2>&1 &

# 查看日志
tail -f agent.log

# 停止进程
pkill -f phone-agent.py
```

---

## 步骤 8：开机自启（可选）

### 使用 Termux:Boot

1. 安装 Termux:Boot（F-Droid）
2. 创建启动脚本：

```bash
mkdir -p ~/.termux/boot
cat > ~/.termux/boot/start-phone-agent.sh << 'EOF'
#!/data/data/com.termux/files/usr/bin/sh
cd ~/phone-agent
python phone-agent.py &
EOF

chmod +x ~/.termux/boot/start-phone-agent.sh
```

3. 打开 Termux:Boot 应用，授予自启动权限
4. 重启手机测试

---

## 测试命令

在 QQ 中发送：

```
# 截图
截个图

# 点击坐标
点击 540, 960

# 滑动
上滑
下滑 3次

# 输入文字
输入 你好世界

# 返回
返回

# 回到桌面
回到桌面

# 打开应用
打开微信
打开抖音
```

---

## 故障排查

### 1. "未找到 adb"

```bash
pkg install adb
```

### 2. "unauthorized" 或 "no devices"

```bash
# 检查 USB 调试是否开启
adb devices

# 如果显示 unauthorized：
# 1. 用 USB 连电脑，电脑运行 adb devices
# 2. 手机点击授权
# 3. 断开 USB，在 Termux 重试
```

### 3. 连接服务器失败

```bash
# 测试网络
ping 你的服务器IP

# 测试端口
curl -k https://你的服务器IP:8766

# 检查防火墙
# 腾讯云控制台 → 防火墙 → 确保 8765/8766 开放
```

### 4. 命令执行超时

- 检查 ADB 连接：`adb devices`
- 手机是否锁屏：解锁屏幕
- 权限问题：部分操作需要 root

### 5. 截图失败

```bash
# 手动测试截图
adb shell screencap -p /sdcard/test.png
adb pull /sdcard/test.png .

# 如果失败，可能需要存储权限
termux-setup-storage
```

---

## 安全提示

1. **API_KEY 保密**：不要泄露给他人
2. **服务器防火墙**：只开放必要端口
3. **手机锁屏**：不用时锁屏，防止误操作
4. **网络环境**：避免在公共 WiFi 使用

---

## 高级配置

### 修改端口

如果不想用 8766，修改服务器 `config.py` 和脚本 `SERVER_URL`。

### 多台手机

每台手机用不同 `DEVICE_ID`，Worker 会记录最后连接的手机。

### 限制命令

在脚本的 `execute_command` 函数中添加白名单/黑名单。

---

## 脚本功能列表

| 命令 | ADB 实现 |
|------|----------|
| click | `adb shell input tap x y` |
| swipe | `adb shell input swipe x1 y1 x2 y2` |
| type | `adb shell input text xxx` |
| back | `adb shell input keyevent 4` |
| home | `adb shell input keyevent 3` |
| recents | `adb shell input keyevent 187` |
| screenshot | `adb shell screencap` + base64 |
| ui_tree | `adb shell uiautomator dump` |
| open_app | `adb shell monkey -p package` |
