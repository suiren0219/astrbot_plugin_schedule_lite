"""iCalendar (.ics) 导入。

教务系统导出的 .ics 差异很大，这里实现一个够用的 RFC 5545 子集：
- DTSTART/DTEND（含 TZID、UTC、浮动时间、全天事件）
- RRULE：WEEKLY（INTERVAL/BYDAY/UNTIL/COUNT）、DAILY、MONTHLY、YEARLY
- EXDATE / RDATE / RECURRENCE-ID 单次改期
- DURATION
不支持的复杂组合会跳过并给出提示，不会整份失败。
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..model import Course, Section
from ..normalize import (
    get_tz,
    monday_of,
    parse_day,
    parse_section_times,
    parse_weeks,
    week_of,
)
from .base import ImportFailure, ImportResult, build_result

MAX_OCCURRENCES = 3000
MAX_WEEKS_WITHOUT_END = 25


# ---------------------------------------------------------------- 行解析


def _unfold(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    lines: list[str] = []
    for line in text.split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _parse_line(line: str) -> tuple[str, dict[str, str], str] | None:
    if not line:
        return None
    sep = line.find(":")
    if sep < 0:
        # 少数国产 App 会把属性分隔符写成全角冒号（“周次：1-16周”）
        sep = line.find("：")
    if sep < 0:
        return None
    left, value = line[:sep], line[sep + 1 :]
    chunks = left.split(";")
    name = chunks[0].strip().upper()
    params: dict[str, str] = {}
    for chunk in chunks[1:]:
        if "=" in chunk:
            key, val = chunk.split("=", 1)
            params[key.strip().upper()] = val.strip().strip('"')
    return name, params, value


def _unescape(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def parse_events(text: str) -> list[dict[str, list[tuple[dict[str, str], str]]]]:
    events: list[dict[str, list[tuple[dict[str, str], str]]]] = []
    current: dict[str, list[tuple[dict[str, str], str]]] | None = None
    for line in _unfold(text):
        parsed = _parse_line(line)
        if not parsed:
            continue
        name, params, value = parsed
        upper_value = value.strip().upper()
        if name == "BEGIN" and upper_value == "VEVENT":
            current = {}
        elif name == "END" and upper_value == "VEVENT":
            if current is not None:
                events.append(current)
            current = None
        elif current is not None:
            current.setdefault(name, []).append((params, value))
    return events


def _first(ev: dict, name: str) -> tuple[dict[str, str], str] | None:
    items = ev.get(name) or []
    return items[0] if items else None


def _value(ev: dict, name: str) -> str:
    item = _first(ev, name)
    return item[1].strip() if item else ""


# ---------------------------------------------------------------- 课程元信息

_LABEL_PATTERN = "任课教师|授课教师|教师|老师|上课地点|地点|教室|周次|周数|上课周次"
_WEEK_TOKEN_RE = re.compile(
    r"(?:第\s*)?\d+(?:\s*[-~至－—]\s*\d+)?"
    r"(?:\s*[,，、]\s*\d+(?:\s*[-~至－—]\s*\d+)?)*\s*周"
    r"(?:\s*[（(]?\s*[单双]\s*周?\s*[)）]?)?"
)
_NON_TEACHER_WORDS = {
    "上", "下", "读写", "听说", "实验", "理论", "实践", "研讨", "习题", "复习",
    "线上", "线下", "必修", "选修", "基础", "进阶", "提高", "一", "二", "三", "四",
}

# 某些 App 生成的 ICS 会把 DESCRIPTION 里的换行写成真换行，导致
# “周次：1-8周”变成一条独立属性；这里把这些字段回收进描述文本。
_EXTRA_DESC_KEYS = (
    "周次", "周数", "上课周次", "教师", "任课教师", "授课教师", "老师",
    "地点", "教室", "上课地点",
)


def _extra_description(ev: dict) -> str:
    parts = []
    for key in _EXTRA_DESC_KEYS:
        for _params, value in ev.get(key, []):
            value = value.strip()
            if value:
                parts.append(f"{key}:{value}")
    return "\n".join(parts)


def _calendar_prop(text: str, key: str) -> str:
    """取 VCALENDAR 级属性，如 X-WR-CALNAME / X-WR-TIMEZONE。"""
    for line in _unfold(text):
        parsed = _parse_line(line)
        if parsed and parsed[0] == key.upper():
            return parsed[2].strip()
    return ""


def _field_value(text: str, keys: list[str]) -> str:
    """从 DESCRIPTION 里取 “教师：张三”“地点：A101” 这类字段。"""
    if not text:
        return ""
    for key in keys:
        m = re.search(
            rf"(?:{key})\s*[:：]\s*(.+?)"
            rf"(?=[，,;；\s]*(?:{_LABEL_PATTERN})\s*[:：]|[\n\r]|$)",
            text,
        )
        if m:
            value = m.group(1).strip(" \t,，;；")
            if value:
                return value
    return ""


def _extract_weeks(text: str) -> list[int]:
    if not text:
        return []
    for m in _WEEK_TOKEN_RE.finditer(text):
        weeks = parse_weeks(m.group(0))
        if weeks:
            return weeks
    return []


def _clean_name(summary: str) -> str:
    name = (summary or "").replace("\n", " ").strip()
    name = _WEEK_TOKEN_RE.sub("", name)
    name = re.sub(r"[（(\[【]\s*[)）\]】]", "", name)
    name = re.sub(r"^\s*[\[【][^\]】]{1,8}[\]】]\s*", "", name)
    name = re.sub(r"\s{2,}", " ", name).strip(" -—,，;；")
    return name or (summary or "").strip()


_LOCATION_TEACHER_RE = re.compile(
    r"^(?P<loc>.+?)\s+(?P<people>[一-龥]{2,4}"
    r"(?:\s*[,，、/]\s*[一-龥]{2,4}){0,3})$"
)
_TEACHING_PLACES = (
    "教学", "教室", "实验室", "运动场", "操场", "场馆", "楼", "室", "场", "中心",
)


def _split_location_teacher(location: str) -> tuple[str, str]:
    """Nexio 等 App 把教室和教师写在同一个 LOCATION 里：`A101 张老师,李老师`。"""
    loc = (location or "").strip()
    m = _LOCATION_TEACHER_RE.match(loc)
    if not m:
        return loc, ""
    place = m.group("loc").strip()
    if not place:
        return loc, ""
    if not (re.search(r"[A-Za-z0-9]", place) or place.endswith(_TEACHING_PLACES)):
        return loc, ""
    people = re.sub(r"\s*[,，、/]\s*", "、", m.group("people").strip())
    return place, people


def _extract_meta(ev: dict) -> dict:
    """兼容不同 App 的 ICS：课程名可能在 SUMMARY，教师/地点/周次在 DESCRIPTION。"""
    summary = _unescape(_value(ev, "SUMMARY"))
    desc = "\n".join(
        part
        for part in (_unescape(_value(ev, "DESCRIPTION")), _extra_description(ev))
        if part
    )
    location = _unescape(_value(ev, "LOCATION")).strip()

    teacher = ""
    if desc:
        teacher = _field_value(desc, ["任课教师", "授课教师", "教师", "老师"])
        if not location:
            location = _field_value(desc, ["上课地点", "地点", "教室"])

    weeks: list[int] = []
    if desc:
        weeks_field = _field_value(desc, ["周次", "周数", "上课周次"])
        if weeks_field:
            weeks = parse_weeks(weeks_field)
        if not weeks:
            weeks = _extract_weeks(desc)
    if not weeks:
        weeks = _extract_weeks(summary)

    name = _clean_name(summary)
    if not location and "@" in name:
        left, _, right = name.rpartition("@")
        right = right.strip()
        if left.strip() and right and len(right) <= 30:
            name, location = left.strip(), right

    if not teacher:
        location, teacher = _split_location_teacher(location)

    if not teacher:
        m = re.search(r"[（(]\s*([\u4e00-\u9fa5]{2,4})\s*[)）]\s*$", name)
        if m and m.group(1) not in _NON_TEACHER_WORDS:
            teacher = m.group(1)
            name = name[: m.start()].strip()

    return {
        "name": name or "未命名课程",
        "location": location,
        "teacher": teacher,
        "weeks": weeks,
    }



# ---------------------------------------------------------------- 时间解析


def _parse_dt(
    params: dict[str, str], value: str, default_tz
) -> tuple[datetime | date | None, bool]:
    value = (value or "").strip()
    if not value:
        return None, False
    utc = value.endswith("Z")
    if utc:
        value = value[:-1]
    if "." in value:
        value = value.split(".", 1)[0]
    value = value.replace("-", "").replace(":", "")
    tzid = params.get("TZID")
    if utc:
        tz = timezone.utc
    elif tzid:
        tz = _tz_for(tzid, default_tz)
    else:
        tz = default_tz

    if re.fullmatch(r"\d{8}", value):
        try:
            return date(int(value[:4]), int(value[4:6]), int(value[6:8])), True
        except ValueError:
            return None, True
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=tz), False
        except ValueError:
            continue
    return None, False


def _tz_for(tzid: str, default_tz):
    """TZID 认不出来时退回配置时区，而不是悄悄按 UTC 处理。"""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(tzid)
    except Exception:  # noqa: BLE001
        pass
    tz = get_tz(tzid)
    if tz == timezone.utc and (tzid or "").lower() not in ("utc", "gmt", "z"):
        return default_tz
    return tz


def _as_aware(value: datetime, tz) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value


def _parse_until(value: str, default_tz) -> datetime | None:
    parsed, all_day = _parse_dt({}, value, default_tz)
    if isinstance(parsed, date) and not isinstance(parsed, datetime):
        return datetime.combine(parsed, datetime.max.time()).replace(tzinfo=default_tz)
    if isinstance(parsed, datetime):
        return _as_aware(parsed, default_tz)
    return None


def _parse_duration(value: str) -> timedelta | None:
    m = re.fullmatch(
        r"P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?",
        value.strip(),
    )
    if not m:
        return None
    weeks, days, hours, minutes, seconds = (int(x or 0) for x in m.groups())
    return timedelta(
        weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds
    )


def _parse_rrule(value: str) -> dict[str, str]:
    rule: dict[str, str] = {}
    for chunk in value.split(";"):
        if "=" in chunk:
            key, val = chunk.split("=", 1)
            rule[key.strip().upper()] = val.strip()
    return rule


def _dt_key(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(microsecond=0)


def _exdate_keys(ev: dict, default_tz) -> set[datetime]:
    keys: set[datetime] = set()
    for params, raw in ev.get("EXDATE") or []:
        for part in raw.split(","):
            parsed, is_date = _parse_dt(params, part, default_tz)
            if isinstance(parsed, datetime):
                keys.add(_dt_key(_as_aware(parsed, default_tz)))
            elif isinstance(parsed, date):
                keys.add(_dt_key(datetime.combine(parsed, datetime.min.time()).replace(tzinfo=default_tz)))
    return keys


def _rdate_values(ev: dict, default_tz) -> list[datetime]:
    out: list[datetime] = []
    for params, raw in ev.get("RDATE") or []:
        for part in raw.split(","):
            parsed, is_date = _parse_dt(params, part, default_tz)
            if isinstance(parsed, datetime):
                out.append(_as_aware(parsed, default_tz))
    return out


def _add_months(dt: datetime, months: int) -> datetime:
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _expand_event(
    ev: dict,
    default_tz,
    *,
    no_end_weeks: int = MAX_WEEKS_WITHOUT_END,
    warnings: list[str] | None = None,
    hint_weeks: list[int] | None = None,
    term_start: date | None = None,
) -> list[tuple[datetime, datetime]]:
    dtstart_item = _first(ev, "DTSTART")
    if not dtstart_item:
        return []
    start, all_day = _parse_dt(dtstart_item[0], dtstart_item[1], default_tz)
    if isinstance(start, date) and not isinstance(start, datetime):
        return []  # 全天事件不是课程
    if all_day or not isinstance(start, datetime):
        return []
    start = _as_aware(start, default_tz)

    end = None
    dtend_item = _first(ev, "DTEND")
    if dtend_item:
        parsed_end, _ = _parse_dt(dtend_item[0], dtend_item[1], default_tz)
        if isinstance(parsed_end, datetime):
            end = _as_aware(parsed_end, default_tz)
    if end is None:
        dur_item = _first(ev, "DURATION")
        duration = _parse_duration(dur_item[1]) if dur_item else None
        end = start + (duration or timedelta(minutes=45))
    if end <= start:
        end = start + timedelta(minutes=45)

    occurrences: list[tuple[datetime, datetime]] = []
    seen: set[datetime] = set()

    def push(occ_start: datetime, occ_end: datetime) -> None:
        key = _dt_key(occ_start)
        if key in seen:
            return
        seen.add(key)
        occurrences.append((occ_start, occ_end))

    duration = end - start
    rule_raw = _value(ev, "RRULE")
    if not rule_raw:
        if hint_weeks and term_start:
            # 有些 App 只导出一个 VEVENT，把周次写在标题/描述里（如“高数(1-16周)”）
            for week in hint_weeks:
                occ_date = term_start + timedelta(weeks=week - 1, days=start.weekday())
                if occ_date < start.date():
                    continue
                occ_start = datetime.combine(occ_date, start.timetz())
                push(occ_start, occ_start + duration)
            return occurrences
        push(start, end)
        return occurrences

    rule = _parse_rrule(rule_raw)
    freq = (rule.get("FREQ") or "").upper()
    try:
        interval = max(int(rule.get("INTERVAL") or 1), 1)
    except ValueError:
        interval = 1
    try:
        count_limit = int(rule["COUNT"]) if rule.get("COUNT") else 0
    except ValueError:
        count_limit = 0
    until = _parse_until(rule["UNTIL"], default_tz) if rule.get("UNTIL") else None

    bydays = []
    for part in (rule.get("BYDAY") or "").split(","):
        part = part.strip().upper()
        if not part:
            continue
        m = re.search(r"(MO|TU|WE|TH|FR|SA|SU)$", part)
        if m:
            day = parse_day(m.group(1))
            if day:
                bydays.append(day)
    bydays = sorted(set(bydays))

    produced = 0
    hard_limit = no_end_weeks if (not until and not count_limit) else 120
    if not until and not count_limit and warnings is not None:
        warnings.append(
            f"有重复课程没有结束日期，已按 {no_end_weeks} 周展开（可在面板调整 default_total_weeks）"
        )

    if freq == "WEEKLY":
        anchor = start.date() - timedelta(days=start.weekday())
        week_no = 0
        while week_no < hard_limit:
            week_start = anchor + timedelta(weeks=week_no * interval)
            for wd in bydays or [start.weekday() + 1]:
                occ_date = week_start + timedelta(days=wd - 1)
                occ_start = start + timedelta(days=(occ_date - start.date()).days)
                if occ_start < start:
                    continue
                if until and occ_start > until:
                    continue
                if count_limit and produced >= count_limit:
                    break
                push(occ_start, occ_start + duration)
                produced += 1
            if count_limit and produced >= count_limit:
                break
            week_no += 1

    elif freq == "DAILY":
        step = 0
        day_limit = no_end_weeks * 7 if (not until and not count_limit) else 365
        while step < day_limit:
            occ_start = start + timedelta(days=step * interval)
            if until and occ_start > until:
                break
            if count_limit and produced >= count_limit:
                break
            push(occ_start, occ_start + duration)
            produced += 1
            step += 1

    elif freq == "MONTHLY":
        for n in range(hard_limit * 2):
            if count_limit and produced >= count_limit:
                break
            occ_start = _add_months(start, n * interval)
            if until and occ_start > until:
                break
            push(occ_start, occ_start + duration)
            produced += 1

    elif freq == "YEARLY":
        for n in range(hard_limit):
            if count_limit and produced >= count_limit:
                break
            occ_start = _add_months(start, 12 * n * interval)
            if until and occ_start > until:
                break
            push(occ_start, occ_start + duration)
            produced += 1

    else:
        push(start, end)

    # EXDATE / RDATE
    ex_keys = _exdate_keys(ev, default_tz)
    if ex_keys:
        occurrences = [
            (s, e) for (s, e) in occurrences if _dt_key(s) not in ex_keys
        ]
    for rdt in _rdate_values(ev, default_tz):
        push(rdt, rdt + duration)

    if hint_weeks and term_start:
        allowed = set(hint_weeks)
        occurrences = [
            (s, e) for (s, e) in occurrences if week_of(term_start, s.date()) in allowed
        ]

    return occurrences


# ---------------------------------------------------------------- 主流程


def parse_ics(
    text: str,
    *,
    fallback_sections: list[Section] | None = None,
    default_total_weeks: int = 20,
    store_teacher: bool = False,
    store_location: bool = False,
    tz: str = "Asia/Shanghai",
    name: str = "iCalendar 课表",
) -> ImportResult:
    if "BEGIN:VCALENDAR" not in (text or "")[:2000].upper():
        raise ImportFailure("这不是有效的 iCalendar 文件（缺少 BEGIN:VCALENDAR）")

    display_tz = get_tz(tz)
    events = parse_events(text)
    if not events:
        raise ImportFailure("iCalendar 文件里没有日程（VEVENT）")

    warnings: list[str] = []
    all_day = 0

    # 日历级元信息：有些 App 会把课表名/时区放在 X-WR-* 里
    cal_tz_name = _calendar_prop(text, "X-WR-TIMEZONE")
    source_tz = _tz_for(cal_tz_name, display_tz) if cal_tz_name else display_tz
    cal_name = _calendar_prop(text, "X-WR-CALNAME")

    def event_start(ev: dict):
        item = _first(ev, "DTSTART")
        if not item:
            return None, False
        return _parse_dt(item[0], item[1], source_tz)

    # 先根据所有 DTSTART 确定第 1 周周一，供“标题里写周次”的课程展开
    start_dates = []
    for ev in events:
        parsed, is_day = event_start(ev)
        if is_day:
            all_day += 1
        if isinstance(parsed, datetime):
            start_dates.append(parsed.astimezone(display_tz).date())
    if not start_dates:
        raise ImportFailure("iCalendar 里没有带具体时间的课程（可能全是全天事件）")
    term_start = monday_of(min(start_dates))

    # 按 UID 分组，处理 RECURRENCE-ID 单次改期
    groups: dict[str, list[dict]] = {}
    for idx, ev in enumerate(events):
        uid = _value(ev, "UID") or f"__no_uid_{idx}"
        groups.setdefault(uid, []).append(ev)

    raw_occurrences: list[tuple[str, str, str, datetime, datetime]] = []
    for uid, evs in groups.items():
        masters = [e for e in evs if not _first(e, "RECURRENCE-ID")]
        overrides = [e for e in evs if _first(e, "RECURRENCE-ID")]
        base_meta = _extract_meta(masters[0] if masters else evs[0])

        group_occurrences: list[tuple[str, str, str, datetime, datetime]] = []
        for ev in masters or evs[:1]:
            meta = _extract_meta(ev)
            occurrences = _expand_event(
                ev,
                source_tz,
                no_end_weeks=max(default_total_weeks, 1),
                warnings=warnings,
                hint_weeks=meta["weeks"] or None,
                term_start=term_start,
            )
            for occ_start, occ_end in occurrences:
                group_occurrences.append(
                    (meta["name"], meta["location"], meta["teacher"], occ_start, occ_end)
                )

        # 单次改期只影响同一个 UID 的日程，避免误删同时段的其他课
        for ov in overrides:
            rec_item = _first(ov, "RECURRENCE-ID")
            if not rec_item:
                continue
            rec, _ = _parse_dt(rec_item[0], rec_item[1], source_tz)
            if not isinstance(rec, datetime):
                continue
            rec_key = _dt_key(_as_aware(rec, source_tz))
            group_occurrences = [
                item for item in group_occurrences if _dt_key(item[3]) != rec_key
            ]
            meta = _extract_meta(ov)
            if not _value(ov, "SUMMARY"):
                meta["name"] = base_meta["name"]
            meta["location"] = meta["location"] or base_meta["location"]
            meta["teacher"] = meta["teacher"] or base_meta["teacher"]
            for ov_start, ov_end in _expand_event(
                ov,
                source_tz,
                no_end_weeks=1,
                warnings=None,
            ):
                group_occurrences.append(
                    (meta["name"], meta["location"], meta["teacher"], ov_start, ov_end)
                )

        raw_occurrences.extend(group_occurrences)

    if not raw_occurrences:
        raise ImportFailure("iCalendar 里没有可用的课程时间（可能全是全天事件）")

    # 统一转成配置时区，按“课程名+地点+教师+星期+起止时间”归并周次
    normalized = [
        (name, location, teacher, start.astimezone(display_tz), end.astimezone(display_tz))
        for name, location, teacher, start, end in raw_occurrences
    ]
    normalized.sort(key=lambda item: item[3])

    grouped: dict[tuple, list[int]] = {}
    for name, location, teacher, start, end in normalized:
        week = week_of(term_start, start.date())
        if week <= 0:
            continue
        key = (
            name,
            location,
            teacher,
            start.weekday() + 1,
            start.strftime("%H:%M"),
            end.strftime("%H:%M"),
        )
        grouped.setdefault(key, []).append(week)

    courses: list[Course] = []
    for (name, location, teacher, day, start_hm, end_hm), weeks in grouped.items():
        courses.append(
            Course(
                name=name,
                day=day,
                weeks=sorted(set(weeks)),
                start=start_hm,
                end=end_hm,
                location=location,
                teacher=teacher,
            )
        )

    if all_day:
        warnings.append(f"已跳过 {all_day} 个没有具体时间的全天日程")

    return build_result(
        kind="ics",
        source="iCalendar",
        name=cal_name or name,
        term_start=term_start,
        total_weeks=max(max((max(c.weeks) for c in courses), default=0), default_total_weeks),
        sections=[],
        courses=courses,
        warnings=warnings,
        # ICS 没有「学期第 1 周」字段，这里的开学日期是按最早一节课推算的
        meta={
            "events": len(events),
            "calendar": cal_name,
            "term_start_guessed": True,
        },
        fallback_sections=fallback_sections,
        default_total_weeks=default_total_weeks,
        store_teacher=store_teacher,
        store_location=store_location,
        tz=tz,
    )
