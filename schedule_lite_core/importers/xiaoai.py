"""小爱课程表（分享链接）导入。

分享链接里的 token 是 base64 后的
    userId%26deviceId%26时间戳(ms)%26ctId
（老版本 token 可能只有 3 段，最后一段是 ctId）。
拿 userId/deviceId/ctId 请求 course-multi/table 即可拿到完整课表。
"""

from __future__ import annotations

import base64
import json
import re
import urllib.parse
from datetime import datetime, timedelta
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

TABLE_URL = "https://i.ai.mi.com/course-multi/table"
HEADERS = {
    "accept": "application/json",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "content-type": "application/json",
    "origin": "https://i.ai.mi.com",
    "referer": "https://i.ai.mi.com/h5/precache/ai-schedule/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
}


def looks_like_link(text: str) -> bool:
    low = (text or "").lower()
    if "linktoken=" in low:
        return True  # 小爱分享短链（cdn.cnbj1.fds.api.mi-img.com/.../?linkToken=）
    if re.search(r"[?&#]token=", low):
        return any(
            x in low
            for x in ("i.ai.mi.com", "ai-schedule", "aischedule", "xiaomi", "mi.com", "mi-img.com")
        )
    return False


def extract_token(text: str) -> str | None:
    m = re.search(r"(?:linkToken|token)=([0-9A-Za-z+/=_\-]+)", text or "")
    return m.group(1) if m else None


def decode_token(token: str) -> dict[str, str]:
    """解出 userId / deviceId / ctId。"""
    token = urllib.parse.unquote((token or "").strip())
    raw_text = token.replace("-", "+").replace("_", "/")
    raw_text += "=" * (-len(raw_text) % 4)
    try:
        decoded = base64.b64decode(raw_text, validate=False).decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        raise ImportFailure("小爱课表分享链接解析失败，请重新复制完整链接") from e

    # 新版 token 可能是 JSON；老版是 userId%26deviceId%26... 的拼接串
    try:
        obj = json.loads(decoded)
    except ValueError:
        obj = None
    if isinstance(obj, dict):
        user_id = obj.get("userId") or obj.get("user_id") or ""
        device_id = obj.get("deviceId") or obj.get("device_id") or ""
        ct_id = obj.get("ctId") or obj.get("ct_id") or obj.get("courseTableId") or ""
        if user_id and ct_id:
            return {
                "userId": str(user_id),
                "deviceId": str(device_id),
                "ctId": str(ct_id),
                "timestamp": str(obj.get("timestamp") or ""),
            }

    parts = [p.strip() for p in re.split(r"%26|&", decoded) if p.strip()]
    if len(parts) < 3:
        raise ImportFailure("小爱课表分享链接格式不对，请重新复制完整链接")

    def pick_numeric(candidates: list[str], *, prefer_long: bool = False) -> str:
        nums = [p for p in candidates if p.isdigit()]
        if not nums:
            return ""
        return max(nums, key=len) if prefer_long else nums[0]

    user_id = parts[0] if parts[0].isdigit() else pick_numeric(parts)
    device_id = parts[1] if len(parts) > 1 else ""
    if len(parts) >= 4:
        third, fourth = parts[2], parts[3]
        # 13 位毫秒时间戳和 ctId 的位置在不同版本里可能互换，用长度区分
        if fourth.isdigit() and len(fourth) >= 12 and third.isdigit() and len(third) < 12:
            ct_id = third
        else:
            ct_id = fourth
    else:
        ct_id = parts[2]
    timestamp = ""
    for p in parts[2:4]:
        if p.isdigit() and len(p) >= 12:
            timestamp = p
            break
    return {
        "userId": user_id,
        "deviceId": device_id,
        "ctId": ct_id,
        "timestamp": timestamp,
    }


async def fetch_table(session, creds: dict[str, str], timeout_s: int = 15) -> dict[str, Any]:
    params = {
        "ctId": str(creds.get("ctId") or ""),
        "userId": str(creds.get("userId") or ""),
        "deviceId": str(creds.get("deviceId") or ""),
        "sourceName": "course-app-browser",
    }
    timeout = None
    if session is not None:
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=timeout_s)
        except Exception:  # noqa: BLE001
            timeout = None
    try:
        async with session.get(TABLE_URL, params=params, headers=HEADERS, timeout=timeout) as resp:
            text = await resp.text()
    except ImportFailure:
        raise
    except Exception as e:  # noqa: BLE001
        raise ImportFailure(f"读取小爱课表失败：{e}") from e

    try:
        payload = json.loads(text)
    except ValueError as e:
        raise ImportFailure("小爱课表接口返回的不是 JSON，可能链接已失效") from e
    if str(payload.get("code")) not in ("0", "None"):
        raise ImportFailure(
            f"小爱课表接口返回错误：{payload.get('message') or payload.get('msg') or payload.get('code')}"
        )
    data = payload.get("data")
    if not isinstance(data, dict) or not data.get("courses"):
        raise ImportFailure("小爱课表里没有课程数据，可能是链接已过期或课表为空")
    return data


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def _course_sections(course: dict[str, Any]) -> tuple[int | None, int | None]:
    raw = course.get("sections", course.get("section", ""))
    if isinstance(raw, (list, tuple)):
        nums = [int(x) for x in raw if str(x).isdigit()]
    else:
        nums = [int(x) for x in re.findall(r"\d+", str(raw))]
    if not nums:
        return None, None
    return min(nums), max(nums)


def parse_table(
    data: dict[str, Any],
    *,
    fallback_sections: list[Section] | None = None,
    default_total_weeks: int = 20,
    store_teacher: bool = False,
    store_location: bool = False,
    tz: str = "Asia/Shanghai",
    now: datetime | None = None,
) -> ImportResult:
    """解析 course-multi/table 的 data 字段。"""
    table = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(table, dict):
        raise ImportFailure("小爱课表数据结构异常")

    warnings: list[str] = []
    setting = _as_dict(table.get("setting"))
    sections = parse_section_times(
        setting.get("sectionTimes")
        or table.get("sectionTimes")
        or setting.get("section_times")
    )

    extend = _as_dict(setting.get("extend"))
    start_raw = (
        extend.get("startSemester")
        or extend.get("startDate")
        or setting.get("startSemester")
        or table.get("startSemester")
    )
    term_start = parse_date(start_raw)

    present_week = 1
    for src in (setting, table, extend):
        try:
            if src.get("presentWeek") or src.get("currentWeek"):
                present_week = int(src.get("presentWeek") or src.get("currentWeek"))
                break
        except (TypeError, ValueError):
            continue

    if term_start is None:
        current = (now or datetime.now(get_tz(tz))).date()
        term_start = monday_of(current) - timedelta(weeks=max(present_week - 1, 0))
        warnings.append(f"课表里没有开学日期，已按当前第 {present_week} 周推算")

    total_weeks = 0
    for src in (setting, table, extend):
        for key in ("totalWeek", "totalWeeks", "maxWeek", "weeks"):
            try:
                if src.get(key):
                    total_weeks = max(total_weeks, int(src.get(key)))
            except (TypeError, ValueError):
                pass

    courses: list[Course] = []
    for raw in table.get("courses") or []:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name") or raw.get("courseName") or ""
        day = parse_day(raw.get("day"))
        weeks = parse_weeks(raw.get("weeks"))
        start_sec, end_sec = _course_sections(raw)
        courses.append(
            Course(
                name=str(name),
                day=day or 0,
                weeks=weeks,
                start=parse_time(raw.get("startTime") or raw.get("start")) or "",
                end=parse_time(raw.get("endTime") or raw.get("end")) or "",
                start_section=start_sec,
                end_section=end_sec,
                location=str(raw.get("position") or raw.get("location") or ""),
                teacher=str(raw.get("teacher") or ""),
            )
        )

    return build_result(
        kind="xiaoai",
        source="小爱课程表",
        name=str(table.get("name") or "小爱课表"),
        term_start=term_start,
        total_weeks=total_weeks,
        sections=sections,
        courses=courses,
        warnings=warnings,
        meta={"presentWeek": present_week, "ctId": table.get("id")},
        fallback_sections=fallback_sections,
        default_total_weeks=default_total_weeks,
        store_teacher=store_teacher,
        store_location=store_location,
        tz=tz,
    )


async def import_link(
    link: str,
    *,
    session,
    fallback_sections: list[Section] | None = None,
    default_total_weeks: int = 20,
    store_teacher: bool = False,
    store_location: bool = False,
    tz: str = "Asia/Shanghai",
) -> ImportResult:
    token = extract_token(link)
    if not token:
        raise ImportFailure("没有在链接里找到小爱课表的 token")
    creds = decode_token(token)
    data = await fetch_table(session, creds)
    return parse_table(
        data,
        fallback_sections=fallback_sections,
        default_total_weeks=default_total_weeks,
        store_teacher=store_teacher,
        store_location=store_location,
        tz=tz,
    )
