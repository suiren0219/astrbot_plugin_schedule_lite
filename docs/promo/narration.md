# 配音文案（10 幕 · 每幕 ≤35 字 · 目标 ≤6.5s/段）

- 后端一：**edge-tts**（`python make_audio.py`，`zh-CN-XiaoxiaoNeural` rate +8%）
- 后端二 **MiMo v2.5 TTS（当前成片使用）**：`python make_audio.py --backend mimo`
  - 协议：`/v1/chat/completions`，`messages` 必须含 user + assistant 两个角色，
    `modalities: ["text","audio"]`、`audio.voice` 指定音色；返回
    `choices[0].message.audio.data` 是 base64 WAV（24kHz 单声道），解码落盘
  - 可用音色：`mimo_default / 冰糖 / 茉莉 / 苏打 / 白桦 / Mia / Chloe / Milo / Dean`
    （**没有「龙井」**，当前用「茉莉」，语气指令写在 tts.json 的 `instruction`）
  - 配置：`docs/promo/tts.json` 模板（**gitignored**），密钥只放文件里或环境变量
    `MIMO_API_KEY`，**永不进仓库**；`--backend mimo` 会自动 atempo 适配到 ≤6.6s
- 节奏：短句 + 顿号断点；每幕配音起点为幕开始后 0.35s；超长段 atempo 无损提速
- 配音增益：`make_video.py --voice-volume 2.0`（默认，实测峰值 -0.5dB 无削波）
- BGM：**由后期添加**；管线仍留 `--bgm <文件>`（音量默认配音的 30%、循环、首尾淡化）

| 幕 | 文案 |
| --- | --- |
| 1 | 可以在群聊里，看到群友上课？课表 Lite——让你的 QQ 机器人，提醒上课。 |
| 2 | Nexio、拾光、WakeUp、iCalendar 都能导；周次、节次、调课，全自动。 |
| 3 | 群友在上什么课？翘课、请假、下一节，一张卡片，全部看穿。 |
| 4 | 课程时长榜，一页二十八人；你的名次，永远单独高亮。 |
| 5 | 一条指令看全天课程。正在上的课蓝框标记；下一节，黄框提醒。 |
| 6 | 明日早八、晚间满课榜，每晚准时推送；四六级倒计时，也帮你数着。 |
| 7 | 网页控制台：课表请假调课群绑定，后台直接改；还能按群收纳。 |
| 8 | 网页伴侣把手册搬上网页；群里发链接，一张图都不发。 |
| 9 | 部署提醒：放行端口，警惕裸 IP，推荐域名反代。 |
| 10 | 觉得有用的话，去 GitHub 点个 Star。数据，只在你自己的服务器上。 |
