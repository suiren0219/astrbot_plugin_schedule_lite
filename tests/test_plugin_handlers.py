"""用最小 AstrBot 桩件跑一遍命令处理，防止只在真实环境才暴露的错误。"""

import asyncio
import importlib
import json
import sys
import types
from pathlib import Path

import pytest

from .test_importers import SHIGUANG_SAMPLE


def _install_astrbot_stub() -> None:
    if "astrbot" in sys.modules:
        return

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")

    class AstrBotConfig(dict):
        pass

    class _Logger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    api.AstrBotConfig = AstrBotConfig
    api.logger = _Logger()

    event_mod = types.ModuleType("astrbot.api.event")

    class MessageChain:
        def __init__(self, chain=None):
            self.chain = list(chain or [])

        def message(self, text):
            self.text = text
            self.chain.append(text)
            return self

    class _Filter:
        @staticmethod
        def command(_name):
            return lambda fn: fn

        @staticmethod
        def event_message_type(_type):
            return lambda fn: fn

        class EventMessageType:
            ALL = "all"

    event_mod.MessageChain = MessageChain
    event_mod.filter = _Filter()

    star_mod = types.ModuleType("astrbot.api.star")

    class Context:
        async def send_message(self, origin, chain):
            self.sent = (origin, chain)

    class Star:
        def __init__(self, context=None):
            self.context = context

    class StarTools:
        _dir = Path(".")

        @classmethod
        def get_data_dir(cls):
            return cls._dir

    def register(*_args, **_kwargs):
        return lambda cls: cls

    star_mod.Context = Context
    star_mod.Star = Star
    star_mod.StarTools = StarTools
    star_mod.register = register

    components = types.ModuleType("astrbot.api.message_components")

    class Image:
        def __init__(self, file=None, **kwargs):
            self.file = file

        @staticmethod
        def fromFileSystem(path, **kwargs):
            return Image(file=str(path))

    class Node:
        def __init__(self, content, name="", uin="", **kwargs):
            self.content = content
            self.name = name
            self.uin = uin

    class Nodes:
        def __init__(self, nodes, **kwargs):
            self.nodes = nodes

    class Plain:
        def __init__(self, message_str="", **kwargs):
            self.message_str = message_str
            self.text = message_str

    class At:
        def __init__(self, qq="", **kwargs):
            self.qq = qq

    class File:
        def __init__(self, name="", file="", url="", **kwargs):
            self.name = name
            self.file = file
            self.url = url

    class Reply:
        def __init__(self, id="", sender_id="", message_str="", chain=None, **kwargs):
            self.id = id
            self.sender_id = sender_id
            self.message_str = message_str
            self.chain = list(chain or [])

    components.Image = Image
    components.Node = Node
    components.Nodes = Nodes
    components.Plain = Plain
    components.At = At
    components.File = File
    components.Reply = Reply

    web_mod = types.ModuleType("astrbot.api.web")

    class _WebRequest:
        method = "GET"
        args = {}
        body = {}

        async def get_json(self, silent=False):
            return self.body

    web_mod.request = _WebRequest()
    web_mod.json_response = lambda data, **_kwargs: data
    web_mod.error_response = lambda message, **_kwargs: {"error": message}

    api.event = event_mod
    api.star = star_mod
    api.web = web_mod
    astrbot.api = api
    sys.modules.update(
        {
            "astrbot": astrbot,
            "astrbot.api": api,
            "astrbot.api.event": event_mod,
            "astrbot.api.star": star_mod,
            "astrbot.api.message_components": components,
            "astrbot.api.web": web_mod,
        }
    )


_install_astrbot_stub()


class _Sender:
    def __init__(self, user_id: str, nickname: str):
        self.user_id = user_id
        self.nickname = nickname


class _Msg:
    def __init__(self, text: str, sender: _Sender, group_id: str):
        self.message_str = text
        self.sender = sender
        self.group_id = group_id
        self.message = []


class _Event:
    def __init__(self, text: str = "", *, uid: str = "10001", nick: str = "张三", group: str = ""):
        self.message_str = text
        self.message_obj = _Msg(text, _Sender(uid, nick), group)
        self.unified_msg_origin = f"test:friend:{uid}" if not group else f"test:group:{group}"
        self._group = group
        self._uid = uid
        self.stopped = False
        self.sent = []

    def get_platform_id(self):
        return "test"

    def get_platform_name(self):
        return "test"

    def get_sender_id(self):
        return self._uid

    def get_sender_name(self):
        return self.message_obj.sender.nickname

    def get_session_id(self):
        return self._uid

    def get_group_id(self):
        return self._group

    def get_self_id(self):
        return "9999"

    def get_messages(self):
        return self.message_obj.message

    def get_extra(self, key, default=None):
        return default

    def stop_event(self):
        self.stopped = True

    async def send(self, chain):
        self.sent.append(chain)

    def plain_result(self, text):
        return text


async def _collect(agen):
    return [item async for item in agen]


@pytest.fixture()
def plugin(tmp_path):
    import main as plugin_main

    plugin_main.StarTools._dir = tmp_path
    return plugin_main.ScheduleLitePlugin(None, {"everyone_image": False, "help_image": False})


@pytest.fixture()
def image_plugin(tmp_path):
    import main as plugin_main

    plugin_main.StarTools._dir = tmp_path
    return plugin_main.ScheduleLitePlugin(None, {})


def test_import_and_query(plugin):
    event = _Event("/课表 导入")
    insert = asyncio.run(_collect(plugin._h_import(event, json.dumps(SHIGUANG_SAMPLE, ensure_ascii=False))))
    assert "已导入" in insert[0]
    assert "拾光课程表" in insert[0]

    query = asyncio.run(_collect(plugin._h_query(event, "2026/9/8")))
    assert "第 1 周" in query[0]
    assert "大学物理" in query[0]


def test_group_everyone_and_rank(plugin):
    event = _Event("/课表 导入", group="g1")
    asyncio.run(_collect(plugin._h_import(event, json.dumps(SHIGUANG_SAMPLE, ensure_ascii=False))))

    everyone = asyncio.run(_collect(plugin._h_everyone(event)))
    assert "群友在上什么课" in everyone[0]
    assert "张三" in everyone[0]

    rank = asyncio.run(_collect(plugin._h_rank(event, "学期 全部")))
    assert "本学期课程时长榜" in rank[0]
    assert "张三" in rank[0]


def test_notify_and_note(plugin):
    event = _Event("/课表 导入")
    asyncio.run(_collect(plugin._h_import(event, json.dumps(SHIGUANG_SAMPLE, ensure_ascii=False))))

    notify = asyncio.run(_collect(plugin._h_notify(event, "15")))
    assert "15 分钟" in notify[0]

    note = asyncio.run(_collect(plugin._h_note(event, "大学物 带课本")))
    assert "已设置" in note[0]

    listed = asyncio.run(_collect(plugin._h_note(event, "")))
    assert "带课本" in listed[0]

    group_event = _Event("/课表 备注", group="g1")
    listed_group = asyncio.run(_collect(plugin._h_note(group_event, "")))
    assert "私聊" in listed_group[0]


def test_delete(plugin):
    event = _Event()
    deleted = asyncio.run(_collect(plugin._h_delete(event)))
    assert "还没有" in deleted[0]


def test_notification_once(plugin):
    from datetime import datetime

    from schedule_lite_core.normalize import get_tz

    event = _Event()
    asyncio.run(_collect(plugin._h_import(event, json.dumps(SHIGUANG_SAMPLE, ensure_ascii=False))))
    asyncio.run(_collect(plugin._h_notify(event, "15")))

    sent = []

    async def send(origin, chain):
        sent.append(chain.text)

    plugin.context = types.SimpleNamespace(send_message=send)
    # 2026-09-08 周二 07:50，08:00 的大学物理还有 10 分钟
    plugin._now = lambda: datetime(2026, 9, 8, 7, 50, tzinfo=get_tz("Asia/Shanghai"))
    asyncio.run(plugin._check_notifications())
    assert len(sent) == 1
    assert "大学物理" in sent[0]
    assert "地点：" not in sent[0]  # 默认不保存、不显示上课地点

    asyncio.run(plugin._check_notifications())
    assert len(sent) == 1  # 同一节课不重复提醒


def test_abandon_skips_notification(plugin):
    from datetime import datetime

    from schedule_lite_core.normalize import get_tz

    event = _Event()
    asyncio.run(_collect(plugin._h_import(event, json.dumps(SHIGUANG_SAMPLE, ensure_ascii=False))))
    asyncio.run(_collect(plugin._h_notify(event, "15")))
    plugin.store.get(plugin._user_key(event)).abandon_date = "2026-09-08"
    plugin.store.get(plugin._user_key(event)).abandon_course = "大学物理"
    plugin.store.get(plugin._user_key(event)).abandon_start = "08:00"

    sent = []

    async def send(origin, chain):
        sent.append(chain.text)

    plugin.context = types.SimpleNamespace(send_message=send)
    plugin._now = lambda: datetime(2026, 9, 8, 7, 50, tzinfo=get_tz("Asia/Shanghai"))
    asyncio.run(plugin._check_notifications())
    assert sent == []


def test_abandon_targets_next_course(plugin):
    from datetime import datetime

    from schedule_lite_core.normalize import get_tz

    event = _Event()
    asyncio.run(_collect(plugin._h_import(event, json.dumps(SHIGUANG_SAMPLE, ensure_ascii=False))))
    plugin._now = lambda: datetime(2026, 9, 8, 7, 50, tzinfo=get_tz("Asia/Shanghai"))
    out = asyncio.run(_collect(plugin._h_abandon(event)))
    assert "下一节" in out[0]
    tt = plugin.store.get(plugin._user_key(event))
    assert tt.abandon_date == "2026-09-08"
    assert tt.abandon_course == "大学物理"
    assert tt.abandon_start == "08:00"

    again = asyncio.run(_collect(plugin._h_abandon(event)))
    assert "已取消" in again[0]
    assert tt.abandon_course == ""


def test_cross_group_privacy(plugin):
    other = _Event(uid="10002", nick="李四")
    asyncio.run(_collect(plugin._h_import(other, json.dumps(SHIGUANG_SAMPLE, ensure_ascii=False))))

    group_event = _Event(group="g1")
    result = asyncio.run(_collect(plugin._h_query(group_event, "10002")))
    assert "隐私" in result[0]


def test_export_roundtrip(plugin):
    from schedule_lite_core.importers import detect
    from schedule_lite_core.importers.generic import dumps_export

    event = _Event()
    asyncio.run(_collect(plugin._h_import(event, json.dumps(SHIGUANG_SAMPLE, ensure_ascii=False))))
    tt = plugin.store.get("test:10001")

    exported = dumps_export(tt)
    again = detect.parse_text(exported)
    assert again.term_start.isoformat() == "2026-09-07"
    assert again.total_weeks == 18
    assert len(again.courses) == len(tt.courses)
    assert {(c.name, c.day, tuple(c.weeks)) for c in again.courses} == {
        (c.name, c.day, tuple(c.weeks)) for c in tt.courses
    }


def test_import_multiple_text_formats(plugin):
    from .test_importers import ICS_SAMPLE, _wakeup_payload

    cases = [
        (ICS_SAMPLE, "iCalendar"),
        (_wakeup_payload(), "WakeUp 课程表"),
        ("课程,星期,开始,结束,周次\n物理,周一,08:00,09:40,1-8\n", "CSV 表格"),
    ]
    for text, source in cases:
        event = _Event()
        out = asyncio.run(_collect(plugin._h_import(event, text)))
        assert "已导入" in out[0], out[0]
        assert source in out[0]
        query = asyncio.run(_collect(plugin._h_query(event, "2026/9/7")))
        assert "第 1 周" in query[0]


def test_glued_command(plugin):
    asyncio.run(_collect(plugin._h_import(_Event(), json.dumps(SHIGUANG_SAMPLE, ensure_ascii=False))))
    glued = _Event("/课表明天")
    out = asyncio.run(_collect(plugin.cmd_glued(glued)))
    assert out and ("没有课" in out[0] or "第" in out[0])

    chat = _Event("/课表真难")
    assert asyncio.run(_collect(plugin.cmd_glued(chat))) == []


def test_ssrf_guard(plugin):
    from schedule_lite_core.importers.base import ImportFailure

    with pytest.raises(ImportFailure):
        asyncio.run(plugin._check_url("http://127.0.0.1:8080/x"))
    with pytest.raises(ImportFailure):
        asyncio.run(plugin._check_url("file:///etc/passwd"))


def test_term_fix(plugin):
    event = _Event()
    asyncio.run(_collect(plugin._h_import(event, json.dumps(SHIGUANG_SAMPLE, ensure_ascii=False))))
    out = asyncio.run(_collect(plugin._h_term(event, "2026-09-07")))
    assert "2026-09-07" in out[0]
    view = asyncio.run(_collect(plugin._h_term(event, "")))
    assert "2026/09/07" in view[0]


def test_help_index_lists_categories(plugin):
    import main as plugin_main

    event = _Event()
    out = asyncio.run(_collect(plugin._h_help(event, "")))
    assert out == [plugin_main.HELP_INDEX_TEXT]
    for title in ("导入", "查询", "请假", "状态", "设置", "面板", "常见", "全部"):
        assert title in out[0]
    assert plugin_main.ISSUE_URL in out[0]
    assert "/issues" in out[0]


def test_help_category_markdown_is_extracted(plugin):
    assert plugin._help_category("入门") == "导入"
    assert plugin._help_category("调休") == "请假"
    assert plugin._help_category("webui") == "面板"
    markdown = plugin._help_markdown("请假")
    assert "请假 / 销假" in markdown
    assert "调课" in markdown
    assert "WebUI 控制台" not in markdown


def test_help_sends_category_images(image_plugin, tmp_path):
    from schedule_lite_core import render

    if not render.available():
        pytest.skip("本机没有可用的中文字体")
    event = _Event()
    out = asyncio.run(_collect(image_plugin._h_help(event, "全部")))
    assert out == []  # 走图片路径，不再发文字
    assert event.sent, "应该发送合并转发消息"
    chain = event.sent[0]
    nodes = chain.chain[0]
    assert len(nodes.nodes) >= 2
    assert (tmp_path / "help" / "help.md").is_file()
    pages = list((tmp_path / "help").glob("help-*.png"))
    assert len(pages) >= 2


def test_help_sends_single_category_image(image_plugin, tmp_path):
    from schedule_lite_core import render

    if not render.available():
        pytest.skip("本机没有可用的中文字体")
    event = _Event()
    out = asyncio.run(_collect(image_plugin._h_help(event, "导入")))
    assert out == []
    assert event.sent
    assert (tmp_path / "help" / "help-导入.md").is_file()


def test_query_without_timetable_shows_onboarding(plugin):
    event = _Event()
    out = asyncio.run(_collect(plugin._h_query(event, "")))
    assert out
    assert "绑定课表" in out[0]
    assert "/课表 导入" in out[0]


def test_avatar_cache_and_fallback(plugin, tmp_path):
    from schedule_lite_core.model import Timetable

    avatar_dir = tmp_path / "cache" / "avatars"
    avatar_dir.mkdir(parents=True)
    cached = avatar_dir / "123456.png"
    cached.write_bytes(b"x" * 1024)

    tt = Timetable(owner_id="123456", owner_name="张三")
    paths = asyncio.run(plugin._avatar_paths([tt]))
    assert paths["123456"] == str(cached)
    entries = [{"owner_id": "123456", "name": "张三"}]
    asyncio.run(plugin._attach_avatars(entries, [tt]))
    assert entries[0]["avatar"] == str(cached)

    assert plugin._qq_number("123456") == "123456"
    assert plugin._avatar_url("123456").endswith("nk=123456&s=100")
    assert plugin._avatar_url("not-a-qq") == ""


def test_everyone_sends_image(image_plugin, tmp_path):
    from schedule_lite_core import render

    if not render.available():
        pytest.skip("本机没有可用的中文字体")
    event = _Event(group="g1")
    asyncio.run(_collect(image_plugin._h_import(event, json.dumps(SHIGUANG_SAMPLE, ensure_ascii=False))))
    out = asyncio.run(_collect(image_plugin._h_everyone(event)))
    assert out == []
    assert event.sent
    first = event.sent[0].chain[0]
    # 单张图直接发，不再套合并转发
    if hasattr(first, "nodes"):
        assert first.nodes
    else:
        assert Path(str(getattr(first, "file", ""))).exists()
    day_dirs = list((tmp_path / "cache" / "everyone").glob("*/everyone-*.png"))
    assert day_dirs


def test_scrub_privacy_clears_historical_data(plugin):
    from schedule_lite_core.model import Course, Timetable

    tt = Timetable(
        owner_id="old-user",
        owner_name="旧用户",
        term_start="2026-09-07",
        total_weeks=20,
        timezone="Asia/Shanghai",
    )
    tt.courses = [
        Course(
            name="高等数学",
            day=1,
            weeks=[1],
            start="08:00",
            end="09:40",
            location="A101",
            teacher="张三",
        )
    ]
    plugin.store.put("old-user", tt)

    assert plugin._scrub_privacy() is True
    course = plugin.store.get("old-user").courses[0]
    assert course.teacher == ""
    assert course.location == ""
    assert plugin._scrub_privacy() is False


def test_web_api_crud(plugin):
    import astrbot.api.web as web

    body = {
        "platform": "aiocqhttp",
        "owner_id": "20001",
        "owner_name": "王五",
        "name": "面板课表",
        "term_start": "2026-09-07",
        "total_weeks": 18,
        "courses": [
            {
                "name": "离散数学",
                "day": 2,
                "weeks": "1-16",
                "start": "08:00",
                "end": "09:40",
                "location": "A101",
                "teacher": "张三",
            }
        ],
    }
    web.request.body = body
    created = asyncio.run(plugin._api_timetable_save())
    assert created["status"] == "ok"
    key = "aiocqhttp:20001"
    tt = plugin.store.get(key)
    assert tt is not None
    assert tt.owner_name == "王五"
    assert tt.courses[0].name == "离散数学"
    # 默认隐私：面板写入也会丢弃教师和地点
    assert tt.courses[0].teacher == ""
    assert tt.courses[0].location == ""
    assert tt.term_start == "2026-09-07"

    web.request.args = {"key": key}
    detail = asyncio.run(plugin._api_timetable())
    assert detail["status"] == "ok"
    assert detail["data"]["courses"][0]["name"] == "离散数学"

    web.request.args = {"q": "王五"}
    listing = asyncio.run(plugin._api_timetables())
    assert listing["data"]["total"] == 1

    web.request.body = {"key": key}
    deleted = asyncio.run(plugin._api_timetable_delete())
    assert deleted["status"] == "ok"
    assert plugin.store.get(key) is None


def test_web_api_settings(plugin):
    import astrbot.api.web as web

    web.request.body = {
        "store_teacher": True,
        "store_location": False,
        "everyone_hide_idle_over": 6,
        "showcase_enabled": True,
        "showcase_time": "23:10",
        "recap_mode": "month",
    }
    result = asyncio.run(plugin._api_settings_save())
    assert result["status"] == "ok"
    assert plugin.config["store_teacher"] is True
    assert plugin.config["showcase_time"] == "23:10"
    assert plugin.config["recap_mode"] == "month"


def _import_sample(plugin, event=None):
    event = event or _Event("/课表 导入")
    asyncio.run(_collect(plugin._h_import(event, json.dumps(SHIGUANG_SAMPLE, ensure_ascii=False))))
    return event


def test_leave_and_cancel(plugin):
    event = _import_sample(plugin)
    out = asyncio.run(_collect(plugin._h_leave(event, "9/8-9/9 病假")))
    assert "已登记请假" in out[0]
    assert "09/08 - 09/09" in out[0]
    tt = plugin.store.get("test:10001")
    assert tt.leave_on(__import__("datetime").date(2026, 9, 8))["reason"] == "病假"

    query = asyncio.run(_collect(plugin._h_query(event, "2026/9/8")))
    assert "已请假：病假" in query[0]
    assert "（请假）" in query[0]

    cancel = asyncio.run(_collect(plugin._h_leave_cancel(event, "9/8")))
    assert "已取消" in cancel[0]
    assert tt.leave_on(__import__("datetime").date(2026, 9, 8)) is None


def test_adjust_and_restore(plugin):
    event = _import_sample(plugin)
    out = asyncio.run(_collect(plugin._h_adjust(event, "9/8 9/9")))
    assert "已把" in out[0]
    assert "大学物理" in out[0]

    moved_in = asyncio.run(_collect(plugin._h_query(event, "2026/9/9")))
    assert "大学物理" in moved_in[0]
    assert "（调课）" in moved_in[0]

    moved_out = asyncio.run(_collect(plugin._h_query(event, "2026/9/8")))
    assert "已调至 2026-09-09" in moved_out[0]

    cleared = asyncio.run(_collect(plugin._h_adjust(event, "清空")))
    assert "已清空 1 条调课记录" in cleared[0]
    assert "大学物理" not in asyncio.run(_collect(plugin._h_query(event, "2026/9/9")))[0]


def test_makeup_day_command(plugin):
    event = _import_sample(plugin)
    out = asyncio.run(_collect(plugin._h_makeup(event, "9/27 周一")))
    assert "按周一课表" in out[0]
    assert plugin.store.get("test:10001").makeup_days["2026-09-27"] == 1

    listed = asyncio.run(_collect(plugin._h_makeup(event, "")))
    assert "你的补班设置" in listed[0]
    assert "2026-09-27 → 按周一课表" in listed[0]

    cleared = asyncio.run(_collect(plugin._h_makeup(event, "9/27 清")))
    assert "已取消" in cleared[0]
    assert plugin.store.get("test:10001").makeup_days == {}


def test_web_routes_use_plugin_page_prefix():
    import main as plugin_main

    class _Context:
        def __init__(self):
            self.routes = []

        def register_web_api(self, route, handler, methods, desc):
            self.routes.append((route, methods, desc))

    ctx = _Context()
    plugin = plugin_main.ScheduleLitePlugin(ctx, {})
    plugin._register_web_apis()
    assert ctx.routes
    assert all(route.startswith("/astrbot_plugin_schedule_lite/page/") for route, _m, _d in ctx.routes)


def test_web_api_overview_and_live(plugin):
    _import_sample(plugin, _Event("/课表 导入", group="g1"))
    overview = asyncio.run(plugin._api_overview())
    assert overview["status"] == "ok"
    assert overview["data"]["users"] == 1
    assert overview["data"]["groups"] == 1

    live = asyncio.run(plugin._api_live())
    assert live["status"] == "ok"
    assert live["data"]["total"] == 1
    assert live["data"]["items"][0]["name"] == "张三"


def _scheduled_plugin(tmp_path, config):
    import main as plugin_main
    from schedule_lite_core.model import Course, Timetable

    class _Ctx:
        def __init__(self):
            self.sent = []

        async def send_message(self, origin, chain):
            self.sent.append((origin, chain))

    plugin_main.StarTools._dir = tmp_path
    ctx = _Ctx()
    plugin = plugin_main.ScheduleLitePlugin(ctx, config)
    day = plugin._now().isoweekday()
    monday = plugin._now().date() - __import__("datetime").timedelta(days=day - 1)
    for uid, name, end in (("1", "甲", "14:00"), ("2", "乙", "10:00")):
        tt = Timetable(
            owner_id=uid,
            owner_name=name,
            term_start=monday.isoformat(),
            total_weeks=20,
            courses=[Course(name="课程" + uid, day=day, weeks=[1], start="08:00", end=end)],
            groups=["g1"],
            group_origins={"g1": "test:group:g1"},
        )
        plugin.store.put(f"test:{uid}", tt)
    return plugin, ctx


def _assert_broadcast_content(ctx, keyword: str) -> None:
    """广播可能渲染成图片（本机有中文字体）或退回文本，两种都要接受。"""
    assert len(ctx.sent) >= 1
    first = ctx.sent[0][1].chain[0]
    if isinstance(first, str):
        assert keyword in first
    else:
        assert Path(str(getattr(first, "file", ""))).exists()


def test_showcase_broadcast_once(tmp_path):
    plugin, ctx = _scheduled_plugin(
        tmp_path,
        {
            "showcase_enabled": True,
            "showcase_time": "22:30",
            "showcase_min_minutes": 60,
            "showcase_min_users": 1,
            "recap_enabled": False,
        },
    )
    now = plugin._now().replace(hour=22, minute=31, second=0, microsecond=0)
    asyncio.run(plugin._check_scheduled_broadcasts(now))
    assert len(ctx.sent) == 1
    assert ctx.sent[0][0] == "test:group:g1"
    _assert_broadcast_content(ctx, "今日满课榜")

    asyncio.run(plugin._check_scheduled_broadcasts(now))
    assert len(ctx.sent) == 1


def test_recap_broadcast_on_configured_weekday(tmp_path):
    plugin, ctx = _scheduled_plugin(
        tmp_path,
        {
            "showcase_enabled": False,
            "recap_enabled": True,
            "recap_time": "21:00",
            "recap_mode": "week",
            "recap_top": 5,
        },
    )
    # 发送星期取插件时区（Asia/Shanghai）的今天，不能用测试机本地日期：
    # CI 机器是 UTC，UTC 下午的上海日期已经翻到次日，会对不上星期。
    plugin.config["recap_weekday"] = plugin._now().date().isoweekday()
    now = plugin._now().replace(hour=21, minute=5, second=0, microsecond=0)
    asyncio.run(plugin._check_scheduled_broadcasts(now))
    assert len(ctx.sent) == 1
    _assert_broadcast_content(ctx, "累计上课榜")


def test_adjust_swap_does_not_duplicate(plugin):
    event = _import_sample(plugin)
    out = asyncio.run(_collect(plugin._h_adjust(event, "9/8 9/10 互换")))
    assert "同时把" in out[0]

    tuesday = asyncio.run(_collect(plugin._h_query(event, "2026/9/8")))[0]
    thursday = asyncio.run(_collect(plugin._h_query(event, "2026/9/10")))[0]
    assert "15:00-16:30 体育" in tuesday
    assert "08:00-09:40 大学物理" not in tuesday
    assert "08:00-09:40 大学物理" in thursday
    assert "15:00-16:30 体育" not in thursday
    assert tuesday.count("（调课）") == 1
    assert thursday.count("（调课）") == 1


def test_status_cache_reused_and_invalidated(plugin):
    event = _import_sample(plugin)
    tt = plugin.store.get("test:10001")
    now = plugin._now()

    first = plugin._cached_entries("g:g1", [tt], now)
    second = plugin._cached_entries("g:g2", [tt], now)
    assert first == second
    assert len(plugin._status_cache) == 1
    assert len(plugin._day_cache) == 1
    assert len(plugin._entries_cache) == 1

    asyncio.run(plugin._save())
    assert plugin._status_cache == {}
    assert plugin._day_cache == {}
    assert plugin._entries_cache == {}


def test_adjust_undo_by_date(plugin):
    event = _import_sample(plugin)
    asyncio.run(_collect(plugin._h_adjust(event, "9/8 9/9")))
    assert plugin.store.get("test:10001").adjustments

    out = asyncio.run(_collect(plugin._h_adjust(event, "撤销 9/9")))
    assert "已撤销" in out[0]
    assert plugin.store.get("test:10001").adjustments == []


def test_makeup_map_config(plugin):
    plugin.config["makeup_weekday_map"] = "2026-10-10 周四\n2026-10-11 周五"
    assert plugin._makeup_map() == {"2026-10-10": 4, "2026-10-11": 5}


def test_rank_month_label(plugin):
    event = _Event("/课表 导入", group="g1")
    _import_sample(plugin, event)
    out = asyncio.run(_collect(plugin._h_rank(event, "本月 全部")))
    assert "本月" in out[0]


def test_broadcast_claim_released_on_failure(tmp_path):
    import main as plugin_main
    from schedule_lite_core.model import Course, Timetable

    class _FailingContext:
        def __init__(self):
            self.calls = 0

        async def send_message(self, origin, chain):
            self.calls += 1
            raise RuntimeError("send failed")

    plugin_main.StarTools._dir = tmp_path
    ctx = _FailingContext()
    plugin = plugin_main.ScheduleLitePlugin(
        ctx,
        {
            "showcase_enabled": True,
            "showcase_time": "22:30",
            "showcase_min_minutes": 60,
            "showcase_min_users": 1,
            "recap_enabled": False,
        },
    )
    today = plugin._now().date()
    tt = Timetable(
        owner_id="1",
        owner_name="甲",
        term_start=(today - __import__("datetime").timedelta(days=today.isoweekday() - 1)).isoformat(),
        total_weeks=20,
        courses=[Course(name="课", day=today.isoweekday(), weeks=[1], start="08:00", end="14:00")],
        groups=["g1"],
        group_origins={"g1": "test:group:g1"},
    )
    plugin.store.put("test:1", tt)
    now = plugin._now().replace(hour=22, minute=31, second=0, microsecond=0)

    asyncio.run(plugin._check_scheduled_broadcasts(now))
    assert ctx.calls == 1
    assert plugin._broadcast_state.get("showcase", {}) == {}

    asyncio.run(plugin._check_scheduled_broadcasts(now))
    assert ctx.calls == 2


def test_web_api_save_records(plugin):
    import astrbot.api.web as web

    key = "aiocqhttp:30001"
    body = {
        "platform": "aiocqhttp",
        "owner_id": "30001",
        "owner_name": "记录同学",
        "name": "记录课表",
        "term_start": "2026-09-07",
        "total_weeks": 20,
        "courses": [
            {"name": "离散数学", "day": 2, "weeks": "1-16", "start": "08:00", "end": "09:40"}
        ],
        "leaves": [{"start": "2026-09-08", "end": "2026-09-09", "reason": "事假"}],
        "adjustments": [],
        "new_adjustments": [
            {"from_date": "2026-09-08", "to_date": "2026-09-09", "course": "", "swap": False}
        ],
        "makeup_days": {"2026-10-10": 4},
        "groups": ["g1"],
        "group_origins": {"g1": "test:group:g1"},
    }
    web.request.body = body
    result = asyncio.run(plugin._api_timetable_save())
    assert result["status"] == "ok"
    tt = plugin.store.get(key)
    assert tt.leaves == [{"start": "2026-09-08", "end": "2026-09-09", "reason": "事假"}]
    assert len(tt.adjustments) == 1
    assert tt.adjustments[0]["from_date"] == "2026-09-08"
    assert tt.makeup_days == {"2026-10-10": 4}
    assert tt.groups == ["g1"]
    assert tt.group_origins == {"g1": "test:group:g1"}

    # 校验失败不能污染已保存的数据
    body["new_adjustments"] = [
        {"from_date": "2026-09-10", "to_date": "2026-09-11", "course": "", "swap": False}
    ]
    web.request.body = body
    failed = asyncio.run(plugin._api_timetable_save())
    assert isinstance(failed, tuple) and failed[1] == 400
    assert plugin.store.get(key).adjustments == tt.adjustments


def test_import_preserves_groups(plugin):
    event = _Event("/课表 导入", group="g1")
    _import_sample(plugin, event)
    assert plugin.store.get("test:10001").group_origins == {"g1": "test:group:g1"}

    # 私聊重新导入不能丢掉群绑定
    asyncio.run(_collect(plugin._h_import(event, json.dumps(SHIGUANG_SAMPLE, ensure_ascii=False))))
    tt = plugin.store.get("test:10001")
    assert tt.groups == ["g1"]
    assert tt.group_origins == {"g1": "test:group:g1"}


def test_web_api_import(plugin):
    import astrbot.api.web as web

    web.request.body = {
        "platform": "aiocqhttp",
        "owner_id": "40001",
        "owner_name": "导入同学",
        "text": json.dumps(
            {
                "name": "面板导入",
                "term_start": "2026-09-07",
                "total_weeks": 18,
                "courses": [
                    {
                        "name": "高等数学",
                        "day": 1,
                        "start": "08:00",
                        "end": "09:40",
                        "weeks": "1-16",
                        "location": "A101",
                        "teacher": "王老师",
                    }
                ],
            },
            ensure_ascii=False,
        ),
    }
    result = asyncio.run(plugin._api_import())
    assert result["status"] == "ok"
    assert result["data"]["key"] == "aiocqhttp:40001"
    tt = plugin.store.get("aiocqhttp:40001")
    assert tt.owner_name == "导入同学"
    assert tt.courses[0].name == "高等数学"
    assert tt.courses[0].teacher == ""
    assert tt.courses[0].location == ""


def test_web_api_import_requires_fields(plugin):
    import astrbot.api.web as web

    web.request.body = {"text": "内容"}
    missing_owner = asyncio.run(plugin._api_import())
    assert isinstance(missing_owner, tuple) and missing_owner[1] == 400

    web.request.body = {"owner_id": "40001"}
    missing_text = asyncio.run(plugin._api_import())
    assert isinstance(missing_text, tuple) and missing_text[1] == 400


async def _dispatch_all(plugin, text, group="g1"):
    event = _Event(text, group=group)
    outs = [item async for item in plugin._dispatch(event)]
    return event, [out if isinstance(out, str) else str(out) for out in outs]


def test_dispatch_share_code_reaches_importer(plugin):
    # 参数必须原样传给导入器；不能回落到帮助文本
    _event, outs = asyncio.run(_dispatch_all(plugin, "/课表 导入 球场三分线已开门"))
    assert outs == ["❌ 导入失败：当前配置禁止联网导入"]


def test_dispatch_import_json(plugin):
    payload = json.dumps(
        {
            "name": "分发测试",
            "term_start": "2026-09-07",
            "total_weeks": 18,
            "courses": [
                {"name": "高等数学", "day": 2, "start": "08:00", "end": "09:40", "weeks": "1-16"}
            ],
        },
        ensure_ascii=False,
    )
    _event, outs = asyncio.run(_dispatch_all(plugin, "/课表 导入 " + payload))
    assert "已导入课表「分发测试」" in outs[0]
    assert plugin.store.get("test:10001").courses[0].name == "高等数学"


def test_dispatch_leave_adjust_makeup(plugin):
    payload = json.dumps(
        {
            "name": "分发测试",
            "term_start": "2026-09-07",
            "total_weeks": 18,
            "courses": [
                {"name": "高等数学", "day": 2, "start": "08:00", "end": "09:40", "weeks": "1-16"}
            ],
        },
        ensure_ascii=False,
    )
    asyncio.run(_dispatch_all(plugin, "/课表 导入 " + payload))

    _event, outs = asyncio.run(_dispatch_all(plugin, "/课表 请假 9/8 病假"))
    assert "已登记请假" in outs[0]

    _event, outs = asyncio.run(_dispatch_all(plugin, "/课表 调课 9/8 9/9"))
    assert "已把 09/08 的 高等数学 调到 09/09" in outs[0]

    _event, outs = asyncio.run(_dispatch_all(plugin, "/课表 调休 9/27 周一"))
    assert "按周一课表" in outs[0]

    tt = plugin.store.get("test:10001")
    assert len(tt.leaves) == 1
    assert len(tt.adjustments) == 1
    assert tt.makeup_days == {"2026-09-27": 1}


def test_glued_commands_route(plugin):
    payload = json.dumps(
        {
            "name": "粘连测试",
            "term_start": "2026-09-07",
            "total_weeks": 18,
            "courses": [
                {"name": "高等数学", "day": 2, "start": "08:00", "end": "09:40", "weeks": "1-16"}
            ],
        },
        ensure_ascii=False,
    )
    async def glued(text, group="g1"):
        event = _Event(text, group=group)
        outs = [item async for item in plugin.cmd_glued(event)]
        return event, [out if isinstance(out, str) else str(out) for out in outs]

    _event, outs = asyncio.run(glued("课表导入" + payload))
    assert "已导入课表「粘连测试」" in outs[0]

    _event, outs = asyncio.run(glued("课表请假 9/8 病假"))
    assert "已登记请假" in outs[0]

    _event, outs = asyncio.run(glued("课表调课9/8 9/9"))
    assert "已把 09/08 的 高等数学 调到 09/09" in outs[0]

    _event, outs = asyncio.run(glued("课表调课撤销9/9"))
    assert "已撤销 09/09 相关的 1 条调课记录" in outs[0]
    assert plugin.store.get("test:10001").adjustments == []


def test_fallback_sections_shapes(plugin):
    plugin.config["default_section_times"] = "1 08:00-08:45\n2 08:55-09:40"
    sections = plugin._fallback_sections()
    assert [s.index for s in sections] == [1, 2]
    assert sections[0].start == "08:00"

    plugin.config["default_section_times"] = [
        {"__template_key": "section", "index": 3, "start": "10:00", "end": "10:45", "alias": ""}
    ]
    sections = plugin._fallback_sections()
    assert [s.index for s in sections] == [3]
    assert sections[0].end == "10:45"

    plugin.config["default_section_times"] = []
    assert len(plugin._fallback_sections()) >= 12


def test_section_config_legacy_text_still_supported(plugin):
    plugin.config["default_section_times"] = "1 08:00-08:45\n2 08:55-09:40"
    assert plugin._fallback_sections()[0].start == "08:00"


def test_sections_api_returns_defaults(plugin):
    defaults = asyncio.run(plugin._api_sections())
    assert defaults["status"] == "ok"
    items = defaults["data"]["items"]
    assert len(items) >= 12
    assert items[0]["start"]


def test_save_clears_sections_to_defaults(plugin):
    import astrbot.api.web as web

    web.request.body = {
        "platform": "aiocqhttp",
        "owner_id": "30001",
        "term_start": "2026-09-07",
        "sections": [],
        "courses": [],
    }
    result = asyncio.run(plugin._api_timetable_save())
    assert result["status"] == "ok"
    stored = plugin.store.get("aiocqhttp:30001")
    assert stored.sections and len(stored.sections) >= 12


def _generic_import_payload(name="引用测试"):
    return json.dumps(
        {
            "name": name,
            "term_start": "2026-09-07",
            "total_weeks": 18,
            "courses": [
                {"name": "高等数学", "day": 2, "start": "08:00", "end": "09:40", "weeks": "1-16"}
            ],
        },
        ensure_ascii=False,
    )


def test_import_via_quoted_text(plugin):
    from astrbot.api.message_components import Plain, Reply

    event = _Event("/课表 导入", group="g1")
    event.message_obj.message = [Reply(id="1", chain=[Plain(_generic_import_payload())])]
    outs = asyncio.run(_collect(plugin._h_import(event, "")))
    assert "已导入课表「引用测试」" in outs[0]
    assert plugin.store.get("test:10001").courses[0].name == "高等数学"


def test_import_via_quoted_message_str(plugin):
    from astrbot.api.message_components import Reply

    event = _Event("/课表 导入", group="g1")
    event.message_obj.message = [Reply(id="2", message_str=_generic_import_payload("摘要引用"))]
    outs = asyncio.run(_collect(plugin._h_import(event, "")))
    assert "已导入课表「摘要引用」" in outs[0]


def test_import_via_quoted_file(plugin, tmp_path):
    from astrbot.api.message_components import File, Reply

    from .test_importers import ICS_SAMPLE

    ics_path = tmp_path / "课表.ics"
    ics_path.write_text(ICS_SAMPLE, encoding="utf-8")
    event = _Event("/课表 导入", group="g1")
    event.message_obj.message = [
        Reply(id="3", chain=[File(name="课表.ics", file=str(ics_path))])
    ]
    outs = asyncio.run(_collect(plugin._h_import(event, "")))
    assert "已导入课表" in outs[0]
    assert plugin.store.get("test:10001").courses


def test_payload_wins_over_quote(plugin):
    from astrbot.api.message_components import Reply

    event = _Event("/课表 导入", group="g1")
    event.message_obj.message = [Reply(id="4", message_str="这条引用没有课表")]
    outs = asyncio.run(_collect(plugin._h_import(event, _generic_import_payload("显式参数"))))
    assert "已导入课表「显式参数」" in outs[0]


def test_bare_file_auto_import_private(plugin, tmp_path):
    from astrbot.api.message_components import File

    from .test_importers import ICS_SAMPLE

    ics_path = tmp_path / "课表.ics"
    ics_path.write_text(ICS_SAMPLE, encoding="utf-8")
    event = _Event("")
    event.message_obj.message = [File(name="课表.ics", file=str(ics_path))]
    outs = asyncio.run(_collect(plugin.cmd_file_auto_import(event)))
    assert "已导入课表" in outs[0]
    assert plugin.store.get("test:10001").courses


def test_bare_file_auto_import_group_extension_filter(plugin, tmp_path):
    from astrbot.api.message_components import File

    from .test_importers import ICS_SAMPLE

    ics_path = tmp_path / "课表.ics"
    ics_path.write_text(ICS_SAMPLE, encoding="utf-8")
    event = _Event("", group="g1")
    event.message_obj.message = [File(name="课表.ics", file=str(ics_path))]
    outs = asyncio.run(_collect(plugin.cmd_file_auto_import(event)))
    assert "已导入课表" in outs[0]

    random_file = tmp_path / "资料.zip"
    random_file.write_bytes(b"PK")
    event = _Event("", group="g1")
    event.message_obj.message = [File(name="资料.zip", file=str(random_file))]
    outs = asyncio.run(_collect(plugin.cmd_file_auto_import(event)))
    assert outs == []


def test_bare_file_skips_command_messages(plugin):
    from astrbot.api.message_components import File

    event = _Event("/课表 导入", group="g1")
    event.message_obj.message = [File(name="x.ics", file="/nonexistent.ics")]
    outs = asyncio.run(_collect(plugin.cmd_file_auto_import(event)))
    assert outs == []


def test_command_handlers_stop_event(plugin):
    event = _Event("/课表 帮助", group="g1")
    asyncio.run(_collect(plugin.cmd_schedule(event)))
    assert event.stopped is True

    event = _Event("/课表导入", group="g1")
    asyncio.run(_collect(plugin.cmd_schedule_import(event)))
    assert event.stopped is True

    event = _Event("/课表排行", group="g1")
    asyncio.run(_collect(plugin.cmd_schedule_rank(event)))
    assert event.stopped is True


def test_glued_skips_command_claimed_messages(plugin):
    async def glued(event):
        return [item async for item in plugin.cmd_glued(event)]

    # 斜杠/空格形式由命令处理器接管，兜底 handler 不能再跑一遍
    event = _Event("/课表导入 " + _generic_import_payload())
    assert asyncio.run(glued(event)) == []
    assert plugin.store.all() == {}

    event = _Event("课表 导入 " + _generic_import_payload(name="空格形式"))
    assert asyncio.run(glued(event)) == []
    assert plugin.store.all() == {}

    event = _Event("课表导入 " + _generic_import_payload(name="带空格唤醒"))
    assert asyncio.run(glued(event)) == []
    assert plugin.store.all() == {}

    # 无边界粘连写法仍由兜底 handler 处理，且只处理一次
    event = _Event("课表导入" + _generic_import_payload(name="粘连形式"))
    outs = asyncio.run(glued(event))
    assert any("已导入课表「粘连形式」" in str(out) for out in outs)
    assert len(plugin.store.all()) == 1


def test_import_archives_raw_file(plugin):
    event = _Event("/课表 导入", group="g1")
    asyncio.run(_collect(plugin._h_import(event, json.dumps(SHIGUANG_SAMPLE, ensure_ascii=False))))
    tt = plugin.store.get("test:10001")
    assert tt.raw_source
    raw_path = plugin._plugin_data_dir() / tt.raw_source
    assert raw_path.exists()
    assert json.loads(raw_path.read_text(encoding="utf-8")) == SHIGUANG_SAMPLE
    assert raw_path.suffix == ".json"


def test_web_api_import_archives_raw(plugin):
    import astrbot.api.web as web

    payload = _generic_import_payload("存档同学")
    web.request.body = {"platform": "aiocqhttp", "owner_id": "50001", "text": payload}
    result = asyncio.run(plugin._api_import())
    assert result["status"] == "ok"
    tt = plugin.store.get("aiocqhttp:50001")
    assert tt.raw_source
    assert (plugin._plugin_data_dir() / tt.raw_source).read_text(encoding="utf-8") == payload


def test_events_api_and_countdown(plugin):
    import astrbot.api.web as web

    web.request.body = {
        "items": [
            {"name": "CET-6", "date": "2026-12-19"},
            {"name": "坏数据", "date": "不是日期"},
        ]
    }
    saved = asyncio.run(plugin._api_events_save())
    assert saved["status"] == "ok"
    assert [item["name"] for item in saved["data"]["items"]] == ["CET-6"]

    web.request.args = {}
    got = asyncio.run(plugin._api_events_get())
    assert got["data"]["items"][0]["date"] == "2026-12-19"

    event = _Event("/课表 倒计时", group="g1")
    outs = asyncio.run(_collect(plugin._h_countdown(event)))
    assert "CET-6" in outs[0]


def test_backup_and_restore_roundtrip(plugin):
    import astrbot.api.web as web

    _import_sample(plugin, _Event("/课表 导入", group="g1"))
    backup = asyncio.run(plugin._api_backup())
    assert backup["status"] == "ok"
    assert "test:10001" in backup["data"]["users"]

    # 破坏现场后再恢复
    plugin.store.delete("test:10001")
    plugin._events = [{"name": "CET-6", "date": "2026-12-19"}]
    web.request.body = backup["data"]
    result = asyncio.run(plugin._api_restore())
    assert result["status"] == "ok"
    assert result["data"]["restored"] == 1
    assert plugin.store.get("test:10001").courses
    assert plugin._events == []


def test_early_push_tomorrow_early_birds(tmp_path):
    import main as plugin_main
    from datetime import timedelta

    from schedule_lite_core.model import Course, Timetable

    class _Ctx:
        def __init__(self):
            self.sent = []

        async def send_message(self, origin, chain):
            self.sent.append((origin, chain))

    plugin_main.StarTools._dir = tmp_path
    ctx = _Ctx()
    plugin = plugin_main.ScheduleLitePlugin(
        ctx,
        {
            "early_enabled": True,
            "early_time": "21:30",
            "early_max_start": "08:00",
            "early_at": True,
            "showcase_enabled": False,
            "recap_enabled": False,
        },
    )
    now = plugin._now()
    tomorrow = now.date() + timedelta(days=1)
    monday = now.date() - timedelta(days=now.isoweekday() - 1)

    def make_tt(uid, name, start):
        return Timetable(
            owner_id=uid,
            owner_name=name,
            term_start=monday.isoformat(),
            total_weeks=20,
            courses=[Course(name=f"课{uid}", day=tomorrow.isoweekday(), weeks=[1, 2], start=start, end="09:40")],
            groups=["g1"],
            group_origins={"g1": "test:group:g1"},
        )

    plugin.store.put("t:1", make_tt("1", "早八同学", "08:00"))
    plugin.store.put("t:2", make_tt("2", "下午同学", "14:00"))

    now21 = now.replace(hour=21, minute=31, second=0, microsecond=0)
    asyncio.run(plugin._check_scheduled_broadcasts(now21))
    assert len(ctx.sent) == 1
    chain = ctx.sent[0][1].chain
    ats = [c for c in chain if c.__class__.__name__ == "At"]
    assert [str(a.qq) for a in ats] == ["1"]

    asyncio.run(plugin._check_scheduled_broadcasts(now21))
    assert len(ctx.sent) == 1


def test_group_index_cached_and_invalidated(plugin):
    event = _import_sample(plugin, _Event("/课表 导入", group="g1"))
    first = plugin._group_timetables("g1")
    assert [tt.owner_id for tt in first] == ["10001"]
    assert "g1" in plugin._group_index

    # 新增用户后写盘，索引失效并重建
    second_event = _Event("/课表 导入", uid="10002", nick="李四", group="g1")
    asyncio.run(_collect(plugin._h_import(second_event, json.dumps(SHIGUANG_SAMPLE, ensure_ascii=False))))
    assert plugin._group_index == {}
    visible = plugin._group_timetables("g1")
    assert {tt.owner_id for tt in visible} == {"10001", "10002"}


def test_warm_day_bucket_prefills_cache(plugin):
    event = _import_sample(plugin, _Event("/课表 导入", group="g1"))
    tt = plugin.store.get("test:10001")
    today = plugin._now().date()
    assert plugin._day_cache == {}
    plugin._warm_day_bucket(today)
    assert len(plugin._day_cache) == 1
    entries = plugin._day_entries_cached(tt, today, None)
    assert entries == plugin._day_cache[(id(tt), today.isoformat(), id(None))]


def test_single_image_sent_directly(image_plugin):
    from schedule_lite_core import render

    if not render.available():
        pytest.skip("本机没有可用的中文字体")
    event = _Event(group="g1")
    asyncio.run(_collect(image_plugin._h_import(event, json.dumps(SHIGUANG_SAMPLE, ensure_ascii=False))))
    asyncio.run(_collect(image_plugin._h_everyone(event)))
    assert event.sent
    first = event.sent[0].chain[0]
    assert not hasattr(first, "nodes"), "单张图片不应套合并转发"


def test_render_merges_half_empty_last_page(image_plugin, tmp_path):
    from schedule_lite_core import render

    if not render.available():
        pytest.skip("本机没有可用的中文字体")
    entries = [
        {"name": f"同学{i}", "type": 1, "label": "进行中", "main": "课", "sub": ""}
        for i in range(9)
    ]
    paths = render.render_everyone(
        entries,
        tmp_path / "cache" / "everyone",
        page_size=8,
        width=900,
        font_path="",
    )
    assert len(paths) == 1, "9 人时不应拆出只有 1 人的第二页"


def test_render_prunes_old_day_dirs(image_plugin, tmp_path):
    from schedule_lite_core import render

    if not render.available():
        pytest.skip("本机没有可用的中文字体")
    cache = tmp_path / "cache" / "everyone"
    for stale in ("20250101", "20250102", "20250103", "20250104"):
        day_dir = cache / stale
        day_dir.mkdir(parents=True)
        (day_dir / "everyone-x.png").write_bytes(b"png")

    paths = render.render_everyone(
        [{"name": "张三", "type": 1, "label": "进行中", "main": "课", "sub": ""}],
        cache,
        title="群友在上什么课？",
        subtitle="",
        footer_note="",
        page_size=8,
        width=900,
        font_path="",
    )
    assert paths
    # 所有图片落在同一个“天”目录里，目录名是 8 位日期
    parents = {p.parent.name for p in paths}
    assert len(parents) == 1
    day_name = next(iter(parents))
    assert len(day_name) == 8 and day_name.isdigit()
    # 旧的日期目录被清理
    remaining = sorted(d.name for d in cache.iterdir() if d.is_dir())
    assert "20250101" not in remaining and len(remaining) <= 3
