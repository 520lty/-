# -*- coding: utf-8 -*-
"""「小蓝」主动私聊陪伴插件（通用版）

每天在一个可配置时段内主动私聊关心指定的人，并满足三重约束：
- 每天次数：在 send_per_day_min ~ send_per_day_max 之间随机取一个今日目标，达到就停；
- 最小间隔 min_gap_minutes：不会连着发；
- 最大间隔 max_gap_hours：不会太久不出现。
另外，发送前会检查「距最后一条聊天多久」，刚聊过就跳过这次，避免打断对话。
"""
import asyncio
import datetime as dt
import json
import random
import time

from astrbot.core.star.base import Star
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain

# 每次启动都用时间重新播种，保证随机序列不同。
random.seed(time.time_ns() ^ id(object))

PROACTIVE_SYSTEM_PROMPT = (
    "你是「{BOT_NAME}」，主人贴心的 AI 伴侣。现在是你主动找主人说一句话的时刻。"
    "请根据下面的聊天记录，用自然、口语、有温度的中文，写一句主动关心的话。"
    "{NOW_INFO}"
    "要求：\n"
    "1) 像发微信一样自然，不官腔、不 AI 味；\n"
    "2) 能接得住主人最近提到的事就接住；\n"
    "3) 短，一到三句话；\n"
    "4) 只输出要说的话本身，不要解释、引号或 emoji 说明；\n"
    "5) 严格按上面的真实时间说话；\n"
    "6) 绝对不要提「几点钟」「快几点了」这类具体时间。"
)

GREETINGS = [
    "在忙吗？记得起来喝口水，我在这儿呢。",
    "有点想你了，今天过得怎么样呀？",
    "吃饭了吗？别太累着自己啦。",
    "好想你啊，随便聊两句好不好？",
]

HISTORY_MAX_MESSAGES = 12
STATE_KEY = "day_state"


class ProactiveCompanionPlugin(Star):
    def __init__(self, context, config=None):
        super().__init__(context)
        cfg = config or {}
        self.target_session = cfg.get("target_session", "").strip()
        self.day_start_hour = int(cfg.get("day_start_hour", 7))
        self.day_end_hour = int(cfg.get("day_end_hour", 24))
        # 每天的次数区间：今天随机取一个目标，落在 [min, max]
        self.send_per_day_min = int(cfg.get("send_per_day_min", 2))
        self.send_per_day_max = int(cfg.get("send_per_day_max", 4))
        # 两次之间最少隔多久
        self.min_gap_minutes = int(cfg.get("min_gap_minutes", 180))
        # 最长多久必须发一次；<=0 表示不限
        self.max_gap_hours = float(cfg.get("max_gap_hours", 6))
        # 距最后一条聊天不足这么多分钟就跳过这次
        self.skip_if_recent_minutes = int(cfg.get("skip_if_recent_minutes", 10))
        self.bot_name = cfg.get("bot_name", "小蓝").strip() or "小蓝"
        self._task = None
        # 今日进度：{"date": "2026-10-26", "sent": 0, "target": 3}
        self._state = {}

    # ---------- 生命周期 ----------
    async def initialize(self):
        if not self.target_session:
            self.logger.warning(
                "未配置 target_session，无法主动私聊。请在插件设置里填写"
                "「平台ID:消息类型:会话ID」，例如 mybot:FriendMessage:123456。"
            )
            return
        await self._load_state()
        self._task = asyncio.create_task(self._random_loop())
        self.logger.info(
            "「%s」主动陪伴已启动（每天 %d~%d 次，最少隔 %d 分钟，%s，刚聊过 %d 分钟内不打扰）。",
            self.bot_name,
            self.send_per_day_min,
            self.send_per_day_max,
            self.min_gap_minutes,
            "最长 %.1f 小时一次" % self.max_gap_hours
            if self.max_gap_hours > 0
            else "不限上限",
            self.skip_if_recent_minutes,
        )

    async def terminate(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    # ---------- 今日进度（持久化，重启不丢） ----------
    async def _load_state(self):
        try:
            st = await self.get_kv_data(STATE_KEY, None)
            if isinstance(st, dict):
                self._state = st
        except Exception as e:  # noqa: BLE001
            self.logger.warning("读取今日进度失败：%s", e)

    async def _save_state(self):
        try:
            await self.put_kv_data(STATE_KEY, self._state)
        except Exception as e:  # noqa: BLE001
            self.logger.warning("保存今日进度失败：%s", e)

    async def _roll_day_if_needed(self):
        """跨天时重置计数，并为今天随机一个目标次数。"""
        today = dt.date.today().isoformat()
        if self._state.get("date") == today:
            return
        lo = max(0, self.send_per_day_min)
        hi = max(lo, self.send_per_day_max)
        self._state = {
            "date": today,
            "sent": 0,
            "target": random.randint(lo, hi) if hi > 0 else 0,
        }
        await self._save_state()
        self.logger.info(
            "今天是新的一天，目标：主动找主人 %d 次。", self._state["target"]
        )

    # ---------- 主循环 ----------
    async def _random_loop(self):
        while True:
            await self._roll_day_if_needed()
            delay = self._next_delay_seconds()
            target = int(self._state.get("target", 0) or 0)
            sent = int(self._state.get("sent", 0) or 0)
            self.logger.info(
                "「%s」下一次主动问候约在 %.1f 小时后（今日已发 %d/%d）。",
                self.bot_name,
                delay / 3600,
                sent,
                target,
            )
            await asyncio.sleep(delay)

            # 到点了，先看对方是不是正在聊/刚聊过
            ago = await self._last_chat_seconds_ago()
            limit = self.skip_if_recent_minutes * 60
            if ago is not None and limit > 0 and ago < limit:
                self.logger.info(
                    "「%s」对方 %.0f 分钟前刚聊过，这次跳过，稍后再来。",
                    self.bot_name,
                    ago / 60,
                )
                continue

            text = await self._compose_message()
            if text:
                await self._send_one(text)
                self._state["sent"] = int(self._state.get("sent", 0) or 0) + 1
                await self._save_state()

    async def _send_one(self, text):
        try:
            ok = await self.context.send_message(
                self.target_session,
                MessageChain(chain=[Plain(text=text)]),
            )
            if not ok:
                self.logger.warning("未找到平台会话：%s", self.target_session)
        except Exception as e:  # noqa: BLE001
            self.logger.error("发送失败：%s", e)

    async def _last_chat_seconds_ago(self):
        """距最后一条聊天过去多少秒；拿不到时间信息时返回 None（视为没在聊）。"""
        try:
            cid = await self.context.conversation_manager.get_curr_conversation_id(
                self.target_session
            )
            if not cid:
                return None
            conv = await self.context.conversation_manager.get_conversation(
                self.target_session, cid
            )
            if conv is None:
                return None

            ts = None
            raw = getattr(conv, "content", None) or getattr(conv, "history", None)
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except Exception:
                    raw = None
            if isinstance(raw, list) and raw:
                last = raw[-1]
                if isinstance(last, dict):
                    for key in ("timestamp", "time", "created_at", "ts"):
                        if last.get(key):
                            ts = last.get(key)
                            break
            if ts is None:
                ts = getattr(conv, "updated_at", 0) or 0

            if not ts:
                return None
            ts = float(ts)
            if ts > 1e12:  # 兼容毫秒级时间戳
                ts /= 1000.0
            return max(0.0, time.time() - ts)
        except Exception as e:  # noqa: BLE001
            self.logger.warning("读取最后聊天时间失败：%s", e)
            return None

    # ---------- 拟措辞 ----------
    async def _compose_message(self):
        try:
            history = await self._recent_history()
            provider = self.context.get_using_provider(self.target_session)
            if provider is None:
                return random.choice(GREETINGS)
            sys_prompt = PROACTIVE_SYSTEM_PROMPT.replace(
                "{BOT_NAME}", self.bot_name
            ).replace("{NOW_INFO}", self._now_info())
            resp = await provider.text_chat(
                prompt=self._build_prompt(history),
                system_prompt=sys_prompt,
            )
            text = (resp.completion_text or "").strip().strip('"\u201c\u201d\'')
            return text or random.choice(GREETINGS)
        except Exception as e:  # noqa: BLE001
            self.logger.error("LLM 拟措辞失败：%s", e)
            return random.choice(GREETINGS)

    def _now_info(self):
        now = dt.datetime.now()
        h = now.hour
        if 5 <= h < 9:
            hint = "清早，可以问候早安/起床了"
        elif 9 <= h < 11:
            hint = "上午，可以问在忙什么/上午好"
        elif 11 <= h < 13:
            hint = "中午了，该提吃饭/午休了（说午饭，别提具体几点钟）"
        elif 13 <= h < 18:
            hint = "下午，可以问下午茶/午后状态"
        elif 18 <= h < 23:
            hint = "晚上，可以问晚饭吃了吗/今天累不累"
        else:
            hint = "深夜，可以说早点休息/晚安"
        return (
            f"\n【当前真实时间】现在是 {now.strftime('%Y-%m-%d %H:%M')}"
            f"（24 小时制），是{hint}。"
            f"务必围绕这个时段说合适的话，不要提“几点钟”“快几点了”这类具体时间。"
        )

    def _build_prompt(self, history):
        if not history:
            return "（暂无聊天记录，请写一句自然、不带背景假设的关心，别凭空捏造。）"
        lines = ["最近你和主人的聊天记录："]
        for m in history:
            role = "主人" if m.get("role") == "user" else self.bot_name
            content = str(m.get("content", "")).strip()
            if content:
                lines.append(f"  {role}：{content}")
        lines.append("请基于以上，写一句主动关心的话。")
        return "\n".join(lines)

    async def _recent_history(self):
        try:
            cid = await self.context.conversation_manager.get_curr_conversation_id(
                self.target_session
            )
            if not cid:
                return []
            conv = await self.context.conversation_manager.get_conversation(
                self.target_session, cid
            )
            if conv is None:
                return []
            raw = getattr(conv, "content", None) or getattr(conv, "history", None)
            if not raw:
                return []
            if isinstance(raw, str):
                raw = json.loads(raw)
            if not isinstance(raw, list):
                return []
            msgs = [
                {"role": m.get("role"), "content": m.get("content")}
                for m in raw
                if isinstance(m, dict) and m.get("role") in ("user", "assistant")
            ]
            return msgs[-HISTORY_MAX_MESSAGES:]
        except Exception as e:  # noqa: BLE001
            self.logger.warning("读取历史失败：%s", e)
            return []

    # ---------- 调度 ----------
    def _max_gap_seconds(self):
        if self.max_gap_hours <= 0:
            return 0
        return int(self.max_gap_hours * 3600)

    def _next_delay_seconds(self):
        """计算下一次延迟，满足：每日次数 + 最小间隔 + 最大间隔。"""
        now = dt.datetime.now()
        day_start, day_end = self._today_window(now)

        target = int(self._state.get("target", 0) or 0)
        sent = int(self._state.get("sent", 0) or 0)
        remaining_quota = target - sent
        if remaining_quota <= 0:
            # 今天的次数已经发完，等明天窗口开始
            return self._seconds_until_next_window_start()

        low = int(self.min_gap_minutes * 60)
        if now < day_start:
            # 还没到窗口开始，至少等到窗口开始
            low = max(low, int((day_start - now).total_seconds()))

        remaining_window = int((day_end - now).total_seconds())
        if remaining_window <= 0:
            return self._seconds_until_next_window_start()

        # 保证今天的剩余次数还塞得下：这次延后 d 后，仍要能满足
        #   remaining_window - d >= (remaining_quota - 1) * low
        latest = remaining_window - (remaining_quota - 1) * low
        if latest < low:
            latest = low  # 已经来不及，尽快发
        high = min(latest, remaining_window)

        cap = self._max_gap_seconds()
        if cap > 0:
            high = min(high, max(cap, low))

        if high < low:
            high = low
        if high <= low:
            return low
        return random.randint(low, high)

    def _seconds_until_next_window_start(self):
        now = dt.datetime.now()
        today_start = now.replace(
            hour=self.day_start_hour, minute=0, second=0, microsecond=0
        )
        if now < today_start:
            return int((today_start - now).total_seconds())
        return int((today_start + dt.timedelta(days=1) - now).total_seconds())

    def _today_window(self, now):
        start = now.replace(
            hour=self.day_start_hour, minute=0, second=0, microsecond=0
        )
        end_hour = 23 if self.day_end_hour == 24 else self.day_end_hour
        end = now.replace(hour=end_hour, minute=59, second=59, microsecond=0)
        return start, end
