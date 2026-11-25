# Hermes 绑定飞书（Feishu）操作指南

适用：刚装好 Hermes，第一次把飞书机器人接上。全程约 5 分钟。
前提：`hermes` 命令能跑、模型已配好（`hermes doctor` 无红字）。

---

## 一、推荐路径：扫码自动建机器人（1 条命令）

```bash
hermes gateway setup
```

1. 平台列表里选 **Feishu / Lark**
2. 选 **Scan QR code to create a new bot automatically (recommended)**
3. 终端会显示二维码；同时打印一条链接，形如：
   `https://open.feishu.cn/page/launcher?user_code=XXXX-XXXX&from=hermes&tp=hermes`
   - 手机飞书扫二维码，或直接在手机飞书里打开这条链接
   - **10 分钟内扫完**，过期就重跑命令
4. 手机端确认后，Hermes 自动创建应用、配好权限和事件订阅，并把
   App ID / Secret 写进 `~/.hermes/.env`（不需要你手抄密钥）
5. 接着终端会问 3 个问题，照下面选：

| 提问 | 建议选择 |
|---|---|
| How should direct messages be authorized? | **Use DM pairing approval (recommended)** |
| How should group chats be handled? | **Respond only when @mentioned in groups (recommended)** |
| Home chat ID (optional) | 直接回车留空，之后在飞书里发 `/set-home` 设置 |

> 扫码账号需要有开发者/管理员权限；没有权限就用下面的手动路径。

## 二、备选路径：手动填 App ID / Secret

1. 打开 <https://open.feishu.cn/>（国际版 Lark 用 <https://open.larksuite.com/>）→ 创建企业自建应用
2. **凭证与基础信息** → 复制 **App ID**、**App Secret**
3. **添加应用能力** → 启用 **机器人**
4. **权限管理** → 批量导入以下权限：
   ```
   im:message
   im:message:send_as_bot
   im:resource
   im:chat
   im:chat:readonly
   ```
   建议再加：`im:message.reactions:readonly`、`admin:app.info:readonly`、`contact:user.id:readonly`
5. **事件与回调** → 订阅方式选 **长连接（WebSocket）** → 添加事件 `im.message.receive_v1`
6. **版本管理与发布** → 创建版本并发布（企业应用可能要管理员审核）
   —— **这一步不做，前面全白配**，消息不会进来
7. 回到终端 `hermes gateway setup` → 选 Feishu / Lark → 选 **Enter existing App ID and App Secret manually**，按提示粘贴，连接模式选 **WebSocket**

## 三、让 gateway 跑起来

```bash
hermes gateway install     # 首次：装成后台服务（systemd --user）
hermes gateway start
hermes gateway status      # 应显示 running / connected
```

只想先临时试：`hermes gateway run`（前台运行，Ctrl+C 停）。

## 四、验证（4 步，别跳）

```bash
# 1) 服务在跑
hermes gateway status

# 2) 飞书通道连上了
grep -i feishu ~/.hermes/logs/gateway.log | tail -20
#    应能看到 connected / websocket 相关行，无 Session expired / 401

# 3) 有到飞书的 443 长连接
ss -tnp | grep -E 'open\.feishu\.cn|accounts\.feishu\.cn'

# 4) 真发一条消息
```

在飞书里搜索机器人名字（扫码成功时终端会打印 `Bot created: xxx`）→ 单聊发「你好」。

- 若开了 DM pairing：第一次会回一个配对码，本机执行
  ```bash
  hermes pairing list
  hermes pairing approve <配对码>
  ```
  然后再发一条，机器人就正常回答了。
- 群聊里必须 **@机器人** 才会响应（默认策略）。

## 五、常见卡点

| 现象 | 原因 / 处理 |
|---|---|
| 终端只打印链接没有二维码 | 正常，手机飞书打开链接即可；想显示二维码：`pip install qrcode` |
| 扫码后一直转圈 / 建应用失败 | 账号无开发者权限，或企业策略限制 → 走手动路径 |
| 扫码/配置都成功，发消息没反应 | ①应用没「发布版本」②事件没订阅 `im.message.receive_v1` ③不是长连接模式 ④改完 .env 没重启 gateway |
| 群里 @ 了也不回 | 检查 `FEISHU_GROUP_POLICY`（默认 allowlist 需在 `FEISHU_ALLOWED_USERS` 里）；确认 @ 的是机器人 |
| 首次装依赖很慢 | Feishu 依赖 `lark-oapi` 是首次使用时自动装的，国内换源：`pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple` |
| 定时任务结果想发到飞书 | 在目标会话发 `/set-home`，或配 `FEISHU_HOME_CHANNEL=oc_xxx` |

## 六、红线

- App Secret 只放在 `~/.hermes/.env`（权限 600），不要贴群里、不要写进代码仓库
- 改完 `.env` 必须重启 gateway 才生效
- 一台机器多个 profile 时，确认操作的 `$HERMES_HOME` 是你要绑的那个
