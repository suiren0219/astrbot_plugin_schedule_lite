"""通用 JSON / CSV 导入。

用于兜底：用户从任何课表软件复制出来、字段名不完全一致的表格，也能尽量吃进去。
同时支持本插件自己导出的 JSON（字段完全一致）。
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any, Iterable

from ..model import Course, Section
from ..normalize import (
    parse_date,
    parse_day,
    parse_section_times,
    parse_time,
    parse_weeks,
)
from .base import ImportFailure, ImportResult, build_result

_NAME_KEYS = ("name", "courseName", "course_name", "course", "title", "课程", "课程名", "课程名称")
_DAY_KEYS = ("day", "weekday", "weekDay", "week_day", "星期", "周几", "上课星期")
_WEEKS_KEYS = ("weeks", "week", "weekList", "week_list", "周次", "周数", "上课周次")
_START_KEYS = ("start", "startTime", "start_time", "begin", "开始", "开始时间", "上课时间", "时间")
_END_KEYS = ("end", "endTime", "end_time", "finish", "结束", "结束时间", "下课时间")
_START_SEC_KEYS = ("startSection", "start_section", "startPeriod", "开始节次", "起始节")
_END_SEC_KEYS = ("endSection", "end_section", "endPeriod", "结束节次", "终止节")
_SECTION_KEYS = ("sections", "section", "节次")
_LOCATION_KEYS = ("location", "position", "room", "place", "教室", "地点", "上课地点")
_TEACHER_KEYS = ("teacher", "老师", "教师", "任课教师")
_TERM_KEYS = ("term_start", "termStart", "startDate", "semesterStartDate", "start_date", "开学日期", "开学时间")
_TOTAL_KEYS = ("total_weeks", "totalWeeks", "semesterTotalWeeks", "weeks_count", "总周数")
_TABLE_NAME_KEYS = ("name", "tableName", "课表名", "课程表名称")
_SECTION_TABLE_KEYS = ("sections", "timeSlots", "time_slots", "sectionTimes", "作息", "作息时间")


def _pick(data: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _split_days(value: Any) -> list[int]:
    if isinstance(value, (list, tuple, set)):
        out = []
        for item in value:
            day = parse_day(item)
            if day:
                out.append(day)
        return sorted(set(out))
    text = str(value or "").strip()
    if not text:
        return []
    if re.search(r"[,，、/\s]", text):
        out = []
        for part in re.split(r"[,，、/\s]+", text):
            day = parse_day(part)
            if day:
                out.append(day)
        if out:
            return sorted(set(out))
    day = parse_day(text)
    return [day] if day else []


def _split_range(value: Any) -> tuple[str | None, str | None]:
    text = str(value or "")
    text = text.replace("~", "-").replace("—", "-").replace("～", "-").replace("至", "-")
    if "-" in text:
        left, right = text.split("-", 1)
        return parse_time(left), parse_time(right)
    return parse_time(text), None


def _section_range(value: Any) -> tuple[int | None, int | None]:
    if value is None or value == "":
        return None, None
    if isinstance(value, (list, tuple)):
        nums = [int(x) for x in value if str(x).strip().isdigit()]
    else:
        nums = [int(x) for x in re.findall(r"\d+", str(value))]
    if not nums:
        return None, None
    return min(nums), max(nums)


def _course_from_dict(raw: dict[str, Any]) -> list[Course]:
    name = str(_pick(raw, _NAME_KEYS) or "").strip()
    if not name:
        return []
    days = _split_days(_pick(raw, _DAY_KEYS))
    if not days:
        return []
    weeks = parse_weeks(_pick(raw, _WEEKS_KEYS))

    start_value = _pick(raw, _START_KEYS)
    end_value = _pick(raw, _END_KEYS)
    start, end = "", ""
    if start_value is not None:
        start, split_end = _split_range(start_value)
        end = parse_time(end_value) if end_value is not None else split_end or ""
    elif end_value is not None:
        end = parse_time(end_value) or ""

    start_sec, end_sec = _section_range(_pick(raw, _START_SEC_KEYS))
    end_sec_from = _section_range(_pick(raw, _END_SEC_KEYS))[1]
    if end_sec_from:
        end_sec = end_sec_from
    if start_sec is None and end_sec is None:
        start_sec, end_sec = _section_range(_pick(raw, _SECTION_KEYS))

    location = str(_pick(raw, _LOCATION_KEYS) or "")
    teacher = str(_pick(raw, _TEACHER_KEYS) or "")

    return [
        Course(
            name=name,
            day=day,
            weeks=weeks,
            start=start or "",
            end=end or "",
            start_section=start_sec,
            end_section=end_sec,
            location=location,
            teacher=teacher,
        )
        for day in days
    ]


def parse_json(
    obj: Any,
    *,
    fallback_sections: list[Section] | None = None,
    default_total_weeks: int = 20,
    store_teacher: bool = False,
    store_location: bool = False,
    tz: str = "Asia/Shanghai",
    name: str = "导入的课表",
) -> ImportResult:
    if isinstance(obj, list):
        courses_raw = obj
        wrapper: dict[str, Any] = {}
    elif isinstance(obj, dict):
        wrapper = obj
        if isinstance(obj.get("timetable"), dict):
            wrapper = obj["timetable"]
        elif isinstance(obj.get("data"), dict) and isinstance(obj["data"].get("courses"), list):
            wrapper = obj["data"]
        courses_raw = wrapper.get("courses")
        if isinstance(courses_raw, dict):  # 少数导出会把课程按名字分组
            courses_raw = [c for group in courses_raw.values() if isinstance(group, list) for c in group]
    else:
        raise ImportFailure("JSON 顶层必须是对象或数组")

    if not isinstance(courses_raw, list):
        raise ImportFailure("JSON 里没有 courses 数组")

    sections = parse_section_times(_pick(wrapper, _SECTION_TABLE_KEYS))
    term_start = parse_date(_pick(wrapper, _TERM_KEYS))
    try:
        total_weeks = int(_pick(wrapper, _TOTAL_KEYS) or 0)
    except (TypeError, ValueError):
        total_weeks = 0

    courses: list[Course] = []
    for raw in courses_raw:
        if isinstance(raw, dict):
            courses.extend(_course_from_dict(raw))

    # 本插件导出的通用 JSON 会带上请假 / 调课 / 补班记录，换设备导入时恢复
    leaves: list[dict[str, Any]] = []
    for item in wrapper.get("leaves") or []:
        if isinstance(item, dict) and item.get("start"):
            start = str(item.get("start") or "")
            leaves.append(
                {
                    "start": start,
                    "end": str(item.get("end") or start),
                    "reason": str(item.get("reason") or ""),
                }
            )

    adjustments: list[dict[str, Any]] = []
    for item in wrapper.get("adjustments") or []:
        if isinstance(item, dict) and item.get("from_date") and item.get("to_date"):
            adjustments.append(dict(item))

    makeup_days: dict[str, int] = {}
    raw_makeup = wrapper.get("makeup_days")
    if isinstance(raw_makeup, dict):
        for key, value in raw_makeup.items():
            try:
                weekday = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= weekday <= 7:
                makeup_days[str(key)] = weekday

    return build_result(
        kind="json",
        source="通用 JSON",
        name=str(_pick(wrapper, _TABLE_NAME_KEYS) or name),
        term_start=term_start,
        total_weeks=total_weeks,
        sections=sections,
        courses=courses,
        warnings=[],
        leaves=leaves,
        adjustments=adjustments,
        makeup_days=makeup_days,
        fallback_sections=fallback_sections,
        default_total_weeks=default_total_weeks,
        store_teacher=store_teacher,
        store_location=store_location,
        tz=tz,
    )


def looks_like_csv(text: str) -> bool:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    first = lines[0]
    if first.lstrip().startswith(("{", "[", "BEGIN:")):
        return False
    return first.count(",") >= 2 or first.count("\t") >= 2


_CSV_ALIASES = {
    "name": _NAME_KEYS,
    "day": _DAY_KEYS,
    "weeks": _WEEKS_KEYS,
    "start": _START_KEYS,
    "end": _END_KEYS,
    "start_section": _START_SEC_KEYS,
    "end_section": _END_SEC_KEYS,
    "sections": _SECTION_KEYS,
    "location": _LOCATION_KEYS,
    "teacher": _TEACHER_KEYS,
}


def _map_header(cells: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(cells):
        key = cell.strip().strip('"').lower()
        for field, aliases in _CSV_ALIASES.items():
            if field in mapping:
                continue
            if key in {a.lower() for a in aliases}:
                mapping[field] = idx
                break
    return mapping


def parse_csv(
    text: str,
    *,
    fallback_sections: list[Section] | None = None,
    default_total_weeks: int = 20,
    store_teacher: bool = False,
    store_location: bool = False,
    tz: str = "Asia/Shanghai",
    name: str = "CSV 课表",
) -> ImportResult:
    body = text.lstrip("\ufeff")
    first_line = next((ln for ln in body.splitlines() if ln.strip()), "")
    delimiter = "\t" if first_line.count("\t") > first_line.count(",") else ","
    reader = csv.reader(io.StringIO(body), delimiter=delimiter)
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        raise ImportFailure("CSV 是空的")

    header_map = _map_header(rows[0])
    has_header = len(header_map) >= 3
    data_rows = rows[1:] if has_header else rows

    courses: list[Course] = []
    for row in data_rows:
        if has_header:
            raw = {
                field: row[idx].strip()
                for field, idx in header_map.items()
                if idx < len(row) and row[idx].strip()
            }
            if "start" in raw and "end" not in raw and "-" in raw["start"]:
                pass  # 交给 _course_from_dict 拆分
        else:
            cells = [c.strip() for c in row]
            raw = {}
            if len(cells) >= 1:
                raw["name"] = cells[0]
            if len(cells) >= 2:
                raw["day"] = cells[1]
            if len(cells) >= 3:
                raw["start"] = cells[2]
            if len(cells) >= 4:
                raw["end"] = cells[3]
            if len(cells) >= 5:
                raw["weeks"] = cells[4]
            if len(cells) >= 6:
                raw["location"] = cells[5]
            if len(cells) >= 7:
                raw["teacher"] = cells[6]
        courses.extend(_course_from_dict(raw))

    return build_result(
        kind="csv",
        source="CSV 表格",
        name=name,
        term_start=None,
        total_weeks=0,
        sections=[],
        courses=courses,
        warnings=[],
        fallback_sections=fallback_sections,
        default_total_weeks=default_total_weeks,
        store_teacher=store_teacher,
        store_location=store_location,
        tz=tz,
    )


def export_dict(tt) -> dict[str, Any]:
    """导出成本插件的通用 JSON，方便换设备/换机器人后重新导入。"""
    return {
        "name": tt.name,
        "term_start": tt.term_start,
        "total_weeks": tt.total_weeks,
        "timezone": tt.timezone,
        "sections": [s.to_dict() for s in tt.sections],
        "courses": [
            {
                "name": c.name,
                "day": c.day,
                "weeks": c.weeks,
                "start": c.start,
                "end": c.end,
                "start_section": c.start_section,
                "end_section": c.end_section,
                "location": c.location,
                "teacher": c.teacher,
            }
            for c in tt.courses
        ],
        "leaves": list(getattr(tt, "leaves", []) or []),
        "adjustments": list(getattr(tt, "adjustments", []) or []),
        "makeup_days": dict(getattr(tt, "makeup_days", {}) or {}),
    }


def dumps_export(tt) -> str:
    return json.dumps(export_dict(tt), ensure_ascii=False, indent=2)
