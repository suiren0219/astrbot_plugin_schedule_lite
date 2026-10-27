"""导入器公共结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from ..model import Course, Section
from ..normalize import (
    default_sections,
    get_tz,
    monday_of,
    normalize_courses,
    parse_section_times,
)


class ImportFailure(Exception):
    """可以直接展示给用户的导入失败原因。"""


@dataclass
class ImportResult:
    source_kind: str
    source: str
    name: str
    term_start: date
    total_weeks: int
    sections: list[Section]
    courses: list[Course]
    merged: int = 0
    dropped: int = 0
    warnings: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


def build_result(
    *,
    kind: str,
    source: str,
    name: str,
    term_start: date | None,
    total_weeks: int | None,
    sections: Any,
    courses: list[Course],
    warnings: list[str] | None = None,
    meta: dict[str, Any] | None = None,
    fallback_sections: list[Section] | None = None,
    default_total_weeks: int = 20,
    store_teacher: bool = False,
    store_location: bool = False,
    tz: str = "Asia/Shanghai",
) -> ImportResult:
    """把任意来源的课程整理成统一的 ImportResult。"""
    warns = list(warnings or [])
    fallback = fallback_sections or default_sections()

    parsed_sections = parse_section_times(sections)
    needs_sections = any(c.start_section is not None for c in courses)
    if not parsed_sections:
        parsed_sections = fallback
        if needs_sections:
            warns.append("课表数据里没有作息时间，已使用插件配置的默认作息")
    else:
        # 有些课表只给了部分节次，缺的用默认作息补齐
        known = {s.index for s in parsed_sections}
        missing = [s for s in fallback if s.index not in known]
        if missing:
            parsed_sections.extend(missing)
            parsed_sections.sort(key=lambda s: s.index)

    if not courses:
        raise ImportFailure("没有解析到任何课程，请确认导入内容是否正确")

    if not store_teacher or not store_location:
        for c in courses:
            if not store_teacher:
                c.teacher = ""
            if not store_location:
                c.location = ""

    normalized, merged, dropped, norm_warns = normalize_courses(courses, parsed_sections)
    warns.extend(norm_warns)
    warns = list(dict.fromkeys(warns))  # 同一原因只提示一次
    if not normalized:
        raise ImportFailure("课程数据不完整（缺少周次或上课时间），无法导入")


    max_week = max((max(c.weeks) for c in normalized if c.weeks), default=0)
    declared = int(total_weeks or 0)
    base_weeks = declared if declared > 0 else int(default_total_weeks or 0)
    total = max(base_weeks, max_week, 1)
    if declared and max_week > declared:
        warns.append(f"课程最晚到第 {max_week} 周，已把学期总周数放宽到 {total} 周")

    meta_data = dict(meta or {})
    if term_start is None:
        term_start = monday_of(datetime.now(get_tz(tz)).date())
        warns.append("数据里没有开学日期，已按本周为第 1 周计算")
        meta_data["term_start_guessed"] = True

    return ImportResult(
        source_kind=kind,
        source=source,
        name=(name or "我的课表").strip() or "我的课表",
        term_start=monday_of(term_start),
        total_weeks=total,
        sections=parsed_sections,
        courses=normalized,
        merged=merged,
        dropped=dropped,
        warnings=warns,
        meta=meta_data,
    )
