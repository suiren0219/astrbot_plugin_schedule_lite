"""生成宣传片配音：每幕一段 WAV，输出到 build/narration/。

用法：
    python docs/promo/make_audio.py                  # edge-tts（默认后端，开箱即用）
    python docs/promo/make_audio.py --backend mimo   # 用自己服务器的 MiMo 配音 API
    python docs/promo/make_audio.py --voice zh-CN-YunxiNeural --rate +12%

MiMo 后端在 docs/promo/tts.json 里配置（不入库）：
    {"url": "https://你的服务器/v1/audio/speech", "key": "sk-...",
     "model": "mimo-audio-tts", "voice": "默认音色", "format": "wav"}
接口按 OpenAI /v1/audio/speech 兼容格式发；协议不同就改 _mimo_tts()。

文案来自 narration.md 的表格（幕号 → 文案），断句用「，；？」给 TTS 制造停顿起伏。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "build" / "narration"
TTS_CONF = HERE / "tts.json"
NARRATION_MD = HERE / "narration.md"

NARRATION: dict[int, str] = {
    1: "可以在群聊里，看到群友上课？课表 Lite——让你的 QQ 机器人，提醒上课。",
    2: "Nexio、拾光、WakeUp、iCalendar 都能导；周次、节次、调课，全自动。",
    3: "群友在上什么课？翘课、请假、下一节，一张卡片，全部看穿。",
    4: "课程时长榜，一页二十八人；你的名次，永远单独高亮。",
    5: "一条指令看全天课程。正在上的课蓝框标记；下一节，黄框提醒。",
    6: "明日早八、晚间满课榜，每晚准时推送；四六级倒计时，也帮你数着。",
    7: "网页控制台：课表请假调课群绑定，后台直接改；还能按群收纳。",
    8: "网页伴侣把手册搬上网页；群里发链接，一张图都不发。",
    9: "部署提醒：放行端口，警惕裸 IP，推荐域名反代。",
    10: "觉得有用的话，去 GitHub 点个 Star。数据，只在你自己的服务器上。",
}


def tempo_fit(path: Path, limit: float = 6.5, cap: float = 1.4) -> float:
    """超长段用 atempo 无损提速（音调不变）；返回最终时长。"""
    import imageio_ffmpeg

    duration = probe_duration(path)
    if duration <= limit:
        return duration
    factor = min(duration / limit, cap)
    tmp = path.with_suffix(".tmp.wav")
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
         "-i", str(path), "-filter:a", f"atempo={factor:.3f}", str(tmp)],
        check=True,
    )
    tmp.replace(path)
    return probe_duration(path)


def probe_duration(path: Path) -> float:
    import imageio_ffmpeg

    out = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-i", str(path)],
        capture_output=True, text=True,
    ).stderr
    match = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out)
    if not match:
        return 0.0
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def _edge_tts(text: str, out: Path, voice: str, rate: str) -> None:
    import edge_tts

    async def run() -> None:
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        await communicate.save(str(out))

    asyncio.run(run())


def _mimo_tts(text: str, out: Path, conf: dict) -> None:
    """MiMo v2.5 TTS：走 chat/completions，messages 必须含 assistant 角色。

    返回 JSON 里 choices[0].message.audio.data 是 base64 的 WAV（24kHz 单声道）。
    可用音色：mimo_default / 冰糖 / 茉莉 / 苏打 / 白桦 / Mia / Chloe / Milo / Dean。
    """
    import base64

    payload = {
        "model": conf.get("model", "mimo-v2.5-tts"),
        "modalities": ["text", "audio"],
        "audio": {"voice": conf.get("voice", "mimo_default"), "format": "wav"},
        "messages": [
            {"role": "user", "content": conf.get("instruction", "请朗读下面这句话，语气自然有起伏。")},
            {"role": "assistant", "content": text},
        ],
    }
    req = urllib.request.Request(
        conf.get("url", "https://api.xiaomimimo.com/v1/chat/completions"),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {conf.get('key', '')}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    b64 = data["choices"][0]["message"]["audio"]["data"]
    out.write_bytes(base64.b64decode(b64))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("edge", "mimo"), default="edge")
    parser.add_argument("--voice", default="zh-CN-XiaoxiaoNeural")
    parser.add_argument("--rate", default="+8%")
    parser.add_argument("--scenes", type=int, default=10)
    args = parser.parse_args()

    if args.backend == "mimo":
        if not TTS_CONF.exists():
            print(f"缺少 {TTS_CONF}：按模板填入 url / model / voice（密钥可只放环境变量 MIMO_API_KEY）")
            sys.exit(1)
        conf = json.loads(TTS_CONF.read_text(encoding="utf-8"))
        # 密钥优先走环境变量，不落盘、不进 git
        env_key = __import__("os").environ.get("MIMO_API_KEY", "").strip()
        if env_key:
            conf["key"] = env_key
        if not conf.get("key"):
            print("缺少密钥：请在 docs/promo/tts.json 填 key，或设置环境变量 MIMO_API_KEY")
            sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ok = True
    for scene in range(1, args.scenes + 1):
        text = NARRATION.get(scene, "").strip()
        if not text:
            print(f"scene{scene:02d}: 无文案，跳过")
            continue
        out = OUT_DIR / f"scene{scene:02d}.wav"
        try:
            if args.backend == "edge":
                _edge_tts(text, out, args.voice, args.rate)
            else:
                _mimo_tts(text, out, conf)
            seconds = tempo_fit(out)
            # 幕长 7s、配音起点 +0.35s，允许到 6.6s 仍有余量
            flag = "OK " if seconds <= 6.6 else "OVER"
            print(f"scene{scene:02d}: {seconds:.1f}s [{flag}] {text}")
            if seconds > 6.6:
                ok = False
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"scene{scene:02d}: 失败 {exc}")
    print("done ->", OUT_DIR)
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
