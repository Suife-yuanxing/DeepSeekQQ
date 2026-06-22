# 安卓控制面板 UI 原型

林念念 Bot 安卓控制面板 — Flutter 前端 UI 原型集。

## 文件清单

| 文件 | 内容 | 状态 |
|------|------|:--:|
| [index.html](index.html) | 原型导航索引 | ✅ |
| [启动页.html](启动页.html) | Splash 启动页（猫娘 Logo + 版本号） | 🆕 |
| [登录页.html](登录页.html) | 手机号 + 验证码登录 | 🆕 |
| [注册页.html](注册页.html) | 注册表单（含隐私协议勾选） | 🆕 |
| [首页仪表盘.html](首页仪表盘.html) | 首页 + 快捷操作 + 背景自定义 | ✅ |
| [聊天页.html](聊天页.html) | 消息气泡 + 流式回复 + 壁纸切换 | ✅ |
| [我的Bot.html](我的Bot.html) | 多 Bot 列表管理（标题：Bot管理） | 🆕 |
| [Bot创建向导.html](Bot创建向导.html) | 3 步创建向导（自定义性格 + 模板） | ✅ |
| [Bot设置.html](Bot设置.html) | 风格滑块 + 回复偏好 + 危险操作 | 🆕 |
| [API Key管理.html](API%20Key管理.html) | Key 管理 + 用量统计 | ✅ |
| [数据面板.html](数据面板.html) | 心情日历 + 话题排行 + 成就墙 | ✅ |
| [我的.html](我的.html) | 个人中心：账户卡片 + 快捷入口 + 全部设置（替代设置.html）| 🆕 |
| [修改密码.html](修改密码.html) | 密码修改 + 强度检测 + 验证原密码 | 🆕 |
| [数据权限.html](数据权限.html) | AI 训练/个性化/数据共享开关 | 🆕 |
| [黑名单.html](黑名单.html) | 拉黑用户列表 + 解除拉黑 + 滑出动画 | 🆕 |
| [编辑个人资料.html](编辑个人资料.html) | 头像更换 + 昵称签名 + 性别生日 | 🆕 |
| [通知.html](通知.html) | 系统通知 + 消息提醒 + 已读/未读 + 分组展示 | 🆕 |
| [品牌色预览.html](品牌色预览.html) | 品牌色系统 + 亮/暗双模式对比 | ✅ |
| [用户协议.html](用户协议.html) | 服务条款 · 行为规范 · 知识产权 | 🆕 |
| [隐私政策.html](隐私政策.html) | 信息收集/使用/安全 · 用户权利 | 🆕 |
| [开源许可.html](开源许可.html) | 12 个开源组件 · MIT/Apache/BSD 许可 | 🆕 |

> 🆕 = 2026-06-20 审计后新增 / 2026-06-21 新增编辑资料+通知+主题同步 / 2026-06-21b 新增协议+许可+字号铃声 / 2026-06-21d 导航重构+个人中心(我的.html)

## 设计系统

| 要素 | 值 |
|------|-----|
| 品牌主色 | `#F472B6` 马卡龙软粉（v9 第四版） |
| 辅色 | `#ADD8E6` 婴儿蓝 / `#C4B5FD` 薰衣草（点缀） |
| 语义色 | `#98FF98` 薄荷绿（成功）/ `#FF6B7A` 错误 / `#FBBF24` 警告 |
| 背景 | `#F5F5F5` 浅灰 / `#F0F0F0` body · `#1A1A2E` 暗色 |
| 玻璃卡片 | `rgba(255,255,255,0.75)` + `backdrop-filter: blur(10px)` |
| 手机框 | `360×800`（安卓 20:9 比例） |
| 字体 | `-apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif` |
| 猫娘头像 | Agnes AI gen_04 图片（.img-cat），6 人格 CSS 滤镜变体；CSS v4 手绘作为备用 |
| 底部导航 | 4 Tab（首页 / 聊天 / Bot管理 / 我的），Apple 风格 + 弹簧动画 |
| 涟漪反馈 | 马卡龙粉色径向渐变水波纹（0.7s cubic-bezier） |
| 暗色模式 | 全 22 页 100% 覆盖，tokens.css Dark Mode Token Overrides 驱动 |

## Flutter 翻译指引

原型中的 CSS 特性在 Flutter 中的对应实现：

| CSS 特性 | Flutter 等价 | 复杂度 |
|----------|-------------|:---:|
| `backdrop-filter: blur()` | `BackdropFilter` widget | ⚠️ 有性能开销 |
| `@keyframes` 动画 | `AnimationController` + `Tween` | 中等 |
| CSS border-trick 猫娘 | `CustomPainter` 或 Widget 组合 | ⚠️ 6种×2尺寸≈1000行 |
| `linear-gradient()` | `BoxDecoration(gradient: LinearGradient(...))` | 低 |
| 玻璃卡片 | `ClipRRect` + `BackdropFilter` | 中等 |
| 弹簧导航动画 | `AnimationController` + `SpringSimulation` | 中等 |
| 消息气泡 | `flutter_gen_ai_chat_ui` 包内置 | **直接用包** |

## 路由覆盖

计划路由中，原型已覆盖 **19/19** ✅（全部路由覆盖完成）：

| 路由 | 原型 | 状态 |
|------|------|:--:|
| `/splash` | 启动页.html | ✅ |
| `/login` | 登录页.html | ✅ |
| `/register` | 注册页.html | ✅ |
| `/onboarding` | Bot创建向导.html | ✅ |
| `/home` | 首页仪表盘.html | ✅ |
| `/chat/:botId` | 聊天页.html | ✅ |
| `/bot/:botId/settings` | Bot设置.html | ✅ |
| `/bots` | 我的Bot.html | ✅ |
| `/abilities/:botId` | API Key管理.html | ✅ |
| `/notifications` | 通知.html | 🆕 |
| `/settings` | 我的.html | ✅ |
| `/settings/profile` | 编辑个人资料.html | 🆕 |
| `/settings/change-password` | 修改密码.html | 🆕 |
| `/settings/data-permissions` | 数据权限.html | 🆕 |
| `/settings/blacklist` | 黑名单.html | 🆕 |
| `/settings/terms` | 用户协议.html | 🆕 |
| `/settings/privacy` | 隐私政策.html | 🆕 |
| `/settings/oss` | 开源许可.html | 🆕 |
| `/admin` | [管理员面板.html](管理员面板.html) | ✅ |

## 待补充

- [ ] 空态 / 加载态 / 网络错误态的全局组件设计（计划 Phase 5.3）
- [ ] 图片消息 / 语音消息的 Flutter Widget 规格
- [x] 核心 4 页 API 接通（注册/登录/Bot创建/聊天）— 2026-06-22 ✅
- [x] WebView 套壳 APK（Capacitor）— 内测期方案，计划中
- [ ] 其余 23 页逐个接通真 API

## 后端对接状态（2026-06-22）

Phase 1 后端 **17/17 Task 全部完成**，8766 端口在服务器 `lhins-n2eeuw4m` 运行中。

| 维度 | 值 |
|------|-----|
| 后端 | FastAPI 8766，systemd `deepseek-api.service` active |
| 端点 | ~85 个（含 JWT 认证 + 6 API Key 端点 + WS 流式聊天） |
| 测试 | 1318/1318 全绿 |
| 前端对接 | **核心 4 页已接通**（注册/登录/Bot创建/聊天），`shared/api.js` 提供统一 API 客户端 |

### 新增共享资源

| 文件 | 行 | 内容 |
|------|:---:|------|
| `config.js` | 29 | 服务器地址配置（localStorage/Capacitor/同源 三级优先级） |
| `api.js` | 267 | API 客户端：fetch 封装 + JWT 自动刷新（F6）+ WebSocket + client_id 幂等 |

### 已接通真 API 的页面

| 页面 | 对接端点 | 说明 |
|------|------|------|
| 注册页.html | `/auth/sms` + `/auth/register` | 真验证码 + 真注册，注册成功跳向导 |
| 登录页.html | `/auth/sms` + `/auth/login` | 真验证码 + 真登录，已登录自动跳首页 |
| Bot创建向导.html | `POST /bots` | 3 步完成 → 调 createBot → 跳聊天页 `?bot_id=` |
| 聊天页.html | `/chat/ws` + `/messages` | 真 WS 流式逐字 + 历史拉取 + 断线重连 |

## 更新日志

- **2026-06-22**：APP 落地核心 4 页接通 8766 后端 — 新建 `shared/api.js`（fetch + JWT 自动刷新 + WebSocket）+ `shared/config.js`（服务器地址）；注册页/登录页 接真 SMS + register/login API；Bot创建向导 接 createBot API 成功后跳聊天页 `?bot_id=`；聊天页 接真 WS（子协议 `bearer.<jwt>`）+ 流式逐字追加 + 历史消息拉取 + client_id 幂等去重 + 断线 3s 重连；`server.py` 加 StaticFiles 托管原型目录到 8766 根路径（浏览器调试用）；Capacitor WebView 套壳 APK 计划中导航胶囊从独立双定时器改为回调链式同步（380ms slide + 420ms compress → onComplete 回调跳转，消除 ~120ms 偏差）；胶囊形状 22px→14px 圆角长方形；方向性拉伸偏差（左滑偏左/右滑偏右）；聊天页附件按钮接入完整上传功能（相册/拍照/文件 → 图片压缩 400px JPEG + 文件卡片智能图标 + 灯箱预览 ESC 关闭）；聊天输入框默认 scale(0.96) → focus-within 弹簧弹至 scale(1)；我的页隐藏滚动条、聊天页滚动条缩至 3px + 不透明度 0.10；导航延迟 920→800ms 与胶囊同步；暗色模式全面覆盖新增组件
126	
127	- **2026-06-21f**：聊天输入 ChatGPT 风格 + 背景同步全局化 + 细节打磨 — 聊天输入栏改为统一白色胶囊（26px 圆角/无边框 textarea/暗色圆形发送上箭头/双层阴影）；背景色同步从 4 页补全至 shared/app.js 实现全站 25 页覆盖 + storage 跨标签实时同步；通知页拼接列表项→独立圆角卡片（16px）；编辑资料性别"保密"→"自定义"+ 模板标签（沃尔玛购物袋/武装直升机）；API Key 管理表单透明度 0.97+blur 消穿模、权限复选框垂直对齐+中文解释（聊天对话/图像生成/语音合成/记忆存储）、新增 API Key 输入框

- **2026-06-21e**：v9 马卡龙色系统一落地 — 全部 26 文件色值对齐 `#F472B6`；tokens.css 新增 Dark Mode Token Overrides（12 个变量）；components.css/effects.css/base.css 硬编码色值 → CSS 变量；dark-mode.css 全面迁移 v9 色系；品牌色预览.html 升级 v3；index.html 修复重复 CSS+版本号；README.md 路由 19/19+色值修正；设置.html 301 重定向至 我的.html

- **2026-06-21d**：导航重构 + 个人中心上线 — 底部导航从"首页/聊天/我的Bot/数据"改为 **首页/聊天/Bot管理/我的**；新增 [我的.html](我的.html) 合并原设置页全部内容+账户卡片+4快捷入口（数据看板/API Key/通知/Bot管理）；首页仪表盘移除设置齿轮按钮；"我的Bot"全站更名为"Bot管理"；"我的"导航图标重绘为 ID 卡片风格（圆角矩形内含人物剪影）；快捷入口去除彩色圆形图标只保留文字；7 个主导航页面移除左上角返回箭头（底部导航替代返回）；6 个子页面（修改密码/数据权限/黑名单/用户协议/隐私政策/开源许可）+ 编辑个人资料 返回链接从设置.html→我的.html
- **2026-06-21**：编辑资料 + 通知 + 主题同步 + 设计统一 — 新增编辑个人资料页（头像/昵称/性别/生日）、新增通知页（系统/消息/Bot/更新 4 类分组+已读未读）、主题模式去 emoji + localStorage 跨页面同步暗色模式、背景色预览合入聊天背景内、"完整色板"链接替代独立行、全部设置子页面圆角统一 16px、通知铃铛直链通知页
- **2026-06-20b**：设置页上线 + 子页面 + 全局过渡动画 — 首页背景切换迁移至设置→外观、"品牌色预览"改名"背景色预览"、登录页 QQ/微信改用官方 SVG 图标、新增修改密码/数据权限/黑名单 3 个子页面、全部 16 个页面添加 Apple Push 风格进入动画（0.35s cubic-bezier）
- **2026-06-20**：全面审计修复 — 品牌色统一 #E85D75、手机框改为 360×800、导航标签统一"我的Bot"、聊天页补底部导航+键盘模拟+离线横幅JS、数据面板 emoji 替换、新增 5 个原型页面（启动/登录/注册/Bot设置/我的Bot）
- **2026-06-21c**：猫娘 CSS v4 全量重构 — 8 页 anime-cat CSS 从 v3 升级到 v4：Claymorphism × Macaron Pastel 设计系统；新增光环 (::before)、眼睛 20%→22% + 双层高光（main ::after + secondary background radial-gradient）、脸部 #F0A0B5→#FFFAFB 瓷白渐变、耳朵软粉+薰衣草内耳、腮红扩散马卡龙粉、嘴线 #C86878→#D4A0AA 柔和玫瑰；6 种人格变体色值同步；修复启动页 `h1{h1{` CSS 语法错误；零 HTML 变更（纯 CSS 实现双层高光） | 🐱
- **2026-06-19**：初始 v2 原型（7 个页面），引入品牌色修正和 CSS 猫娘表情系统
