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


def _load_background(path: str, width: int, height: int) -> Any | None:
    """把背景图裁成卡片尺寸（cover 裁切），读不到就返回 None。"""
    if not path:
        return None
    try:
        with Image.open(path) as source:
            bg = source.convert("RGB")
    except Exception:  # noqa: BLE001
        return None
    target_w, target_h = _s(width), _s(height)
    if bg.width <= 0 or bg.height <= 0:
        return None
    ratio = max(target_w / bg.width, target_h / bg.height)
    bg = bg.resize(
        (max(1, int(bg.width * ratio)), max(1, int(bg.height * ratio))),
        Image.LANCZOS,
    )
    left = max((bg.width - target_w) // 2, 0)
    top = max((bg.height - target_h) // 2, 0)
    return bg.crop((left, top, left + target_w, top + target_h))


def _line_height(font) -> int:
    """量一行文字的实际高度（以中英文混合串为准），用于整块垂直居中。"""
    try:
        box = font.getbbox("国Ay")
        return max(box[3] - box[1], 1)
    except Exception:  # noqa: BLE001
        return int(getattr(font, "size", 16) * 1.2)


def _draw_text_block(
    draw: Any,
    x: float,
    center_y: float,
    lines: Sequence[tuple[str, Any, str, float]],
    gap: int = 4,
) -> float:
    """按「整块垂直居中」绘制多行文字，返回块高度。

    lines: [(文本, 字体, 颜色, 最大宽度)]，宽度为 0 表示不截断。
    这样不依赖固定偏移，换字体也不会压到卡片边框。
    """
    prepared = []
    for text, font, fill, max_width in lines:
        text = _strip_unrenderable(font, text)
        shown = _ellipsize(draw, text, font, max_width) if max_width else text
        prepared.append((shown, font, fill, _line_height(font)))
    total = sum(item[3] for item in prepared) + gap * max(len(prepared) - 1, 0)
    top = center_y - total / 2
    for shown, font, fill, height in prepared:
        draw.text((x, top + height / 2), shown, font=font, fill=fill, anchor="lm")
        top += height + gap
    return total


def _contrast_text(image: Any, box: tuple[int, int, int, int], dark: str = "#0F172A", light: str = "#FFFFFF") -> str:
    """按区域平均亮度挑文字颜色，自定义背景图偏暗时用白字。"""
    try:
        region = image.crop(box).convert("L").resize((24, 8))
        values = list(region.getdata())
        mean = sum(values) / max(len(values), 1)
    except Exception:  # noqa: BLE001
        return dark
    return dark if mean >= 160 else light


def _save_card(
    image: Any,
    overlay: Any,
    path: Path,
    *,
    width: int,
    height: int,
    background: bool,
    quality: int = 85,
) -> Path:
    """合成图层后落盘：带照片背景的卡片用 JPEG 压体积，纯文字卡用 PNG 保锐度。"""
    merged = Image.alpha_composite(image, overlay).convert("RGB")
    if background:
        quality = max(50, min(int(quality), 95))
        path = path.with_suffix(".jpg")
        merged.resize((width, height), Image.LANCZOS).save(
            path, "JPEG", quality=quality, optimize=True
        )
    else:
        merged.resize((width, height), Image.LANCZOS).save(path, "PNG", optimize=True)
    return path


def _new_page(
    width: int,
    height: int,
    background: str = "",
    background_dim: int = 55,
    dark: bool = False,
) -> tuple[Any, Any, Any]:
    """创建画布，返回 (底图, 覆盖层, draw)。

    半透明色必须画在独立覆盖层上再合并，PIL 的 draw 直接写像素不会与背景混合。
    """
    size = (_s(width), _s(height))
    bg = _load_background(background, width, height)
    if bg is None:
        base = Image.new("RGBA", size, "#0F172A" if dark else "#FFFFFF")
    else:
        base = bg.convert("RGBA")
        try:
            dim = max(0, min(int(background_dim), 90))
        except (TypeError, ValueError):
            dim = 55
        if dark:
            # 深色模式用遮罩：先压暗再叠深色层，背景保留轮廓但整体沉稳
            from PIL import ImageEnhance

            factor = max(1 - dim / 100 * 0.6, 0.25)
            base = ImageEnhance.Brightness(base).enhance(factor).convert("RGBA")
            scrim = Image.new("RGBA", size, (8, 12, 24) + (int(dim / 100 * 130),))
            base = Image.alpha_composite(base, scrim)
        else:
            veil = Image.new("RGBA", size, (255, 255, 255) + (int(dim / 100 * 235),))
            base = Image.alpha_composite(base, veil)
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    return base, overlay, ImageDraw.Draw(overlay)


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

    pages: list[tuple[Any, Any, Any]] = []
    image, overlay, draw = _new_page(width, height)
    pages.append((image, overlay, draw))
    y = _s(104)
    bottom = _s(height - 74)
    content_top = y

    def new_page():
        nonlocal image, overlay, draw, y
        image, overlay, draw = _new_page(width, height)
        pages.append((image, overlay, draw))
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
    for page_no, (_page_image, _page_overlay, page_draw) in enumerate(pages, start=1):
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
    for page_no, (page_image, page_overlay, _draw) in enumerate(pages, start=1):
        path = out / f"help-{digest}-{page_no}.png"
        Image.alpha_composite(page_image, page_overlay).convert("RGB").resize(
            (width, height), Image.LANCZOS
        ).save(path, "PNG", optimize=True)
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


_MISSING_GLYPH_CACHE: dict[int, set[str]] = {}


def _strip_unrenderable(font, text: str) -> str:
    """删掉当前字体画不出来的字符（emoji 等），避免出现方框占位符。"""
    if not text:
        return text
    cache = _MISSING_GLYPH_CACHE.setdefault(id(font), set())
    out = []
    for ch in text:
        code = ord(ch)
        # emoji / 符号区：CJK 字体基本没有这些字形，直接删
        if (
            code >= 0x1F000
            or 0x2600 <= code <= 0x27BF
            or 0x2B00 <= code <= 0x2BFF
            or 0xFE00 <= code <= 0xFE0F
            or 0x200B <= code <= 0x200F
            or code == 0xFFFD
        ):
            continue
        if ch in cache:
            continue
        try:
            mask = font.getmask(ch, mode="L")
            missing = mask.getbbox() is None
        except Exception:  # noqa: BLE001
            missing = False
        if missing:
            cache.add(ch)
            continue
        out.append(ch)
    return "".join(out)


def _ellipsize(draw, text: str, font, max_width: float) -> str:
    text = _strip_unrenderable(font, text)
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


def _draw_avatar(
    overlay: Any,
    draw: Any,
    cx: float,
    cy: float,
    radius: float,
    avatar_path: str,
    name: str,
) -> bool:
    """画圆形头像；失败时画首字母底色圆。"""
    if avatar_path:
        try:
            with Image.open(avatar_path) as source:
                avatar = source.convert("RGB")
                side = min(avatar.size)
                left = (avatar.width - side) // 2
                top = (avatar.height - side) // 2
                avatar = avatar.crop((left, top, left + side, top + side))
                diameter = int(radius * 2)
                avatar = avatar.resize((diameter, diameter), Image.LANCZOS)
                mask = Image.new("L", (diameter, diameter), 0)
                ImageDraw.Draw(mask).ellipse(
                    (0, 0, diameter - 1, diameter - 1), fill=255
                )
                overlay.paste(avatar, (int(cx - radius), int(cy - radius)), mask)
            return True
        except Exception:  # noqa: BLE001
            pass
    draw.ellipse(
        (cx - radius, cy - radius, cx + radius, cy + radius),
        fill=_avatar_color(name),
    )
    initial = (name or "?")[:1]
    initial_font = _font(_font_pair("")[1], _s(radius * 0.9))
    draw.text(
        (cx - initial_font.getlength(initial) / 2, cy - _s(radius * 0.55)),
        initial,
        font=initial_font,
        fill="#FFFFFF",
    )
    return False


def _everyone_blocks(entries: Sequence[dict]) -> list[tuple[str, Any, float]]:
    """把状态列表拆成排版块：重点状态整行，已下课/无课压成紧凑网格。

    返回 (kind, payload, 高度权重)：row=1.0、chips=0.6、divider=0.4。
    """
    key_rows = [e for e in entries if int(e.get("type", 5)) in (0, 1, 2, 3, 6)]
    ended_rows = [e for e in entries if int(e.get("type", 5)) == 4]
    idle_rows = [e for e in entries if int(e.get("type", 5)) == 5]

    blocks: list[tuple[str, Any, float]] = [("row", e, 1.0) for e in key_rows]
    if ended_rows:
        blocks.append(("divider", f"已下课 · {len(ended_rows)} 人", 0.4))
        for start in range(0, len(ended_rows), 3):
            blocks.append(("chips", ended_rows[start : start + 3], 0.6))
    if idle_rows:
        blocks.append(("divider", f"今日无课 · {len(idle_rows)} 人", 0.4))
        for start in range(0, len(idle_rows), 3):
            blocks.append(("chips", idle_rows[start : start + 3], 0.6))
    return blocks


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
    background: str = "",
    background_dim: int = 55,
    dark: bool = False,
    image_quality: int = 85,
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

    # 已下课/无课压成 3 列小卡，一张图能放下更多人；重点状态仍是整行
    blocks = _everyone_blocks(entries)
    capacity = max(int(page_size or 8), 12)
    pages: list[list[tuple[str, Any, float]]] = []
    current: list[tuple[str, Any, float]] = []
    used = 0.0
    for block in blocks:
        cost = block[2]
        if current and used + cost > capacity:
            pages.append(current)
            current, used = [], 0.0
        current.append(block)
        used += cost
    if current:
        pages.append(current)

    style = CARD_STYLE[dark]
    status_style = STATUS_STYLE_DARK if dark else STATUS_STYLE
    stamp = time.strftime("%Y%m%d%H%M%S") + f"{int(time.time() * 1000) % 1000:03d}"
    paths: list[Path] = []
    row_h, row_gap = 96, 10
    divider_h = 40
    chip_h, chip_gap = 56, 8

    for page_no, chunk in enumerate(pages, start=1):
        height = 150 + 64
        for kind, _payload, _cost in chunk:
            if kind == "row":
                height += row_h + row_gap
            elif kind == "divider":
                height += divider_h
            else:
                height += chip_h + chip_gap
        image, overlay, draw = _new_page(width, height, background, background_dim, dark)

        draw.rectangle((0, 0, _s(width), _s(8)), fill="#2563EB")
        head_color = _contrast_text(image, (0, 0, _s(width), _s(150)))
        sub_color = style["subtitle"] if head_color == style["title"] else style["subtitle_on_dark"]
        brand_color = style["brand"] if head_color == style["title"] else style["brand_dark_bg"]
        draw.text((_s(48), _s(34)), "SCHEDULE LITE", font=_font(bold, _s(15)), fill=brand_color)
        draw.text(
            (_s(48), _s(58)),
            _strip_unrenderable(_font(bold, _s(36)), title),
            font=_font(bold, _s(36)),
            fill=head_color,
        )
        if subtitle:
            draw.text(
                (_s(48), _s(108)),
                _strip_unrenderable(_font(regular, _s(18)), subtitle),
                font=_font(regular, _s(18)),
                fill=sub_color,
            )

        y = _s(150)
        for kind, payload, _cost in chunk:
            if kind == "row":
                entry = payload
                status_type = int(entry.get("type", 5))
                fg, bg = status_style.get(status_type, status_style[5])
                draw.rounded_rectangle(
                    (_s(28), y, _s(width - 28), y + _s(row_h)),
                    radius=_s(16),
                    fill=style["surface"],
                    outline=style["outline"],
                    width=_s(1),
                )
                row_center = y + _s(row_h) / 2
                cx, cy = _s(76), row_center
                _draw_avatar(overlay, draw, cx, cy, _s(28), str(entry.get("avatar") or ""), entry.get("name", ""))

                # 三行文字按整块垂直居中，换字体也不会压到卡片边框
                _draw_text_block(
                    draw,
                    _s(122),
                    row_center,
                    [
                        (str(entry.get("name", "")), _font(bold, _s(23)), style["name"], _s(430)),
                        (str(entry.get("main", "")), _font(regular, _s(21)), style["main"], _s(width - 122 - 44)),
                        (str(entry.get("sub", "")), _font(regular, _s(17)), style["sub"], _s(width - 122 - 44)),
                    ],
                    gap=_s(4),
                )

                label = entry.get("label", "")
                chip_font = _font(bold, _s(17))
                chip_w = chip_font.getlength(label) + _s(28)
                chip_h_label = _s(32)
                chip_x = _s(width - 44) - chip_w
                chip_y = row_center - chip_h_label / 2
                draw.rounded_rectangle(
                    (chip_x, chip_y, chip_x + chip_w, chip_y + chip_h_label),
                    radius=_s(16),
                    fill=bg,
                )
                draw.text(
                    (chip_x + _s(14), chip_y + chip_h_label / 2),
                    label,
                    font=chip_font,
                    fill=fg,
                    anchor="lm",
                )
                y += _s(row_h + row_gap)
            elif kind == "divider":
                draw.text((_s(48), y + _s(4)), str(payload), font=_font(bold, _s(18)), fill=style["muted"])
                y += _s(divider_h)
            else:
                chip_w = (width - 56 - 2 * 10) / 3
                for index, entry in enumerate(payload):
                    x = 28 + index * (chip_w + 10)
                    draw.rounded_rectangle(
                        (_s(x), y, _s(x + chip_w), y + _s(chip_h)),
                        radius=_s(12),
                        fill=style["surface"],
                        outline=style["outline"],
                        width=_s(1),
                    )
                    _draw_avatar(
                        overlay, draw,
                        _s(x + 26), y + _s(chip_h // 2), _s(16),
                        str(entry.get("avatar") or ""), entry.get("name", ""),
                    )
                    name_x = x + 52
                    name_w = chip_w - 52 - 10
                    name = _ellipsize(draw, entry.get("name", ""), _font(bold, _s(16)), _s(name_w))
                    draw.text((_s(name_x), y + _s(9)), name, font=_font(bold, _s(16)), fill=style["name"])
                    hours = entry.get("hours")
                    note = f"{hours:.1f} 小时" if isinstance(hours, (int, float)) else "无课"
                    note = _ellipsize(draw, note, _font(regular, _s(12)), _s(name_w))
                    draw.text((_s(name_x), y + _s(31)), note, font=_font(regular, _s(12)), fill="#94A3B8")
                y += _s(chip_h + chip_gap)

        draw.line(
            (_s(48), _s(height - 52), _s(width - 48), _s(height - 52)),
            fill="#E2E8F0",
            width=_s(1),
        )
        footer_color = _contrast_text(image, (0, _s(height - 64), _s(width), _s(height)), "#94A3B8", "#E2E8F0")
        draw.text((_s(48), _s(height - 40)), footer_note, font=_font(regular, _s(14)), fill=footer_color)
        if len(pages) > 1:
            label = f"{page_no} / {len(pages)}"
            page_font = _font(bold, _s(14))
            draw.text(
                (_s(width - 48) - page_font.getlength(label), _s(height - 40)),
                label,
                font=page_font,
                fill=footer_color,
            )

        path = _save_card(
            image,
            overlay,
            day_dir / f"everyone-{stamp}-{page_no}.png",
            width=width,
            height=height,
            background=bool(background),
            quality=image_quality,
        )
        paths.append(path)

    return paths


# ---------------------------------------------------------------- 单日课表卡片

MARK_STYLE = {
    "skip": ("#B45309", "#FEF3C7"),   # 翘课
    "leave": ("#1D4ED8", "#DBEAFE"),  # 请假
    "moved": ("#6D28D9", "#EDE9FE"),  # 调课
}

# 状态徽章的深色配色（半透明底 + 亮字）
STATUS_STYLE_DARK = {
    0: ("#C4B5FD", (124, 58, 237, 90)),    # 分身中
    1: ("#6EE7B7", (4, 120, 87, 110)),     # 进行中
    2: ("#FCD34D", (180, 83, 9, 110)),     # 翘课中
    3: ("#93C5FD", (29, 78, 216, 110)),    # 下一节
    4: ("#CBD5E1", (100, 116, 139, 110)),  # 已结束
    5: ("#94A3B8", (71, 85, 105, 110)),    # 无课程
    6: ("#93C5FD", (59, 130, 246, 110)),   # 请假中
}

# 卡片整体配色：light / dark
CARD_STYLE = {
    False: {
        "surface": (248, 250, 252, 238),
        "outline": (226, 232, 240, 255),
        "name": "#0F172A",
        "main": "#334155",
        "sub": "#94A3B8",
        "time": "#475569",
        "muted": "#64748B",
        "divider": (226, 232, 240, 255),
        "band_fill": (37, 99, 235, 30),
        "band_text": "#1D4ED8",
        "brand": "#2563EB",
        "brand_dark_bg": "#BFDBFE",
        "title": "#0F172A",
        "subtitle": "#64748B",
        "subtitle_on_dark": "#E2E8F0",
        "footer": "#94A3B8",
        "footer_on_dark": "#E2E8F0",
        "empty": "#64748B",
    },
    True: {
        "surface": (30, 41, 59, 235),
        "outline": (51, 65, 85, 255),
        "name": "#F8FAFC",
        "main": "#CBD5E1",
        "sub": "#94A3B8",
        "time": "#94A3B8",
        "muted": "#94A3B8",
        "divider": (51, 65, 85, 255),
        "band_fill": (59, 130, 246, 80),
        "band_text": "#DBEAFE",
        "brand": "#93C5FD",
        "brand_dark_bg": "#93C5FD",
        "title": "#F8FAFC",
        "subtitle": "#94A3B8",
        "subtitle_on_dark": "#94A3B8",
        "footer": "#94A3B8",
        "footer_on_dark": "#94A3B8",
        "empty": "#94A3B8",
    },
}


def render_day_card(
    rows: Sequence[dict],
    out_dir: str | Path,
    *,
    title: str = "今日课表",
    subtitle: str = "",
    footer_note: str = "发送 /课表 查看自己的课表",
    next_note: str = "",
    empty_text: str = "今天没有课～",
    width: int = 900,
    font_path: str = "",
    background: str = "",
    background_dim: int = 55,
    dark: bool = False,
    image_quality: int = 85,
) -> list[Path]:
    """渲染单日课表卡片。

    rows: [{"time": "08:00-09:40", "name": "高等数学", "place": "松1-303",
            "marks": "翘课", "mark_kind": "skip"}]
    """
    if not available(font_path):
        return []
    regular, bold = _font_pair(font_path)
    out = Path(out_dir)
    day_dir = out / time.strftime("%Y%m%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    _prune_day_dirs(out, day_dir.name, keep=3)

    style = CARD_STYLE[dark]
    mark_style = STATUS_STYLE_DARK if dark else MARK_STYLE
    row_h, gap, header_h, footer_h = 76, 10, 150, 64
    note_h = 56 if next_note else 0
    count = max(len(rows), 1)
    height = header_h + count * (row_h + gap) - gap + note_h + footer_h
    image, overlay, draw = _new_page(width, height, background, background_dim, dark)

    draw.rectangle((0, 0, _s(width), _s(8)), fill="#2563EB")
    head_box = (0, 0, _s(width), _s(header_h))
    title_color = _contrast_text(image, head_box)
    sub_color = style["subtitle"] if title_color == style["title"] else style["subtitle_on_dark"]
    brand_color = style["brand"] if title_color == style["title"] else style["brand_dark_bg"]
    draw.text((_s(48), _s(34)), "SCHEDULE LITE", font=_font(bold, _s(15)), fill=brand_color)
    draw.text(
        (_s(48), _s(58)),
        _strip_unrenderable(_font(bold, _s(36)), title),
        font=_font(bold, _s(36)),
        fill=title_color,
    )
    if subtitle:
        draw.text(
            (_s(48), _s(108)),
            _strip_unrenderable(_font(regular, _s(18)), subtitle),
            font=_font(regular, _s(18)),
            fill=sub_color,
        )

    y = _s(header_h)
    if not rows:
        draw.text(
            (_s(52), y + _s(12)),
            empty_text,
            font=_font(regular, _s(22)),
            fill=style["empty"],
        )
        y += _s(row_h + gap)
    for row in rows:
        draw.rounded_rectangle(
            (_s(28), y, _s(width - 28), y + _s(row_h)),
            radius=_s(16),
            fill=style["surface"],
            outline=style["outline"],
            width=_s(1),
        )
        row_center = y + _s(row_h) / 2
        time_text = str(row.get("time") or "")
        draw.text(
            (_s(52), row_center),
            _ellipsize(draw, time_text, _font(bold, _s(19)), _s(180)),
            font=_font(bold, _s(19)),
            fill=style["time"],
            anchor="lm",
        )
        place = str(row.get("place") or "")
        block = [
            (str(row.get("name") or ""), _font(bold, _s(23)), style["name"], _s(width - 420)),
        ]
        if place:
            block.append((place, _font(regular, _s(15)), style["sub"], _s(width - 300)))
        _draw_text_block(draw, _s(250), row_center, block, gap=_s(4))

        marks = str(row.get("marks") or "")
        if marks:
            fg, bg = mark_style.get(str(row.get("mark_kind") or ""), (style["time"], style["outline"]))
            chip_font = _font(bold, _s(16))
            chip_w = chip_font.getlength(marks) + _s(26)
            chip_x = _s(width - 44) - chip_w
            chip_y = row_center - _s(16)
            draw.rounded_rectangle(
                (chip_x, chip_y, chip_x + chip_w, chip_y + _s(32)),
                radius=_s(16),
                fill=bg,
            )
            draw.text(
                (chip_x + _s(13), chip_y + _s(16)),
                marks,
                font=chip_font,
                fill=fg,
                anchor="lm",
            )
        y += _s(row_h + gap)

    if next_note:
        draw.rounded_rectangle(
            (_s(28), y, _s(width - 28), y + _s(48)),
            radius=_s(14),
            fill=style["band_fill"],
        )
        draw.text(
            (_s(48), y + _s(12)),
            _ellipsize(draw, next_note, _font(bold, _s(19)), _s(width - 120)),
            font=_font(bold, _s(19)),
            fill=style["band_text"],
        )
        y += _s(note_h)

    draw.line(
        (_s(48), _s(height - 52), _s(width - 48), _s(height - 52)),
        fill=style["divider"],
        width=_s(1),
    )
    footer_color = _contrast_text(image, (0, _s(height - footer_h), _s(width), _s(height)), style["footer"], style["footer_on_dark"])
    draw.text(
        (_s(48), _s(height - 40)),
        footer_note,
        font=_font(regular, _s(14)),
        fill=footer_color,
    )

    path = _save_card(
        image,
        overlay,
        day_dir / f"day-{time.strftime('%Y%m%d%H%M%S')}.png",
        width=width,
        height=height,
        background=bool(background),
        quality=image_quality,
    )
    return [path]
