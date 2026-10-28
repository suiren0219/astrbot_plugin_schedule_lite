"""Nexio 课程表导入：分享口令（30 分钟有效）与 JSON 导出。

Nexio 的分享口令由 App 上传到官方中转服务，取回的是 v4 课表 JSON：

    {"ok": true, "schedule_name": "…", "schedule_data": {
        "settings": {"class_start_time": "2026/09/07", "total_weeks": 18, …},
        "times": {"morning": {"1": "08:00-08:40"}, "afternoon": {…}, "evening": {…}},
        "courses": [{"name": …, "dayOfWeek": 1, "startSection": 1, "endSection": 2,
                     "startWeek": 1, "endWeek": 16, "weekType": 0,
                     "selectedWeeks": [], "classroom": "A101", "teacher": "张三"}]
    }}

课表 JSON 也可以直接从 Nexio 导出后发给机器人，因此这里同时提供本地解析。
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

from ..model import Course, Section
from ..normalize import parse_date, parse_time
from .base import ImportFailure, ImportResult, build_result

# 官方域名走 HTTPS，兼容部分网络封禁 IP 直连 3000 端口的情况；
# 原来的 IP 直连保留为兜底，两者由 Nexio App 共用同一套后端。
SHARE_URLS = (
    "https://nexioschedule.icu/api/share",
    "http://182.92.193.223:3000/api/share",
)
SHARE_URL = SHARE_URLS[0]

# 趣味口令：中文短语为主，也兼容字母数字；排除明显不是口令的内容。
_CODE_RE = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9_-]{2,64}$")
_HINT_RE = re.compile(
    r"(?:分享|课程)?(?:口令|分享码|课程码)\s*(?:为|是)?\s*[:：]?\s*"
    r"([^\s，,。；;、()（）]{2,64})"
)
_GROUP_ORDER = ("morning", "afternoon", "evening")


def _clean_code(text: str) -> str:
    return (text or "").strip().strip("「」『』“”\"' \t\r\n")


def looks_like_code(text: str) -> bool:
    t = _clean_code(text)
    return bool(t) and "\n" not in t and bool(_CODE_RE.fullmatch(t))


def extract_code(text: str) -> str | None:
    """从裸口令或「分享口令为：xxx」整段话里取出口令。"""
    raw = (text or "").strip()
    if not raw:
        return None
    if looks_like_code(raw):
        return _clean_code(raw)
    m = _HINT_RE.search(raw)
    if not m:
        return None
    code = _clean_code(m.group(1))
    return code if looks_like_code(code) else None


def looks_like_export(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    if isinstance(obj.get("schedule_data"), dict):
        return True
    courses = obj.get("courses")
    return (
        isinstance(courses, list)
        and bool(courses)
        and isinstance(courses[0], dict)
        and ("dayOfWeek" in courses[0] or "startSection" in courses[0])
        and ("settings" in obj or "times" in obj or "schedule_name" in obj)
    )


def _int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _sections_from_times(times: Any, settings: dict[str, Any]) -> list[Section]:
    """把 morning/afternoon/evening 三组作息拼成全局节次（下午接着上午编号）。"""
    if not isinstance(times, dict):
        return []
    counts = {
        "morning": _int(settings.get("morning_sections"), 0),
        "afternoon": _int(settings.get("afternoon_sections"), 0),
        "evening": _int(settings.get("evening_sections"), 0),
    }
    sections: list[Section] = []
    offset = 0
    for group in _GROUP_ORDER:
        block = times.get(group)
        if not isinstance(block, dict) or not block:
            offset += counts[group]
            continue
        indexes = sorted({_int(key, 0) for key in block if _int(key, 0) > 0})
        if not indexes:
            offset += counts[group]
            continue
        width = max(indexes) if max(indexes) > counts[group] else counts[group]
        for index in indexes:
            raw = str(block.get(str(index), block.get(index, "")) or "")
            m = re.match(r"^\s*(\d{1,2})[:：](\d{2})\s*[-~—–至]\s*(\d{1,2})[:：](\d{2})", raw)
            start = f"{int(m.group(1)):02d}:{m.group(2)}" if m else (parse_time(raw) or "")
            end = f"{int(m.group(3)):02d}:{m.group(4)}" if m else ""
            if start and end:
                sections.append(Section(index=offset + index, start=start, end=end))
        offset += width
    return sections


def _course_weeks(item: dict[str, Any], total_weeks: int) -> list[int]:
    selected = item.get("selectedWeeks")
    if isinstance(selected, list):
        weeks = sorted({_int(w, 0) for w in selected if _int(w, 0) > 0})
        if weeks:
            return weeks
    start = _int(item.get("startWeek"), 0)
    end = _int(item.get("endWeek"), 0)
    if start > 0 and end >= start:
        weeks = list(range(start, end + 1))
    elif total_weeks > 0:
        weeks = list(range(1, total_weeks + 1))
    else:
        weeks = [1]
    week_type = _int(item.get("weekType"), 0)
    if week_type == 1:
        weeks = [w for w in weeks if w % 2 == 1]
    elif week_type == 2:
        weeks = [w for w in weeks if w % 2 == 0]
    return weeks


def parse_export(
    obj: Any,
    *,
    fallback_sections: list[Section] | None = None,
    default_total_weeks: int = 20,
    store_teacher: bool = False,
    store_location: bool = False,
    tz: str = "Asia/Shanghai",
    name: str = "",
    source: str = "Nexio 课表",
) -> ImportResult:
    if isinstance(obj, dict) and isinstance(obj.get("schedule_data"), dict):
        wrapper = obj
        data = obj["schedule_data"]
        name = name or str(wrapper.get("schedule_name") or "")
    elif isinstance(obj, dict):
        data = obj
    else:
        raise ImportFailure("Nexio 课表数据格式不对")

    settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    raw_courses = data.get("courses")
    if not isinstance(raw_courses, list) or not raw_courses:
        raise ImportFailure("Nexio 课表里没有课程")

    term_start = parse_date(settings.get("class_start_time"))
    total_weeks = _int(settings.get("total_weeks"), 0)
    sections = _sections_from_times(data.get("times"), settings)

    courses: list[Course] = []
    warnings: list[str] = []
    for item in raw_courses:
        if not isinstance(item, dict):
            continue
        course_name = str(item.get("name") or "").strip()
        if not course_name:
            continue
        day = _int(item.get("dayOfWeek", item.get("day")), 0)
        if not 1 <= day <= 7:
            continue
        weeks = _course_weeks(item, total_weeks or default_total_weeks)

        start_section = _int(item.get("startSection"), 0)
        end_section = _int(item.get("endSection"), start_section)
        custom_start = str(item.get("customStartTime") or "").strip()
        custom_end = str(item.get("customEndTime") or "").strip()
        is_custom = bool(item.get("isCustomTime")) or bool(custom_start and custom_end)

        if is_custom and custom_start and custom_end:
            start = parse_time(custom_start) or custom_start
            end = parse_time(custom_end) or custom_end
            start_section = 0
            end_section = 0
        elif start_section > 0:
            start = ""
            end = ""
            end_section = max(end_section, start_section)
        else:
            start_minutes = _int(item.get("startTotalMinutes"), -1)
            end_minutes = _int(item.get("endTotalMinutes"), -1)
            if start_minutes < 0 or end_minutes <= start_minutes:
                continue
            start = f"{start_minutes // 60:02d}:{start_minutes % 60:02d}"
            end = f"{end_minutes // 60:02d}:{end_minutes % 60:02d}"

        courses.append(
            Course(
                name=course_name,
                day=day,
                weeks=weeks,
                start=start,
                end=end,
                start_section=start_section or None,
                end_section=end_section or None,
                location=str(
                    item.get("classroom") or item.get("location") or item.get("position") or ""
                ).strip(),
                teacher=str(item.get("teacher") or "").strip(),
            )
        )

    if not courses:
        raise ImportFailure("Nexio 课表里没有可用的课程（缺少星期或时间）")
    if not sections:
        warnings.append("Nexio 课表没有自带作息，已使用插件配置的默认作息")

    return build_result(
        kind="nexio",
        source=source,
        name=name or str(data.get("schedule_name") or "Nexio 课表"),
        term_start=term_start,
        total_weeks=total_weeks or None,
        sections=sections,
        courses=courses,
        warnings=warnings,
        fallback_sections=fallback_sections,
        default_total_weeks=default_total_weeks,
        store_teacher=store_teacher,
        store_location=store_location,
        tz=tz,
    )


async def import_code(
    code: str,
    *,
    fetch,
    api_fetch=None,
    fallback_sections: list[Section] | None = None,
    default_total_weeks: int = 20,
    store_teacher: bool = False,
    store_location: bool = False,
    tz: str = "Asia/Shanghai",
) -> ImportResult:
    clean = _clean_code(code)
    if not clean:
        raise ImportFailure("Nexio 分享口令是空的")
    encoded = quote(clean, safe="")
    last_error = ""
    for base in SHARE_URLS:
        url = f"{base}?code={encoded}"
        try:
            if api_fetch is not None:
                status, text = await api_fetch(url)
                status = int(status or 0)
            else:
                text = await fetch(url)
                status = 200
        except ImportFailure as exc:
            message = str(exc)
            if "不允许访问" in message:
                raise
            if "HTTP 404" in message:
                raise ImportFailure(
                    "Nexio 分享口令不存在或已过期（口令 30 分钟内有效）"
                ) from exc
            if "HTTP 429" in message:
                raise ImportFailure("Nexio 口令服务请求过于频繁，请稍后再试") from exc
            last_error = message
            continue
        except Exception as exc:  # noqa: BLE001
            last_error = f"连接失败：{exc}"
            continue

        text = str(text or "")
        if not text.strip():
            last_error = "口令服务没有返回内容"
            continue
        if status == 404:
            raise ImportFailure("Nexio 分享口令不存在或已过期（口令 30 分钟内有效）")
        if status == 429:
            raise ImportFailure("Nexio 口令服务请求过于频繁，请稍后再试")
        try:
            obj = json.loads(text)
        except ValueError:
            if status >= 500:
                last_error = f"口令服务暂时不可用（HTTP {status}）"
                continue
            last_error = "Nexio 口令服务返回的不是有效 JSON"
            continue

        if status >= 400:
            reason = ""
            if isinstance(obj, dict):
                reason = str(obj.get("error") or "").strip()
            if reason:
                raise ImportFailure(reason)
            if status >= 500:
                last_error = f"口令服务暂时不可用（HTTP {status}）"
                continue
            raise ImportFailure(f"Nexio 口令服务返回 HTTP {status}")

        if not isinstance(obj, dict) or not obj.get("ok"):
            reason = ""
            if isinstance(obj, dict):
                reason = str(obj.get("error") or "").strip()
            raise ImportFailure(
                reason or "Nexio 分享口令不存在或已过期（口令 30 分钟内有效）"
            )
        data = obj.get("schedule_data")
        if not isinstance(data, dict):
            raise ImportFailure("Nexio 口令里没有课表数据")

        return parse_export(
            obj,
            fallback_sections=fallback_sections,
            default_total_weeks=default_total_weeks,
            store_teacher=store_teacher,
            store_location=store_location,
            tz=tz,
            name=str(obj.get("schedule_name") or "Nexio 分享课表"),
            source="Nexio 分享口令",
        )

    raise ImportFailure(last_error or "Nexio 口令服务连接失败，请稍后再试")
