# 小蓝·主动陪伴（proactive-companion）

一个 AstrBot 插件，让你的 QQ 机器人在每天一个可配置的时段里**随机几次**，主动私聊来关心你、陪你聊天——像真人一样，而不是你发它才回。

## 它做什么

- 每天 `day_start_hour` 到 `day_end_hour` 之间，随机挑 `send_per_day_min`～`send_per_day_max` 次，主动发私聊。
- 每次发送前，把最近和对方聊过的记录交给**当前启用的 LLM**，让它以「小蓝」的口吻现想一句贴心话（能接住对方之前说过的事）。
- 说话前会注入**当前真实时间（24 小时制 + 中文时段）**，不会把中午说成晚上、把 11 点说成 23 点。
- LLM 失败了会自动退回几句写好的备选，不会断。

## 安装

1. 把这个插件的整个目录 `proactive_companion` 放进 AstrBot 的插件目录：
   ```
   <AstrBot实例>/core/data/plugins/
   ```
2. 在 AstrBot Dashboard → 「插件」页找到「小蓝·主动陪伴」，点开**设置**。
3. 填写 `target_session`（必填），格式：`平台ID:消息类型:会话ID`，例如：
   ```
   mybot:FriendMessage:123456789
   ```
   - 平台ID：AstrBot 平台设置里那个平台的名字。
   - 消息类型：私聊用 `FriendMessage`。
   - 会话ID：对方的 QQ 号。
4. 保存，重载插件，即可。

## 配置项

| 键 | 含义 | 默认 |
|---|---|---|
| `target_session` | 主动私聊发往的会话（必填） | 空 |
| `bot_name` | 伴侣名字 | 小蓝 |
| `day_start_hour` | 每天最早发言小时 | 7 |
| `day_end_hour` | 每天最晚小时 | 24 |
| `send_per_day_min` / `max` | 每天随机几次的区间 | 2 / 4 |
| `min_gap_minutes` | 两次之间最少间隔 | 180 |

## 前提

- 装好了 AstrBot（≥ 4.x）。
- 已配置好一个可用的聊天 LLM Provider（本插件复用 AstrBot 当前的 Provider，不内置任何 Key）。
- 平台（如 NapCat / OneBot）已连接、能正常发私聊。

## 常见问题

- **一直没消息？** 它是随机节奏，首次可能随到当天很晚，不是坏了。
- **时间说错？** 确认 AstrBot 加载的是最新版，可看插件日志有没有「下一次主动问候在 X 小时后」。
- **收不到？** 检查 `target_session` 里平台ID是否和 Dashboard 平台设置一致。

## 说明

- 本插件只做「主动发私聊关心」，不含账号、密钥等敏感信息。
- 主动频率请适度设置，避免打扰他人。
