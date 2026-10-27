"""图片渲染：Markdown 帮助文档分页 + “群友在上什么课”卡片。

只依赖 Pillow 和中文字体，找不到字体/没装 Pillow 时由调用方退回文本。
图片统一按 2 倍分辨率绘制再缩小，保证文字边缘清晰。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    from PIL import Image, ImageDraw, ImageFont

    PIL_AVAILABLE = True
except Exception:  # noqa: BLE001
    PIL_AVAILABLE = False
    Image = ImageDraw = ImageFont = None  # type: ignore

SCALE = 2
RENDER_VERSION = "3"

# Windows / Linux / macOS 常见中文字体
FONT_PAIRS = (
    ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc"),
    ("C:/Windows/Fonts/msyh.ttf", "C:/Windows/Fonts/msyhbd.ttf"),
    ("C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simhei.ttf"),
    ("C:/Windows/Fonts/simsun.ttc", "C:/Windows/Fonts/simsun.ttc"),
    (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    ),
    (
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    ),
    (
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
    ),
    (
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ),
    (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ),
)

_font_cache: dict[tuple[str, int], Any] = {}
_pair_cache: dict[str, tuple[str, str]] = {}

STATUS_STYLE = {
    0: ("#6D28D9", "#EDE9FE"),  # 分身中
    1: ("#047857", "#D1FAE5"),  # 进行中
    2: ("#B45309", "#FEF3C7"),  # 翘课中
    3: ("#1D4ED8", "#DBEAFE"),  # 下一节
    4: ("#64748B", "#F1F5F9"),  # 已结束
    5: ("#94A3B8", "#F8FAFC"),  # 无课程
}

AVATAR_COLORS = (
    "#2563EB", "#7C3AED", "#DB2777", "#EA580C",
    "#059669", "#0891B2", "#4F46E5", "#B45309",
)


def _font_pair(custom: str = "") -> tuple[str, str]:
    key = custom or ""
    if key in _pair_cache:
        return _pair_cache[key]
    if custom:
        path = Path(custom)
        if path.is_file():
            _pair_cache[key] = (str(path), str(path))
            return _pair_cache[key]
    for regular, bold in FONT_PAIRS:
        if Path(regular).is_file():
            pair = (regular, bold if Path(bold).is_file() else regular)
            _pair_cache[key] = pair
            return pair
    _pair_cache[key] = ("", "")
    return _pair_cache[key]


def available(font_path: str = "") -> bool:
    if not PIL_AVAILABLE:
        return False
    regular, _ = _font_pair(font_path)
    return bool(regular)


def _font(path: str, size: int):
    key = (path, size)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(path, size, index=0)
    return _font_cache[key]


def _s(value: float) -> int:
    return int(round(value * SCALE))


def _cleanup(out_dir: Path, pattern: str, keep: int = 60) -> None:
    files = sorted(out_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[keep:]:
        try:
            path.unlink()
        except OSError:
            pass


def _prune_day_dirs(out_dir: Path, current: str, keep: int = 3) -> None:
    """状态卡片缓存按天分目录，只保留最近几天，避免缓存目录无限增长。"""
    try:
        day_dirs = sorted(
            (
                d
                for d in out_dir.iterdir()
                if d.is_dir() and d.name.isdigit() and len(d.name) == 8
            ),
            key=lambda d: d.name,
        )
        for stale in day_dirs:
            if stale.name == current or len(day_dirs) - day_dirs.index(stale) <= keep:
                continue
            for f in stale.iterdir():
                try:
                    f.unlink()
                except OSError:
                    pass
            try:
                stale.rmdir()
            except OSError:
                pass
    except OSError:
        pass


# ---------------------------------------------------------------- Markdown


_INLINE_RE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`)")
_UNIT_RE = re.compile(r"[A-Za-z0-9_@#./:%+\-]+|\s+|.")


def _segments(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos = 0
    for match in _INLINE_RE.finditer(text):
        if match.start() > pos:
            out.append((text[pos : match.start()], "normal"))
        token = match.group(0)
        if token.startswith("**"):
            out.append((token[2:-2], "bold"))
        else:
            out.append((token[1:-1], "code"))
        pos = match.end()
    if pos < len(text):
        out.append((text[pos:], "normal"))
    return out


def _wrap(
    text: str,
    max_width: int,
    size: int,
    regular: str,
    bold: str,
) -> list[list[tuple[str, str]]]:
    fonts = {
        "normal": _font(regular, _s(size)),
        "bold": _font(bold, _s(size)),
        "code": _font(regular, _s(size)),
    }
    lines: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    width = 0.0

    def flush():
        nonlocal current, width
        while current and current[-1][0].isspace():
            current.pop()
        lines.append(current)
        current = []
        width = 0.0

    for piece, style in _segments(text):
        for unit in _UNIT_RE.findall(piece):
            if unit == "\n":
                flush()
                continue
            font = fonts[style]
            unit_w = font.getlength(unit)
            if current and width + unit_w > max_width:
                flush()
                if unit.isspace():
                    continue
            if not current and unit == " ":
                continue
            current.append((unit, style))
            width += unit_w
    if current:
        flush()
    return lines or [[]]


def _merge(line: list[tuple[str, str]]) -> list[tuple[str, str]]:
    merged: list[tuple[str, str]] = []
    for text, style in line:
        if merged and merged[-1][1] == style:
            merged[-1] = (merged[-1][0] + text, style)
        else:
            merged.append((text, style))
    return merged


def _build_items(markdown: str, content_width: int, regular: str, bold: str) -> list[dict]:
    items: list[dict] = []
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0
    while index < len(lines):
        raw = lines[index]
        line = raw.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            index += 1
            code_lines = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index].rstrip())
                index += 1
            index += 1
            if code_lines:
                wrapped = []
                for code_line in code_lines:
                    wrapped.extend(_wrap(code_line or " ", content_width - 32, 18, regular, bold))
                items.append(
                    {
                        "kind": "code",
                        "lines": wrapped,
                        "size": 18,
                        "line_h": 30,
                        "pad": 14,
                        "height": len(wrapped) * 30 + 28 + 10,
                    }
                )
            continue

        heading = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if heading:
            level = len(heading.group(1))
            text = heading.group(2).strip()
            size = {1: 36, 2: 28, 3: 23}[level]
            wrapped = _wrap(text, content_width, size, regular, bold)
            items.append(
                {
                    "kind": "heading",
                    "level": level,
                    "lines": wrapped,
                    "size": size,
                    "line_h": int(size * 1.45),
                    "height": len(wrapped) * int(size * 1.45) + (30 if level == 1 else 20),
                    "top": 12 if level == 1 else 20,
                }
            )
            index += 1
            continue

        bullet = re.match(r"^([-*]|\d+\.)\s+(.*)$", stripped)
        if bullet:
            text = bullet.group(2)
            indent = 30
            wrapped = _wrap(text, content_width - indent, 21, regular, bold)
            items.append(
                {
                    "kind": "bullet",
                    "prefix": "•" if bullet.group(1) in ("-", "*") else bullet.group(1),
                    "lines": wrapped,
                    "size": 21,
                    "line_h": 33,
                    "height": len(wrapped) * 33 + 8,
                    "indent": indent,
                }
            )
            index += 1
            continue

        if stripped in ("---", "***", "___"):
            items.append({"kind": "rule", "height": 28})
            index += 1
            continue

        if stripped.startswith(">"):
            text = stripped.lstrip("> ").strip()
            wrapped = _wrap(text, content_width - 16, 20, regular, bold)
            items.append(
                {
                    "kind": "quote",
                    "lines": wrapped,
                    "size": 20,
                    "line_h": 32,
                    "height": len(wrapped) * 32 + 12,
                }
            )
            index += 1
            continue

        if not stripped:
            items.append({"kind": "spacer", "height": 14})
            index += 1
            continue

        # 表格行（帮助文档里尽量不用，但保留基本支持）
        if stripped.startswith("|"):
            if re.fullmatch(r"\|[\s:|-]+\|?", stripped):
                index += 1
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            text = "   ".join(cells)
            wrapped = _wrap(text, content_width - 16, 18, regular, bold)
            items.append(
                {
                    "kind": "code",
                    "lines": wrapped,
                    "size": 18,
                    "line_h": 29,
                    "pad": 10,
                    "height": len(wrapped) * 29 + 20 + 6,
                }
            )
            index += 1
            continue

        wrapped = _wrap(stripped, content_width, 21, regular, bold)
        items.append(
            {
                "kind": "text",
                "lines": wrapped,
                "size": 21,
                "line_h": 34,
                "height": len(wrapped) * 34 + 6,
            }
        )
        index += 1
    return items


def _draw_lines(
    draw,
    lines: Sequence[list[tuple[str, str]]],
    x: int,
    y: int,
    size: int,
    line_h: int,
    regular: str,
    bold: str,
    color: str,
    code_color: str = "#B91C1C",
) -> int:
    fonts = {
        "normal": _font(regular, _s(size)),
        "bold": _font(bold, _s(size)),
        "code": _font(regular, _s(size)),
    }
    for line in lines:
        cursor = x
        for text, style in _merge(line):
            font = fonts[style]
            fill = code_color if style == "code" else color
            draw.text((cursor, y), text, font=font, fill=fill)
            cursor += font.getlength(text)
        y += _s(line_h)
    return y


def _new_page(width: int, height: int) -> tuple[Any, Any]:
    image = Image.new("RGB", (_s(width), _s(height)), "#FFFFFF")
    return image, ImageDraw.Draw(image)


def _draw_page_frame(
    draw,
    width: int,
    height: int,
    title: str,
    page_no: int,
    total: int,
    regular: str,
    bold: str,
) -> None:
    draw.rectangle((0, 0, _s(width), _s(8)), fill="#2563EB")
    draw.text((_s(56), _s(28)), title, font=_font(regular, _s(18)), fill="#64748B")
    draw.line((_s(56), _s(74), _s(width - 56), _s(74)), fill="#E2E8F0", width=_s(1))
    draw.line(
        (_s(56), _s(height - 58), _s(width - 56), _s(height - 58)),
        fill="#E2E8F0",
        width=_s(1),
    )
    draw.text(
        (_s(56), _s(height - 46)),
        "课表 Lite · astrbot_plugin_schedule_lite",
        font=_font(regular, _s(14)),
        fill="#94A3B8",
    )
    label = f"{page_no} / {total}"
    font = _font(bold, _s(14))
    draw.text(
        (_s(width - 56) - font.getlength(label), _s(height - 46)),
        label,
        font=font,
        fill="#94A3B8",
    )


def render_help(
    markdown: str,
    out_dir: str | Path,
    *,
    width: int = 900,
    height: int = 1400,
    font_path: str = "",
) -> list[Path]:
    """把 Markdown 渲染成多页 PNG，带内容哈希缓存。"""
    if not available(font_path):
        return []
    regular, bold = _font_pair(font_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256(
        (markdown + f"|{width}x{height}|{RENDER_VERSION}").encode("utf-8")
    ).hexdigest()[:12]
    manifest = out / f"help-{digest}.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            paths = [out / name for name in data.get("files", [])]
            if paths and all(p.is_file() for p in paths):
                return paths
        except (ValueError, OSError):
            pass

    margin = 56
    content_width = _s(width - margin * 2)
    items = _build_items(markdown, content_width, regular, bold)

    pages: list[tuple[Any, Any]] = []
    image, draw = _new_page(width, height)
    pages.append((image, draw))
    y = _s(104)
    bottom = _s(height - 74)
    content_top = y

    def new_page():
        nonlocal image, draw, y
        image, draw = _new_page(width, height)
        pages.append((image, draw))
        y = content_top

    for item in items:
        height_item = _s(item["height"])
        top_margin = _s(item.get("top", 0))
        if y + top_margin + height_item > bottom and y > content_top:
            new_page()
        y += top_margin

        kind = item["kind"]
        if kind == "spacer":
            y += _s(item["height"])
            continue
        if kind == "rule":
            draw.line((_s(margin), y + _s(10), _s(width - margin), y + _s(10)), fill="#E2E8F0", width=_s(1))
            y += _s(item["height"])
            continue

        if kind == "heading":
            level = item["level"]
            color = {1: "#0F172A", 2: "#1E293B", 3: "#334155"}[level]
            if level == 1:
                draw.rectangle((_s(margin), y + _s(6), _s(margin + 6), y + _s(40)), fill="#2563EB")
                x = _s(margin + 20)
            else:
                x = _s(margin)
            y = _draw_lines(draw, item["lines"], x, y, item["size"], item["line_h"], regular, bold, color)
            y += _s(6 if level == 1 else 2)
            continue

        if kind == "bullet":
            prefix_font = _font(bold, _s(21))
            draw.text((_s(margin), y), item["prefix"], font=prefix_font, fill="#2563EB")
            y = _draw_lines(
                draw,
                item["lines"],
                _s(margin + item["indent"]),
                y,
                item["size"],
                item["line_h"],
                regular,
                bold,
                "#334155",
            )
            y += _s(4)
            continue

        if kind == "quote":
            draw.rectangle((_s(margin), y + _s(2), _s(margin + 4), y + _s(item["height"] - 12)), fill="#CBD5E1")
            y = _draw_lines(
                draw,
                item["lines"],
                _s(margin + 18),
                y,
                item["size"],
                item["line_h"],
                regular,
                bold,
                "#64748B",
            )
            y += _s(8)
            continue

        if kind == "code":
            pad = item["pad"]
            box = (
                _s(margin),
                y,
                _s(width - margin),
                y + _s(item["height"] - 6),
            )
            draw.rounded_rectangle(box, radius=_s(8), fill="#F8FAFC", outline="#E2E8F0", width=_s(1))
            _draw_lines(
                draw,
                item["lines"],
                _s(margin + pad),
                y + _s(pad),
                item["size"],
                item["line_h"],
                regular,
                bold,
                "#334155",
                code_color="#334155",
            )
            y += _s(item["height"])
            continue

        y = _draw_lines(
            draw,
            item["lines"],
            _s(margin),
            y,
            item["size"],
            item["line_h"],
            regular,
            bold,
            "#334155",
        )
        y += _s(4)

    total = len(pages)
    for page_no, (page_image, page_draw) in enumerate(pages, start=1):
        _draw_page_frame(
            page_draw,
            width,
            height,
            "课表 Lite · 使用手册",
            page_no,
            total,
            regular,
            bold,
        )

    paths: list[Path] = []
    for page_no, (page_image, _draw) in enumerate(pages, start=1):
        path = out / f"help-{digest}-{page_no}.png"
        page_image.resize((width, height), Image.LANCZOS).save(path, "PNG", optimize=True)
        paths.append(path)

    manifest.write_text(
        json.dumps({"files": [p.name for p in paths], "digest": digest}, ensure_ascii=False),
        encoding="utf-8",
    )
    # 清掉旧版本页面
    for old in out.glob("help-*.png"):
        if old not in paths:
            try:
                old.unlink()
            except OSError:
                pass
    return paths


# ---------------------------------------------------------------- 群友卡片


def _ellipsize(draw, text: str, font, max_width: float) -> str:
    if not text:
        return ""
    if font.getlength(text) <= max_width:
        return text
    ellipsis = "…"
    while text and font.getlength(text + ellipsis) > max_width:
        text = text[:-1]
    return text + ellipsis


def _avatar_color(name: str) -> str:
    value = sum(ord(ch) for ch in (name or "?"))
    return AVATAR_COLORS[value % len(AVATAR_COLORS)]


def render_everyone(
    entries: Sequence[dict],
    out_dir: str | Path,
    *,
    title: str = "群友在上什么课？",
    subtitle: str = "",
    footer_note: str = "发送 /课表 查看自己的课表",
    page_size: int = 8,
    width: int = 900,
    font_path: str = "",
) -> list[Path]:
    if not available(font_path):
        return []
    if not entries:
        entries = [
            {
                "name": "提示",
                "type": 5,
                "label": "还没有数据",
                "main": "本群还没有人导入课表",
                "sub": "发送 /课表 导入 开始使用",
            }
        ]
    regular, bold = _font_pair(font_path)
    out = Path(out_dir)
    # 缓存按天分目录：当天查询只看当天这一类，旧目录自动清理
    day_dir = out / time.strftime("%Y%m%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    _prune_day_dirs(out, day_dir.name, keep=3)

    page_size = max(1, min(int(page_size or 8), 20))
    # 最后一页不足半页时，优先并页或重新均分，避免出现只放一两个人的尾页。
    if len(entries) > page_size:
        pages = math.ceil(len(entries) / page_size)
        last = len(entries) - (pages - 1) * page_size
        if pages > 1 and last * 2 < page_size:
            merged = math.ceil(len(entries) / (pages - 1))
            page_size = merged if merged <= 20 else math.ceil(len(entries) / pages)
    chunks = [list(entries[i : i + page_size]) for i in range(0, len(entries), page_size)]
    stamp = time.strftime("%Y%m%d%H%M%S") + f"{int(time.time() * 1000) % 1000:03d}"
    paths: list[Path] = []

    for page_no, chunk in enumerate(chunks, start=1):
        row_h = 108
        gap = 12
        header_h = 150
        footer_h = 64
        height = header_h + len(chunk) * (row_h + gap) - gap + footer_h
        image, draw = _new_page(width, height)

        draw.rectangle((0, 0, _s(width), _s(8)), fill="#2563EB")
        draw.text((_s(48), _s(34)), "SCHEDULE LITE", font=_font(bold, _s(15)), fill="#2563EB")
        draw.text((_s(48), _s(58)), title, font=_font(bold, _s(36)), fill="#0F172A")
        if subtitle:
            draw.text((_s(48), _s(108)), subtitle, font=_font(regular, _s(18)), fill="#64748B")

        y = _s(header_h)
        for entry in chunk:
            status_type = int(entry.get("type", 5))
            fg, bg = STATUS_STYLE.get(status_type, STATUS_STYLE[5])
            draw.rounded_rectangle(
                (_s(28), y, _s(width - 28), y + _s(row_h)),
                radius=_s(16),
                fill="#F8FAFC",
                outline="#E2E8F0",
                width=_s(1),
            )
            cx, cy = _s(76), y + _s(row_h // 2)
            radius = _s(28)
            avatar_drawn = False
            avatar_path = str(entry.get("avatar") or "")
            if avatar_path:
                try:
                    with Image.open(avatar_path) as source:
                        avatar = source.convert("RGB")
                        side = min(avatar.size)
                        left = (avatar.width - side) // 2
                        top = (avatar.height - side) // 2
                        avatar = avatar.crop((left, top, left + side, top + side))
                        diameter = radius * 2
                        avatar = avatar.resize((diameter, diameter), Image.LANCZOS)
                        mask = Image.new("L", (diameter, diameter), 0)
                        ImageDraw.Draw(mask).ellipse(
                            (0, 0, diameter - 1, diameter - 1), fill=255
                        )
                        image.paste(avatar, (cx - radius, cy - radius), mask)
                    avatar_drawn = True
                except Exception:  # noqa: BLE001
                    avatar_drawn = False
            if not avatar_drawn:
                draw.ellipse(
                    (cx - radius, cy - radius, cx + radius, cy + radius),
                    fill=_avatar_color(entry.get("name", "")),
                )
                initial = (entry.get("name") or "?")[:1]
                initial_font = _font(bold, _s(25))
                draw.text(
                    (cx - initial_font.getlength(initial) / 2, cy - _s(16)),
                    initial,
                    font=initial_font,
                    fill="#FFFFFF",
                )

            name = _ellipsize(draw, entry.get("name", ""), _font(bold, _s(23)), _s(430))
            draw.text((_s(122), y + _s(16)), name, font=_font(bold, _s(23)), fill="#0F172A")

            label = entry.get("label", "")
            chip_font = _font(bold, _s(17))
            chip_w = chip_font.getlength(label) + _s(28)
            chip_h = _s(32)
            chip_x = _s(width - 44) - chip_w
            draw.rounded_rectangle(
                (chip_x, y + _s(16), chip_x + chip_w, y + _s(16) + chip_h),
                radius=_s(16),
                fill=bg,
            )
            draw.text(
                (chip_x + _s(14), y + _s(21)),
                label,
                font=chip_font,
                fill=fg,
            )

            main = _ellipsize(draw, entry.get("main", ""), _font(regular, _s(21)), _s(width - 122 - 44))
            draw.text((_s(122), y + _s(50)), main, font=_font(regular, _s(21)), fill="#334155")

            sub = _ellipsize(draw, entry.get("sub", ""), _font(regular, _s(17)), _s(width - 122 - 44))
            draw.text((_s(122), y + _s(78)), sub, font=_font(regular, _s(17)), fill="#94A3B8")
            y += _s(row_h + gap)

        draw.line(
            (_s(48), _s(height - 52), _s(width - 48), _s(height - 52)),
            fill="#E2E8F0",
            width=_s(1),
        )
        draw.text((_s(48), _s(height - 40)), footer_note, font=_font(regular, _s(14)), fill="#94A3B8")
        if len(chunks) > 1:
            label = f"{page_no} / {len(chunks)}"
            page_font = _font(bold, _s(14))
            draw.text(
                (_s(width - 48) - page_font.getlength(label), _s(height - 40)),
                label,
                font=page_font,
                fill="#94A3B8",
            )

        path = day_dir / f"everyone-{stamp}-{page_no}.png"
        image.resize((width, height), Image.LANCZOS).save(path, "PNG", optimize=True)
        paths.append(path)

    return paths
