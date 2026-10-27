"""课表业务逻辑：周次计算、当日课程、状态、排行、日期解析。"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Sequence

from .model import Course, Section, Timetable
from .normalize import (
    WEEKDAY_CN,
    describe_weeks,
    fmt_date_cn,
    fmt_minutes,
    get_tz,
    minutes_of,
    monday_of,
    parse_date,
    week_of,
)

STATUS_LABEL = {
    0: "分身中",
    1: "进行中",
    2: "翘课中",
    3: "下一节",
    4: "已结束",
    5: "无课程",
    6: "请假中",
}
STATUS_EMOJI = {0: "🟪", 1: "🟩", 2: "🟨", 3: "🟦", 4: "⬜", 5: "⬛", 6: "🟧"}

# 群友列表排序：翘课/请假优先，其次正在上课，再到下一节，无课最后
STATUS_PRIORITY = {2: 0, 6: 0, 1: 1, 0: 1, 3: 2, 4: 4, 5: 5}

RANK_MODE_LABEL = {"today": "今日", "week": "本周", "month": "本月", "term": "本学期"}


def resolve_time(course: Course, sections: dict[int, Section]) -> tuple[str, str]:
    """优先按节次 + 当前作息计算时间，没有节次时用导入时的时间。"""
    start, end = course.start, course.end
    if course.start_section is not None:
        first = sections.get(int(course.start_section))
        if first:
            start = first.start
    if course.end_section is not None:
        last = sections.get(int(course.end_section))
        if last:
            end = last.end
    return start, end


def week_state(tt: Timetable, d: date) -> tuple[int, str]:
    """返回 (第几周, "before"|"during"|"after")。"""
    term = tt.term_date()
    week = week_of(term, d)
    if week < 1:
        return week, "before"
    if week > max(tt.total_weeks, 1):
        return week, "after"
    return week, "during"


def courses_on(tt: Timetable, d: date) -> list[Course]:
    week, state = week_state(tt, d)
    if state != "during":
        return []
    weekday = d.weekday() + 1
    return [c for c in tt.weekday_courses(weekday) if c.weeks and week in c.weeks]


@dataclass
class DayItem:
    course: Course
    start: str
    end: str
    moved: bool = False


def _snapshot_course(raw: dict[str, Any], day: int) -> Course | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    start = str(raw.get("start") or "").strip()
    end = str(raw.get("end") or "").strip()
    if not name or not start or not end:
        return None
    return Course(
        name=name,
        day=day,
        weeks=[],
        start=start,
        end=end,
        location=str(raw.get("location") or ""),
        teacher=str(raw.get("teacher") or ""),
    )


def day_entries(
    tt: Timetable,
    d: date,
    holiday=None,
) -> list[DayItem]:
    """当天课程：应用法定节假日、调休补班、请假外的调课记录。"""
    week, state = week_state(tt, d)
    if state != "during":
        return []

    weekday = d.weekday() + 1
    if holiday is not None and holiday.is_off(d):
        base: list[Course] = []
    else:
        if holiday is not None and holiday.is_workday(d):
            weekday = tt.makeup_weekday(
                d, getattr(holiday, "makeup_weekdays", None)
            )
        base = [c for c in tt.weekday_courses(weekday) if c.weeks and week in c.weeks]

    # 从这一天调出的课程
    moved_out = tt.adjustments_from(d)
    if moved_out:
        removed_all = any(not str(a.get("course") or "").strip() for a in moved_out)
        removed_names = {
            str(a.get("course") or "").strip()
            for a in moved_out
            if str(a.get("course") or "").strip()
        }
        if removed_all:
            base = []
        elif removed_names:
            base = [c for c in base if c.name not in removed_names]

    sec_map = tt.section_map()
    items: list[DayItem] = []
    for course in base:
        start, end = resolve_time(course, sec_map)
        if start and end:
            items.append(DayItem(course, start, end))

    # 调到这一天的课程，使用调课时保存的快照
    for adjustment in tt.adjustments_to(d):
        for raw in adjustment.get("courses") or []:
            course = _snapshot_course(raw, weekday)
            if course:
                items.append(DayItem(course, course.start, course.end, moved=True))

    items.sort(key=lambda item: (minutes_of(item.start) or 0, item.course.name))
    return items


def day_courses(
    tt: Timetable, d: date, holiday=None
) -> list[tuple[Course, str, str]]:
    """当天课程（含最终时间），按开始时间排序。"""
    return [(item.course, item.start, item.end) for item in day_entries(tt, d, holiday)]


def course_line(course: Course, start: str, end: str, show_teacher: bool = False) -> str:
    extras = []
    if course.location:
        extras.append(course.location)
    if show_teacher and course.teacher:
        extras.append(course.teacher)
    suffix = f" @{' · '.join(extras)}" if extras else ""
    return f"{start}-{end} {course.name}{suffix}"


def format_day(
    tt: Timetable,
    d: date,
    *,
    label: str = "",
    show_teacher: bool = False,
    today: date | None = None,
    holiday=None,
) -> str:
    today = today or d
    week, state = week_state(tt, d)
    prefix = "📚 "
    owner = f"{label}｜" if label else ""
    day_label = f"{fmt_date_cn(d)} {weekday_cn(d)}"
    holiday_name = holiday.label(d) if holiday is not None else ""
    holiday_off = bool(holiday is not None and holiday.is_off(d))
    holiday_work = bool(holiday is not None and holiday.is_workday(d))

    if state == "before":
        term = tt.term_date()
        return (
            f"{prefix}{owner}{day_label}\n"
            f"学期还没开始（第 1 周从 {fmt_date_cn(term)} 起）"
        )
    if state == "after":
        return (
            f"{prefix}{owner}{day_label}\n"
            f"本学期课程已结束（共 {tt.total_weeks} 周）"
        )

    extra = ""
    if holiday_off:
        extra = f" · 🏮 {holiday_name or '法定假日'}放假"
    elif holiday_work:
        makeup_day = tt.makeup_weekday(
            d, getattr(holiday, "makeup_weekdays", None)
        )
        extra = f" · 调休补班（按{WEEKDAY_CN[makeup_day - 1]}课表）"
    header = f"{prefix}{owner}{day_label} · 第 {week} 周（共 {tt.total_weeks} 周）{extra}"

    leave = tt.leave_on(d)
    items = day_entries(tt, d, holiday)

    def moved_out_notes() -> list[str]:
        notes = []
        for adjustment in tt.adjustments_from(d):
            target = str(adjustment.get("to_date") or "")
            names = [
                str(c.get("name") or "")
                for c in (adjustment.get("courses") or [])
                if isinstance(c, dict) and c.get("name")
            ]
            if target:
                what = "、".join(names) if names else "当天课程"
                notes.append(f"↪ {what}已调至 {target}")
        return notes

    if not items:
        if holiday_off:
            return header + f"\n{('今天' if d == today else '这天')}放假～"
        day_word = "今天" if d == today else "这天"
        return "\n".join([header, f"{day_word}没有课～"] + moved_out_notes())

    lines = [header]
    if leave:
        reason = str(leave.get("reason") or "")
        lines.append(f"📝 已请假{f'：{reason}' if reason else ''}")
    for item in items:
        line = course_line(item.course, item.start, item.end, show_teacher)
        marks = []
        if item.moved:
            marks.append("调课")
        if leave:
            marks.append("请假")
        if marks:
            line += f"（{'·'.join(marks)}）"
        lines.append(line)

    lines.extend(moved_out_notes())
    return "\n".join(lines)

def weekday_cn(d: date) -> str:
    return WEEKDAY_CN[d.weekday()]


def status_now(
    tt: Timetable,
    now: datetime | None = None,
    *,
    show_teacher: bool = False,
    holiday=None,
    entries: Sequence[DayItem] | None = None,
) -> dict:
    """返回当前在上课状态；entries 可由调用方复用当天快照，避免重复计算。"""
    tz = get_tz(tt.timezone)
    now = now or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    now = now.astimezone(tz)
    today = now.date()
    now_min = now.hour * 60 + now.minute
    items = entries if entries is not None else day_entries(tt, today, holiday)
    tuples = [(item.course, item.start, item.end) for item in items]
    starts = [minutes_of(start) or 0 for _course, start, _end in tuples]
    abandoned = tt.abandon_date == today.isoformat()
    leave = tt.leave_on(today)
    holiday_off = bool(holiday is not None and holiday.is_off(today))

    result = {"type": 5, "order": (86400, 0), "main": "", "sub": ""}

    if leave:
        in_class = [
            (c, s, e)
            for c, s, e in tuples
            if (minutes_of(s) or 0) <= now_min < (minutes_of(e) or 0)
        ]
        reason = str(leave.get("reason") or "")
        if in_class:
            course, start, end = in_class[0]
            result.update(
                type=6,
                order=(0, 0),
                main=course.name,
                sub=f"{start}-{end}（请假）",
            )
        else:
            result.update(type=6, order=(0, 0), main="今日请假", sub=reason)
        return result

    if not items:
        if holiday_off:
            name = holiday.label(today) or "法定假日"
            result.update(type=5, order=(5, 0), main=f"今日放假（{name}）", sub="")
        else:
            result.update(type=5, order=(5, 0), main="今日无课程", sub="")
        return result

    in_class = [
        (c, s, e)
        for c, s, e in tuples
        if (minutes_of(s) or 0) <= now_min < (minutes_of(e) or 0)
    ]
    if in_class:
        if abandoned:
            course, start, end = in_class[0]
            remain = max((minutes_of(end) or now_min) - now_min, 0)
            result.update(
                type=2,
                order=(2, remain),
                main=course.name,
                sub=f"{start}-{end}（剩余 {fmt_minutes(remain)}）",
            )
        elif len(in_class) == 1:
            course, start, end = in_class[0]
            remain = max((minutes_of(end) or now_min) - now_min, 0)
            result.update(
                type=1,
                order=(1, remain),
                main=course.name,
                sub=f"{start}-{end}（剩余 {fmt_minutes(remain)}）",
            )
        else:
            names = " / ".join(c.name for c, _, _ in in_class)
            detail = " / ".join(f"{c.name[:1]}{s}-{e}" for c, s, e in in_class)
            result.update(type=0, order=(0, 0), main=names, sub=detail)
        return result

    upcoming_index = bisect_right(starts, now_min)
    if upcoming_index < len(tuples):
        course, start, end = tuples[upcoming_index]
        wait = max((minutes_of(start) or now_min) - now_min, 0)
        result.update(
            type=3,
            order=(3, wait),
            main=course.name,
            sub=f"{start}-{end}（{fmt_minutes(wait)}后）",
        )
        return result

    total = sum((minutes_of(e) or 0) - (minutes_of(s) or 0) for _, s, e in tuples)
    result.update(
        type=4,
        order=(4, -total),
        main="今日课程已上完",
        sub=f"共计 {total / 60:.1f} 小时",
    )
    return result

def everyone_entries(
    timetables: Sequence[Timetable],
    now: datetime | None = None,
    *,
    show_teacher: bool = False,
    holiday=None,
    hide_idle_over: int = 0,
    statuses: Sequence[dict | None] | None = None,
) -> list[dict]:
    """把群友状态整理成结构化列表，文本和图片共用同一份排序。

    排序：翘课/请假 → 正在上课 → 下一节 → 已结束 → 无课程。
    人数超过 hide_idle_over 时隐藏无课程的人，避免图片页数过多。
    statuses 可传入调用方按用户缓存好的状态，避免多个群重复计算。
    """
    cached_statuses = statuses or ()
    entries = []
    for index, tt in enumerate(timetables):
        status = (
            cached_statuses[index]
            if index < len(cached_statuses)
            else None
        )
        if status is None:
            status = status_now(tt, now, show_teacher=show_teacher, holiday=holiday)
        entries.append(
            {
                "name": tt.owner_name or tt.owner_id,
                "owner_id": tt.owner_id,
                "type": status["type"],
                "label": STATUS_LABEL.get(status["type"], ""),
                "main": status["main"],
                "sub": status["sub"],
                "order": status["order"],
            }
        )
    entries.sort(key=lambda item: (STATUS_PRIORITY.get(item["type"], 9), item["order"]))
    if hide_idle_over and len(entries) > hide_idle_over:
        active = [entry for entry in entries if entry["type"] != 5]
        if active:
            entries = active
    return entries


def everyone_report(
    timetables: Sequence[Timetable],
    now: datetime | None = None,
    *,
    show_teacher: bool = False,
    limit: int | None = 30,
    holiday=None,
    hide_idle_over: int = 0,
) -> str:
    entries = everyone_entries(
        timetables,
        now,
        show_teacher=show_teacher,
        holiday=holiday,
        hide_idle_over=hide_idle_over,
    )
    if not entries:
        return ""
    total = len(entries)
    if limit and total > limit:
        entries = entries[:limit]

    lines = ["“群友在上什么课？”"]
    for entry in entries:
        emoji = STATUS_EMOJI.get(entry["type"], "")
        line = f"{emoji} {entry['name']} · {entry['label']}"
        if entry["main"]:
            line += f"\n　{entry['main']}"
        if entry["sub"]:
            line += f"\n　{entry['sub']}"
        lines.append(line)
    if total > len(entries):
        lines.append(f"…还有 {total - len(entries)} 人")
    return "\n".join(lines)


def rank_hours(
    tt: Timetable,
    mode: str,
    now: datetime | None = None,
    *,
    holiday=None,
) -> float:
    tz = get_tz(tt.timezone)
    now = now or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    today = now.astimezone(tz).date()
    term = tt.term_date()
    if term is None:
        term = today - timedelta(days=today.weekday())

    if mode == "today":
        days = [today]
    elif mode == "month":
        start = today.replace(day=1)
        days = [start + timedelta(days=i) for i in range((today - start).days + 1)]
    elif mode == "term":
        days = [term + timedelta(days=i) for i in range(max(tt.total_weeks, 1) * 7)]
    else:
        monday = today - timedelta(days=today.weekday())
        days = [monday + timedelta(days=i) for i in range(7)]

    total = 0
    for day in days:
        for _course, start, end in day_courses(tt, day, holiday):
            total += max((minutes_of(end) or 0) - (minutes_of(start) or 0), 0)
    return total / 60.0

def format_rank(
    timetables: Sequence[Timetable],
    mode: str,
    now: datetime | None = None,
    *,
    full: bool = False,
    title: str = "",
    holiday=None,
) -> str:
    mode = mode if mode in RANK_MODE_LABEL else "week"
    entries = []
    for tt in timetables:
        hours = rank_hours(tt, mode, now, holiday=holiday)
        if hours > 0:
            entries.append((hours, tt))
    if not entries:
        return f"{RANK_MODE_LABEL[mode]}大家都没有课哦…"

    entries.sort(key=lambda item: item[0], reverse=True)
    if not full:
        entries = entries[:9]

    digits = {"today": 2, "week": 1, "month": 1, "term": 0}[mode]
    lines = [title or f"{RANK_MODE_LABEL[mode]}课程时长榜"]
    prev_hours = None
    prev_rank = 0
    for index, (hours, tt) in enumerate(entries):
        shown = f"{hours:.{digits}f}"
        if shown != prev_hours:
            prev_rank = index + 1
            prev_hours = shown
        name = tt.owner_name or tt.owner_id
        lines.append(f"#{prev_rank} {shown}小时 {name}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 查询日期


def parse_query_date(text: str, today: date | None = None) -> date | None:
    """解析 “明天 / 周一 / 下周三 / 9/1 / 2026-09-01”。无法识别返回 None。"""
    today = today or date.today()
    t = (text or "").strip()
    if not t:
        return today
    lowered = t.lower()
    if lowered in ("今天", "今日", "today"):
        return today
    if lowered in ("明天", "明日", "tomorrow"):
        return today + timedelta(days=1)
    if lowered in ("后天",):
        return today + timedelta(days=2)
    if lowered in ("昨天", "昨日", "yesterday"):
        return today - timedelta(days=1)
    if lowered in ("大后天",):
        return today + timedelta(days=3)

    m = re.fullmatch(r"(下|本|这)?(?:周|星期|礼拜)([一二三四五六日天1-7])", t)
    if m:
        prefix, day_text = m.group(1), m.group(2)
        day_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
        weekday = day_map.get(day_text) or int(day_text)
        if prefix == "下":
            return monday_of(today) + timedelta(weeks=1, days=weekday - 1)
        if prefix in ("本", "这"):
            return monday_of(today) + timedelta(days=weekday - 1)
        # 不写前缀时取“最近一次”，今天就是该星期几则取今天
        return today + timedelta(days=(weekday - today.isoweekday()) % 7)

    return parse_date(t)


def group_visible(tt: Timetable, group_id: str) -> bool:
    if not group_id:
        return False
    return group_id in tt.groups


def weeks_text(course: Course) -> str:
    return describe_weeks(course.weeks)


def timetables_of_group(timetables: Iterable[Timetable], group_id: str) -> list[Timetable]:
    return [tt for tt in timetables if group_visible(tt, group_id)]
