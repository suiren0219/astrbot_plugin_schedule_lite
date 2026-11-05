"""生成课表 Lite 宣传视频（动效版）：docs/promo/promo.mp4

用法：
    python docs/promo/make_video.py

1920x1080 · 30fps · 约 63s。动效用 MoviePy（https://github.com/Zulko/moviepy）：
每幕元素预渲染成透明 PNG，再在时间线上做滑入 / 弹入 / 交叉淡入等动画。
卡片素材用主插件渲染管线实时生成（schedule_lite_core.render），
网页截图来自 docs/promo/assets/web-help.png。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from moviepy import CompositeVideoClip, ImageClip, vfx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from schedule_lite_core import render  # noqa: E402

W, H = 1920, 1080
FPS = 30
SCENE = 7.0
TOTAL_TIME = SCENE * 9
FRAMES_DIR = Path(__file__).resolve().parent / "build" / "elements"
OUT_MP4 = Path(__file__).resolve().parent / "promo.mp4"
AVATAR_DIR = Path("D:/tmp/card_test/av")
WEB_SHOT = Path(__file__).resolve().parent / "assets" / "web-help.png"
BG_TOP, BG_BOT = (11, 18, 32), (22, 32, 54)
ACCENT = "#3B82F6"
TEXT = "#F8FAFC"
MUTED = "#94A3B8"
LINE = "#26314A"
CARD = "#111A2C"

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    key = ("b" if bold else "r", size)
    if key not in _font_cache:
        path = "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"
        _font_cache[key] = ImageFont.truetype(path, size)
    return _font_cache[key]


def ease_out_cubic(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    return 1 - (1 - t) ** 3


def ease_out_back(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def gradient_bg() -> Image.Image:
    base = Image.new("RGB", (W, H), BG_TOP)
    for y in range(H):
        t = y / H
        color = tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t) for i in range(3))
        base.paste(color, (0, y, W, y + 1))
    glow = Image.new("RGB", (W, H), BG_TOP)
    gd = ImageDraw.Draw(glow)
    gd.ellipse((W - 620, -260, W + 320, 660), fill=(24, 36, 62))
    gd.ellipse((-320, H - 420, 420, H + 320), fill=(20, 30, 52))
    base = Image.blend(base, glow, 0.65)
    draw = ImageDraw.Draw(base)
    draw.rectangle((0, 0, W, 10), fill="#2563EB")
    return base


def wrap(text: str, size: int, width: int) -> list[str]:
    f = font(False, size)
    out, current = [], ""
    for ch in text:
        if f.getlength(current + ch) > width:
            out.append(current)
            current = ch
        else:
            current += ch
    if current:
        out.append(current)
    return out


def text_layer(x: int, y: int, width: int, title: str, lines: list[str], *,
               title_size: int = 56, body_size: int = 31, line_gap: int = 20,
               title_color: str = TEXT) -> tuple[Image.Image, tuple[int, int]]:
    """标题 + 正文打包成一张透明层，返回 (PNG, 锚点)。"""
    probe = Image.new("RGBA", (8, 8))
    pd = ImageDraw.Draw(probe)
    th = title_size + 36
    body: list[tuple[str, int]] = []
    for line in lines:
        for piece in wrap(line, body_size, width):
            body.append((piece, body_size + 14))
        body.append(("", line_gap - 14))
    height = th + sum(h for _t, h in body) + 40
    img = Image.new("RGBA", (width + 120, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((0, 0), title, font=font(True, title_size), fill=title_color)
    ty = th
    for piece, h in body:
        if piece:
            d.text((0, ty), piece, font=font(False, body_size), fill=MUTED)
        ty += h
    return img, (x - 60, y)


def badge_layer(text: str, *, size: int = 26, fg: str = TEXT,
                bg: tuple = (30, 41, 66), outline: str = LINE) -> Image.Image:
    f = font(True, size)
    w = int(f.getlength(text)) + 44
    h = size + 30
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=h // 2, fill=bg, outline=outline, width=2)
    d.text((22, h // 2), text, font=f, fill=fg, anchor="lm")
    return img


def countdown_pill(tag: str, date_text: str, big: str, *, big_color: str = "#FCD34D") -> Image.Image:
    """倒计时横条：左侧事件 + 日期，右侧大号数字。"""
    img = Image.new("RGBA", (640, 108), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, 639, 107), radius=24, fill=CARD, outline=LINE, width=2)
    d.rounded_rectangle((0, 0, 8, 107), radius=4, fill=ACCENT)
    d.text((36, 30), tag, font=font(True, 30), fill=TEXT, anchor="lm")
    d.text((36, 76), date_text, font=font(False, 22), fill=MUTED, anchor="lm")
    d.text((600, 54), big, font=font(True, 44), fill=big_color, anchor="rm")
    return img


def paste_shot(img: Image.Image, shot: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    """素材图等比塞进 box（返回含阴影/描边的新图层，尺寸同画布）。"""
    x0, y0, x1, y1 = box
    max_w, max_h = x1 - x0, y1 - y0
    scale = min(max_w / shot.width, max_h / shot.height)
    resized = shot.resize((int(shot.width * scale), int(shot.height * scale)), Image.LANCZOS)
    rw, rh = resized.size
    px, py = x0 + (max_w - rw) // 2, y0 + (max_h - rh) // 2
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((px - 10, py - 8, px + rw + 10, py + rh + 18),
                                             radius=30, fill=(0, 0, 0, 130))
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    layer.alpha_composite(shadow)
    layer.paste(resized, (px, py))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((px, py, px + rw, py + rh), radius=26, outline=LINE, width=2)
    return layer


def chrome_layer(index: int, total: int) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((72, 46), "SCHEDULE LITE · 课表 Lite", font=font(True, 24), fill="#60A5FA")
    gap, radius = 34, 6
    start_x = W // 2 - (total - 1) * gap // 2
    for i in range(total):
        color = ACCENT if i == index else "#334155"
        cx = start_x + i * gap
        d.ellipse((cx - radius, H - 56 - radius, cx + radius, H - 56 + radius), fill=color)
    return img


# ------------------------------------------------------------------ 素材卡


def build_assets() -> dict[str, Image.Image]:
    if not AVATAR_DIR.exists():
        AVATAR_DIR.mkdir(parents=True)
        for i, color in enumerate([(59, 130, 246), (124, 58, 237), (219, 39, 119),
                                   (234, 88, 12), (5, 150, 105), (13, 148, 136)]):
            im = Image.new("RGB", (96, 96), color)
            d = ImageDraw.Draw(im)
            d.ellipse((28, 18, 68, 58), fill=(255, 255, 255))
            d.ellipse((14, 60, 82, 116), fill=(255, 255, 255))
            im.save(AVATAR_DIR / f"a{i}.png")
    av = lambda i: str(AVATAR_DIR / f"a{i % 6}.png")  # noqa: E731
    out_dir = FRAMES_DIR.parent / "assets-render"
    assets: dict[str, Image.Image] = {}

    mixed = []
    labels = ["进行中", "下一节", "翘课中", "请假中", "分身中"]
    for i in range(12):
        mixed.append({"name": f"同学{i}", "type": (1, 3, 2, 6, 0)[i % 5], "label": labels[i % 5],
                      "main": ["高等数学", "大学英语", "数据结构", "大学物理", "体育"][i % 5],
                      "sub": ["08:00-09:40 松1-303", "10:00-11:40 松2-201", "14:00-15:40 松1-105",
                              "16:00-17:40 松2-305", "19:00-20:40 操场"][i % 5], "avatar": av(i)})
    for i in range(6):
        mixed.append({"name": f"已下课{i}", "type": 4, "label": "已结束", "main": "今日课程已上完",
                      "sub": "共 3.2 小时", "hours": 3.2, "avatar": av(i)})
    for i in range(6):
        mixed.append({"name": f"无课{i}", "type": 5, "label": "无课程", "main": "今天没有课",
                      "sub": "", "avatar": av(i)})
    paths = render.render_everyone(mixed, out_dir, title="群友在上什么课？",
                                   subtitle="2026/11/06 周五 · 12:30 · 共 30 人",
                                   dark=True, font_path="C:/Windows/Fonts/msyh.ttc")
    assets["everyone"] = Image.open(paths[0])

    rows = [{"name": f"同学{i}", "sub": f"{max(28 - i * 0.9, 1.0):.1f} 小时", "badge": f"#{i + 1}",
             "badge_kind": "rank", "avatar": av(i), "highlight": "mine" if i == 4 else ""}
            for i in range(27)]
    rows.append({"name": "你 · 皮卡丘", "sub": "第 41 名 · 2.5 小时", "badge": "#41",
                 "badge_kind": "mine", "highlight": "mine", "avatar": av(0)})
    paths = render.render_grid_card(rows, out_dir, title="本群本周课程时长榜",
                                    subtitle="11/06 · 共 46 人 · 显示前 27 名",
                                    dark=True, font_path="C:/Windows/Fonts/msyh.ttc",
                                    columns=2, cell_h=60, rows_per_col=14,
                                    name_size=17, sub_size=13, file_prefix="rank")
    assets["rank"] = Image.open(paths[0])

    drows = [{"time": f"{8 + (i // 2) if i < 8 else 13 + (i - 8) // 2:02d}:00-"
                      f"{8 + (i // 2) if i < 8 else 13 + (i - 8) // 2:02d}:40",
              "name": f"课程{i + 1}", "place": f"松1-{300 + i}"} for i in range(12)]
    drows[2]["highlight"] = "now"
    drows[3]["highlight"] = "next"
    paths = render.render_day_card(drows, out_dir, dark=True,
                                   subtitle="2026/11/06 周五 · 第 10 周",
                                   font_path="C:/Windows/Fonts/msyh.ttc")
    assets["day"] = Image.open(paths[0])

    early = [{"name": f"同学{i}", "sub": "高等数学 08:00-09:40", "avatar": av(i)} for i in range(20)]
    paths = render.render_grid_card(early, out_dir, title="明日早八提醒",
                                    subtitle="11/07 周六 · 共 20 人", footer_note="发送 /课表 绑定课表",
                                    dark=True, font_path="C:/Windows/Fonts/msyh.ttc",
                                    columns=2, cell_h=64, rows_per_col=10)
    assets["early"] = Image.open(paths[0])

    if WEB_SHOT.exists():
        assets["web"] = Image.open(WEB_SHOT).convert("RGB")
    return assets


# ------------------------------------------------------------------ 时间线


def slide(png: Image.Image, pos: tuple[int, int], *, scene: float, delay: float = 0.0,
          dur: float = SCENE - 0.4, dx: int = 0, dy: int = 0, ease_dur: float = 0.8,
          fade: float = 0.45, fade_out: float = 0.35):
    """滑入 + 交叉淡入淡出。pos 为最终锚点（PNG 左上）。"""
    clip = (ImageClip(np.array(png))
            .with_start(scene + delay)
            .with_duration(dur)
            .with_position(lambda t: (
                pos[0] + dx * (1 - ease_out_cubic(t / ease_dur)),
                pos[1] + dy * (1 - ease_out_cubic(t / ease_dur)),
            ))
            .with_effects([vfx.CrossFadeIn(fade), vfx.CrossFadeOut(fade_out)]))
    return clip


def pop(png: Image.Image, center: tuple[int, int], *, scene: float, delay: float = 0.0,
        dur: float = SCENE - 0.4, scale_dur: float = 0.45, fade: float = 0.3):
    """回弹缩放弹入。center 为 PNG 中心点。"""
    base_w, base_h = png.size

    def scale_at(t: float) -> float:
        return 0.8 + 0.2 * ease_out_back(t / scale_dur)

    clip = (ImageClip(np.array(png))
            .with_start(scene + delay)
            .with_duration(dur)
            .resized(scale_at)
            .with_position(lambda t: (
                center[0] - base_w * scale_at(t) / 2,
                center[1] - base_h * scale_at(t) / 2,
            ))
            .with_effects([vfx.CrossFadeIn(fade), vfx.CrossFadeOut(0.35)]))
    return clip


def build_clips(assets: dict[str, Image.Image]) -> list:
    bg = ImageClip(np.array(gradient_bg())).with_duration(TOTAL_TIME)
    clips = [bg]
    layers = FRAMES_DIR
    layers.mkdir(parents=True, exist_ok=True)

    def save(png: Image.Image, name: str) -> Image.Image:
        path = layers / f"{name}.png"
        png.save(path)
        return png

    # ---- S1 开场
    s = 0.0
    brand = Image.new("RGBA", (600, 60), (0, 0, 0, 0))
    ImageDraw.Draw(brand).text((300, 30), "SCHEDULE LITE", font=font(True, 34),
                               fill="#60A5FA", anchor="mm")
    clips.append(slide(brand, (W // 2 - 300, 266), scene=s, delay=0.2, dy=26))
    title = Image.new("RGBA", (1200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(title).text((600, 100), "课表 Lite", font=font(True, 132), fill=TEXT, anchor="mm")
    clips.append(pop(title, (W // 2, 440), scene=s, delay=0.35, dur=SCENE - 0.4))
    sub = Image.new("RGBA", (1400, 80), (0, 0, 0, 0))
    ImageDraw.Draw(sub).text((700, 40), "把课表变成群里的公共设施", font=font(False, 40),
                             fill=MUTED, anchor="mm")
    clips.append(slide(sub, (W // 2 - 700, 552), scene=s, delay=0.65, dy=26))
    b1 = badge_layer("v1.10.7", fg="#BFDBFE", bg=(30, 58, 95), outline="#3B82F6")
    clips.append(pop(b1, (W // 2 - 180, 736), scene=s, delay=0.95))
    b2 = badge_layer("179 tests passed", fg=MUTED)
    clips.append(pop(b2, (W // 2 + 120, 736), scene=s, delay=1.1))

    # ---- S2 导入
    s = SCENE
    tl, pos = text_layer(W // 2 - 560, 120, 1120, "多来源导入，全都自动归一化", [],
                         title_size=58)
    clips.append(slide(tl, pos, scene=s, delay=0.15, dy=30))
    sources = ["Nexio 分享口令", "拾光课程表", "WakeUp", "iCalendar (.ics)", "通用 JSON / CSV"]
    widths = [badge_layer(t, size=30).width for t in sources]
    total_w = sum(widths) + 4 * 20
    x = (W - total_w) // 2
    for i, (t, w) in enumerate(zip(sources, widths)):
        clips.append(pop(badge_layer(t, size=30), (x + w / 2, 396), scene=s, delay=0.5 + i * 0.12))
        x += w + 20
    steps = [("口令 / 文件 / 链接", "① 复制粘贴"), ("发给机器人", "② 自动解析"), ("全群即用", "③ 群里可用")]
    box_w, box_h, gap = 420, 150, 130
    start_x = W // 2 - (box_w * 3 + gap * 2) // 2
    for i, (sx, (step, sub_t)) in enumerate(zip(
            range(start_x, start_x + 3 * (box_w + gap), box_w + gap), steps)):
        card = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
        d = ImageDraw.Draw(card)
        d.rounded_rectangle((0, 0, box_w - 1, box_h - 1), radius=26, fill=CARD, outline=LINE, width=2)
        d.text((box_w / 2, 52), step, font=font(True, 36), fill=TEXT, anchor="mm")
        d.text((box_w / 2, 108), sub_t, font=font(False, 24), fill=MUTED, anchor="mm")
        clips.append(slide(card, (sx, 560), scene=s, delay=0.9 + i * 0.15, dy=40))
        if i < 2:
            arrow = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
            ImageDraw.Draw(arrow).text((30, 30), "→", font=font(True, 44), fill=ACCENT, anchor="mm")
            clips.append(slide(arrow, (sx + box_w + gap / 2 - 30, 605), scene=s,
                               delay=1.25 + i * 0.15, fade=0.2))
    note = Image.new("RGBA", (1600, 60), (0, 0, 0, 0))
    ImageDraw.Draw(note).text((800, 30), "单双周 · 节次换算 · 调课 · 时区 —— 全部自动处理",
                              font=font(False, 30), fill=MUTED, anchor="mm")
    clips.append(slide(note, (W // 2 - 800, 856), scene=s, delay=1.7, dy=20))

    # ---- S3/S4/S5/S7 卡片幕
    shots = [
        (2 * SCENE, "everyone", "群友在上什么课",
         ["翘课 / 请假 / 正在上课 / 下一节", "双列头像格子，一页 30 人", "3 天没说话自动隐藏，发言即恢复"]),
        (3 * SCENE, "rank", "课程时长榜",
         ["今日 / 本周 / 本月 / 学期", "双列排行一页 28 人", "你的名次单独高亮，翻不到也会追加"]),
        (4 * SCENE, "day", "课表随手查",
         ["/课表 直接展示全天课程", "正在上的课蓝框「进行中」", "下一节课黄框「下一节」"]),
        (6 * SCENE, "web", "网页伴侣（附属插件）",
         ["帮助手册上线为网页", "群里发链接，一张图都不发", "支持个人域名反代与深色模式"]),
    ]
    for scene, key, title, lines in shots:
        tl, pos = text_layer(96, 160, 620, title, lines, title_size=56, body_size=31)
        clips.append(slide(tl, pos, scene=scene, delay=0.15, dy=30))
        shot = paste_shot(assets[key], assets[key], (760, 90, W - 70, H - 130))
        clips.append(slide(shot, (0, 0), scene=scene, delay=0.3, dx=W, ease_dur=0.9))

    # ---- S6 定时节目 + 倒计时/节日
    s = 5 * SCENE
    tl, pos = text_layer(96, 150, 620, "定时节目，每晚准时", [
        "明日早八提醒：一键点名，可 @ 同学",
        "晚间满课榜 · 每周累计榜：渲染成卡片图",
        "失败自动退回文字，每群每天最多一次",
        "课上完后的查询附带重要日期倒数",
    ], title_size=56, body_size=30)
    clips.append(slide(tl, pos, scene=s, delay=0.15, dy=30))
    shot = paste_shot(assets["early"], assets["early"], (760, 90, W - 70, H - 460))
    clips.append(slide(shot, (0, 0), scene=s, delay=0.3, dx=W, ease_dur=0.9))
    pills = [
        countdown_pill("四六级考试", "12/12 周六 · 全国统考", "还有 36 天"),
        countdown_pill("国庆节", "10/01 周四 · 法定假日", "放假 8 天"),
        countdown_pill("期末周", "01/12 周一起", "冲刺中"),
    ]
    for i, pill in enumerate(pills):
        clips.append(pop(pill, (480, 660 + i * 128), scene=s, delay=1.0 + i * 0.2))

    # ---- S8 安全部署
    s = 7 * SCENE
    title_img = Image.new("RGBA", (1200, 120), (0, 0, 0, 0))
    d = ImageDraw.Draw(title_img)
    tf = font(True, 60)
    tw = tf.getlength("部署前，花一分钟看这里")
    icon_cx = 600 - tw / 2 - 70
    tri = [(icon_cx, 26), (icon_cx - 34, 80), (icon_cx + 34, 80)]
    d.polygon(tri, fill="#FBBF24")
    d.text((icon_cx, 62), "!", font=font(True, 30), fill="#0B1220", anchor="mm")
    d.text((600 + 40, 54), "部署前，花一分钟看这里", font=tf, fill=TEXT, anchor="mm")
    clips.append(slide(title_img, (W // 2 - 600, 120), scene=s, delay=0.15, dy=30))
    tips = [
        ("放行端口", "云服务器安全组 / 防火墙放行网页端口（默认 8766 TCP）"),
        ("警惕裸 IP", "裸 IP 链接发到群里 = 公开服务器 IP，可能被扫描攻击"),
        ("域名反代", "web_host=127.0.0.1 + Nginx/Caddy 反代，安全组只放行 443"),
    ]
    for i, (tag, desc) in enumerate(tips):
        card = Image.new("RGBA", (W - 480, 130), (0, 0, 0, 0))
        d = ImageDraw.Draw(card)
        d.rounded_rectangle((0, 0, W - 481, 129), radius=24, fill=CARD, outline=LINE, width=2)
        d.text((50, 40), tag, font=font(True, 38), fill="#FCD34D")
        d.text((280, 46), desc, font=font(False, 30), fill=TEXT)
        clips.append(slide(card, (240, 330 + i * 160), scene=s, delay=0.5 + i * 0.15, dy=44))
    note = Image.new("RGBA", (1400, 50), (0, 0, 0, 0))
    ImageDraw.Draw(note).text((700, 25), "完整指南：主插件仓库 docs/deploy-security.md",
                              font=font(False, 26), fill=MUTED, anchor="mm")
    clips.append(slide(note, (W // 2 - 700, 866), scene=s, delay=1.4, dy=20))

    # ---- S9 结尾
    s = 8 * SCENE
    title = Image.new("RGBA", (1200, 160), (0, 0, 0, 0))
    ImageDraw.Draw(title).text((600, 80), "课表 Lite", font=font(True, 96), fill=TEXT, anchor="mm")
    clips.append(pop(title, (W // 2, 330), scene=s, delay=0.2, dur=SCENE - 0.4))
    sub = Image.new("RGBA", (1700, 60), (0, 0, 0, 0))
    ImageDraw.Draw(sub).text((850, 30), "课表数据只保存在你自己的服务器，不连校园网，不抓教务系统",
                             font=font(False, 32), fill=MUTED, anchor="mm")
    clips.append(slide(sub, (W // 2 - 850, 470), scene=s, delay=0.5, dy=24))
    repo_card = Image.new("RGBA", (1440, 140), (0, 0, 0, 0))
    d = ImageDraw.Draw(repo_card)
    d.rounded_rectangle((0, 0, 1439, 139), radius=26, fill=CARD, outline=ACCENT, width=2)
    d.text((720, 70), "github.com/suiren0219/astrbot_plugin_schedule_lite",
           font=font(True, 44), fill="#93C5FD", anchor="mm")
    clips.append(slide(repo_card, (W // 2 - 720, 570), scene=s, delay=0.8, dy=44))
    tags = ["AstrBot 插件", "MIT 开源", "QQ 群友好"]
    widths = [badge_layer(t, size=28).width for t in tags]
    x = W // 2 - (sum(widths) + 2 * 24) // 2
    for i, (t, w) in enumerate(zip(tags, widths)):
        clips.append(pop(badge_layer(t, size=28), (x + w / 2, 800), scene=s, delay=1.3 + i * 0.12))
        x += w + 24
    note = Image.new("RGBA", (1500, 50), (0, 0, 0, 0))
    ImageDraw.Draw(note).text((750, 25), "装上网页伴侣，帮助手册自动上线为网页",
                              font=font(False, 28), fill=MUTED, anchor="mm")
    clips.append(slide(note, (W // 2 - 750, 900), scene=s, delay=1.7, dy=20))

    # ---- 各幕进度点
    for i in range(9):
        clips.append(slide(chrome_layer(i, 9), (0, 0), scene=i * SCENE, delay=0.0,
                           dur=SCENE, fade=0.3))
    return clips


def encode(clips: list) -> None:
    video = CompositeVideoClip(clips, size=(W, H)).with_duration(TOTAL_TIME)
    video.write_videofile(
        str(OUT_MP4), fps=FPS, codec="libx264", preset="medium",
        ffmpeg_params=["-crf", "20"], logger=None,
    )


def main() -> None:
    assets = build_assets()
    clips = build_clips(assets)
    encode(clips)
    print("video:", OUT_MP4)


if __name__ == "__main__":
    main()
