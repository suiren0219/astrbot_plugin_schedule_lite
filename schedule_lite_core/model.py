"""课表插件的核心数据模型。

这里只放与平台无关的数据结构，方便单元测试直接导入，不需要 AstrBot 运行环境。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any


@dataclass
class Section:
    """一个节次对应的起止时间。"""

    index: int
    start: str  # HH:MM
    end: str  # HH:MM
    alias: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Section":
        return cls(
            index=int(data.get("index") or 0),
            start=str(data.get("start") or ""),
            end=str(data.get("end") or ""),
            alias=str(data.get("alias") or ""),
        )


@dataclass
class Course:
    """一门课程在某个星期几的一次上课安排。

    同一门课不同周次若时间地点完全一致，会在导入时合并成一条，weeks 存并集。
    start/end 是最终展示用的时间；start_section/end_section 存在时用于按个人作息重算。
    """

    name: str
    day: int  # 1=周一 ... 7=周日
    weeks: list[int] = field(default_factory=list)
    start: str = ""
    end: str = ""
    start_section: int | None = None
    end_section: int | None = None
    location: str = ""
    teacher: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Course":
        weeks = [int(w) for w in (data.get("weeks") or []) if str(w).strip()]
        return cls(
            name=str(data.get("name") or ""),
            day=int(data.get("day") or 0),
            weeks=sorted(set(weeks)),
            start=str(data.get("start") or ""),
            end=str(data.get("end") or ""),
            start_section=_opt_int(data.get("start_section")),
            end_section=_opt_int(data.get("end_section")),
            location=str(data.get("location") or ""),
            teacher=str(data.get("teacher") or ""),
        )


def _opt_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class Timetable:
    """一个用户的一份课表。"""

    owner_id: str
    owner_name: str = ""
    name: str = "我的课表"
    term_start: str = ""  # 第 1 周周一，ISO 日期
    total_weeks: int = 20
    timezone: str = "Asia/Shanghai"
    source: str = ""
    imported_at: str = ""
    sections: list[Section] = field(default_factory=list)
    courses: list[Course] = field(default_factory=list)

    # 订阅与个人设置
    notify_minutes: int | None = None
    notify_origin: str = ""
    notify_enabled: bool = False
    notified_date: str = ""
    notified_keys: list[str] = field(default_factory=list)
    notes: dict[str, str] = field(default_factory=dict)
    abandon_date: str = ""
    abandon_course: str = ""
    abandon_start: str = ""

    # 记录用户在哪些群用过课表指令，用于群内排行/查看，避免依赖平台成员列表 API
    groups: list[str] = field(default_factory=list)
    # 群号 -> 该群的 unified_msg_origin，用于定时节目/周报主动发送
    group_origins: dict[str, str] = field(default_factory=dict)
    # 请假记录：[{"start": "2026-09-10", "end": "2026-09-12", "reason": "病假"}]
    leaves: list[dict[str, Any]] = field(default_factory=list)
    # 调课记录：from_date 当天的课程快照移到 to_date
    adjustments: list[dict[str, Any]] = field(default_factory=list)
    # 调休补班：{"2026-09-27": 1} 表示按周一课表上课
    makeup_days: dict[str, int] = field(default_factory=dict)
    # 原始导入文件存档路径（相对插件数据目录），便于追溯与重新解析
    raw_source: str = field(default="")
    # 运行期索引，不写入 JSON
    _day_index: dict[int, list[Course]] | None = field(
        default=None, repr=False, compare=False
    )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("_day_index", None)
        data["sections"] = [s.to_dict() for s in self.sections]
        data["courses"] = [c.to_dict() for c in self.courses]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Timetable":
        return cls(
            owner_id=str(data.get("owner_id") or ""),
            owner_name=str(data.get("owner_name") or ""),
            name=str(data.get("name") or "我的课表"),
            term_start=str(data.get("term_start") or ""),
            total_weeks=int(data.get("total_weeks") or 20),
            timezone=str(data.get("timezone") or "Asia/Shanghai"),
            source=str(data.get("source") or ""),
            imported_at=str(data.get("imported_at") or ""),
            sections=[Section.from_dict(s) for s in (data.get("sections") or [])],
            courses=[Course.from_dict(c) for c in (data.get("courses") or [])],
            notify_minutes=_opt_int(data.get("notify_minutes")),
            notify_origin=str(data.get("notify_origin") or ""),
            notify_enabled=bool(data.get("notify_enabled")),
            notified_date=str(data.get("notified_date") or ""),
            notified_keys=list(data.get("notified_keys") or []),
            notes={str(k): str(v) for k, v in (data.get("notes") or {}).items()},
            abandon_date=str(data.get("abandon_date") or ""),
            abandon_course=str(data.get("abandon_course") or ""),
            abandon_start=str(data.get("abandon_start") or ""),
            groups=[str(g) for g in (data.get("groups") or [])],
            group_origins={
                str(k): str(v) for k, v in (data.get("group_origins") or {}).items()
            },
            leaves=[
                {
                    "start": str(item.get("start") or ""),
                    "end": str(item.get("end") or item.get("start") or ""),
                    "reason": str(item.get("reason") or ""),
                }
                for item in (data.get("leaves") or [])
                if isinstance(item, dict) and item.get("start")
            ],
            adjustments=[
                dict(item)
                for item in (data.get("adjustments") or [])
                if isinstance(item, dict) and item.get("from_date") and item.get("to_date")
            ],
            makeup_days={
                str(k): int(v)
                for k, v in (data.get("makeup_days") or {}).items()
                if str(v).isdigit() and 1 <= int(v) <= 7
            },
            raw_source=str(data.get("raw_source") or ""),
        )

    # ---------- 便捷方法 ----------

    def term_date(self) -> date | None:
        try:
            return date.fromisoformat(self.term_start)
        except (TypeError, ValueError):
            return None

    def section_map(self) -> dict[int, Section]:
        return {s.index: s for s in self.sections}

    def note_for(self, course_name: str) -> str:
        if not self.notes:
            return ""
        return self.notes.get(course_name, "") or ""

    def remember_group(self, group_id: str, origin: str = "") -> bool:
        """记录群号和群会话，返回是否有变化（避免每次都写盘）。"""
        if not group_id:
            return False
        changed = False
        if group_id not in self.groups:
            self.groups.append(group_id)
            if len(self.groups) > 50:  # 上限防止无限增长
                self.groups = self.groups[-50:]
            changed = True
        if origin and self.group_origins.get(group_id) != origin:
            self.group_origins[group_id] = origin
            changed = True
        return changed

    def weekday_courses(self, weekday: int) -> list[Course]:
        """按星期几取课程，避免每次状态查询都扫描整张课表。"""
        if self._day_index is None:
            index: dict[int, list[Course]] = {}
            for course in self.courses:
                if 1 <= course.day <= 7:
                    index.setdefault(course.day, []).append(course)
            for items in index.values():
                items.sort(key=lambda c: c.start or "99:99")
            self._day_index = index
        return self._day_index.get(weekday, [])

    def invalidate_index(self) -> None:
        self._day_index = None

    def leave_on(self, day: date) -> dict[str, Any] | None:
        key = day.isoformat()
        for item in self.leaves:
            start = str(item.get("start") or "")
            end = str(item.get("end") or start)
            if start and start <= key <= end:
                return item
        return None

    def makeup_weekday(self, day: date, global_map: dict[str, int] | None = None) -> int:
        key = day.isoformat()
        value = self.makeup_days.get(key)
        if value:
            return int(value)
        if global_map:
            value = global_map.get(key)
            if value:
                return int(value)
        return day.isoweekday()

    def adjustments_from(self, day: date) -> list[dict[str, Any]]:
        key = day.isoformat()
        return [a for a in self.adjustments if str(a.get("from_date")) == key]

    def adjustments_to(self, day: date) -> list[dict[str, Any]]:
        key = day.isoformat()
        return [a for a in self.adjustments if str(a.get("to_date")) == key]
