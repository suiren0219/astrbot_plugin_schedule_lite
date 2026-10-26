"""国务院节假日与调休数据：本地缓存，查询时零网络请求。

数据源优先使用 timor.tech 的年度接口（字段包含调休补班），失败时回退到
holiday-cn 的 GitHub 静态数据。两者都整理自国务院办公厅放假通知，官方本身
没有公开的 JSON API，所以这里要做本地缓存和来源标注。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .normalize import get_tz

DEFAULT_API = "https://timor.tech/api/holiday/year/{year}"
FALLBACK_API = "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json"


@dataclass
class HolidayCalendar:
    year: int
    days: dict[str, dict[str, Any]] = field(default_factory=dict)
    source: str = ""
    updated_at: str = ""
    # 全局补班映射：2026-10-10 -> 4（按周四课表）。个人设置优先于这里。
    makeup_weekdays: dict[str, int] = field(default_factory=dict)

    # ---------------------------------------------------------------- 查询

    def is_off(self, day: date) -> bool:
        item = self.days.get(day.isoformat())
        return bool(item and item.get("off"))

    def is_workday(self, day: date) -> bool:
        """国务院标注的调休补班日（通常是周末上班）。"""
        item = self.days.get(day.isoformat())
        return bool(item and not item.get("off"))

    def label(self, day: date) -> str:
        item = self.days.get(day.isoformat())
        if not item:
            return ""
        name = str(item.get("name") or "")
        return name

    def next_off_day(self, day: date) -> date | None:
        """从 day 往后（含当天）找最近的休息日，用于判断「放假前夜」。"""
        for offset in range(0, 15):
            target = day + timedelta(days=offset)
            if self.is_off(target):
                return target
        return None

    def holiday_eve(self, day: date) -> bool:
        """day 的次日是法定假日，且 day 本身不是假日。"""
        return not self.is_off(day) and self.is_off(day + timedelta(days=1))

    # ---------------------------------------------------------------- 缓存

    @classmethod
    def load(cls, path: Path, year: int) -> "HolidayCalendar":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls(year=year)
        if not isinstance(payload, dict) or int(payload.get("year") or 0) != year:
            return cls(year=year)
        days = payload.get("days")
        return cls(
            year=year,
            days=days if isinstance(days, dict) else {},
            source=str(payload.get("source") or ""),
            updated_at=str(payload.get("updated_at") or ""),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "year": self.year,
            "source": self.source,
            "updated_at": self.updated_at,
            "days": self.days,
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def stale(self, max_age_hours: int) -> bool:
        if not self.updated_at:
            return True
        try:
            updated = datetime.fromisoformat(self.updated_at)
        except ValueError:
            return True
        return datetime.now(get_tz("Asia/Shanghai")) - updated > timedelta(hours=max_age_hours)


def _parse_timor(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = payload.get("holiday")
    if not isinstance(raw, dict):
        return {}
    days: dict[str, dict[str, Any]] = {}
    for item in raw.values():
        if not isinstance(item, dict):
            continue
        day = str(item.get("date") or "")
        if not day:
            continue
        days[day] = {
            "name": str(item.get("name") or ""),
            "off": bool(item.get("holiday")),
        }
    return days


def _parse_holiday_cn(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    days: dict[str, dict[str, Any]] = {}
    for item in payload.get("days") or []:
        if not isinstance(item, dict):
            continue
        day = str(item.get("date") or "")
        if not day:
            continue
        days[day] = {
            "name": str(item.get("name") or ""),
            "off": bool(item.get("isOffDay")),
        }
    return days


async def fetch_calendar(
    session,
    year: int,
    *,
    api_url: str = "",
    timeout_s: int = 15,
) -> HolidayCalendar:
    """抓取并标准化年度节假日；失败时抛异常，由调用方决定是否沿用旧缓存。"""
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=timeout_s)
    headers = {"user-agent": "Mozilla/5.0 astrbot-schedule-lite"}
    primary = (api_url or DEFAULT_API).format(year=year)
    fallback = FALLBACK_API.format(year=year)
    errors: list[str] = []

    for url, parser, source in (
        (primary, _parse_timor, "timor.tech"),
        (fallback, _parse_holiday_cn, "holiday-cn"),
    ):
        try:
            async with session.get(url, headers=headers, timeout=timeout) as resp:
                if resp.status >= 400:
                    errors.append(f"{source}: HTTP {resp.status}")
                    continue
                payload = await resp.json(content_type=None)
                days = parser(payload if isinstance(payload, dict) else {})
                if days:
                    return HolidayCalendar(
                        year=year,
                        days=days,
                        source=source,
                        updated_at=datetime.now(get_tz("Asia/Shanghai")).isoformat(
                            timespec="seconds"
                        ),
                    )
                errors.append(f"{source}: 没有数据")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{source}: {exc}")

    raise RuntimeError("；".join(errors) or "节假日接口没有返回数据")
