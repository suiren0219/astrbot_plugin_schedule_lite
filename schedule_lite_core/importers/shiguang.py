"""拾光课程表（ShiGuangSchedule）单表 JSON 导入。

字段以 App 源码 CourseImportExport.kt 为准：
    { "courses": [{name, teacher, position, day, startSection, endSection,
                   weeks[], isCustomTime, customStartTime, customEndTime}],
      "timeSlots": [{number, startTime, endTime, alias}],
      "config": {semesterStartDate, semesterTotalWeeks, ...} }
"""

from __future__ import annotations

from typing import Any

from ..model import Course, Section
from ..normalize import (
    parse_date,
    parse_day,
    parse_section_times,
    parse_time,
    parse_weeks,
)
from .base import ImportFailure, ImportResult, build_result


def looks_like(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    courses = data.get("courses")
    if not isinstance(courses, list):
        return False
    config = data.get("config")
    if "timeSlots" in data or "time_slots" in data:
        return True
    if isinstance(config, dict) and any(
        k in config for k in ("semesterStartDate", "semesterTotalWeeks", "firstDayOfWeek")
    ):
        return True
    # 兼容只有课程数组、但字段明显是拾光风格的导出
    sample = next((c for c in courses if isinstance(c, dict)), None)
    return bool(sample and ("startSection" in sample or "endSection" in sample))


def _pick(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _slot_sections(slots: Any) -> list[Section]:
    return parse_section_times(slots)


def parse_export(
    data: dict[str, Any],
    *,
    fallback_sections: list[Section] | None = None,
    default_total_weeks: int = 20,
    store_teacher: bool = False,
    store_location: bool = False,
    tz: str = "Asia/Shanghai",
    name: str = "拾光课表",
) -> ImportResult:
    if isinstance(data.get("databasePayload"), dict):
        raise ImportFailure(
            "这是拾光 App 的整库备份，暂不支持；请在 App 里导出「课程表 JSON」后导入"
        )

    courses_raw = data.get("courses")
    if not isinstance(courses_raw, list):
        raise ImportFailure("拾光 JSON 里没有 courses 数组")

    config = data.get("config") if isinstance(data.get("config"), dict) else {}
    slots = data.get("timeSlots") or data.get("time_slots") or config.get("timeSlots")
    sections = _slot_sections(slots)

    term_start = parse_date(
        _pick(config, "semesterStartDate", "startDate")
        or _pick(data, "semesterStartDate", "startDate", "term_start")
    )
    try:
        total_weeks = int(
            _pick(config, "semesterTotalWeeks", "totalWeeks")
            or _pick(data, "totalWeeks", "total_weeks")
            or 0
        )
    except (TypeError, ValueError):
        total_weeks = 0

    warnings: list[str] = []
    first_day = config.get("firstDayOfWeek")
    try:
        if first_day not in (None, 1, "1"):
            warnings.append(f"课表设置的一周起始日是 {first_day}，已按周一为一周起点导入")
    except Exception:  # noqa: BLE001
        pass

    courses: list[Course] = []
    for raw in courses_raw:
        if not isinstance(raw, dict):
            continue
        name_value = _pick(raw, "name", "courseName", "course", "title", "课程")
        day = parse_day(_pick(raw, "day", "weekday", "weekDay", "星期"))
        weeks = parse_weeks(_pick(raw, "weeks", "week", "weekList", "周次", "周数"))
        start_sec = _pick(raw, "startSection", "start_section", "startPeriod", "开始节次")
        end_sec = _pick(raw, "endSection", "end_section", "endPeriod", "结束节次")
        try:
            start_sec = int(start_sec) if start_sec is not None else None
            end_sec = int(end_sec) if end_sec is not None else None
        except (TypeError, ValueError):
            start_sec = end_sec = None

        start = end = ""
        if raw.get("isCustomTime") and raw.get("customStartTime"):
            start = parse_time(raw.get("customStartTime")) or ""
            end = parse_time(raw.get("customEndTime")) or ""
        else:
            start = parse_time(_pick(raw, "startTime", "start")) or ""
            end = parse_time(_pick(raw, "endTime", "end")) or ""

        if not start and start_sec and sections:
            sec = {s.index: s for s in sections}.get(start_sec)
            if sec:
                start = sec.start
        if not end and end_sec and sections:
            sec = {s.index: s for s in sections}.get(end_sec)
            if sec:
                end = sec.end

        courses.append(
            Course(
                name=str(name_value or ""),
                day=day or 0,
                weeks=weeks,
                start=start,
                end=end,
                start_section=start_sec,
                end_section=end_sec,
                location=str(_pick(raw, "position", "location", "room", "地点", "教室") or ""),
                teacher=str(_pick(raw, "teacher", "老师", "教师") or ""),
            )
        )

    return build_result(
        kind="shiguang",
        source="拾光课程表",
        name=str(_pick(data, "name", "tableName") or name),
        term_start=term_start,
        total_weeks=total_weeks,
        sections=sections,
        courses=courses,
        warnings=warnings,
        meta={"firstDayOfWeek": first_day},
        fallback_sections=fallback_sections,
        default_total_weeks=default_total_weeks,
        store_teacher=store_teacher,
        store_location=store_location,
        tz=tz,
    )
