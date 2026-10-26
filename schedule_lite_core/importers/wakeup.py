"""WakeUp 课程表导入：分享口令（在线）与备份文件（本地）两种。

分享接口返回的 data 是多行 JSON：
  第 1 段：作息节次（node/startTime/endTime）
  第 2 段：课表信息（tableName/startDate）
  第 3 段：课程名称与颜色
  第 4 段：课程安排（day/startWeek/endWeek/startNode/step/room/teacher/type）
不同版本行号可能变化，所以这里按内容分类，不按行号硬编码。
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..model import Course, Section
from ..normalize import (
    get_tz,
    monday_of,
    parse_date,
    parse_day,
    parse_section_times,
    parse_time,
    parse_weeks,
)
from .base import ImportFailure, ImportResult, build_result

SHARE_HOSTS = (
    "https://api.wakeup.fun/share_schedule/get",
    "https://i.wakeup.fun/share_schedule/get",
)
HEADERS = {
    "version": "280",
    "user-agent": "okhttp/4.9.0",
    "accept": "application/json",
}
_CODE_RE = re.compile(r"^[0-9A-Za-z_-]{12,}$")


def looks_like_code(text: str) -> bool:
    t = (text or "").strip()
    return bool(_CODE_RE.fullmatch(t)) and not t.startswith(("http://", "https://"))


def extract_code(text: str) -> str | None:
    t = (text or "").strip()
    m = re.search(r"「([0-9A-Za-z\-_]{6,})」", t)
    if m:
        return m.group(1)
    return t if looks_like_code(t) else None


async def fetch_share(session, code: str, timeout_s: int = 15) -> str:
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=timeout_s)
    last_error = ""
    for host in SHARE_HOSTS:
        try:
            async with session.get(
                host, params={"key": code}, headers=HEADERS, timeout=timeout
            ) as resp:
                text = await resp.text()
        except Exception as e:  # noqa: BLE001
            last_error = str(e)
            continue
        try:
            payload = json.loads(text)
        except ValueError:
            last_error = "接口返回的不是 JSON"
            continue
        data = payload.get("data")
        if data:
            if isinstance(data, str):
                return data
            return json.dumps(data, ensure_ascii=False)
        last_error = str(payload.get("message") or payload.get("msg") or payload.get("code") or "无数据")
    raise ImportFailure(f"读取 WakeUp 课表失败：{last_error or '口令无效或已过期'}")


def _classify(obj: Any, nodes: list, info: dict, infos: list, instances: list) -> None:
    if isinstance(obj, list):
        for item in obj:
            _classify(item, nodes, info, infos, instances)
        return
    if not isinstance(obj, dict):
        return
    if "tableName" in obj or "startDate" in obj:
        info.update(obj)
    elif "node" in obj and ("startTime" in obj or "endTime" in obj):
        nodes.append(obj)
    elif "courseName" in obj:
        infos.append(obj)
    elif "startWeek" in obj or "startNode" in obj:
        instances.append(obj)
    elif isinstance(obj.get("courses"), list):
        _classify(obj["courses"], nodes, info, infos, instances)


def _parse_instances(text_or_obj: Any) -> tuple[list, dict, list, list]:
    nodes: list = []
    info: dict = {}
    infos: list = []
    instances: list = []
    if isinstance(text_or_obj, (dict, list)):
        _classify(text_or_obj, nodes, info, infos, instances)
        return nodes, info, infos, instances
    for line in str(text_or_obj).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        _classify(obj, nodes, info, infos, instances)
    return nodes, info, infos, instances


def _parity_weeks(inst: dict[str, Any]) -> list[int]:
    if inst.get("weeks") not in (None, "", []):
        return parse_weeks(inst.get("weeks"))
    try:
        start = int(inst.get("startWeek") or 0)
        end = int(inst.get("endWeek") or start)
    except (TypeError, ValueError):
        return []
    if start <= 0 or end < start:
        return []
    try:
        type_value = int(inst.get("type") or 0)
    except (TypeError, ValueError):
        type_value = 0
    # WakeUp: 0=每周, 1=单周, 2=双周（2 % 2 == 0，所以要先取模再比较）
    return [
        w
        for w in range(start, end + 1)
        if type_value == 0 or w % 2 == type_value % 2
    ]


def parse_backup(
    data: Any,
    *,
    fallback_sections: list[Section] | None = None,
    default_total_weeks: int = 20,
    store_teacher: bool = False,
    store_location: bool = False,
    tz: str = "Asia/Shanghai",
) -> ImportResult:
    nodes, info, infos, instances = _parse_instances(data)
    if not instances:
        raise ImportFailure("没有在 WakeUp 数据里找到课程安排")

    warnings: list[str] = []
    node_map: dict[int, dict[str, Any]] = {}
    for node in nodes:
        try:
            node_map[int(node.get("node"))] = node
        except (TypeError, ValueError):
            continue

    sections = parse_section_times(nodes)
    course_info = {str(x.get("id")): x for x in infos if x.get("id") is not None}

    term_start = parse_date(info.get("startDate"))
    if term_start is None:
        warnings.append("WakeUp 数据里没有开学日期，将按当前周推算")

    total_weeks = 0
    for key in ("totalWeek", "totalWeeks", "maxWeek", "weeks"):
        try:
            if info.get(key):
                total_weeks = max(total_weeks, int(info.get(key)))
        except (TypeError, ValueError):
            pass

    # 同一条安排可能被拆成多行（单周/双周），先按键合并周次
    grouped: dict[tuple, Course] = {}
    order: list[tuple] = []
    for inst in instances:
        if not isinstance(inst, dict):
            continue
        weeks = _parity_weeks(inst)
        if not weeks:
            continue
        course_id = str(inst.get("id") or "")
        meta = course_info.get(course_id, {})
        name = meta.get("courseName") or inst.get("courseName") or "未命名课程"
        day = parse_day(inst.get("day")) or 0

        start_time = end_time = ""
        if inst.get("ownTime") and inst.get("startTime"):
            start_time = parse_time(inst.get("startTime")) or ""
            end_time = parse_time(inst.get("endTime")) or ""
        else:
            try:
                start_node = int(inst.get("startNode") or 0)
                step = max(int(inst.get("step") or 1), 1)
            except (TypeError, ValueError):
                start_node, step = 0, 1
            first = node_map.get(start_node)
            last = node_map.get(start_node + step - 1)
            if first and last:
                start_time = parse_time(first.get("startTime")) or ""
                end_time = parse_time(last.get("endTime")) or ""

        start_sec = end_sec = None
        try:
            if not inst.get("ownTime") and inst.get("startNode"):
                start_sec = int(inst.get("startNode"))
                end_sec = start_sec + max(int(inst.get("step") or 1), 1) - 1
        except (TypeError, ValueError):
            start_sec = end_sec = None

        key = (
            course_id,
            day,
            start_time,
            end_time,
            start_sec,
            end_sec,
            str(inst.get("room") or ""),
            str(inst.get("teacher") or ""),
            bool(inst.get("ownTime")),
        )
        if key in grouped:
            grouped[key].weeks = sorted(set(grouped[key].weeks) | set(weeks))
        else:
            grouped[key] = Course(
                name=str(name),
                day=day,
                weeks=weeks,
                start=start_time,
                end=end_time,
                start_section=start_sec,
                end_section=end_sec,
                location=str(inst.get("room") or ""),
                teacher=str(inst.get("teacher") or meta.get("teacher") or ""),
            )
            order.append(key)
        total_weeks = max(total_weeks, max(weeks))

    courses = [grouped[k] for k in order]
    if not courses:
        raise ImportFailure("WakeUp 数据里的课程都没有有效周次")

    return build_result(
        kind="wakeup",
        source="WakeUp 课程表",
        name=str(info.get("tableName") or "WakeUp 课表"),
        term_start=term_start,
        total_weeks=total_weeks,
        sections=sections,
        courses=courses,
        warnings=warnings,
        meta={"startDate": info.get("startDate")},
        fallback_sections=fallback_sections,
        default_total_weeks=default_total_weeks,
        store_teacher=store_teacher,
        store_location=store_location,
        tz=tz,
    )


async def import_code(
    code: str,
    *,
    session,
    fallback_sections: list[Section] | None = None,
    default_total_weeks: int = 20,
    store_teacher: bool = False,
    store_location: bool = False,
    tz: str = "Asia/Shanghai",
) -> ImportResult:
    payload = await fetch_share(session, code)
    return parse_backup(
        payload,
        fallback_sections=fallback_sections,
        default_total_weeks=default_total_weeks,
        store_teacher=store_teacher,
        store_location=store_location,
        tz=tz,
    )
