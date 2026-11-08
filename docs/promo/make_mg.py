"""课表 Lite 宣传片 MG 渲染器（移植 DrawAI mg-code-video 方法论）。

思路（对齐 skills/mg-code-video）：
- 分镜表驱动：10 幕 × 7s = 70s，与配音对齐；
- 确定性逐帧渲染：render_frame(t) 纯函数，PIL 合成后 pipe 给 ffmpeg；
- 镜头语言：每幕全局缓推（Ken Burns）、卡片光扫、字符级动态排版、
  数字滚动、连线生长；幕间横向滑动转场而非单纯淡入淡出；
- 配色 token：深底 #0B1220 / 正文 #F8FAFC / 主强调 #3B82F6 / 警示 #F59E0B。

用法：
    python docs/promo/make_mg.py            # 渲染整片
    python docs/promo/make_mg.py --frame 3.2  # 只导出某一时刻的静帧到 build/frame.png
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from make_video import (  # noqa: E402
    ACCENT, BG_BOT, BG_TOP, CARD, LINE, MUTED, TEXT,
    badge_layer, build_assets, countdown_pill, font, wrap,
)

W, H = 1920, 1080
FPS = 30
SCENE = 7.0
SCENES = 10
TOTAL = SCENE * SCENES
OUT_MP4 = HERE / "promo.mp4"
NARRATION_DIR = HERE / "build" / "narration"
BUILD = HERE / "build"


# ---------------------------------------------------------------- 缓动与工具


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def eo_cubic(x: float) -> float:
    x = clamp(x)
    return 1 - (1 - x) ** 3


def eo_back(x: float) -> float:
    x = clamp(x)
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def eo_quint(x: float) -> float:
    x = clamp(x)
    return 1 - (1 - x) ** 5


def alpha_composite(dst: Image.Image, src: Image.Image, xy: tuple[int, int]) -> None:
    """按偏移合成（自动裁剪越界部分，要求双方都是 RGBA）。"""
    if src.mode != "RGBA":
        src = src.convert("RGBA")
    x, y = int(round(xy[0])), int(round(xy[1]))
    sw, sh = src.size
    if x >= dst.width or y >= dst.height or x + sw <= 0 or y + sh <= 0:
        return
    cx0, cy0 = max(0, -x), max(0, -y)
    cx1, cy1 = min(sw, dst.width - x), min(sh, dst.height - y)
    if cx1 <= cx0 or cy1 <= cy0:
        return
    crop = src.crop((cx0, cy0, cx1, cy1))
    dst.alpha_composite(crop, (x + cx0, y + cy0))


def card_plate(card: Image.Image, *, radius: int = 26) -> Image.Image:
    """给卡片贴图加圆角、细描边和投影，避免直角边直接怼在画面上。"""
    pad = 46
    w, h = card.size
    canvas = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    # 投影
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((pad - 8, pad - 4, pad + w + 8, pad + h + 16), radius=radius + 6,
                         fill=(0, 0, 0, 150))
    shadow = shadow.filter(ImageFilter.GaussianBlur(16))
    canvas.alpha_composite(shadow)
    # 圆角裁切卡片
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    canvas.paste(card.convert("RGBA"), (pad, pad), mask)
    # 细描边
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle((pad, pad, pad + w - 1, pad + h - 1), radius=radius,
                        outline=LINE, width=2)
    return canvas


def fade(img: Image.Image, k: float) -> Image.Image:
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    if k >= 0.999:
        return img
    out = img.copy()
    a = out.getchannel("A").point(lambda v: int(v * clamp(k)))
    out.putalpha(a)
    return out


# ---------------------------------------------------------------- 背景


_BG_CACHE: Image.Image | None = None
_DOT_SEEDS = [(i * 137.5) % W for i in range(26)]


def background(t: float) -> Image.Image:
    global _BG_CACHE
    if _BG_CACHE is None:
        base = Image.new("RGB", (W, H), BG_TOP)
        for y in range(H):
            k = y / H
            color = tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * k) for i in range(3))
            base.paste(color, (0, y, W, y + 1))
        glow = Image.new("RGB", (W, H), BG_TOP)
        gd = ImageDraw.Draw(glow)
        gd.ellipse((W - 620, -260, W + 320, 660), fill=(25, 37, 64))
        gd.ellipse((-320, H - 420, 420, H + 320), fill=(20, 30, 52))
        base = Image.blend(base, glow, 0.65)
        ImageDraw.Draw(base).rectangle((0, 0, W, 10), fill="#2563EB")
        _BG_CACHE = base.convert("RGBA")
    frame = _BG_CACHE.copy()
    # 缓慢漂浮的光点，给静止背景一点呼吸
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    for i, x0 in enumerate(_DOT_SEEDS):
        y0 = (i * 431) % H
        x = x0 + 40 * np.sin(0.12 * t + i)
        y = y0 + 30 * np.cos(0.09 * t + i * 1.7)
        r = 3 + (i % 4) * 2
        a = 26 + 18 * (0.5 + 0.5 * np.sin(0.4 * t + i))
        d.ellipse((x - r, y - r, x + r, y + r), fill=(96, 165, 250, int(a)))
    frame.alpha_composite(overlay)
    return frame


# ---------------------------------------------------------------- 文字动效


def char_in(
    img: Image.Image,
    text: str,
    x: float,
    y: float,
    size: int,
    t: float,
    delay: float,
    *,
    stagger: float = 0.035,
    color: str = TEXT,
    bold: bool = True,
    dur: float = 0.5,
    rise: int = 34,
    center: bool = True,
) -> None:
    """字符级入场：逐字上浮 + 淡入 + 轻微过冲。"""
    f = font(bold, size)
    advance = sum(f.getlength(ch) for ch in text)
    cx = x - advance / 2 if center else x
    for i, ch in enumerate(text):
        p = eo_back((t - delay - i * stagger) / dur)
        if p <= 0:
            continue
        a = clamp((t - delay - i * stagger) / (dur * 0.7))
        width = f.getlength(ch)
        layer = Image.new("RGBA", (int(width) + 8, int(size * 1.6)), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((4, layer.height / 2), ch, font=f, fill=color, anchor="lm")
        alpha_composite(img, fade(layer, a), (cx + 20 * (1 - p), y - rise * (1 - p) - layer.height / 2))
        cx += width


def text_fade(
    img: Image.Image,
    text: str,
    x: float,
    y: float,
    size: int,
    t: float,
    delay: float,
    *,
    color: str = MUTED,
    bold: bool = False,
    dur: float = 0.45,
    rise: int = 22,
    center: bool = False,
) -> None:
    p = eo_cubic((t - delay) / dur)
    if p <= 0:
        return
    f = font(bold, size)
    layer = Image.new("RGBA", (int(f.getlength(text)) + 12, int(size * 1.8)), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((6, layer.height / 2), text, font=f, fill=color, anchor="lm")
    dx = -f.getlength(text) / 2 if center else 0
    alpha_composite(img, fade(layer, p), (x + dx - 6, y - rise * (1 - p) - layer.height / 2))


def count_up(t: float, delay: float, dur: float, target: int) -> int:
    p = eo_quint((t - delay) / dur)
    return int(round(target * p))


def sweep(img: Image.Image, bbox: tuple[int, int, int, int], t: float, delay: float, *, dur: float = 0.75) -> None:
    """卡片上掠过的一道高光（限制在卡片圆角区域内）。"""
    p = (t - delay) / dur
    if not 0 <= p <= 1:
        return
    from PIL import ImageChops

    x0, y0, x1, y1 = bbox
    band_w = 220
    height = max(10, int(y1 - y0))
    cx = x0 - band_w + (x1 - x0 + 2 * band_w) * eo_cubic(p)
    band = Image.new("RGBA", (band_w * 2, height), (0, 0, 0, 0))
    bd = ImageDraw.Draw(band)
    for i in range(band_w):
        a = int(58 * (1 - abs(i - band_w / 2) / (band_w / 2)))
        if a <= 0:
            continue
        bd.rectangle((band_w / 2 - i, 0, band_w / 2 + i, height), fill=(255, 255, 255, a // 8))
    band = band.rotate(18, expand=True, resample=Image.BICUBIC)
    # 先用卡片圆角区域裁出「卡片画布」，再在画布内按偏移贴光带
    clip = Image.new("L", (W, H), 0)
    ImageDraw.Draw(clip).rounded_rectangle((x0, y0, x1, y1), radius=26, fill=255)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    alpha_composite(canvas, band, (int(cx - band.width / 2), int(y0 - 30)))
    canvas.putalpha(ImageChops.multiply(canvas.getchannel("A"), clip))
    img.alpha_composite(canvas)


def ring_pulse(img: Image.Image, cx: float, cy: float, r0: float, t: float, delay: float, *, color: str = "#3B82F6") -> None:
    """一圈扩散的强调环。"""
    p = (t - delay) / 1.1
    if 0 <= p <= 1:
        r = r0 + 46 * eo_cubic(p)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        a = int(150 * (1 - p))
        ImageDraw.Draw(layer).ellipse((cx - r, cy - r, cx + r, cy + r), outline=color, width=3)
        img.alpha_composite(fade(layer, a / 150))


# ---------------------------------------------------------------- 每幕绘制


def scene_hook(t: float, assets: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    char_in(img, "可以在群聊里，看到群友上课？", W / 2, 210, 46, t, 0.15, color="#93C5FD", stagger=0.022)
    char_in(img, "课表 Lite", W / 2, 430, 132, t, 0.75, stagger=0.06, dur=0.6)
    # 下划线扫开
    p = eo_cubic((t - 1.15) / 0.6)
    if p > 0:
        half = 170 * p
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((W / 2 - half, 515, W / 2 + half, 523), radius=4, fill=ACCENT)
    text_fade(img, "把课表变成群里的公共设施", W / 2, 585, 36, t, 1.3, center=True)
    text_fade(img, "让你的 QQ 机器人，提醒上课。", W / 2, 650, 34, t, 1.5, color="#93C5FD", center=True)
    b1 = badge_layer("v1.10.7", fg="#BFDBFE", bg=(30, 58, 95), outline="#3B82F6")
    p1 = eo_back((t - 1.9) / 0.45)
    if p1 > 0:
        alpha_composite(img, fade(b1, clamp(p1)), (W / 2 - 190 - b1.width / 2, 790))
    tests = count_up(t, 2.0, 0.9, 179)
    b2 = badge_layer(f"{tests} tests passed", fg=MUTED)
    p2 = eo_back((t - 2.1) / 0.45)
    if p2 > 0:
        alpha_composite(img, fade(b2, clamp(p2)), (W / 2 + 20 - b2.width / 2, 790))
    return img


def scene_import(t: float, assets: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    char_in(img, "多来源导入，全都自动归一化", W / 2, 170, 58, t, 0.1, stagger=0.02)
    sources = ["Nexio 分享口令", "拾光课程表", "WakeUp", "iCalendar (.ics)", "通用 JSON / CSV"]
    widths = [badge_layer(s, size=30).width for s in sources]
    total_w = sum(widths) + 4 * 20
    x = (W - total_w) / 2
    for i, (s, w) in enumerate(zip(sources, widths)):
        p = eo_back((t - 0.5 - i * 0.13) / 0.42)
        if p > 0:
            badge = badge_layer(s, size=30)
            alpha_composite(img, fade(badge, clamp(p)), (x + w / 2 - badge.width / 2, 320 - 26 * (1 - p)))
        x += w + 20
    steps = [("口令 / 文件 / 链接", "① 复制粘贴"), ("发给机器人", "② 自动解析"), ("全群即用", "③ 群里可用")]
    box_w, box_h, gap = 420, 150, 130
    start_x = W / 2 - (box_w * 3 + gap * 2) / 2
    d = ImageDraw.Draw(img)
    # 连线生长
    for i in range(2):
        x0 = start_x + box_w + i * (box_w + gap) + 8
        p = eo_cubic((t - 1.35 - i * 0.18) / 0.5)
        if p > 0:
            d.line((x0, 632, x0 + (gap - 20) * p, 632), fill=ACCENT, width=4)
            tip = x0 + (gap - 20) * p
            d.polygon([(tip, 624), (tip + 14, 632), (tip, 640)], fill=ACCENT)
    for i, (step, sub) in enumerate(steps):
        p = eo_cubic((t - 0.9 - i * 0.16) / 0.55)
        if p <= 0:
            continue
        sx = start_x + i * (box_w + gap)
        card = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
        cd = ImageDraw.Draw(card)
        cd.rounded_rectangle((0, 0, box_w - 1, box_h - 1), radius=26, fill=CARD, outline=LINE, width=2)
        cd.text((box_w / 2, 52), step, font=font(True, 36), fill=TEXT, anchor="mm")
        cd.text((box_w / 2, 108), sub, font=font(False, 24), fill=MUTED, anchor="mm")
        alpha_composite(img, fade(card, p), (sx, 557 + 40 * (1 - p)))
    text_fade(img, "单双周 · 节次换算 · 调课 · 时区 —— 全部自动处理", W / 2, 850, 30, t, 2.2, center=True)
    return img


def _card_scene(t: float, assets: dict, key: str, title: str, lines: list[str],
                stat: tuple[str, int] | None = None) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    char_in(img, title, 96, 200, 56, t, 0.1, stagger=0.03, center=False)
    y = 270
    for i, line in enumerate(lines):
        text_fade(img, line, 96, y, 31, t, 0.55 + i * 0.16, rise=24)
        y += 52
    card = assets[key]
    box = (700, 90, W - 40, H - 120)
    scale = min((box[2] - box[0]) / card.width, (box[3] - box[1]) / card.height)
    resized = card.resize((int(card.width * scale), int(card.height * scale)), Image.LANCZOS)
    plate = card_plate(resized)
    p = eo_quint((t - 0.3) / 0.9)
    if p > 0:
        px = box[0] + (box[2] - box[0]) / 2 - plate.width / 2
        py = box[1] + (box[3] - box[1]) / 2 - plate.height / 2
        alpha_composite(img, fade(plate, clamp(p)), (px + 340 * (1 - p), py))
        sweep(img, (px + 46, py + 46, px + plate.width - 46, py + plate.height - 46), t, 1.15)
        if stat:
            label, target = stat
            n = count_up(t, 1.2, 0.8, target)
            chip = badge_layer(f"{n} {label}", size=26, fg="#BFDBFE", bg=(30, 58, 95), outline="#3B82F6")
            pc = eo_back((t - 1.2) / 0.45)
            if pc > 0:
                alpha_composite(img, fade(chip, clamp(pc)),
                                (px + plate.width - chip.width - 54, py + 58))
    return img


def scene_everyone(t: float, assets: dict) -> Image.Image:
    return _card_scene(t, assets, "everyone", "群友在上什么课", [
        "翘课 / 请假 / 正在上课 / 下一节",
        "双列头像格子，一张图看完",
        "3 天没说话自动隐藏，发言即恢复",
    ], stat=("人 / 页", 30))


def scene_rank(t: float, assets: dict) -> Image.Image:
    img = _card_scene(t, assets, "rank", "课程时长榜", [
        "今日 / 本周 / 本月 / 学期",
        "双列排行，一页 28 人",
        "你的名次单独高亮，翻不到也会追加",
    ], stat=("人 / 页", 28))
    ring_pulse(img, 1180, 700, 18, t, 1.6)
    return img


def scene_day(t: float, assets: dict) -> Image.Image:
    return _card_scene(t, assets, "day", "课表随手查", [
        "/课表 直接展示全天课程",
        "正在上的课，蓝框「进行中」",
        "下一节，黄框「下一节」",
    ])


def scene_schedule(t: float, assets: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    char_in(img, "定时节目，每晚准时", 96, 190, 56, t, 0.1, stagger=0.03, center=False)
    y = 262
    for i, line in enumerate([
        "明日早八提醒：一键点名，可 @ 同学",
        "晚间满课榜 · 每周累计榜：卡片图",
        "课上完后的查询附带重要日期倒数",
    ]):
        text_fade(img, line, 96, y, 30, t, 0.5 + i * 0.15, rise=24)
        y += 50
    card = assets["early"]
    box = (700, 90, W - 40, H - 460)
    scale = min((box[2] - box[0]) / card.width, (box[3] - box[1]) / card.height)
    resized = card.resize((int(card.width * scale), int(card.height * scale)), Image.LANCZOS)
    plate = card_plate(resized)
    p = eo_quint((t - 0.3) / 0.9)
    if p > 0:
        px = box[0] + (box[2] - box[0]) / 2 - plate.width / 2
        py = box[1] + (box[3] - box[1]) / 2 - plate.height / 2
        alpha_composite(img, fade(plate, clamp(p)), (px + 340 * (1 - p), py))
        sweep(img, (px + 46, py + 46, px + plate.width - 46, py + plate.height - 46), t, 1.1)
    pills = [
        ("四六级考试", "12/12 周六 · 全国统考", "还有 36 天", 36),
        ("国庆节", "10/01 周四 · 法定假日", "放假 8 天", 8),
        ("期末周", "01/12 周一起", "冲刺中", None),
    ]
    for i, (tag, date_text, big, num) in enumerate(pills):
        pc = eo_back((t - 0.95 - i * 0.2) / 0.5)
        if pc <= 0:
            continue
        label = big if num is None else (f"还有 {count_up(t, 0.95 + i * 0.2, 0.7, num)} 天" if i == 0 else f"放假 {count_up(t, 0.95 + i * 0.2, 0.7, num)} 天")
        pill = countdown_pill(tag, date_text, label)
        alpha_composite(img, fade(pill, clamp(pc)), (300 + 30 * (1 - pc), 620 + i * 128))
    return img


def scene_web(t: float, assets: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    char_in(img, "网页伴侣（附属插件）", 96, 200, 56, t, 0.1, stagger=0.03, center=False)
    y = 270
    for i, line in enumerate([
        "帮助手册上线为网页",
        "群里发链接，一张图都不发",
        "支持个人域名反代与深色模式",
    ]):
        text_fade(img, line, 96, y, 31, t, 0.5 + i * 0.15, rise=24)
        y += 52
    p = eo_quint((t - 0.3) / 0.9)
    if p > 0 and "web" in assets:
        shot = assets["web"]
        box = (740, 120, W - 70, H - 190)
        scale = min((box[2] - box[0]) / shot.width, (box[3] - box[1]) / shot.height)
        resized = shot.resize((int(shot.width * scale), int(shot.height * scale)), Image.LANCZOS)
        px = box[0] + (box[2] - box[0]) / 2 - resized.width / 2
        py = box[1] + (box[3] - box[1]) / 2 - resized.height / 2
        # 浏览器窗框
        chrome_h = 56
        frame = Image.new("RGBA", (resized.width + 24, resized.height + chrome_h + 12), (0, 0, 0, 0))
        fd = ImageDraw.Draw(frame)
        fd.rounded_rectangle((0, 0, frame.width - 1, frame.height - 1), radius=22, fill=(20, 28, 46), outline=LINE, width=2)
        for i, color in enumerate(("#F87171", "#FBBF24", "#34D399")):
            fd.ellipse((18 + i * 22, 20, 30 + i * 22, 32), fill=color)
        addr = "kb.example.com/help"
        typed = addr[: int(len(addr) * clamp((t - 1.2) / 0.9))]
        fd.rounded_rectangle((100, 14, frame.width - 16, 44), radius=8, fill=(10, 16, 28))
        fd.text((114, 29), typed, font=font(False, 18), fill="#93C5FD", anchor="lm")
        frame.paste(resized, (12, chrome_h + 2))
        alpha_composite(img, fade(frame, clamp(p)), (px + 360 * (1 - p), py))
    return img


def scene_dashboard(t: float, assets: dict) -> Image.Image:
    return _card_scene(t, assets, "dashboard", "后台控制台，管理不刷屏", [
        "课表 / 请假 / 调课 / 群绑定，直接改",
        "群聊筛选 + 一键收纳，几十个群也不乱",
        "节假日、补班映射、全部运行设置",
    ])


def scene_security(t: float, assets: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    # 警示三角
    p = eo_back((t - 0.1) / 0.5)
    if p > 0:
        tri = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
        td = ImageDraw.Draw(tri)
        td.polygon([(60, 14), (18, 92), (102, 92)], fill="#FBBF24")
        td.text((60, 74), "!", font=font(True, 40), fill="#0B1220", anchor="mm")
        alpha_composite(img, fade(tri, clamp(p)), (W / 2 - 520, 230 - 20 * (1 - p)))
    char_in(img, "部署前，记住三件事", W / 2, 300, 64, t, 0.25, stagger=0.035)
    items = [
        ("放行端口", "安全组 / 防火墙放行网页端口"),
        ("警惕裸 IP", "裸 IP 链接 = 公开服务器地址"),
        ("域名反代", "127.0.0.1 + 反代，只放行 443"),
    ]
    for i, (tag, desc) in enumerate(items):
        pc = eo_cubic((t - 0.7 - i * 0.22) / 0.55)
        if pc <= 0:
            continue
        card = Image.new("RGBA", (520, 180), (0, 0, 0, 0))
        cd = ImageDraw.Draw(card)
        cd.rounded_rectangle((0, 0, 519, 179), radius=22, fill=CARD, outline=LINE, width=2)
        cd.text((260, 56), tag, font=font(True, 38), fill="#FCD34D", anchor="mm")
        cd.text((260, 118), desc, font=font(False, 24), fill=MUTED, anchor="mm")
        alpha_composite(img, fade(card, pc), (W / 2 - 800 + i * 540, 470 + 36 * (1 - pc)))
    text_fade(img, "详细步骤：主插件仓库 docs/deploy-security.md", W / 2, 800, 26, t, 1.9, center=True)
    return img


def _star(size: int, fill: str = "#FBBF24") -> Image.Image:
    import math

    img = Image.new("RGBA", (size + 8, size + 8), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = (size + 8) / 2
    r_out, r_in = size / 2, size / 4.6
    pts = []
    for k in range(10):
        r = r_out if k % 2 == 0 else r_in
        ang = -math.pi / 2 + k * math.pi / 5
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    d.polygon(pts, fill=fill)
    return img


def scene_outro(t: float, assets: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    char_in(img, "课表 Lite", W / 2, 300, 96, t, 0.15, stagger=0.05, dur=0.55)
    text_fade(img, "课表数据只保存在你自己的服务器，不连校园网，不抓教务系统", W / 2, 400, 30, t, 0.6, center=True)
    p = eo_quint((t - 0.8) / 0.6)
    if p > 0:
        card = Image.new("RGBA", (1440, 130), (0, 0, 0, 0))
        d = ImageDraw.Draw(card)
        d.rounded_rectangle((0, 0, 1439, 129), radius=24, fill=CARD, outline=ACCENT, width=2)
        d.text((720, 65), "github.com/suiren0219/astrbot_plugin_schedule_lite", font=font(True, 42), fill="#93C5FD", anchor="mm")
        alpha_composite(img, fade(card, p), (W / 2 - 720, 470 + 24 * (1 - p)))
    # 星星弹入 + 闪烁
    stars = [(W / 2 - 380, 700, 56), (W / 2 + 380, 700, 56), (W / 2, 636, 34)]
    for i, (sx, sy, size) in enumerate(stars):
        ps = eo_back((t - 1.5 - i * 0.12) / 0.5)
        if ps > 0:
            twinkle = 0.82 + 0.18 * np.sin(5 * t + i * 2)
            star = fade(_star(size), clamp(ps) * twinkle)
            alpha_composite(img, star, (sx - size / 2 - 4, sy - size / 2 - 4))
    char_in(img, "欢迎 Star", W / 2, 790, 62, t, 1.6, stagger=0.04)
    text_fade(img, "觉得有用的话，去 GitHub 点一颗星吧", W / 2, 856, 27, t, 1.9, center=True)
    text_fade(img, "装上网页伴侣，帮助手册自动上线为网页", W / 2, 940, 26, t, 2.2, center=True)
    return img


SCENE_FUNCS = [
    scene_hook, scene_import, scene_everyone, scene_rank, scene_day,
    scene_schedule, scene_web, scene_dashboard, scene_security, scene_outro,
]


# ---------------------------------------------------------------- 逐帧渲染


TRANSITION = 0.38


def _wipe_band(frame: Image.Image, boundary_x: float) -> None:
    """转场边界的一条光带：遮住换幕瞬间的空档，也强化「推动」的动感。"""
    x = int(boundary_x)
    if -240 <= x <= W + 240:
        band = Image.new("RGBA", (240, H), (0, 0, 0, 0))
        bd = ImageDraw.Draw(band)
        for i in range(120):
            a = int(105 * (1 - i / 120) ** 2)
            bd.rectangle((120 - i, 0, 120 - i + 1, H), fill=(96, 165, 250, a))
            bd.rectangle((120 + i, 0, 120 + i + 1, H), fill=(59, 130, 246, a))
        frame.alpha_composite(band, (x - 120, 0))


def render_frame(t: float, assets: dict) -> Image.Image:
    scene_idx = min(int(t // SCENE), SCENES - 1)
    local = t - scene_idx * SCENE
    frame = background(t)
    if scene_idx > 0 and local < TRANSITION:
        # 幕首统一转场：上一幕向左滑出、当前幕从右推入
        # （当前幕按真实 local 时间渲染，入场动画随滑动同步进行）
        p = eo_cubic(local / TRANSITION)
        prev = SCENE_FUNCS[scene_idx - 1](SCENE - 0.001, assets)
        alpha_composite(frame, prev, (-W * p, 0))
        cur = SCENE_FUNCS[scene_idx](local, assets)
        alpha_composite(frame, cur, (W * (1 - p), 0))
        _wipe_band(frame, W * (1 - p))
    else:
        alpha_composite(frame, SCENE_FUNCS[scene_idx](local, assets), (0, 0))
    # 全局缓推（Ken Burns），镜头一直在极缓慢地推近
    zoom = 1.0 + 0.024 * eo_cubic(local / SCENE)
    zw, zh = int(W / zoom), int(H / zoom)
    box = ((W - zw) // 2, (H - zh) // 2, (W + zw) // 2, (H + zh) // 2)
    return frame.crop(box).resize((W, H), Image.LANCZOS).convert("RGB")


def build_audio(out: Path) -> Path | None:
    """把十段配音按幕起点混合，音量为 2x。"""
    import imageio_ffmpeg

    inputs = []
    filters = []
    for i in range(1, SCENES + 1):
        wav = NARRATION_DIR / f"scene{i:02d}.wav"
        if not wav.exists():
            continue
        inputs += ["-i", str(wav)]
        delay = int(((i - 1) * SCENE + 0.35) * 1000)
        filters.append(f"[{len(inputs) // 2 - 1}:a]volume=1.8,adelay={delay}|{delay}[a{i}]")
    if not filters:
        return None
    mix = "".join(f"[a{i}]" for i in range(1, SCENES + 1))
    filters.append(f"{mix}amix=inputs={len(filters)}:normalize=0,alimiter=limit=0.8[mix]")
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", *inputs,
         "-filter_complex", ";".join(filters), "-map", "[mix]", "-t", str(TOTAL),
         "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(out.with_suffix(".wav"))],
        check=True,
    )
    return out.with_suffix(".wav")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frame", type=float, default=None, help="只导出某一时刻静帧")
    parser.add_argument("--fps", type=int, default=FPS)
    args = parser.parse_args()
    BUILD.mkdir(parents=True, exist_ok=True)
    assets = {k: v.convert("RGBA") for k, v in build_assets().items()}

    if args.frame is not None:
        img = render_frame(args.frame, assets)
        path = BUILD / "frame.png"
        img.save(path)
        print("frame:", path)
        return

    import imageio_ffmpeg
    from PIL import Image as PILImage

    audio = build_audio(BUILD / "audio.m4a")
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    total_frames = int(TOTAL * args.fps)
    cmd = [ffmpeg, "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(args.fps), "-i", "-"]
    if audio is not None:
        cmd += ["-i", str(audio)]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"]
    if audio is not None:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
    cmd += [str(OUT_MP4)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    for n in range(total_frames):
        t = n / args.fps
        frame = render_frame(t, assets)
        proc.stdin.write(np.asarray(frame, dtype=np.uint8).tobytes())
        if n % 150 == 0:
            print(f"frame {n}/{total_frames}")
    proc.stdin.close()
    proc.wait()
    print("video:", OUT_MP4)


if __name__ == "__main__":
    main()
