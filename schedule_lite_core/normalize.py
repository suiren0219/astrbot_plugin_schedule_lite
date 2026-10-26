"""导入数据归一化：周次、节次、时间、日期、去重合并。

课表软件之间最大的差异不在课程名，而在“周次怎么写、时间怎么表示”。
这里把各家格式统一成：day=1..7、weeks=[int]、start/end="HH:MM"。
"""

from __future__ import annotations

import ast
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None  # type: ignore

from .model import Course, Section

WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
DEFAULT_TIMEZONE = "Asia/Shanghai"

# 常见高校作息，仅在导入数据没有任何作息信息时兜底；实际以课表数据为准
DEFAULT_SECTION_TIMES = (
    (1, "08:00", "08:45"),
    (2, "08:55", "09:40"),
    (3, "10:00", "10:45"),
    (4, "10:55", "11:40"),
    (5, "14:00", "14:45"),
    (6, "14:55", "15:40"),
    (7, "16:00", "16:45"),
    (8, "16:55", "17:40"),
    (9, "19:00", "19:45"),
    (10, "19:55", "20:40"),
    (11, "20:50", "21:35"),
    (12, "21:45", "22:30"),
)

DEFAULT_SECTION_TEXT = "\n".join(
    f"{i} {s}-{e}" for i, s, e in DEFAULT_SECTION_TIMES
)

_FW_TRANS = str.maketrans("０１２３４５６７８９，、；：（）【】", "0123456789,,;:()[]")


def get_tz(name: str | None):
    """取时区；Windows 上可能没有 tzdata，退化到固定 UTC+8 / UTC。"""
    name = (name or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    name = {
        "China Standard Time": "Asia/Shanghai",
        "Taipei Standard Time": "Asia/Taipei",
        "Tokyo Standard Time": "Asia/Tokyo",
        "Korea Standard Time": "Asia/Seoul",
        "UTC": "UTC",
    }.get(name, name)
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    if name in ("Asia/Shanghai", "Asia/Chongqing", "PRC", "GMT+8", "UTC+8"):
        return timezone(timedelta(hours=8))
    return timezone.utc


def now_in_tz(name: str | None = None) -> datetime:
    return datetime.now(get_tz(name))


# ---------------------------------------------------------------- 星期 / 周次


_DAY_WORDS = {
    "周一": 1, "星期二": 2, "礼拜三": 3, "周四": 4, "星期五": 5, "周六": 6, "星期日": 7,
    "周天": 7, "星期天": 7, "礼拜天": 7, "礼拜日": 7,
}
for _i, _cn in enumerate(("一", "二", "三", "四", "五", "六", "日")):
    _DAY_WORDS[f"周{_cn}"] = _i + 1
    _DAY_WORDS[f"星期{_cn}"] = _i + 1
    _DAY_WORDS[f"礼拜{_cn}"] = _i + 1

_EN_DAYS = {
    "MO": 1, "MON": 1, "MONDAY": 1,
    "TU": 2, "TUE": 2, "TUESDAY": 2,
    "WE": 3, "WED": 3, "WEDNESDAY": 3,
    "TH": 4, "THU": 4, "THURSDAY": 4,
    "FR": 5, "FRI": 5, "FRIDAY": 5,
    "SA": 6, "SAT": 6, "SATURDAY": 6,
    "SU": 7, "SUN": 7, "SUNDAY": 7,
}


def parse_day(value: Any) -> int | None:
    """把各种“星期几”写法转成 1..7（周一=1）。0 视为周日。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = int(value)
        if 1 <= n <= 7:
            return n
        if n == 0:
            return 7
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return parse_day(int(text))
    key = text.replace(" ", "")
    if key in _DAY_WORDS:
        return _DAY_WORDS[key]
    upper = key.upper()
    if upper in _EN_DAYS:
        return _EN_DAYS[upper]
    if upper.endswith("DAY") and upper in _EN_DAYS:
        return _EN_DAYS[upper]
    # 兼容 “星期一(1)” / “Mon.” 之类
    m = re.search(r"[（(]?\s*([1-7])\s*[)）]?$", key)
    if m:
        return int(m.group(1))
    for word, idx in _DAY_WORDS.items():
        if word in key:
            return idx
    m = re.match(r"^([A-Za-z]{2,9})", key)
    if m and m.group(1).upper() in _EN_DAYS:
        return _EN_DAYS[m.group(1).upper()]
    return None


def _normalize_weeks_text(text: str) -> str:
    t = str(text).translate(_FW_TRANS)
    t = t.replace("周次", "").replace("周", "").replace("第", "")
    for ch in ("－", "—", "–", "~", "～", "至", "到", "－"):
        t = t.replace(ch, "-")
    t = t.replace("／", "/").replace("单双", "")
    return t


def _expand_token(core: str) -> list[int]:
    core = core.strip("-/")
    if not core:
        return []
    m = re.fullmatch(r"(\d+)-(\d+)(?:/(\d+))?", core)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        step = int(m.group(3) or 1)
        if a > b:
            a, b = b, a
        if step <= 0:
            step = 1
        return list(range(a, b + 1, step))
    # 剩余情况：把这段里的数字都当单周处理（如 “1-4-7” 这种脏数据）
    return [int(x) for x in re.findall(r"\d+", core)]


def parse_weeks(value: Any, total_weeks: int | None = None) -> list[int]:
    """解析周次，支持：

    - [1, 2, 3] / 1 / "1,2,3"
    - "1-16周"、"第1-16周"、"1~16"、"1至16"
    - "1-16周(单)"、"1-16单周"、"1-16双周"
    - "1-8单,10-16双"（分段奇偶）
    - "1-16/2"（步长）
    """
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        n = int(value)
        if 1 <= n <= 60:
            return [n]
        return []
    if isinstance(value, (list, tuple, set)):
        out: set[int] = set()
        for item in value:
            if isinstance(item, str) and re.search(r"[-~—至,，、\s/]", item):
                out.update(parse_weeks(item, None))
            else:
                try:
                    n = int(item)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    continue
                if 1 <= n <= 60:
                    out.add(n)
        return sorted(out)

    text = str(value).strip()
    if not text:
        return []
    t = _normalize_weeks_text(text)
    has_odd = ("单" in t) or bool(re.search(r"\bodd\b", t, re.I))
    has_even = ("双" in t) or bool(re.search(r"\beven\b", t, re.I))
    global_parity: int | None = None
    if has_odd and not has_even:
        global_parity = 1
    elif has_even and not has_odd:
        global_parity = 0

    out: set[int] = set()
    for seg in re.split(r"[,;|\s]+", t):
        if not seg:
            continue
        parity: int | None = None
        if "单" in seg or re.search(r"\bodd\b", seg, re.I):
            parity = 1
        elif "双" in seg or re.search(r"\beven\b", seg, re.I):
            parity = 0
        else:
            parity = global_parity
        core = re.sub(r"[^\d\-/]", "", seg)
        for n in _expand_token(core):
            if parity is not None and n % 2 != parity:
                continue
            if 1 <= n <= 60:
                out.add(n)
    return sorted(out)


def compress_weeks(weeks: Iterable[int]) -> str:
    nums = sorted({int(w) for w in weeks if int(w) > 0})
    if not nums:
        return ""
    runs: list[tuple[int, int]] = []
    start = prev = nums[0]
    for w in nums[1:]:
        if w == prev + 1:
            prev = w
            continue
        runs.append((start, prev))
        start = prev = w
    runs.append((start, prev))
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


def describe_weeks(weeks: Iterable[int]) -> str:
    nums = sorted({int(w) for w in weeks if int(w) > 0})
    if not nums:
        return "无"
    if (
        len(nums) >= 3
        and nums[0] % 2 == nums[-1] % 2
        and nums == list(range(nums[0], nums[-1] + 1, 2))
    ):
        return f"{nums[0]}-{nums[-1]}周({'单' if nums[0] % 2 else '双'})"
    return compress_weeks(nums) + "周"


# ---------------------------------------------------------------- 时间 / 节次


def parse_time(value: Any) -> str | None:
    """解析 HH:MM / HH：MM / HHMM / 8点30 / 8:00:00，返回 "HH:MM"。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = int(value)
        if 0 <= n <= 2359 and 0 <= n % 100 < 60:
            return f"{n // 100:02d}:{n % 100:02d}"
        return None
    text = str(value).strip()
    if not text:
        return None
    text = (
        text.replace("：", ":")
        .replace("．", ":")
        .replace(".", ":")
        .replace("点", ":")
        .replace("分", "")
    )
    m = re.fullmatch(r"(\d{1,2}):(\d{0,2})(?::\d{1,2})?", text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
    else:
        m = re.fullmatch(r"(\d{3,4})", text)
        if m and 0 <= int(text[:-2]) <= 23 and 0 <= int(text[-2:]) <= 59:
            hour, minute = int(text[:-2]), int(text[-2:])
        elif text.isdigit() and 0 <= int(text) <= 1439:
            # 有些导出把时间写成“距 0 点的分钟数”，如 480 = 08:00
            hour, minute = divmod(int(text), 60)
        elif re.fullmatch(r"\d{1,2}", text):
            hour, minute = int(text), 0
        else:
            return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def minutes_of(hhmm: str | None) -> int | None:
    if not hhmm:
        return None
    t = parse_time(hhmm)
    if not t:
        return None
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def fmt_minutes(total: int) -> str:
    total = max(0, int(total))
    if total >= 60:
        hours, mins = divmod(total, 60)
        return f"{hours}小时" if mins == 0 else f"{hours}小时{mins}分"
    return f"{total}分钟"


def _split_time_range(text: str) -> tuple[str | None, str | None]:
    text = text.replace("~", "-").replace("—", "-").replace("～", "-").replace("至", "-")
    parts = re.split(r"[-–]", text, maxsplit=1)
    if len(parts) < 2:
        return parse_time(parts[0]), None
    return parse_time(parts[0]), parse_time(parts[1])


def parse_section_text(text: str) -> list[Section]:
    """解析 “1 08:00-08:45” / “1: 08:00-08:45” / “1,08:00,08:45” 多行文本。"""
    out: dict[int, Section] = {}
    for raw_line in str(text).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(
            r"^(?:第)?\s*(\d+)\s*[节次]?\D*?"
            r"(\d{1,2}\s*[:：.]\s*\d{1,2})\D+"
            r"(\d{1,2}\s*[:：.]\s*\d{1,2})",
            line,
        )
        if not m:
            continue
        idx = int(m.group(1))
        start, end = parse_time(m.group(2)), parse_time(m.group(3))
        if idx > 0 and start and end:
            out[idx] = Section(index=idx, start=start, end=end)
    return [out[k] for k in sorted(out)]


def parse_section_times(value: Any) -> list[Section]:
    """解析各家作息字段，支持 list/dict/JSON 字符串/多行文本。"""
    if value is None:
        return []
    if isinstance(value, Section):
        return [value]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith(("{", "[")):
            try:
                return parse_section_times(json.loads(text))
            except (TypeError, ValueError):
                # 小爱课程的 sectionTimes 是 Python repr 字符串（单引号），不是 JSON
                try:
                    return parse_section_times(ast.literal_eval(text))
                except (ValueError, SyntaxError):
                    return parse_section_text(text)
        return parse_section_text(text)

    out: dict[int, Section] = {}

    def add(index: Any, start: Any, end: Any, alias: Any = "") -> None:
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return
        s, e = parse_time(start), parse_time(end)
        if idx <= 0 or not s or not e:
            return
        out[idx] = Section(index=idx, start=s, end=e, alias=str(alias or ""))

    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, dict):
                add(
                    item.get("i", item.get("index", item.get("number", key))),
                    item.get("s", item.get("start", item.get("startTime"))),
                    item.get("e", item.get("end", item.get("endTime"))),
                    item.get("alias", item.get("name", "")),
                )
            elif isinstance(item, str):
                s, e = _split_time_range(item)
                add(key, s, e)
            elif isinstance(item, Section):
                out[item.index] = item
        return [out[k] for k in sorted(out)]

    if isinstance(value, (list, tuple)):
        auto_index = 1
        for item in value:
            if isinstance(item, Section):
                out[item.index] = item
                auto_index = max(auto_index, item.index + 1)
            elif isinstance(item, str):
                parsed = parse_section_text(item)
                if not parsed:
                    # 允许 ["08:00-08:45", ...] 这种只有时间的列表，按顺序编号
                    start, end = _split_time_range(item)
                    if start and end:
                        out[auto_index] = Section(index=auto_index, start=start, end=end)
                        auto_index += 1
                    continue
                for sec in parsed:
                    out[sec.index] = sec
                auto_index = max(auto_index, max(s.index for s in parsed) + 1)
            elif isinstance(item, dict):
                add(
                    item.get("i", item.get("index", item.get("number", item.get("node", item.get("section"))))),
                    item.get("s", item.get("start", item.get("startTime", item.get("begin")))),
                    item.get("e", item.get("end", item.get("endTime", item.get("finish")))),
                    item.get("alias", item.get("name", "")),
                )
    return [out[k] for k in sorted(out)]


def default_sections() -> list[Section]:
    return [Section(index=i, start=s, end=e) for i, s, e in DEFAULT_SECTION_TIMES]


# ---------------------------------------------------------------- 日期 / 学期


def parse_date(value: Any) -> date | None:
    """解析日期/时间戳，兼容 2026-09-01、2026/9/1、20260901、2026年9月1日、毫秒时间戳。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        n = float(value)
        if n > 1e11:  # 毫秒
            n /= 1000.0
        try:
            return datetime.fromtimestamp(n, get_tz(DEFAULT_TIMEZONE)).date()
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None
    if text.isdigit() and len(text) >= 10:
        # 字符串形式的时间戳（小爱课表的 startSemester 是 13 位毫秒字符串）
        return parse_date(int(text))
    text = text.replace("：", ":")
    m = re.match(r"^(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*日?", text)
    if m:
        try:
            return date(date.today().year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_of(term_start: date | None, d: date) -> int:
    """第几周（第 1 周从 term_start 那个周一开始）。term_start 为空时返回 1。"""
    if not term_start:
        return 1
    return (d - term_start).days // 7 + 1


def fmt_date_cn(d: date | None) -> str:
    if not d:
        return ""
    return f"{d.year}/{d.month:02d}/{d.day:02d}"


def weekday_cn(d: date) -> str:
    return WEEKDAY_CN[d.weekday()]


# ---------------------------------------------------------------- 课程整理


def normalize_courses(
    courses: Iterable[Course],
    sections: Iterable[Section] = (),
) -> tuple[list[Course], int, int, list[str]]:
    """校验、补全时间、合并重复安排。

    返回 (courses, merged_count, dropped_count, warnings)。
    """
    sec_map = {s.index: s for s in sections}
    kept: list[Course] = []
    dropped = 0
    warnings: list[str] = []

    for c in courses:
        name = re.sub(r"\s+", " ", (c.name or "")).strip()
        if not name:
            dropped += 1
            continue
        if not (1 <= int(c.day) <= 7):
            dropped += 1
            warnings.append(f"「{name}」星期数据异常（{c.day}），已忽略")
            continue
        weeks = sorted({int(w) for w in c.weeks if int(w) > 0})
        if not weeks:
            dropped += 1
            warnings.append(f"「{name}」没有可识别的周次，已忽略")
            continue

        start, end = c.start, c.end
        if (not start or not end) and c.start_section and c.end_section:
            sec_start = sec_map.get(int(c.start_section))
            sec_end = sec_map.get(int(c.end_section))
            if sec_start and sec_end:
                start = start or sec_start.start
                end = end or sec_end.end
        start, end = parse_time(start), parse_time(end)
        if not start or not end:
            dropped += 1
            warnings.append(f"「{name}」缺少上课时间，已忽略")
            continue
        if (minutes_of(end) or 0) <= (minutes_of(start) or 0):
            dropped += 1
            warnings.append(f"「{name}」时间异常（{start}-{end}），已忽略")
            continue

        kept.append(
            Course(
                name=name,
                day=int(c.day),
                weeks=weeks,
                start=start,
                end=end,
                start_section=c.start_section,
                end_section=c.end_section,
                location=(c.location or "").strip(),
                teacher=(c.teacher or "").strip(),
            )
        )

    # 同一门课、同一天、同一时间段、同一地点 → 合并周次
    merged = 0
    merged_map: dict[tuple, Course] = {}
    order: list[tuple] = []
    for c in kept:
        key = (c.name, c.day, c.start, c.end, c.location)
        if key in merged_map:
            old = merged_map[key]
            old.weeks = sorted(set(old.weeks) | set(c.weeks))
            if not old.teacher and c.teacher:
                old.teacher = c.teacher
            if old.start_section is None and c.start_section is not None:
                old.start_section, old.end_section = c.start_section, c.end_section
            merged += 1
        else:
            merged_map[key] = c
            order.append(key)

    result = [merged_map[k] for k in order]
    result.sort(key=lambda c: (c.day, minutes_of(c.start) or 0))
    return result, merged, dropped, warnings


def describe_source(kind: str) -> str:
    return {
        "xiaoai": "小爱课程表",
        "wakeup": "WakeUp 课程表",
        "ics": "iCalendar",
        "shiguang": "拾光课程表",
        "json": "通用 JSON",
        "csv": "CSV 表格",
    }.get(kind, kind)
