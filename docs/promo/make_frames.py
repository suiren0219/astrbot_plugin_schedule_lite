"""生成课表 Lite 宣传视频：docs/promo/promo.mp4

用法：
    python docs/promo/make_frames.py            # 画帧 + 合成（需要 Pillow / imageio-ffmpeg）
    python docs/promo/make_frames.py --frames   # 只画帧不合成

帧输出到 build/frames，成片输出到 docs/promo/promo.mp4（1920x1080，30fps，约 63s）。
素材：深色卡片用主插件渲染管线实时生成（schedule_lite_core.render），
网页截图来自 docs/promo/assets/web-help.png（可重截）。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from schedule_lite_core import render  # noqa: E402

W, H = 1920, 1080
FRAMES_DIR = Path(__file__).resolve().parent / "build" / "frames"
OUT_MP4 = Path(__file__).resolve().parent / "promo.mp4"
ASSETS = Path("D:/tmp/promo/assets")
AVATAR_DIR = Path("D:/tmp/card_test/av")
WEB_SHOT = Path(__file__).resolve().parent / "assets" / "web-help.png"
BG_TOP, BG_BOT = (11, 18, 32), (22, 32, 54)
ACCENT = "#3B82F6"
ACCENT_SOFT = (59, 130, 246, 40)
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


def gradient_bg() -> Image.Image:
    base = Image.new("RGB", (W, H), BG_TOP)
    top, bottom = BG_TOP, BG_BOT
    for y in range(H):
        t = y / H
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        base.paste(color, (0, y, W, y + 1))
    glow = Image.new("RGB", (W, H), BG_TOP)
    gd = ImageDraw.Draw(glow)
    gd.ellipse((W - 620, -260, W + 320, 660), fill=(24, 36, 62))
    gd.ellipse((-320, H - 420, 420, H + 320), fill=(20, 30, 52))
    base = Image.blend(base, glow, 0.65)
    draw = ImageDraw.Draw(base)
    draw.rectangle((0, 0, W, 10), fill="#2563EB")
    return base


def chrome(draw: ImageDraw.Draw, index: int, total: int, brand: str = "SCHEDULE LITE · 课表 Lite") -> None:
    draw.text((72, 46), brand, font=font(True, 24), fill="#60A5FA")
    # 底部进度点
    gap, radius = 34, 6
    start_x = W // 2 - (total - 1) * gap // 2
    for i in range(total):
        color = ACCENT if i == index else "#334155"
        cx = start_x + i * gap
        draw.ellipse((cx - radius, H - 56 - radius, cx + radius, H - 56 + radius), fill=color)


def badge(draw: ImageDraw.Draw, x: int, y: int, text: str, *, size: int = 26, fg: str = TEXT,
          bg: tuple = (30, 41, 66), outline: str | None = LINE) -> int:
    f = font(True, size)
    w = f.getlength(text) + 44
    h = size + 30
    draw.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=bg,
                           outline=outline, width=2)
    draw.text((x + 22, y + h // 2), text, font=f, fill=fg, anchor="lm")
    return int(w)


def paste_shot(canvas: Image.Image, img: Image.Image, box: tuple[int, int, int, int]) -> None:
    """把素材图等比塞进 box，画圆角描边。"""
    x0, y0, x1, y1 = box
    max_w, max_h = x1 - x0, y1 - y0
    scale = min(max_w / img.width, max_h / img.height)
    resized = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    rw, rh = resized.size
    px, py = x0 + (max_w - rw) // 2, y0 + (max_h - rh) // 2
    mask = Image.new("L", (rw, rh), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, rw, rh), radius=26, fill=255)
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((px - 10, py - 8, px + rw + 10, py + rh + 18),
                                             radius=30, fill=(0, 0, 0, 130))
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    canvas.alpha_composite(shadow)
    canvas.paste(resized, (px, py), mask)
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle((px, py, px + rw, py + rh), radius=26, outline=LINE, width=2)


def text_block(draw: ImageDraw.Draw, x: int, y: int, width: int, title: str, lines: list[str],
               *, title_size: int = 54, body_size: int = 30, line_gap: int = 18) -> None:
    draw.text((x, y), title, font=font(True, title_size), fill=TEXT)
    ty = y + title_size + 34
    for line in lines:
        wrapped = wrap(line, body_size, width)
        for piece in wrapped:
            draw.text((x, ty), piece, font=font(False, body_size), fill=MUTED)
            ty += body_size + 14
        ty += line_gap - 14


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


# ------------------------------------------------------------------ 九帧


def frame_01_intro(index: int, total: int) -> Image.Image:
    canvas = gradient_bg().convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    draw.text((W // 2, 300), "SCHEDULE LITE", font=font(True, 34), fill="#60A5FA", anchor="mm")
    draw.text((W // 2, 440), "课表 Lite", font=font(True, 132), fill=TEXT, anchor="mm")
    draw.text((W // 2, 590), "把课表变成群里的公共设施", font=font(False, 40), fill=MUTED, anchor="mm")
    x = W // 2 - 320
    x += badge(draw, x, 700, "v1.10.7", fg="#BFDBFE", bg=(30, 58, 95), outline="#3B82F6") + 18
    badge(draw, x, 700, "179 tests passed", fg=MUTED)
    chrome(draw, index, total)
    return canvas


def frame_02_import(index: int, total: int) -> Image.Image:
    canvas = gradient_bg().convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    draw.text((W // 2, 210), "多来源导入，全都自动归一化", font=font(True, 58), fill=TEXT, anchor="mm")
    sources = ["Nexio 分享口令", "拾光课程表", "WakeUp", "iCalendar (.ics)", "通用 JSON / CSV"]
    widths = [font(True, 30).getlength(t) + 44 for t in sources]
    total_w = sum(widths) + 4 * 20
    x = (W - total_w) // 2
    y = 320
    for t, w in zip(sources, widths):
        badge(draw, x, y, t, size=30)
        x += w + 20
    steps = ["口令 / 文件 / 链接", "发给机器人", "全群即用"]
    box_w, box_h = 420, 150
    gap = 130
    start_x = W // 2 - (box_w * 3 + gap * 2) // 2
    for i, (sx, step) in enumerate(zip(range(start_x, start_x + 3 * (box_w + gap), box_w + gap), steps)):
        draw.rounded_rectangle((sx, 560, sx + box_w, 560 + box_h), radius=26,
                               fill=CARD, outline=LINE, width=2)
        draw.text((sx + box_w / 2, 560 + box_h / 2 - 26), step, font=font(True, 36),
                  fill=TEXT, anchor="mm")
        draw.text((sx + box_w / 2, 560 + box_h / 2 + 30),
                  ["① 复制粘贴", "② 自动解析", "③ 群里可用"][i],
                  font=font(False, 24), fill=MUTED, anchor="mm")
        if i < 2:
            ax = sx + box_w + gap / 2
            draw.text((ax, 560 + box_h / 2), "→", font=font(True, 44), fill=ACCENT, anchor="mm")
    draw.text((W // 2, 830), "单双周 · 节次换算 · 调课 · 时区 —— 全部自动处理",
              font=font(False, 30), fill=MUTED, anchor="mm")
    chrome(draw, index, total)
    return canvas


def frame_shot(index: int, total: int, assets: dict, shot: str, title: str,
               lines: list[str]) -> Image.Image:
    canvas = gradient_bg().convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    text_block(draw, 96, 150, 620, title, lines, title_size=56, body_size=31)
    paste_shot(canvas, assets[shot], (760, 90, W - 70, H - 130))
    chrome(draw, index, total)
    return canvas


def frame_06_schedule(index: int, total: int, assets: dict) -> Image.Image:
    canvas = gradient_bg().convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    text_block(draw, 96, 150, 620, "定时节目，每晚准时", [
        "明日早八提醒：一键点名，可 @ 同学",
        "晚间满课榜 · 每周累计榜：渲染成卡片图",
        "失败自动退回文字，每群每天最多一次",
    ], title_size=56, body_size=31)
    paste_shot(canvas, assets["early"], (760, 90, W - 70, H - 130))
    chrome(draw, index, total)
    return canvas


def frame_08_security(index: int, total: int) -> Image.Image:
    canvas = gradient_bg().convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    title = "部署前，花一分钟看这里"
    tf = font(True, 60)
    tw = tf.getlength(title)
    icon_cx = W // 2 - tw / 2 - 70
    icon_cy = 190
    # 自绘警示三角（msyh 没有 ⚠️ 字形）
    tri = [(icon_cx, icon_cy - 30), (icon_cx - 34, icon_cy + 24), (icon_cx + 34, icon_cy + 24)]
    draw.polygon(tri, fill="#FBBF24")
    draw.text((icon_cx, icon_cy + 6), "!", font=font(True, 30), fill="#0B1220", anchor="mm")
    draw.text((W // 2 + 40, 190), title, font=tf, fill=TEXT, anchor="mm")
    tips = [
        ("放行端口", "云服务器安全组 / 防火墙放行网页端口（默认 8766 TCP）"),
        ("警惕裸 IP", "裸 IP 链接发到群里 = 公开服务器 IP，可能被扫描攻击"),
        ("域名反代", "web_host=127.0.0.1 + Nginx/Caddy 反代，安全组只放行 443"),
    ]
    y = 330
    for tag, desc in tips:
        draw.rounded_rectangle((240, y, W - 240, y + 130), radius=24, fill=CARD,
                               outline=LINE, width=2)
        draw.text((290, y + 40), tag, font=font(True, 38), fill="#FCD34D")
        draw.text((520, y + 46), desc, font=font(False, 30), fill=TEXT)
        y += 160
    draw.text((W // 2, y + 30), "完整指南：主插件仓库 docs/deploy-security.md",
              font=font(False, 26), fill=MUTED, anchor="mm")
    chrome(draw, index, total)
    return canvas


def frame_09_outro(index: int, total: int) -> Image.Image:
    canvas = gradient_bg().convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    draw.text((W // 2, 300), "课表 Lite", font=font(True, 96), fill=TEXT, anchor="mm")
    draw.text((W // 2, 430), "课表数据只保存在你自己的服务器，不连校园网，不抓教务系统",
              font=font(False, 32), fill=MUTED, anchor="mm")
    draw.rounded_rectangle((W // 2 - 720, 540, W // 2 + 720, 680), radius=26,
                           fill=CARD, outline=ACCENT, width=2)
    draw.text((W // 2, 610), "github.com/suiren0219/astrbot_plugin_schedule_lite",
              font=font(True, 44), fill="#93C5FD", anchor="mm")
    x = W // 2 - 520
    for t in ("AstrBot 插件", "MIT 开源", "QQ 群友好"):
        x += badge(draw, x, 740, t, size=28) + 24
    draw.text((W // 2, 900), "装上网页伴侣，帮助手册自动上线为网页",
              font=font(False, 28), fill=MUTED, anchor="mm")
    chrome(draw, index, total)
    return canvas


# ------------------------------------------------------------------ 合成


def encode(frames: list[Path], fps: int = 30, seconds: float = 7.0) -> None:
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    seg_dir = FRAMES_DIR.parent / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    parts = []
    for i, frame in enumerate(frames):
        seg = seg_dir / f"seg{i:02d}.mp4"
        fade_out = seconds - 0.5
        cmd = [
            ffmpeg, "-y", "-loglevel", "error",
            "-loop", "1", "-t", str(seconds), "-i", str(frame),
            "-vf", f"fps={fps},scale={W}:{H},format=yuv420p,"
                   f"fade=t=in:st=0:d=0.5,fade=t=out:st={fade_out}:d=0.5",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", str(seg),
        ]
        subprocess.run(cmd, check=True)
        parts.append(seg)
    list_file = seg_dir / "list.txt"
    list_file.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    subprocess.run([
        ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
        "-i", str(list_file), "-c", "copy", str(OUT_MP4),
    ], check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", action="store_true", help="只画帧不合成")
    args = parser.parse_args()

    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    assets = build_assets()
    total = 9
    frames = [
        frame_01_intro(0, total),
        frame_02_import(1, total),
        frame_shot(2, total, assets, "everyone", "群友在上什么课", [
            "翘课 / 请假 / 正在上课 / 下一节",
            "双列头像格子，一页 30 人",
            "3 天没说话自动隐藏，发言即恢复",
        ]),
        frame_shot(3, total, assets, "rank", "课程时长榜", [
            "今日 / 本周 / 本月 / 学期",
            "双列排行一页 28 人",
            "你的名次单独高亮，翻不到也会追加",
        ]),
        frame_shot(4, total, assets, "day", "课表随手查", [
            "/课表 直接展示全天课程",
            "正在上的课蓝框「进行中」",
            "下一节课黄框「下一节」",
        ]),
        frame_06_schedule(5, total, assets),
        frame_shot(6, total, assets, "web", "网页伴侣（附属插件）", [
            "帮助手册上线为网页",
            "群里发链接，一张图都不发",
            "支持个人域名反代与深色模式",
        ]),
        frame_08_security(7, total),
        frame_09_outro(8, total),
    ]
    paths = []
    for i, frame in enumerate(frames):
        path = FRAMES_DIR / f"frame{i:02d}.png"
        frame.convert("RGB").save(path, "PNG")
        paths.append(path)
        print("frame", i + 1, path)
    if not args.frames:
        encode(paths)
        print("video:", OUT_MP4)


if __name__ == "__main__":
    main()
