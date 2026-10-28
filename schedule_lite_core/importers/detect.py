"""导入入口：自动识别用户发来的链接、口令、JSON、ICS、CSV。"""

from __future__ import annotations

import json
import re
from typing import Awaitable, Callable
from urllib.parse import unquote, urlparse

from ..model import Section
from . import generic, ics, nexio, shiguang, wakeup
from .base import ImportFailure, ImportResult

Fetch = Callable[..., Awaitable[str]]
ApiFetch = Callable[[str], Awaitable[tuple[int, str]]]

HELP = (
    "支持这些导入方式：\n"
    "· WakeUp 课程表：分享口令（在线）或 .wakeup_schedule 备份文件\n"
    "· iCalendar：.ics 下载链接或文件内容\n"
    "· 拾光课程表：App 导出的课程表 JSON\n"
    "· Nexio 课程表：分享口令 / JSON / ICS\n"
    "· 通用：本插件导出的 JSON、其他软件整理的 JSON/CSV\n"
    "直接把链接、口令或文件发给机器人即可。"
)


def _looks_wakeup(text: str) -> bool:
    return "startWeek" in text and ("startNode" in text or '"node"' in text)


def _looks_json(text: str) -> bool:
    t = text.lstrip("\ufeff").lstrip()
    return t.startswith(("{", "["))


def parse_text(
    text: str,
    *,
    fallback_sections: list[Section] | None = None,
    default_total_weeks: int = 20,
    store_teacher: bool = False,
    store_location: bool = False,
    tz: str = "Asia/Shanghai",
    source_name: str = "",
) -> ImportResult:
    """解析已经拿到的文本内容（不发起网络请求）。"""
    body = (text or "").lstrip("\ufeff").strip()
    if not body:
        raise ImportFailure("导入内容是空的")

    upper_head = body[:3000].upper()
    if "BEGIN:VCALENDAR" in upper_head:
        return ics.parse_ics(
            body,
            fallback_sections=fallback_sections,
            default_total_weeks=default_total_weeks,
            store_teacher=store_teacher,
            store_location=store_location,
            tz=tz,
            name=source_name or "iCalendar 课表",
        )

    if _looks_json(body):
        try:
            obj = json.loads(body)
        except ValueError:
            if _looks_wakeup(body):
                return wakeup.parse_backup(
                    body,
                    fallback_sections=fallback_sections,
                    default_total_weeks=default_total_weeks,
                    store_teacher=store_teacher,
                    store_location=store_location,
                    tz=tz,
                )
            raise ImportFailure("JSON 解析失败，请检查内容是否完整")
        if nexio.looks_like_export(obj):
            return nexio.parse_export(
                obj,
                fallback_sections=fallback_sections,
                default_total_weeks=default_total_weeks,
                store_teacher=store_teacher,
                store_location=store_location,
                tz=tz,
                name=source_name or "Nexio 课表",
            )
        if shiguang.looks_like(obj):
            return shiguang.parse_export(
                obj,
                fallback_sections=fallback_sections,
                default_total_weeks=default_total_weeks,
                store_teacher=store_teacher,
                store_location=store_location,
                tz=tz,
                name=source_name or "拾光课表",
            )
        if (
            isinstance(obj, list)
            and obj
            and isinstance(obj[0], dict)
            and "startWeek" in obj[0]
            and ("startNode" in obj[0] or "weeks" in obj[0])
        ):
            return wakeup.parse_backup(
                obj,
                fallback_sections=fallback_sections,
                default_total_weeks=default_total_weeks,
                store_teacher=store_teacher,
                store_location=store_location,
                tz=tz,
            )
        return generic.parse_json(
            obj,
            fallback_sections=fallback_sections,
            default_total_weeks=default_total_weeks,
            store_teacher=store_teacher,
            store_location=store_location,
            tz=tz,
            name=source_name or "导入的课表",
        )

    if _looks_wakeup(body):
        return wakeup.parse_backup(
            body,
            fallback_sections=fallback_sections,
            default_total_weeks=default_total_weeks,
            store_teacher=store_teacher,
            store_location=store_location,
            tz=tz,
        )

    if generic.looks_like_csv(body):
        return generic.parse_csv(
            body,
            fallback_sections=fallback_sections,
            default_total_weeks=default_total_weeks,
            store_teacher=store_teacher,
            store_location=store_location,
            tz=tz,
        )

    raise ImportFailure("没认出这个格式。\n" + HELP)


async def import_any(
    raw: str,
    *,
    fetch: Fetch,
    api_fetch: ApiFetch | None = None,
    fallback_sections: list[Section] | None = None,
    default_total_weeks: int = 20,
    store_teacher: bool = False,
    store_location: bool = False,
    tz: str = "Asia/Shanghai",
    allow_network: bool = True,
    session=None,
) -> ImportResult:
    text = (raw or "").strip()
    if not text:
        raise ImportFailure(HELP)

    # 1) 普通链接（iCalendar / ICS 直链）
    if re.match(r"^(?:https?|webcal)://", text, re.I):
        if not allow_network:
            raise ImportFailure("当前配置禁止联网导入")
        url = re.sub(r"^webcal://", "https://", text, flags=re.I)
        content = await fetch(url)
        if not content or not content.strip():
            raise ImportFailure("链接内容是空的")
        head = content.lstrip()[:200].lower()
        if head.startswith("<!doctype") or head.startswith("<html"):
            raise ImportFailure(
                "这个链接打开是网页而不是课表数据。请使用 .ics 直链，或复制网页里的课表 JSON。"
            )
        return parse_text(
            content,
            fallback_sections=fallback_sections,
            default_total_weeks=default_total_weeks,
            store_teacher=store_teacher,
            store_location=store_location,
            tz=tz,
            source_name=unquote(urlparse(url).path.rsplit("/", 1)[-1]) or "iCalendar 课表",
        )

    # 2) Nexio 分享口令（中文趣味短语，30 分钟内有效）
    nexio_code = nexio.extract_code(text)
    is_local_payload = _looks_json(text) or "BEGIN:VCALENDAR" in text[:200].upper()
    if (
        nexio_code
        and not is_local_payload
        and re.search(r"[一-龥]", nexio_code)
        and not generic.looks_like_csv(text)
    ):
        if not allow_network:
            raise ImportFailure("当前配置禁止联网导入")
        return await nexio.import_code(
            nexio_code,
            fetch=fetch,
            api_fetch=api_fetch,
            fallback_sections=fallback_sections,
            default_total_weeks=default_total_weeks,
            store_teacher=store_teacher,
            store_location=store_location,
            tz=tz,
        )

    # 3) WakeUp 分享口令（形如「xxxxx」，或一整串字母数字）
    code = wakeup.extract_code(text)
    quoted_code = bool(re.search(r"「([0-9A-Za-z\-_]{6,})」", text))
    if (
        code
        and not is_local_payload
        and (quoted_code or not generic.looks_like_csv(text))
    ):
        if not allow_network:
            raise ImportFailure("当前配置禁止联网导入")
        try:
            return await wakeup.import_code(
                code,
                session=session,
                fallback_sections=fallback_sections,
                default_total_weeks=default_total_weeks,
                store_teacher=store_teacher,
                store_location=store_location,
                tz=tz,
            )
        except ImportFailure:
            if not nexio_code:
                raise

    # 4) Nexio 分享口令（纯字母数字时与 WakeUp 口令同形，WakeUp 失败后再试）
    if nexio_code and not is_local_payload:
        if not allow_network:
            raise ImportFailure("当前配置禁止联网导入")
        return await nexio.import_code(
            nexio_code,
            fetch=fetch,
            api_fetch=api_fetch,
            fallback_sections=fallback_sections,
            default_total_weeks=default_total_weeks,
            store_teacher=store_teacher,
            store_location=store_location,
            tz=tz,
        )

    # 5) 本地文本：ICS / JSON / CSV / WakeUp 备份
    return parse_text(
        text,
        fallback_sections=fallback_sections,
        default_total_weeks=default_total_weeks,
        store_teacher=store_teacher,
        store_location=store_location,
        tz=tz,
    )
