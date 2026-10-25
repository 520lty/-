# -*- coding: utf-8 -*-
"""「小蓝」主动私聊陪伴插件（通用版）

每天在一个可配置时段内随机几次，用当前启用的 LLM 以小蓝的口吻，
主动私聊去关心一个你指定的人。所有参数都可在 Dashboard 插件设置里填。

- 主动频率、时段、间隔、目标会话、人格名字均由配置决定。
- 每次发送前，把最近的聊天记录交给 LLM，并注入当前真实时间（24 小时制 + 时段），
  让它写出符合时间氛围、贴心的主动问候。
- LLM 失败时回落到内置的几句备选，不会断。
"""
import asyncio
import datetime as dt
import json
import random

from astrbot.core.star.base import Star
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain


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
TIME_STEP_MINUTES = 5


class ProactiveCompanionPlugin(Star):
    def __init__(self, context, config=None):
        super().__init__(context)
        cfg = config or {}
        self.target_session = cfg.get("target_session", "").strip()
        self.day_start_hour = int(cfg.get("day_start_hour", 7))
        self.day_end_hour = int(cfg.get("day_end_hour", 24))
        self.send_per_day_min = int(cfg.get("send_per_day_min", 2))
        self.send_per_day_max = int(cfg.get("send_per_day_max", 4))
        self.min_gap_minutes = int(cfg.get("min_gap_minutes", 180))
        self.bot_name = cfg.get("bot_name", "小蓝").strip() or "小蓝"
        self._task = None

    async def initialize(self):
        if not self.target_session:
            self.logger.warning(
                "未配置 target_session，无法主动私聊。请在插件设置里填写"
                "「平台ID:消息类型:会话ID」，例如 mybot:FriendMessage:123456。"
            )
            return
        self._task = asyncio.create_task(self._random_loop())
        self.logger.info("「%s」主动陪伴循环已启动。", self.bot_name)

    async def terminate(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _random_loop(self):
        waits = 0
        while True:
            delay = (
                self._first_delay_seconds()
                if waits == 0
                else self._next_delay_seconds()
            )
            self.logger.info("「%s」下一次主动问候约在 %.1f 小时后。", self.bot_name, delay / 3600)
            await asyncio.sleep(delay)
            text = await self._compose_message()
            if text:
                await self._send_one(text)
            waits += 1

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

    # ---------- 随机调度 ----------
    def _first_delay_seconds(self):
        now = dt.datetime.now()
        _, day_end = self._today_window(now)
        if now >= day_end:
            return self._seconds_until_next_window_start()
        remaining = int((day_end - now).total_seconds())
        step = int(TIME_STEP_MINUTES * 60)
        if remaining <= step:
            return self._seconds_until_next_window_start()
        return random.randint(step, remaining)

    def _next_delay_seconds(self):
        now = dt.datetime.now()
        day_start, day_end = self._today_window(now)
        earliest = now + dt.timedelta(minutes=self.min_gap_minutes)
        if earliest >= day_end:
            return self._seconds_until_next_window_start()
        if earliest < day_start:
            earliest = day_start
        span = int((day_end - earliest).total_seconds())
        # 最终 sleep 时长 = 到达 earliest 的固定间隔 + 在 [earliest, day_end] 内随机延后
        return int(self.min_gap_minutes * 60) + random.randint(0, span)

    def _seconds_until_next_window_start(self):
        now = dt.datetime.now()
        today_start = now.replace(hour=self.day_start_hour, minute=0, second=0, microsecond=0)
        if now < today_start:
            return int((today_start - now).total_seconds())
        return int((today_start + dt.timedelta(days=1) - now).total_seconds())

    def _today_window(self, now):
        start = now.replace(hour=self.day_start_hour, minute=0, second=0, microsecond=0)
        end_hour = 23 if self.day_end_hour == 24 else self.day_end_hour
        end = now.replace(hour=end_hour, minute=59, second=59, microsecond=0)
        return start, end