"""AstrBot Star 插件：课表 Lite（BLBot 课表功能的独立实现）。

核心在“导入”：小爱课程表分享链接、WakeUp 分享口令/备份、iCalendar、
拾光课程表 JSON、通用 JSON/CSV 都归一化成同一套数据结构（周次列表 +
星期 + 起止时间），再统一做去重合并、缺时间补全和异常提示。

命令：
  /课表 [明天|周一|9/1] [@某人]     查看课表
  /课表 在上                       群友现在在上什么课
  /课表 排行 [今日|本周|本月|学期] [全部] 课程时长榜
  /课表 导入 <链接/口令/JSON/文件>  导入课表
  /课表 删除                       删除自己的课表
  /课表 提醒 [分钟|关]             上课提醒（私聊）
  /课表 备注 <课程> [备注]          课程备注
  /课表 翘课                       切换今日翘课状态
  /课表 作息 [1 08:00-08:45 ...]   查看/修改个人作息
  /课表 请假 [日期/范围] [原因]     登记请假
  /课表 销假 [日期]                取消请假
  /课表 调课 [课程] A B [互换]      调课；撤销 [日期] / 清空
  /课表 调休 [日期] [周几|清]       查看节假日/设置补班
  /课表 帮助

WebUI：AstrBot 插件页的「课表控制台」可增删课表、刷新节假日和修改设置。
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import socket
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import MessageChain, filter
from astrbot.api.star import Context, Star, StarTools, register

PLUGIN_NAME = "astrbot_plugin_schedule_lite"
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

# 允许以包或单文件方式加载
try:
    from .schedule_lite_core import render as render_mod
    from .schedule_lite_core.holiday import HolidayCalendar, fetch_calendar
    from .schedule_lite_core.importers import detect
    from .schedule_lite_core.importers.base import ImportFailure
    from .schedule_lite_core.importers.generic import dumps_export
    from .schedule_lite_core.model import Course, Timetable
    from .schedule_lite_core.normalize import (
        default_sections,
        fmt_minutes,
        get_tz,
        monday_of,
        parse_date,
        parse_day,
        parse_section_text,
        parse_section_times,
        parse_time,
        parse_weeks,
        minutes_of,
    )
    from .schedule_lite_core.service import (
        STATUS_EMOJI,
        RANK_MODE_LABEL,
        STATUS_LABEL,
        day_courses,
        day_entries,
        everyone_entries,
        everyone_report,
        format_day,
        format_rank,
        parse_query_date,
        status_now,
        timetables_of_group,
        weekday_cn,
    )
    from .schedule_lite_core.storage import TimetableStore
except ImportError:  # pragma: no cover - AstrBot 以脚本方式加载时
    if PLUGIN_DIR not in sys.path:
        sys.path.insert(0, PLUGIN_DIR)
    from schedule_lite_core import render as render_mod
    from schedule_lite_core.holiday import HolidayCalendar, fetch_calendar
    from schedule_lite_core.importers import detect
    from schedule_lite_core.importers.base import ImportFailure
    from schedule_lite_core.importers.generic import dumps_export
    from schedule_lite_core.model import Course, Timetable
    from schedule_lite_core.normalize import (
        default_sections,
        fmt_minutes,
        get_tz,
        monday_of,
        parse_date,
        parse_day,
        parse_section_text,
        parse_section_times,
        parse_time,
        parse_weeks,
        minutes_of,
    )
    from schedule_lite_core.service import (
        STATUS_EMOJI,
        RANK_MODE_LABEL,
        STATUS_LABEL,
        day_courses,
        day_entries,
        everyone_entries,
        everyone_report,
        format_day,
        format_rank,
        parse_query_date,
        status_now,
        timetables_of_group,
        weekday_cn,
    )
    from schedule_lite_core.storage import TimetableStore

def _safe_int(value, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


HELP_TEXT = """📚 课表指令
/课表 [明天|周一|9/1] [@某人]  查看课表
/课表 在上                    群友现在在上什么课
/课表 排行 [今日|本周|本月|学期] [全部]
/课表 导入 <链接/口令/JSON/文件>
/课表 删除
/课表 提醒 [分钟|关]           私聊订阅上课提醒
/课表 备注 <课程> [备注]
/课表 翘课                     切换今日翘课状态
/课表 作息 [1 08:00-08:45 …]   查看/修改个人作息
/课表 学期 2026-09-07           修正第 1 周周一
/课表 请假 [日期/范围] [原因]   登记请假
/课表 销假 [日期]               取消请假
/课表 调课 [课程] A B [互换]    调课；撤销 [日期] / 清空
/课表 调休 [日期] [周几|清]     查看节假日/设置补班
/课表 帮助

群友状态优先显示翘课/请假，无课排最后；人多自动隐藏无课并分页。
控制台可增删课表、刷新节假日、设置满课榜和累计榜。

支持导入：Nexio 分享口令/JSON/ICS、小爱课程表分享链接、WakeUp 口令/备份、
iCalendar(.ics)、拾光课程表 JSON、通用 JSON/CSV。也可以直接发文件。"""

SUBCOMMANDS = {
    "帮助": "help", "help": "help", "用法": "help", "说明": "help",
    "导入": "import", "import": "import", "添加": "import", "设置": "import",
    "删除": "delete", "del": "delete", "解绑": "delete", "清除": "delete",
    "提醒": "notify", "notify": "notify", "订阅": "notify",
    "备注": "note", "note": "note",
    "排行": "rank", "rank": "rank", "榜": "rank",
    "翘课": "abandon", "abandon": "abandon", "逃课": "abandon",
    "作息": "sections", "节次": "sections", "section": "sections",
    "在上": "everyone", "谁": "everyone", "全员": "everyone", "everyone": "everyone",
    "导出": "export", "export": "export",
    "学期": "term", "开学": "term", "term": "term",
}

RANK_MODES = {
    "今日": "today", "今天": "today", "today": "today",
    "本周": "week", "这周": "week", "周": "week", "week": "week",
    "本月": "month", "这月": "month", "这个月": "month", "month": "month",
    "学期": "term", "本学期": "term", "整学期": "term", "term": "term",
}


@register(
    "schedule_lite",
    "suiren0219",
    "课表 Lite：多来源导入、群友状态、请假调课、节假日与 WebUI 控制台",
    "v1.4.1",
)
class ScheduleLitePlugin(Star):
    """课表 Lite：多来源导入课表、群友在上什么课、时长榜、上课提醒。

    发送 /课表 帮助 查看完整指令。
    """

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        self._store: TimetableStore | None = None
        self._lock = asyncio.Lock()
        self._http = None
        self._notify_task: asyncio.Task | None = None
        self._holiday_task: asyncio.Task | None = None
        self._holidays: dict[int, HolidayCalendar] = {}
        self._makeup_text: str | None = None
        self._makeup_values: dict[str, int] = {}
        self._broadcast_state: dict[str, dict[str, str]] = {}
        # 按用户缓存当天的课程快照和分钟级状态，多个群查询可直接复用
        self._day_cache: dict[tuple[int, str, int], list] = {}
        self._status_cache: dict[tuple[int, str, int, bool], dict] = {}
        self._entries_cache: dict[tuple, list[dict]] = {}

    # ---------------------------------------------------------- 生命周期

    async def initialize(self):
        try:
            import aiohttp

            self._http = aiohttp.ClientSession()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{PLUGIN_NAME}] aiohttp 不可用，联网导入会被禁用: {e}")
        if self._scrub_privacy():
            await self._save()
            logger.info(f"[{PLUGIN_NAME}] 已按隐私配置清除历史课表中的教师/地点")
        self._load_broadcast_state()
        self._load_holiday_cache(self._now().year)
        if self._cfg("holiday_enabled", True):
            self._holiday_task = asyncio.create_task(self._holiday_loop())
        self._register_web_apis()
        if self._cfg("notify_enabled", True):
            self._notify_task = asyncio.create_task(self._notify_loop())
        logger.info(f"[{PLUGIN_NAME}] 已加载，共 {len(self.store.all())} 份课表")

    async def terminate(self):
        for task_name in ("_notify_task", "_holiday_task"):
            task = getattr(self, task_name)
            if task:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
                setattr(self, task_name, None)
        if self._http is not None:
            try:
                await self._http.close()
            except Exception:  # noqa: BLE001
                pass
            self._http = None

    # ---------------------------------------------------------- 配置 / 路径

    @property
    def store(self) -> TimetableStore:
        """延迟到初始化后再取数据目录，避免插件注册前 StarTools 还拿不到插件名。"""
        if self._store is None:
            self._store = TimetableStore(self._data_path())
        return self._store

    @staticmethod
    def _data_path() -> str:
        try:
            return str(StarTools.get_data_dir() / "timetables.json")
        except Exception:  # noqa: BLE001
            return os.path.join("data", "plugin_data", PLUGIN_NAME, "timetables.json")

    def _cfg(self, key: str, default=None):
        try:
            value = self.config.get(key, default)
        except Exception:  # noqa: BLE001
            value = getattr(self.config, key, default)
        return default if value is None else value

    @property
    def _tz(self) -> str:
        return str(self._cfg("timezone", "Asia/Shanghai") or "Asia/Shanghai")

    def _now(self) -> datetime:
        return datetime.now(get_tz(self._tz))

    def _fallback_sections(self):
        text = str(self._cfg("default_section_times", "") or "")
        sections = parse_section_text(text)
        return sections or default_sections()

    def _default_total_weeks(self) -> int:
        try:
            return max(1, min(int(self._cfg("default_total_weeks", 20)), 60))
        except (TypeError, ValueError):
            return 20

    def _font_path(self) -> str:
        return str(self._cfg("font_path", "") or "")

    def _image_width(self) -> int:
        try:
            return max(720, min(int(self._cfg("image_width", 900)), 1200))
        except (TypeError, ValueError):
            return 900

    def _everyone_page_size(self) -> int:
        try:
            return max(3, min(int(self._cfg("everyone_page_size", 8)), 20))
        except (TypeError, ValueError):
            return 8

    def _plugin_data_dir(self) -> Path:
        return Path(self._data_path()).parent

    # ---------------------------------------------------------- 节假日 / 定时节目

    def _holiday_cache_path(self, year: int) -> Path:
        return self._plugin_data_dir() / "holidays" / f"{year}.json"

    def _holiday_cache_hours(self) -> int:
        try:
            return max(1, min(int(self._cfg("holiday_cache_hours", 24)), 168))
        except (TypeError, ValueError):
            return 24

    def _load_holiday_cache(self, year: int) -> HolidayCalendar | None:
        calendar = HolidayCalendar.load(self._holiday_cache_path(year), year)
        if calendar.days:
            self._holidays[year] = calendar
            return calendar
        return None

    def _makeup_map(self) -> dict[str, int]:
        """全局补班映射，例如 `2026-10-10 周四`，个人设置优先。"""
        text = str(self._cfg("makeup_weekday_map", "") or "").strip()
        if text == self._makeup_text:
            return self._makeup_values
        today = self._now().date()
        values: dict[str, int] = {}
        for line in text.splitlines():
            parts = [p for p in re.split(r"[\s,，;；]+", line.strip()) if p]
            if len(parts) < 2:
                continue
            day = parse_date(parts[0]) or self._parse_cmd_date(parts[0], today)
            weekday = parse_day(parts[1])
            if day is not None and weekday is not None:
                values[day.isoformat()] = weekday
        self._makeup_text = text
        self._makeup_values = values
        return values

    def _calendar_for(self, day: date) -> HolidayCalendar | None:
        calendar = self._holidays.get(day.year)
        if calendar is None:
            calendar = self._load_holiday_cache(day.year)
        if calendar and calendar.days:
            calendar.makeup_weekdays = self._makeup_map()
            return calendar
        return None

    async def _refresh_holidays(self, year: int, *, force: bool = False) -> None:
        if self._http is None:
            return
        cached = self._holidays.get(year) or self._load_holiday_cache(year)
        if not force and cached and not cached.stale(self._holiday_cache_hours()):
            return
        try:
            calendar = await fetch_calendar(
                self._http,
                year,
                api_url=str(self._cfg("holiday_api_url", "") or ""),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[{PLUGIN_NAME}] {year} 年节假日更新失败: {exc}")
            return
        calendar.save(self._holiday_cache_path(year))
        self._holidays[year] = calendar
        self._day_cache.clear()
        self._status_cache.clear()
        self._entries_cache.clear()
        logger.info(
            f"[{PLUGIN_NAME}] 已更新 {year} 年节假日（来源 {calendar.source}，"
            f"{len(calendar.days)} 天）"
        )

    async def _holiday_loop(self) -> None:
        while True:
            try:
                now = self._now()
                years = {now.year}
                if now.month >= 10:
                    years.add(now.year + 1)
                for year in years:
                    await self._refresh_holidays(year)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{PLUGIN_NAME}] 节假日后台任务失败: {exc}")
            await asyncio.sleep(self._holiday_cache_hours() * 3600)

    def _broadcast_state_path(self) -> Path:
        return self._plugin_data_dir() / "broadcast_state.json"

    def _load_broadcast_state(self) -> None:
        try:
            payload = json.loads(self._broadcast_state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        if isinstance(payload, dict):
            self._broadcast_state = {
                str(k): v for k, v in payload.items() if isinstance(v, dict)
            }

    def _save_broadcast_state(self) -> None:
        path = self._broadcast_state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self._broadcast_state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError as exc:
            logger.warning(f"[{PLUGIN_NAME}] 定时节目状态写入失败: {exc}")

    def _day_entries_cached(self, tt: Timetable, day: date, holiday):
        """同一用户的当天课程快照只算一次；写盘或节假日刷新时整体失效。"""
        key = (id(tt), day.isoformat(), id(holiday))
        cached = self._day_cache.get(key)
        if cached is not None:
            return cached
        entries = day_entries(tt, day, holiday)
        if len(self._day_cache) > 512:
            self._day_cache.clear()
        self._day_cache[key] = entries
        return entries

    @staticmethod
    def _local_now(tt: Timetable, now: datetime) -> datetime:
        tz = get_tz(tt.timezone)
        local = now if now.tzinfo is not None else now.replace(tzinfo=tz)
        return local.astimezone(tz)

    def _status_for(self, tt: Timetable, now: datetime) -> dict:
        """按用户 + 分钟缓存状态，多个群在同一分钟查询时直接复用。"""
        local = self._local_now(tt, now)
        day = local.date()
        holiday = self._calendar_for(day)
        show_teacher = self._store_teacher()
        key = (id(tt), local.strftime("%Y%m%d%H%M"), id(holiday), show_teacher)
        cached = self._status_cache.get(key)
        if cached is not None:
            return cached
        entries = self._day_entries_cached(tt, day, holiday)
        status = status_now(
            tt,
            local,
            show_teacher=show_teacher,
            holiday=holiday,
            entries=entries,
        )
        if len(self._status_cache) > 2048:
            self._status_cache.clear()
        self._status_cache[key] = status
        return status

    def _cached_entries(
        self,
        scope: str,
        timetables,
        now: datetime,
        *,
        hide_idle_over: int = 0,
    ) -> list[dict]:
        """先按用户复用状态，再统一排序和隐藏，避免每个群重复算整批人。"""
        signature = tuple(
            (id(tt), self._local_now(tt, now).strftime("%Y%m%d%H%M"))
            for tt in timetables
        )
        key = (signature, hide_idle_over)
        cached = self._entries_cache.get(key)
        if cached is not None:
            return cached
        statuses = [self._status_for(tt, now) for tt in timetables]
        entries = everyone_entries(
            timetables,
            now,
            holiday=self._calendar_for(now.date()),
            hide_idle_over=hide_idle_over,
            statuses=statuses,
        )
        if len(self._entries_cache) > 256:
            self._entries_cache.clear()
        self._entries_cache[key] = entries
        return entries

    def _claim_broadcast(self, kind: str, group_id: str, day: date) -> bool:
        bucket = self._broadcast_state.setdefault(kind, {})
        key = day.isoformat()
        if bucket.get(group_id) == key:
            return False
        bucket[group_id] = key
        self._save_broadcast_state()
        return True

    def _release_broadcast(self, kind: str, group_id: str) -> None:
        """发送失败时退回当天名额，下一轮检查还能重试。"""
        bucket = self._broadcast_state.get(kind)
        if bucket and bucket.pop(group_id, None) is not None:
            self._save_broadcast_state()

    def _hide_idle_over(self) -> int:
        try:
            return max(0, min(int(self._cfg("everyone_hide_idle_over", 12)), 200))
        except (TypeError, ValueError):
            return 12

    def _max_file_bytes(self) -> int:
        try:
            return max(64, min(int(self._cfg("max_file_kb", 2048)), 16384)) * 1024
        except (TypeError, ValueError):
            return 2048 * 1024

    def _store_teacher(self) -> bool:
        return bool(self._cfg("store_teacher", False))

    def _store_location(self) -> bool:
        return bool(self._cfg("store_location", False))

    def _scrub_privacy(self) -> bool:
        """配置关闭时清掉历史数据里的教师/地点，避免旧数据绕过隐私默认值。"""
        keep_teacher = self._store_teacher()
        keep_location = self._store_location()
        if keep_teacher and keep_location:
            return False
        changed = False
        for tt in self.store.all().values():
            for course in tt.courses:
                if not keep_teacher and course.teacher:
                    course.teacher = ""
                    changed = True
                if not keep_location and course.location:
                    course.location = ""
                    changed = True
        return changed

    # ---------------------------------------------------------- 用户 / 群

    @staticmethod
    def _platform(event) -> str:
        try:
            return event.get_platform_id() or event.get_platform_name() or "unknown"
        except Exception:  # noqa: BLE001
            return event.get_platform_name() or "unknown"

    @staticmethod
    def _sender_id(event) -> str:
        uid = event.get_sender_id() or ""
        if not uid:
            # 个别适配器把 user_id 存成 int，get_sender_id() 会返回空字符串，
            # 此时不能退回 session_id（群里就是群号，会让全群共用一个存档）。
            sender = getattr(getattr(event, "message_obj", None), "sender", None)
            raw = getattr(sender, "user_id", "")
            if raw not in (None, ""):
                uid = str(raw)
        return uid or event.get_session_id()

    @staticmethod
    def _sender_name(event) -> str:
        name = event.get_sender_name() or ""
        if not name:
            sender = getattr(getattr(event, "message_obj", None), "sender", None)
            name = str(getattr(sender, "nickname", "") or "")
        return name

    @classmethod
    def _user_key(cls, event) -> str:
        return f"{cls._platform(event)}:{cls._sender_id(event)}"

    @classmethod
    def _target_key(cls, event, target_id: str) -> str:
        return f"{cls._platform(event)}:{target_id}"

    def _own_tt(self, event) -> Timetable | None:
        return self.store.get(self._user_key(event))

    def _touch(self, event, tt: Timetable) -> bool:
        changed = False
        name = self._sender_name(event)
        if name and name != tt.owner_name:
            tt.owner_name = name
            changed = True
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        if tt.remember_group(event.get_group_id() or "", origin):
            changed = True
        return changed

    async def _save(self) -> None:
        async with self._lock:
            self.store.save()
        self._day_cache.clear()
        self._status_cache.clear()
        self._entries_cache.clear()

    # ---------------------------------------------------------- 命令入口

    @filter.command("课表")
    async def cmd_schedule(self, event):
        async for result in self._dispatch(event):
            yield result

    @filter.command("课表导入")
    async def cmd_schedule_import(self, event):
        async for result in self._dispatch(event):
            yield result

    @filter.command("课表排行")
    async def cmd_schedule_rank(self, event):
        async for result in self._dispatch(event):
            yield result

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def cmd_glued(self, event):
        """兼容“课表明天”“课表导入https://…”“课表排行本周”这类无空格写法。"""
        if event.get_extra("parsed_params") is not None:
            return  # 正式命令已经处理
        text = (event.message_str or "").strip().lstrip("/").strip()
        if not text.startswith("课表") or len(text) <= 2:
            return
        raw_rest = text[2:]
        if raw_rest[:1].isspace():
            return  # “课表 xxx” 由命令过滤器处理
        rest = raw_rest.strip()
        normalized = None
        for key in sorted(SUBCOMMANDS, key=len, reverse=True):
            if rest.startswith(key):
                normalized = key + " " + rest[len(key) :]
                break
        if normalized is None:
            if not (rest.startswith("@") or rest[:1].isdigit() or parse_query_date(rest) is not None):
                return  # 不像课表指令，放给 LLM
            normalized = rest
        event.stop_event()
        async for item in self._dispatch(event, rest_override=normalized):
            yield item

    async def _dispatch(self, event, rest_override: str | None = None):
        rest = rest_override if rest_override is not None else self._rest(event)
        first, _, tail = (re.split(r"\s+", rest, maxsplit=1) + ["", ""])[:3]
        sub = SUBCOMMANDS.get(first.lower()) if first else None

        if sub == "help":
            async for item in self._h_help(event, tail.strip()):
                yield item
            return
        if sub == "import":
            async for item in self._h_import(event, tail.strip()):
                yield item
            return
        if sub == "delete":
            async for item in self._h_delete(event):
                yield item
            return
        if sub == "notify":
            async for item in self._h_notify(event, tail.strip()):
                yield item
            return
        if sub == "note":
            async for item in self._h_note(event, tail.strip()):
                yield item
            return
        if sub == "rank":
            async for item in self._h_rank(event, tail.strip()):
                yield item
            return
        if sub == "abandon":
            async for item in self._h_abandon(event):
                yield item
            return
        if sub == "sections":
            async for item in self._h_sections(event, tail.strip()):
                yield item
            return
        if sub == "everyone":
            async for item in self._h_everyone(event):
                yield item
            return
        if sub == "export":
            async for item in self._h_export(event):
                yield item
            return
        if sub == "term":
            async for item in self._h_term(event, tail.strip()):
                yield item
            return
        if sub == "leave":
            async for item in self._h_leave(event, tail.strip()):
                yield item
            return
        if sub == "leave_cancel":
            async for item in self._h_leave_cancel(event, tail.strip()):
                yield item
            return
        if sub == "adjust":
            async for item in self._h_adjust(event, tail.strip()):
                yield item
            return
        if sub == "makeup":
            async for item in self._h_makeup(event, tail.strip()):
                yield item
            return

        async for item in self._h_query(event, rest):
            yield item

    @staticmethod
    def _rest(event) -> str:
        text = (event.message_str or "").strip()
        if text.startswith("/"):
            text = text[1:].strip()
        if text.startswith("课表"):
            text = text[2:].strip()
        return text

    # ---------------------------------------------------------- 查询课表

    async def _h_query(self, event, rest: str):
        tokens = [t for t in re.split(r"\s+", rest.strip()) if t]
        at_id = self._at_target(event)
        numeric_id = ""
        date_tokens = []
        for token in tokens:
            if token.startswith("@"):
                continue
            is_compact_date = (
                len(token) == 8 and token.isdigit() and token[:2] in ("19", "20")
            )
            if token.isdigit() and len(token) >= 5 and not is_compact_date:
                numeric_id = token
                continue
            date_tokens.append(token)

        target_id = at_id or numeric_id
        self_id = str(event.get_self_id() or "")
        if target_id and target_id == self_id:
            target_id = ""

        group_id = event.get_group_id() or ""
        if target_id:
            if not self._cfg("allow_group_query", True):
                yield event.plain_result("本插件已关闭查询他人课表。")
                return
            tt = self.store.get(self._target_key(event, target_id))
            if not tt:
                yield event.plain_result("对方还没有导入课表哦。")
                return
            if (
                group_id
                and not self._cfg("allow_cross_group", False)
                and group_id not in tt.groups
            ):
                yield event.plain_result("对方还没有在本群使用过课表，为保护隐私不能跨群查询。")
                return
            label = tt.owner_name or str(target_id)
        else:
            tt = self._own_tt(event)
            if not tt:
                yield event.plain_result("你还没有导入课表哦。\n" + HELP_TEXT)
                return
            label = ""
            if self._touch(event, tt):
                await self._save()

        date_text = " ".join(date_tokens).strip()
        target_date = parse_query_date(date_text, self._now().date()) if date_text else self._now().date()
        if target_date is None:
            yield event.plain_result(
                "日期没看懂。可以用：今天、明天、周一、下周三、9/1、2026-09-01"
            )
            return

        yield event.plain_result(
            format_day(
                tt,
                target_date,
                label=label,
                show_teacher=self._store_teacher(),
                today=self._now().date(),
                holiday=self._calendar_for(target_date),
            )
        )

    @staticmethod
    def _messages(event):
        try:
            return event.get_messages() or []
        except Exception:  # noqa: BLE001
            return getattr(getattr(event, "message_obj", None), "message", []) or []

    def _at_target(self, event) -> str:
        self_id = str(event.get_self_id() or "")
        for comp in self._messages(event):
            if comp.__class__.__name__ == "At":
                qq = str(getattr(comp, "qq", "") or "")
                if qq and qq != "all" and qq != self_id:
                    return qq
        return ""

    # ---------------------------------------------------------- 群友在上什么课

    async def _h_everyone(self, event):
        tt = self._own_tt(event)
        group_id = event.get_group_id() or ""
        now = self._now()
        if not group_id:
            if not tt:
                yield event.plain_result("你还没有导入课表哦。\n" + HELP_TEXT)
                return
            entries = self._cached_entries(f"u:{tt.owner_id}", [tt], now)
            if self._image_enabled("everyone_image"):
                pages = self._render_everyone(
                    entries,
                    title="你现在的课程",
                    subtitle=f"{now:%Y/%m/%d} {weekday_cn(now.date())} · {now:%H:%M}",
                )
                if pages and await self._send_image_pages(event, pages, title="课表 Lite"):
                    return
            status = status_now(
                tt,
                now,
                show_teacher=self._store_teacher(),
                holiday=self._calendar_for(now.date()),
            )
            emoji = STATUS_EMOJI.get(status["type"], "")
            label = STATUS_LABEL.get(status["type"], "")
            text = f"{emoji} 你 · {label}"
            if status["main"]:
                text += f"\n　{status['main']}"
            if status["sub"]:
                text += f"\n　{status['sub']}"
            yield event.plain_result(text)
            return

        if tt and self._touch(event, tt):
            await self._save()
        visible = timetables_of_group(self.store.all().values(), group_id)
        # 自己刚在本群用过指令，即使没记录过群号也要算进来
        if tt and tt not in visible:
            visible.append(tt)
        if not visible:
            yield event.plain_result(
                "本群还没有人导入课表哦。\n发送 /课表 导入 <链接或文件> 即可加入。"
            )
            return

        entries = self._cached_entries(
            f"g:{group_id}", visible, now, hide_idle_over=self._hide_idle_over()
        )
        hidden = max(len(visible) - len(entries), 0)
        if self._image_enabled("everyone_image"):
            pages = self._render_everyone(
                entries,
                title="群友在上什么课？",
                subtitle=(
                    f"{now:%Y/%m/%d} {weekday_cn(now.date())} · "
                    f"{now:%H:%M} · 共 {len(entries)} 人"
                ),
                footer_note=(
                    f"已隐藏 {hidden} 位无课同学 · 发送 /课表 查看自己的课表"
                    if hidden
                    else ""
                ),
            )
            if pages and await self._send_image_pages(event, pages, title="群友在上什么课"):
                return
        yield event.plain_result(
            everyone_report(
                visible,
                now,
                show_teacher=self._store_teacher(),
                holiday=self._calendar_for(now.date()),
                hide_idle_over=self._hide_idle_over(),
            )
        )

    # ---------------------------------------------------------- 排行

    async def _h_rank(self, event, args: str):
        group_id = event.get_group_id() or ""
        if not group_id:
            yield event.plain_result("排行要在群里用哦。")
            return
        tt = self._own_tt(event)
        if tt and self._touch(event, tt):
            await self._save()

        mode = "week"
        full = False
        for token in re.split(r"\s+", args.strip()):
            if not token:
                continue
            if token in ("全部", "all", "所有人"):
                full = True
            elif token in RANK_MODES:
                mode = RANK_MODES[token]

        visible = timetables_of_group(self.store.all().values(), group_id)
        if tt and tt not in visible:
            visible.append(tt)
        if not visible:
            yield event.plain_result("本群还没有人导入课表哦。")
            return
        mode_label = RANK_MODE_LABEL.get(mode, "本周")
        yield event.plain_result(
            format_rank(
                visible,
                mode,
                self._now(),
                full=full,
                title=f"本群{mode_label}课程时长榜",
                holiday=self._calendar_for(self._now().date()),
            )
        )

    # ---------------------------------------------------------- 导入

    async def _h_import(self, event, payload: str):
        try:
            file_text = await self._attachment_text(event)
        except ImportFailure as e:
            yield event.plain_result(f"❌ 导入失败：{e}")
            return
        raw = file_text or payload
        if not raw:
            yield event.plain_result("把课表链接、口令、JSON 文本或文件发给我即可。\n" + detect.HELP)
            return

        try:
            result = await detect.import_any(
                raw,
                fetch=self._fetch,
                session=self._http,
                fallback_sections=self._fallback_sections(),
                default_total_weeks=self._default_total_weeks(),
                store_teacher=self._store_teacher(),
                store_location=self._store_location(),
                tz=self._tz,
                allow_network=bool(self._cfg("allow_network", True)) and self._http is not None,
            )
        except ImportFailure as e:
            yield event.plain_result(f"❌ 导入失败：{e}")
            return
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[{PLUGIN_NAME}] 导入异常")
            yield event.plain_result(f"❌ 导入失败：{e}")
            return

        key = self._user_key(event)
        old = self.store.get(key)
        tt = Timetable(
            owner_id=self._sender_id(event),
            owner_name=self._sender_name(event) or (old.owner_name if old else ""),
            name=result.name,
            term_start=result.term_start.isoformat(),
            total_weeks=result.total_weeks,
            timezone=self._tz,
            source=result.source,
            imported_at=self._now().isoformat(timespec="seconds"),
            sections=result.sections,
            courses=result.courses,
        )
        removed_notes: list[str] = []
        if old:
            tt.notify_minutes = old.notify_minutes
            tt.notify_enabled = old.notify_enabled
            tt.notify_origin = old.notify_origin
            tt.notified_date = old.notified_date
            tt.notified_keys = list(old.notified_keys)
            tt.abandon_date = old.abandon_date
            tt.groups = list(old.groups)
            tt.group_origins = dict(old.group_origins)
            names = {c.name for c in tt.courses}
            for course_name, note in old.notes.items():
                if course_name in names:
                    tt.notes[course_name] = note
                else:
                    removed_notes.append(course_name)
        group_id = event.get_group_id() or ""
        if group_id:
            tt.remember_group(group_id, str(getattr(event, "unified_msg_origin", "") or ""))

        self.store.put(key, tt)
        await self._save()

        distinct = len({c.name for c in tt.courses})
        lines = [
            f"✅ 已导入课表「{tt.name}」",
            f"来源：{result.source}",
            f"学期：{result.term_start:%Y/%m/%d} 起 · 共 {tt.total_weeks} 周",
            f"数据：{distinct} 门课 / {len(tt.courses)} 条安排",
        ]
        if result.merged:
            lines[-1] += f"（自动合并 {result.merged} 条重复）"
        if result.dropped:
            lines[-1] += f"（忽略 {result.dropped} 条异常）"
        if tt.sections:
            lines.append(f"作息：{len(tt.sections)} 节")
        for warning in result.warnings[:4]:
            lines.append(f"⚠️ {warning}")
        if len(result.warnings) > 4:
            lines.append(f"⚠️ 另有 {len(result.warnings) - 4} 条提示未显示")
        if removed_notes:
            lines.append(f"ℹ️ 以下课程已不在新表中，备注已清除：{'、'.join(removed_notes[:5])}")
        lines.append("发送 /课表 查看今天的课表。")
        yield event.plain_result("\n".join(lines))

    async def _attachment_text(self, event) -> str:
        limit = self._max_file_bytes()
        for comp in self._messages(event):
            if comp.__class__.__name__ != "File":
                continue
            filename = str(getattr(comp, "name", "") or "").lower()
            if filename.endswith((".xlsx", ".xls")):
                raise ImportFailure("暂不支持 Excel，请在表格软件里另存为 CSV 后再发")
            try:
                path = await asyncio.to_thread(lambda: comp.file)
            except Exception as e:  # noqa: BLE001
                raise ImportFailure(f"读取附件失败：{e}") from e
            if not path or not os.path.exists(path):
                raise ImportFailure("附件没有成功下载")
            size = os.path.getsize(path)
            if size > limit:
                raise ImportFailure(f"附件太大（{size / 1024:.0f} KB），上限是 {limit // 1024} KB")
            data = Path(path).read_bytes()
            text = self._decode_bytes(data)
            if text is None:
                raise ImportFailure("附件编码无法识别：Excel 请另存为 CSV，文本请另存为 UTF-8 后重试")
            return text
        return ""

    async def _check_url(self, url: str) -> None:
        """阻止把机器人当成代理访问内网/元数据地址（SSRF）。"""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ImportFailure("只支持 http/https 链接")
        host = parsed.hostname
        if not host:
            raise ImportFailure("链接里没有主机名")
        if self._cfg("allow_private_network", False):
            return
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            try:
                infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
            except OSError as e:
                raise ImportFailure(f"域名解析失败：{host}") from e
            addresses = []
            for info in infos:
                try:
                    addresses.append(ipaddress.ip_address(info[4][0]))
                except ValueError:
                    continue
        for ip in addresses:
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
            ):
                raise ImportFailure("不允许访问内网地址；确实需要请打开 allow_private_network")

    async def _fetch(self, url: str) -> str:
        if self._http is None:
            raise ImportFailure("网络组件未初始化，无法从链接导入")
        limit = self._max_file_bytes()
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=20)
        headers = {
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            )
        }
        current = url
        data = b""
        followed = False
        for _ in range(4):  # 手动跟随重定向，每一跳都做一次内网检查
            await self._check_url(current)
            try:
                async with self._http.get(
                    current, headers=headers, timeout=timeout, allow_redirects=False
                ) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        location = resp.headers.get("Location")
                        if not location:
                            raise ImportFailure("链接重定向缺少目标地址")
                        current = urljoin(current, location)
                        followed = True
                        continue
                    if resp.status >= 400:
                        raise ImportFailure(f"链接下载失败：HTTP {resp.status}")
                    data = await resp.content.read(limit + 1)
                    break
            except ImportFailure:
                raise
            except Exception as e:  # noqa: BLE001
                raise ImportFailure(f"链接下载失败：{e}") from e
        if followed and not data:
            raise ImportFailure("链接重定向次数过多，或重定向后没有内容")
        if len(data) > limit:
            raise ImportFailure(f"文件太大，上限 {limit // 1024} KB")
        text = self._decode_bytes(data)
        return text if text is not None else data.decode("utf-8", errors="replace")

    @staticmethod
    def _decode_bytes(data: bytes) -> str | None:
        for encoding in ("utf-8-sig", "utf-8", "gbk", "utf-16"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return None

    # ---------------------------------------------------------- 删除 / 备注 / 提醒

    async def _h_delete(self, event):
        tt = self.store.delete(self._user_key(event))
        if not tt:
            yield event.plain_result("你还没有导入课表哦。")
            return
        await self._save()
        yield event.plain_result(f"已删除课表「{tt.name}」。")

    async def _h_note(self, event, args: str):
        tt = self._own_tt(event)
        if not tt:
            yield event.plain_result("你还没有导入课表哦。")
            return
        if not args:
            if event.get_group_id():
                yield event.plain_result("备注属于隐私内容，请私聊机器人查看。")
                return
            if not tt.notes:
                yield event.plain_result("你还没有设置过备注。")
                return
            lines = ["当前设置的备注："]
            for course, note in tt.notes.items():
                lines.append(f"「{course}」\n· {note}")
            yield event.plain_result("\n".join(lines))
            return

        name, _, note = args.partition(" ")
        name, note = name.strip(), note.strip()
        names = {c.name for c in tt.courses}
        if name in names:
            matched = name
        else:
            hits = sorted({n for n in names if name and name in n})
            if len(hits) == 1:
                matched = hits[0]
            elif len(hits) > 1:
                yield event.plain_result(f"有多门课程匹配「{name}」：{' / '.join(hits)}，请写具体一点。")
                return
            else:
                yield event.plain_result(f"没有找到课程「{name}」。")
                return

        if note:
            tt.notes[matched] = note
            message = f"已设置「{matched}」的备注。"
        else:
            tt.notes.pop(matched, None)
            message = f"已清除「{matched}」的备注。"
        await self._save()
        yield event.plain_result(message)

    async def _h_notify(self, event, args: str):
        tt = self._own_tt(event)
        if not tt:
            yield event.plain_result("请先导入课表再订阅提醒。")
            return
        if event.get_group_id():
            yield event.plain_result("上课提醒会私聊发送，请私聊机器人使用 /课表 提醒。")
            return

        arg = args.strip()
        if arg in ("关", "关闭", "取消", "disable", "off", "0"):
            tt.notify_enabled = False
            tt.notify_minutes = None
            tt.notify_origin = ""
            await self._save()
            yield event.plain_result("已关闭上课提醒。")
            return

        minutes = 15
        if arg:
            if not arg.isdigit():
                yield event.plain_result("用法：/课表 提醒 [分钟数|关]，分钟数 0~60。")
                return
            minutes = int(arg)
        if not (0 <= minutes <= 60):
            yield event.plain_result("提醒分钟数只能在 0~60 之间。")
            return

        tt.notify_minutes = minutes
        tt.notify_enabled = True
        tt.notify_origin = event.unified_msg_origin
        await self._save()
        yield event.plain_result(f"已开启上课提醒：每节课开始前 {minutes} 分钟私聊你。")

    async def _h_abandon(self, event):
        tt = self._own_tt(event)
        if not tt:
            yield event.plain_result("你还没有导入课表哦。")
            return
        today = self._now().date().isoformat()
        if tt.abandon_date == today:
            tt.abandon_date = ""
            message = "已取消今日翘课。"
        else:
            tt.abandon_date = today
            message = "已设置今日翘课。"
        await self._save()
        yield event.plain_result(message)

    async def _h_sections(self, event, args: str):
        tt = self._own_tt(event)
        if not tt:
            yield event.plain_result("你还没有导入课表哦。")
            return
        if not args:
            if not tt.sections:
                yield event.plain_result("当前没有作息数据。\n发送 /课表 作息 1 08:00-08:45 逐行添加。")
                return
            lines = ["当前作息（可发 /课表 作息 1 08:00-08:45 … 覆盖）："]
            for section in tt.sections:
                alias = f" {section.alias}" if section.alias else ""
                lines.append(f"{section.index}. {section.start}-{section.end}{alias}")
            yield event.plain_result("\n".join(lines))
            return

        sections = parse_section_text(args)
        if not sections:
            yield event.plain_result(
                "没解析到作息。格式示例：\n/课表 作息\n1 08:00-08:45\n2 08:55-09:40"
            )
            return
        tt.sections = sections
        await self._save()
        yield event.plain_result(f"已更新作息，共 {len(sections)} 节。")

    async def _h_help(self, event, args: str):
        if args.strip().lower() in ("文本", "text", "纯文本", "md"):
            yield event.plain_result(HELP_TEXT)
            return
        if not self._image_enabled("help_image"):
            yield event.plain_result(HELP_TEXT + "\n\n（发送 /课表 帮助 文本 可只看文字版）")
            return
        try:
            doc_src = Path(PLUGIN_DIR) / "docs" / "help.md"
            markdown = doc_src.read_text(encoding="utf-8")
            help_dir = self._plugin_data_dir() / "help"
            help_dir.mkdir(parents=True, exist_ok=True)
            local_doc = help_dir / "help.md"
            try:
                if not local_doc.is_file() or local_doc.read_text(encoding="utf-8") != markdown:
                    local_doc.write_text(markdown, encoding="utf-8")
            except OSError:
                pass
            pages = render_mod.render_help(
                markdown,
                help_dir,
                width=self._image_width(),
                font_path=self._font_path(),
            )
            if pages and await self._send_image_pages(event, pages, title="课表 Lite 使用手册"):
                return
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{PLUGIN_NAME}] 帮助图片生成失败: {e}")
        yield event.plain_result(HELP_TEXT)

    def _image_enabled(self, key: str) -> bool:
        return bool(self._cfg(key, True)) and render_mod.available(self._font_path())

    def _render_everyone(
        self, entries, *, title: str, subtitle: str, footer_note: str = ""
    ):
        try:
            cache_dir = self._plugin_data_dir() / "cache" / "everyone"
            return render_mod.render_everyone(
                entries,
                cache_dir,
                title=title,
                subtitle=subtitle,
                footer_note=footer_note or "发送 /课表 查看自己的课表",
                page_size=self._everyone_page_size(),
                width=self._image_width(),
                font_path=self._font_path(),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{PLUGIN_NAME}] 课表图片生成失败: {e}")
            return []

    async def _send_image_pages(self, event, paths, *, title: str = "课表 Lite") -> bool:
        """优先发合并转发（一个节点一张图），平台不支持时退回逐张发送。"""
        if not paths:
            return False
        try:
            try:
                from astrbot.api.message_components import Image, Node, Nodes
            except ImportError:
                from astrbot.core.message.components import Image, Node, Nodes  # type: ignore
        except ImportError:
            logger.warning(f"[{PLUGIN_NAME}] 当前 AstrBot 版本没有合并转发组件")
            return False

        try:
            uin = str(event.get_self_id() or "0")
            nodes = [
                Node(content=[Image.fromFileSystem(str(path))], name=title, uin=uin)
                for path in paths
            ]
            await event.send(MessageChain([Nodes(nodes)]))
            return True
        except Exception as e:  # noqa: BLE001
            logger.info(f"[{PLUGIN_NAME}] 合并转发不可用，改为逐张发送: {e}")

        for path in paths:
            try:
                await event.send(MessageChain([Image.fromFileSystem(str(path))]))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[{PLUGIN_NAME}] 图片发送失败: {e}")
                return False
        return True

    async def _h_export(self, event):
        tt = self._own_tt(event)
        if not tt:
            yield event.plain_result("你还没有导入课表哦。")
            return
        text = dumps_export(tt)
        if len(text) > 3000:
            yield event.plain_result(
                f"课表较大（{len(text)} 字节），QQ 消息可能发不全。"
                "可在插件数据目录中查看 timetables.json。"
            )
            return
        yield event.plain_result("```json\n" + text + "\n```")

    async def _h_term(self, event, args: str):
        """手动修正学期开始日期（CSV/JSON 没带开学日期时用）。"""
        tt = self._own_tt(event)
        if not tt:
            yield event.plain_result("你还没有导入课表哦。")
            return
        if not args:
            term = tt.term_date()
            if term:
                yield event.plain_result(
                    f"当前学期从 {term:%Y/%m/%d}（第 1 周周一）开始。\n"
                    "修正：/课表 学期 2026-09-07"
                )
            else:
                yield event.plain_result("还没有学期开始日期。\n设置：/课表 学期 2026-09-07")
            return
        target = parse_date(args)
        if target is None:
            yield event.plain_result("日期没看懂，示例：/课表 学期 2026-09-07")
            return
        tt.term_start = monday_of(target).isoformat()
        await self._save()
        yield event.plain_result(
            f"已把第 1 周设为 {tt.term_start}（{monday_of(target):%Y/%m/%d}）。\n"
            "发送 /课表 检查日期。"
        )

    # ---------------------------------------------------------- 定时节目 / 周报

    @staticmethod
    def _hhmm(value: str, default: str) -> tuple[int, int]:
        for candidate in (value, default):
            m = re.fullmatch(r"(\d{1,2}):(\d{2})", (candidate or "").strip())
            if not m:
                continue
            hour, minute = int(m.group(1)), int(m.group(2))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return hour, minute
        return (0, 0)

    def _group_origins(self) -> dict[str, str]:
        origins: dict[str, str] = {}
        for tt in self.store.all().values():
            for group_id, origin in tt.group_origins.items():
                if origin and group_id not in origins:
                    origins[group_id] = origin
        return origins

    def _period_hours(self, tt: Timetable, mode: str, today: date) -> float:
        calendar = self._calendar_for(today)
        if mode == "month":
            start = today.replace(day=1)
        else:
            start = today - timedelta(days=today.weekday())
        total = 0
        day = start
        while day <= today:
            for _course, start_hm, end_hm in day_courses(tt, day, calendar):
                total += max(
                    (minutes_of(end_hm) or 0) - (minutes_of(start_hm) or 0), 0
                )
            day += timedelta(days=1)
        return total / 60.0

    async def _check_scheduled_broadcasts(self, now: datetime) -> None:
        showcase_enabled = bool(self._cfg("showcase_enabled", False))
        recap_enabled = bool(self._cfg("recap_enabled", False))
        if not showcase_enabled and not recap_enabled:
            return
        today = now.date()
        now_min = now.hour * 60 + now.minute
        origins = self._group_origins()
        if not origins:
            return
        all_tt = self.store.all()

        if showcase_enabled:
            hour, minute = self._hhmm(
                str(self._cfg("showcase_time", "22:30") or "22:30"), "22:30"
            )
            target = hour * 60 + minute
            if now_min >= target:
                try:
                    min_minutes = max(
                        30, min(int(self._cfg("showcase_min_minutes", 300)), 1440)
                    )
                except (TypeError, ValueError):
                    min_minutes = 300
                try:
                    min_users = max(1, min(int(self._cfg("showcase_min_users", 2)), 50))
                except (TypeError, ValueError):
                    min_users = 2
                calendar = self._calendar_for(today)
                for group_id, origin in origins.items():
                    visible = timetables_of_group(all_tt.values(), group_id)
                    ranked = []
                    for tt in visible:
                        if tt.leave_on(today):
                            continue
                        minutes = sum(
                            max((minutes_of(e) or 0) - (minutes_of(s) or 0), 0)
                            for _c, s, e in day_courses(tt, today, calendar)
                        )
                        if minutes >= min_minutes:
                            ranked.append((minutes, tt.owner_name or tt.owner_id))
                    if len(ranked) < min_users:
                        continue
                    if not self._claim_broadcast("showcase", group_id, today):
                        continue
                    ranked.sort(reverse=True)
                    lines = ["🌙 今日满课榜", f"{today:%m/%d} · 共 {len(ranked)} 人"]
                    for index, (minutes, name) in enumerate(ranked[:10], 1):
                        lines.append(f"{index}. {name} · {minutes / 60:.1f} 小时")
                    try:
                        await self.context.send_message(
                            origin, MessageChain().message("\n".join(lines))
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"[{PLUGIN_NAME}] 满课榜发送失败 {group_id}: {exc}")
                        self._release_broadcast("showcase", group_id)

        if recap_enabled:
            hour, minute = self._hhmm(
                str(self._cfg("recap_time", "21:00") or "21:00"), "21:00"
            )
            target = hour * 60 + minute
            try:
                weekday = max(
                    1, min(int(self._cfg("recap_weekday", 5)), 7)
                )
            except (TypeError, ValueError):
                weekday = 5
            calendar = self._calendar_for(today)
            holiday_eve = bool(calendar and calendar.holiday_eve(today))
            if now_min >= target and (
                today.isoweekday() == weekday or holiday_eve
            ):
                mode = str(self._cfg("recap_mode", "week") or "week")
                if mode not in ("week", "month"):
                    mode = "week"
                try:
                    top_n = max(1, min(int(self._cfg("recap_top", 5)), 20))
                except (TypeError, ValueError):
                    top_n = 5
                for group_id, origin in origins.items():
                    visible = timetables_of_group(all_tt.values(), group_id)
                    ranked = []
                    for tt in visible:
                        hours = self._period_hours(tt, mode, today)
                        if hours > 0:
                            ranked.append((hours, tt.owner_name or tt.owner_id))
                    if len(ranked) < 2:
                        continue
                    if not self._claim_broadcast("recap", group_id, today):
                        continue
                    ranked.sort(reverse=True)
                    label = "本月" if mode == "month" else "本周"
                    lines = [f"📊 {label}累计上课榜", f"截至 {today:%m/%d}"]
                    for index, (hours, name) in enumerate(ranked[:top_n], 1):
                        lines.append(f"#{index} {name} · {hours:.1f} 小时")
                    try:
                        await self.context.send_message(
                            origin, MessageChain().message("\n".join(lines))
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"[{PLUGIN_NAME}] 周报发送失败 {group_id}: {exc}")
                        self._release_broadcast("recap", group_id)

    # ---------------------------------------------------------- 请假 / 调课 / 调休

    def _parse_cmd_date(self, text: str, today: date | None = None) -> date | None:
        today = today or self._now().date()
        token = (text or "").strip()
        if not token:
            return None
        m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})", token)
        if m:
            try:
                return date(today.year, int(m.group(1)), int(m.group(2)))
            except ValueError:
                return None
        return parse_query_date(token, today)

    def _take_dates(
        self, tokens: list[str], today: date
    ) -> tuple[list[date], list[str]]:
        dates: list[date] = []
        rest: list[str] = []
        range_re = re.compile(
            r"^\s*(\d{1,4}[/.-]\d{1,2}(?:[/.-]\d{1,4})?)"
            r"\s*(?:到|至|~|～|—|–|-)\s*"
            r"(\d{1,4}[/.-]\d{1,2}(?:[/.-]\d{1,4})?)\s*$"
        )
        for token in tokens:
            if len(dates) < 2:
                match = range_re.match(token)
                if match:
                    first = self._parse_cmd_date(match.group(1), today)
                    second = self._parse_cmd_date(match.group(2), today)
                    if first and second:
                        dates.extend([first, second])
                        continue
                parsed = self._parse_cmd_date(token, today)
                if parsed is not None:
                    dates.append(parsed)
                    continue
                parts = re.split(r"\s*(?:到|至|~|～|—|–)\s*", token)
                if len(parts) == 2:
                    first = self._parse_cmd_date(parts[0], today)
                    second = self._parse_cmd_date(parts[1], today)
                    if first and second:
                        dates.extend([first, second])
                        continue
            rest.append(token)
        return dates, rest

    async def _h_leave(self, event, args: str):
        tt = self._own_tt(event)
        if not tt:
            yield event.plain_result("你还没有导入课表哦。")
            return
        today = self._now().date()
        tokens = [t for t in re.split(r"\s+", args.strip()) if t]
        dates, rest = self._take_dates(tokens, today)
        if not dates:
            start = end = today
            reason = args.strip()
        else:
            start, end = min(dates), max(dates)
            reason = " ".join(rest).strip()
        if end < start:
            start, end = end, start
        if (end - start).days > 120:
            yield event.plain_result("请假范围太长了，最多 120 天。")
            return
        tt.leaves = [
            item
            for item in tt.leaves
            if not (
                str(item.get("start") or "") <= end.isoformat()
                and str(item.get("end") or item.get("start") or "") >= start.isoformat()
            )
        ]
        tt.leaves.append(
            {"start": start.isoformat(), "end": end.isoformat(), "reason": reason}
        )
        await self._save()
        span = f"{start:%m/%d}" if start == end else f"{start:%m/%d} - {end:%m/%d}"
        yield event.plain_result(
            f"📝 已登记请假：{span}{f'（{reason}）' if reason else ''}。\n"
            "查询和「在上」会优先显示请假；发送 /课表 销假 可取消。"
        )

    async def _h_leave_cancel(self, event, args: str):
        tt = self._own_tt(event)
        if not tt:
            yield event.plain_result("你还没有导入课表哦。")
            return
        today = self._now().date()
        target = self._parse_cmd_date(args.strip(), today) if args.strip() else today
        if target is None:
            yield event.plain_result("日期没看懂，示例：/课表 销假 9/10")
            return
        key = target.isoformat()
        kept = [
            item
            for item in tt.leaves
            if not (
                str(item.get("start") or "") <= key
                <= str(item.get("end") or item.get("start") or "")
            )
        ]
        if len(kept) == len(tt.leaves):
            yield event.plain_result(f"{target:%m/%d} 没有请假记录。")
            return
        tt.leaves = kept
        await self._save()
        yield event.plain_result(f"已取消 {target:%m/%d} 的请假。")

    def _add_adjustment(
        self,
        tt: Timetable,
        source_day: date,
        target_day: date,
        course_filter: str = "",
        swap: bool = False,
    ) -> tuple[list[dict], int]:
        """把某天的课程快照写入 adjustments；命令和 WebUI 共用。"""
        if source_day == target_day:
            raise ValueError("原日期和目标日期不能是同一天。")
        calendar = self._calendar_for(source_day)
        items = day_entries(tt, source_day, calendar)
        if course_filter:
            items = [item for item in items if course_filter in item.course.name]
        if not items:
            label = f"「{course_filter}」" if course_filter else ""
            raise ValueError(f"{source_day:%m/%d} 没有{label}课程可调。")

        snapshot = [
            {"name": item.course.name, "start": item.start, "end": item.end}
            for item in items
        ]
        target_items = []
        if swap:
            target_calendar = self._calendar_for(target_day)
            target_items = day_entries(tt, target_day, target_calendar)
            if course_filter:
                target_items = [
                    item for item in target_items if course_filter in item.course.name
                ]
        created_at = self._now().isoformat(timespec="seconds")
        tt.adjustments.append(
            {
                "from_date": source_day.isoformat(),
                "to_date": target_day.isoformat(),
                "course": course_filter,
                "courses": snapshot,
                "created_at": created_at,
            }
        )
        if target_items:
            tt.adjustments.append(
                {
                    "from_date": target_day.isoformat(),
                    "to_date": source_day.isoformat(),
                    "course": course_filter,
                    "courses": [
                        {
                            "name": item.course.name,
                            "start": item.start,
                            "end": item.end,
                        }
                        for item in target_items
                    ],
                    "created_at": created_at,
                }
            )
        return snapshot, len(target_items)

    async def _h_adjust(self, event, args: str):
        tt = self._own_tt(event)
        if not tt:
            yield event.plain_result("你还没有导入课表哦。")
            return
        text = args.strip()
        if text in ("清空", "清除", "重置"):
            count = len(tt.adjustments)
            tt.adjustments = []
            await self._save()
            yield event.plain_result(f"已清空 {count} 条调课记录。")
            return

        today = self._now().date()
        if text.startswith(("撤销", "撤回", "undo")):
            parts = text.split(maxsplit=1)
            if len(parts) == 2:
                target = self._parse_cmd_date(parts[1], today)
                if target is None:
                    yield event.plain_result("日期没看懂，示例：/课表 调课 撤销 9/10")
                    return
                key = target.isoformat()
                kept = [
                    item
                    for item in tt.adjustments
                    if item.get("from_date") != key and item.get("to_date") != key
                ]
                removed = len(tt.adjustments) - len(kept)
                if not removed:
                    yield event.plain_result(f"{target:%m/%d} 没有调课记录。")
                    return
                tt.adjustments = kept
                await self._save()
                yield event.plain_result(f"已撤销 {target:%m/%d} 相关的 {removed} 条调课记录。")
                return
            if not tt.adjustments:
                yield event.plain_result("没有可撤销的调课记录。")
                return
            tt.adjustments.pop()
            await self._save()
            yield event.plain_result("已撤销最近一条调课记录。")
            return

        tokens = [t for t in re.split(r"\s+", text) if t]
        swap = any(t in ("互换", "交换", "swap") for t in tokens)
        dates, rest = self._take_dates(
            [t for t in tokens if t not in ("互换", "交换", "swap")], today
        )
        if len(dates) < 2:
            yield event.plain_result(
                "格式：/课表 调课 [课程] 原日期 目标日期 [互换]\n"
                "例：/课表 调课 9/10 9/12（把 9/10 全部课移到 9/12）\n"
                "　　/课表 调课 高等数学 9/10 9/12\n"
                "　　/课表 调课 9/10 9/12 互换\n"
                "撤销：/课表 调课 撤销 [日期]"
            )
            return
        source_day, target_day = dates[0], dates[1]
        course_filter = " ".join(rest).strip()
        try:
            snapshot, swapped = self._add_adjustment(
                tt, source_day, target_day, course_filter, swap
            )
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return
        await self._save()
        names = "、".join(item["name"] for item in snapshot)
        msg = f"🔁 已把 {source_day:%m/%d} 的 {names} 调到 {target_day:%m/%d}。"
        if swapped:
            msg += f"\n同时把 {target_day:%m/%d} 的 {swapped} 节课换回 {source_day:%m/%d}。"
        yield event.plain_result(msg)

    async def _h_makeup(self, event, args: str):
        tt = self._own_tt(event)
        if not tt:
            yield event.plain_result("你还没有导入课表哦。")
            return
        today = self._now().date()
        text = args.strip()
        if not text:
            calendar = self._calendar_for(today)
            lines = ["📅 国务院节假日 / 调休"]
            if calendar is None:
                lines.append("还没有本地缓存，插件会在后台自动更新。")
            else:
                upcoming = [
                    (day, item)
                    for day, item in sorted(calendar.days.items())
                    if day >= (today - timedelta(days=3)).isoformat()
                ][:10]
                for day, item in upcoming:
                    mark = "放假" if item.get("off") else "补班"
                    lines.append(f"{day} {item.get('name') or ''} · {mark}")
                lines.append(f"数据来源：{calendar.source}")
            if tt.makeup_days:
                lines.append("\n你的补班设置：")
                for day, weekday in sorted(tt.makeup_days.items()):
                    lines.append(
                        f"{day} → 按周{'一二三四五六日'[weekday - 1]}课表"
                    )
            global_map = self._makeup_map()
            if global_map:
                lines.append("\n全局补班映射：")
                for day, weekday in sorted(global_map.items()):
                    lines.append(
                        f"{day} → 按周{'一二三四五六日'[weekday - 1]}课表"
                    )
            lines.append("\n设置补班：/课表 调休 9/27 周一")
            yield event.plain_result("\n".join(lines))
            return

        tokens = [t for t in re.split(r"\s+", text) if t]
        target = self._parse_cmd_date(tokens[0], today) if tokens else None
        if target is None:
            yield event.plain_result("日期没看懂，示例：/课表 调休 9/27 周一")
            return
        if len(tokens) >= 2 and tokens[1] in ("清", "取消", "删除"):
            if tt.makeup_days.pop(target.isoformat(), None) is None:
                yield event.plain_result(f"{target:%m/%d} 没有补班设置。")
            else:
                await self._save()
                yield event.plain_result(f"已取消 {target:%m/%d} 的补班设置。")
            return
        weekday = parse_day(tokens[1]) if len(tokens) >= 2 else None
        if weekday is None:
            yield event.plain_result("星期没看懂，示例：/课表 调休 9/27 周一")
            return
        tt.makeup_days[target.isoformat()] = weekday
        await self._save()
        yield event.plain_result(
            f"✅ {target:%Y/%m/%d} 将按周{'一二三四五六日'[weekday - 1]}课表上课。"
        )

    # ---------------------------------------------------------- WebUI 控制台

    def _register_web_apis(self) -> None:
        register = getattr(self.context, "register_web_api", None)
        if not callable(register):
            return
        routes = (
            ("overview", self._api_overview, ["GET"], "课表概览"),
            ("timetables", self._api_timetables, ["GET"], "课表列表"),
            ("timetable", self._api_timetable, ["GET"], "课表详情"),
            ("timetable/save", self._api_timetable_save, ["POST"], "新增/保存课表"),
            ("timetable/delete", self._api_timetable_delete, ["POST"], "删除课表"),
            ("settings", self._api_settings, ["GET"], "读取设置"),
            ("settings/save", self._api_settings_save, ["POST"], "保存设置"),
            ("holidays", self._api_holidays, ["GET"], "节假日"),
            ("holidays/refresh", self._api_holidays_refresh, ["POST"], "刷新节假日"),
            ("live", self._api_live, ["GET"], "当前状态"),
        )
        for route, handler, methods, desc in routes:
            try:
                register(
                    f"/{PLUGIN_NAME}/page/{route}", handler, methods, desc
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{PLUGIN_NAME}] 注册 Web API {route} 失败: {exc}")

    @staticmethod
    def _web_request():
        try:
            from astrbot.api.web import request
        except Exception:  # noqa: BLE001
            from quart import request
        return request

    @classmethod
    def _query(cls, name: str, default: str = "") -> str:
        req = cls._web_request()
        for attr in ("args", "query"):
            source = getattr(req, attr, None)
            if source is not None and name in source:
                return str(source.get(name) or default)
        return default

    @classmethod
    async def _json_body(cls, default=None):
        fallback = default if default is not None else {}
        req = cls._web_request()
        get_json = getattr(req, "get_json", None)
        if callable(get_json):
            try:
                payload = await get_json(silent=True)
            except TypeError:
                payload = await get_json()
            if payload is not None:
                return payload
        else:
            json_method = getattr(req, "json", None)
            if callable(json_method):
                try:
                    payload = await json_method(default=fallback)
                except TypeError:
                    payload = await json_method()
                if payload is not None:
                    return payload
        body = getattr(req, "body", None)
        return body if body is not None else fallback

    def _ok(self, data):
        return {"status": "ok", "data": data}

    def _err(self, message: str, status_code: int = 400):
        return {"status": "error", "message": message}, status_code

    def _tt_summary(self, key: str, tt: Timetable) -> dict:
        return {
            "key": key,
            "owner_id": tt.owner_id,
            "owner_name": tt.owner_name,
            "name": tt.name,
            "term_start": tt.term_start,
            "total_weeks": tt.total_weeks,
            "timezone": tt.timezone,
            "source": tt.source,
            "course_count": len(tt.courses),
            "groups": tt.groups,
            "notify_enabled": tt.notify_enabled,
            "leave_count": len(tt.leaves),
            "adjustment_count": len(tt.adjustments),
            "makeup_count": len(tt.makeup_days),
        }

    async def _api_overview(self):
        try:
            now = self._now()
            today = now.date()
            records = self.store.all()
            timetables = list(records.values())
            calendar = self._calendar_for(today)
            statuses = everyone_entries(
                timetables,
                now,
                holiday=calendar,
                statuses=[self._status_for(tt, now) for tt in timetables],
            )
            counts = {label: 0 for label in STATUS_LABEL.values()}
            for entry in statuses:
                counts[entry["label"]] = counts.get(entry["label"], 0) + 1
            return self._ok(
                {
                    "users": len(records),
                    "courses": sum(len(tt.courses) for tt in records.values()),
                    "groups": len(self._group_origins()),
                    "today": today.isoformat(),
                    "weekday": weekday_cn(today),
                    "status_counts": counts,
                    "holiday": {
                        "name": calendar.label(today) if calendar else "",
                        "off": calendar.is_off(today) if calendar else False,
                        "workday": calendar.is_workday(today) if calendar else False,
                        "source": calendar.source if calendar else "",
                    },
                    "config": {
                        "store_teacher": self._store_teacher(),
                        "store_location": self._store_location(),
                        "everyone_image": self._image_enabled("everyone_image"),
                        "help_image": self._image_enabled("help_image"),
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[{PLUGIN_NAME}] overview API 失败")
            return self._err(str(exc), 500)

    async def _api_timetables(self):
        try:
            query = self._query("q").strip().lower()
            rows = []
            for key, tt in self.store.all().items():
                if query and query not in " ".join(
                    (key, tt.owner_id, tt.owner_name, tt.name)
                ).lower():
                    continue
                rows.append(self._tt_summary(key, tt))
            rows.sort(key=lambda item: (item["owner_name"] or item["key"]))
            return self._ok({"items": rows, "total": len(rows)})
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc), 500)

    async def _api_timetable(self):
        try:
            key = self._query("key").strip()
            tt = self.store.get(key)
            if tt is None:
                return self._err("课表不存在", 404)
            return self._ok({"key": key, **tt.to_dict()})
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc), 500)

    def _course_from_api(self, raw: dict) -> Course | None:
        if not isinstance(raw, dict):
            return None
        name = str(raw.get("name") or "").strip()
        day = _safe_int(raw.get("day"), 0)
        if not name or not 1 <= day <= 7:
            return None
        weeks = parse_weeks(raw.get("weeks")) or []
        start = parse_time(raw.get("start")) or ""
        end = parse_time(raw.get("end")) or ""
        start_section = _safe_int(raw.get("start_section"), 0) or None
        end_section = _safe_int(raw.get("end_section"), 0) or None
        if start_section and not end_section:
            end_section = start_section
        if not start and not start_section:
            return None
        if start and not end:
            return None
        return Course(
            name=name,
            day=day,
            weeks=weeks,
            start=start,
            end=end,
            start_section=start_section,
            end_section=end_section,
            location=str(raw.get("location") or "").strip(),
            teacher=str(raw.get("teacher") or "").strip(),
        )

    @staticmethod
    def _clean_leaves(raw) -> list[dict]:
        result = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            start = parse_date(item.get("start"))
            if start is None:
                continue
            end = parse_date(item.get("end")) or start
            if end < start:
                start, end = end, start
            result.append(
                {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "reason": str(item.get("reason") or "")[:120],
                }
            )
        return result

    @staticmethod
    def _clean_adjustments(raw) -> list[dict]:
        result = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            source = parse_date(item.get("from_date"))
            target = parse_date(item.get("to_date"))
            if source is None or target is None or source == target:
                continue
            courses = []
            for course in item.get("courses") or []:
                if not isinstance(course, dict):
                    continue
                name = str(course.get("name") or "").strip()
                start = parse_time(course.get("start")) or ""
                end = parse_time(course.get("end")) or ""
                if name and start and end:
                    courses.append({"name": name, "start": start, "end": end})
            if not courses:
                continue
            result.append(
                {
                    "from_date": source.isoformat(),
                    "to_date": target.isoformat(),
                    "course": str(item.get("course") or "").strip(),
                    "courses": courses,
                    "created_at": str(item.get("created_at") or ""),
                }
            )
        return result

    @staticmethod
    def _clean_makeup_days(raw) -> dict[str, int]:
        result = {}
        for key, value in (raw.items() if isinstance(raw, dict) else []):
            day = parse_date(key)
            weekday = _safe_int(value, 0)
            if day is not None and 1 <= weekday <= 7:
                result[day.isoformat()] = weekday
        return result

    @staticmethod
    def _clean_groups(raw) -> tuple[list[str], dict[str, str]]:
        groups = []
        for item in raw if isinstance(raw, list) else []:
            group_id = str(item or "").strip()
            if group_id and group_id not in groups:
                groups.append(group_id)
        return groups[:50], {}

    async def _api_timetable_save(self):
        try:
            payload = await self._json_body()
            if not isinstance(payload, dict):
                return self._err("请求内容不是对象")
            owner_id = str(payload.get("owner_id") or "").strip()
            platform = str(payload.get("platform") or "aiocqhttp").strip() or "aiocqhttp"
            key = str(payload.get("key") or "").strip()
            if not key:
                if not owner_id:
                    return self._err("owner_id 不能为空")
                key = f"{platform}:{owner_id}"
            if len(key) > 128 or any(ch in key for ch in "\r\n\t"):
                return self._err("key 不合法")
            old = self.store.get(key)
            term = parse_date(payload.get("term_start"))
            if term is None:
                return self._err("开学日期格式不对，示例：2026-09-07")
            total_weeks = max(1, min(_safe_int(payload.get("total_weeks"), 20), 60))
            raw_courses = payload.get("courses")
            if not isinstance(raw_courses, list):
                raw_courses = []
            courses = [
                course
                for course in (self._course_from_api(raw) for raw in raw_courses)
                if course is not None
            ]
            # 在副本上改，校验失败时不会污染内存里的旧课表
            tt = Timetable.from_dict(old.to_dict()) if old else Timetable(
                owner_id=owner_id or key.split(":", 1)[-1]
            )
            tt.sections = parse_section_times(payload.get("sections")) or tt.sections or (
                self._fallback_sections()
            )
            if not self._store_teacher():
                for course in courses:
                    course.teacher = ""
            if not self._store_location():
                for course in courses:
                    course.location = ""
            if owner_id:
                tt.owner_id = owner_id
            tt.name = str(payload.get("name") or tt.name or "我的课表")
            tt.owner_name = str(payload.get("owner_name") or tt.owner_name)
            tt.term_start = monday_of(term).isoformat()
            tt.total_weeks = total_weeks
            tt.timezone = str(payload.get("timezone") or tt.timezone or self._tz)
            tt.source = str(payload.get("source") or tt.source or "控制面板")
            tt.courses = courses
            tt.invalidate_index()

            if "leaves" in payload:
                tt.leaves = self._clean_leaves(payload.get("leaves"))
            if "adjustments" in payload:
                tt.adjustments = self._clean_adjustments(payload.get("adjustments"))
            if "makeup_days" in payload:
                tt.makeup_days = self._clean_makeup_days(payload.get("makeup_days"))
            if "groups" in payload:
                tt.groups, _ = self._clean_groups(payload.get("groups"))
            if "group_origins" in payload:
                raw_origins = payload.get("group_origins")
                tt.group_origins = {
                    str(group): str(origin)
                    for group, origin in (
                        raw_origins.items() if isinstance(raw_origins, dict) else []
                    )
                    if str(group).strip() and str(origin).strip()
                }
                if tt.groups:
                    tt.group_origins = {
                        group: origin
                        for group, origin in tt.group_origins.items()
                        if group in tt.groups
                    }
            if "groups" in payload:
                tt.group_origins = {
                    group: origin
                    for group, origin in tt.group_origins.items()
                    if group in tt.groups
                }

            for raw in payload.get("new_adjustments") or []:
                if not isinstance(raw, dict):
                    continue
                source = parse_date(raw.get("from_date")) or self._parse_cmd_date(
                    str(raw.get("from_date") or ""), self._now().date()
                )
                target = parse_date(raw.get("to_date")) or self._parse_cmd_date(
                    str(raw.get("to_date") or ""), self._now().date()
                )
                if source is None or target is None:
                    return self._err("调课日期格式不对")
                try:
                    self._add_adjustment(
                        tt,
                        source,
                        target,
                        str(raw.get("course") or "").strip(),
                        bool(raw.get("swap")),
                    )
                except ValueError as exc:
                    return self._err(str(exc))

            self.store.put(key, tt)
            await self._save()
            return self._ok(self._tt_summary(key, tt))
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[{PLUGIN_NAME}] 保存课表失败")
            return self._err(str(exc), 500)

    async def _api_timetable_delete(self):
        try:
            payload = await self._json_body()
            key = str((payload or {}).get("key") or "").strip()
            if not key:
                return self._err("key 不能为空")
            deleted = self.store.delete(key)
            if deleted is None:
                return self._err("课表不存在", 404)
            await self._save()
            return self._ok({"deleted": key})
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc), 500)

    _SETTING_KEYS = (
        "store_teacher",
        "store_location",
        "everyone_image",
        "help_image",
        "image_width",
        "everyone_page_size",
        "everyone_hide_idle_over",
        "showcase_enabled",
        "showcase_time",
        "showcase_min_minutes",
        "showcase_min_users",
        "recap_enabled",
        "recap_weekday",
        "recap_time",
        "recap_mode",
        "recap_top",
        "holiday_enabled",
        "holiday_api_url",
        "holiday_cache_hours",
        "makeup_weekday_map",
    )

    def _settings_view(self) -> dict:
        return {key: self._cfg(key, None) for key in self._SETTING_KEYS}

    async def _api_settings(self):
        try:
            return self._ok(self._settings_view())
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc), 500)

    async def _api_settings_save(self):
        try:
            payload = await self._json_body()
            if not isinstance(payload, dict):
                return self._err("请求内容不是对象")
            bool_keys = {
                "store_teacher",
                "store_location",
                "everyone_image",
                "help_image",
                "showcase_enabled",
                "recap_enabled",
                "holiday_enabled",
            }
            int_keys = {
                "image_width",
                "everyone_page_size",
                "everyone_hide_idle_over",
                "showcase_min_minutes",
                "showcase_min_users",
                "recap_weekday",
                "recap_top",
                "holiday_cache_hours",
            }
            for key in self._SETTING_KEYS:
                if key not in payload:
                    continue
                value = payload[key]
                if key in bool_keys:
                    value = bool(value)
                elif key in int_keys:
                    value = _safe_int(value, _safe_int(self._cfg(key, 0), 0))
                elif key == "recap_mode":
                    value = str(value) if str(value) in ("week", "month") else "week"
                else:
                    value = str(value or "")
                try:
                    self.config[key] = value
                except Exception:  # noqa: BLE001
                    setattr(self.config, key, value)
            save_config = getattr(self.config, "save_config", None)
            if callable(save_config):
                save_config()
            self._day_cache.clear()
            self._status_cache.clear()
            self._entries_cache.clear()
            self._makeup_text = None
            return self._ok(self._settings_view())
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc), 500)

    async def _api_holidays(self):
        try:
            year = _safe_int(self._query("year"), self._now().year)
            calendar = self._holidays.get(year) or self._load_holiday_cache(year)
            if calendar is None:
                return self._ok(
                    {"year": year, "source": "", "updated_at": "", "days": []}
                )
            days = [
                {
                    "date": day,
                    "name": str(item.get("name") or ""),
                    "off": bool(item.get("off")),
                }
                for day, item in sorted(calendar.days.items())
            ]
            return self._ok(
                {
                    "year": year,
                    "source": calendar.source,
                    "updated_at": calendar.updated_at,
                    "days": days,
                }
            )
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc), 500)

    async def _api_holidays_refresh(self):
        try:
            payload = await self._json_body()
            year = _safe_int((payload or {}).get("year"), self._now().year)
            await self._refresh_holidays(year, force=True)
            calendar = self._holidays.get(year) or self._load_holiday_cache(year)
            if calendar is None:
                return self._err("节假日刷新失败，请检查网络或接口地址", 502)
            return self._ok(
                {
                    "year": year,
                    "source": calendar.source,
                    "updated_at": calendar.updated_at,
                    "count": len(calendar.days),
                }
            )
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc), 500)

    async def _api_live(self):
        try:
            group_id = self._query("group").strip()
            now = self._now()
            timetables = (
                timetables_of_group(self.store.all().values(), group_id)
                if group_id
                else list(self.store.all().values())
            )
            entries = self._cached_entries(
                "api",
                timetables,
                now,
                hide_idle_over=self._hide_idle_over(),
            )
            return self._ok({"items": entries, "total": len(entries)})
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc), 500)

    # ---------------------------------------------------------- 提醒循环

    async def _notify_loop(self):
        while True:
            try:
                if self._cfg("notify_enabled", True):
                    await self._check_notifications()
                await self._check_scheduled_broadcasts(self._now())
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[{PLUGIN_NAME}] 提醒检查失败: {e}")
            interval = 60
            try:
                interval = max(30, min(int(self._cfg("notify_check_seconds", 60)), 600))
            except (TypeError, ValueError):
                pass
            await asyncio.sleep(interval)

    async def _check_notifications(self):
        now = self._now()
        changed = False
        for _key, tt in list(self.store.all().items()):
            if not tt.notify_enabled or tt.notify_minutes is None or not tt.notify_origin:
                continue
            tz = get_tz(tt.timezone)
            local_now = now.astimezone(tz)
            today = local_now.date()
            now_min = local_now.hour * 60 + local_now.minute
            if tt.leave_on(today):
                continue
            if tt.notified_date != today.isoformat():
                tt.notified_date = today.isoformat()
                tt.notified_keys = []
                changed = True

            for course, start, end in day_courses(
                tt, today, self._calendar_for(today)
            ):
                start_min = minutes_of(start)
                if start_min is None:
                    continue
                delta = start_min - now_min
                if delta < 0 or delta > tt.notify_minutes:
                    continue
                notify_key = f"{course.name}|{start}"
                if notify_key in tt.notified_keys:
                    continue
                note = tt.note_for(course.name)
                lines = [
                    "⏰ 上课提醒",
                    f"{course.name} {start}-{end}",
                ]
                if course.location:
                    lines.append(f"地点：{course.location}")
                if tt.notify_minutes == 0:
                    lines.append("现在开始上课")
                else:
                    lines.append(f"{fmt_minutes(delta)}后开始")
                if note:
                    lines.append(f"📝 {note}")
                try:
                    await self.context.send_message(
                        tt.notify_origin, MessageChain().message("\n".join(lines))
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[{PLUGIN_NAME}] 提醒发送失败 {_key}: {e}")
                    continue
                tt.notified_keys.append(notify_key)
                changed = True
        if changed:
            await self._save()
