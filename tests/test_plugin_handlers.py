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

    components.Image = Image
    components.Node = Node
    components.Nodes = Nodes

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
        return []

    def get_extra(self, key, default=None):
        return default

    def stop_event(self):
        pass

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


def test_help_sends_forward_images(image_plugin, tmp_path):
    from schedule_lite_core import render

    if not render.available():
        pytest.skip("本机没有可用的中文字体")
    event = _Event()
    out = asyncio.run(_collect(image_plugin._h_help(event, "")))
    assert out == []  # 走图片路径，不再发文字
    assert event.sent, "应该发送合并转发消息"
    chain = event.sent[0]
    nodes = chain.chain[0]
    assert len(nodes.nodes) >= 2
    assert (tmp_path / "help" / "help.md").is_file()
    pages = list((tmp_path / "help").glob("help-*.png"))
    assert len(pages) >= 2


def test_everyone_sends_image(image_plugin, tmp_path):
    from schedule_lite_core import render

    if not render.available():
        pytest.skip("本机没有可用的中文字体")
    event = _Event(group="g1")
    asyncio.run(_collect(image_plugin._h_import(event, json.dumps(SHIGUANG_SAMPLE, ensure_ascii=False))))
    out = asyncio.run(_collect(image_plugin._h_everyone(event)))
    assert out == []
    assert event.sent
    nodes = event.sent[0].chain[0]
    assert nodes.nodes
    assert list((tmp_path / "cache" / "everyone").glob("everyone-*.png"))


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
    assert "今日满课榜" in ctx.sent[0][1].chain[0]
    assert "甲" in ctx.sent[0][1].chain[0]

    asyncio.run(plugin._check_scheduled_broadcasts(now))
    assert len(ctx.sent) == 1


def test_recap_broadcast_on_configured_weekday(tmp_path):
    today = __import__("datetime").date.today()
    plugin, ctx = _scheduled_plugin(
        tmp_path,
        {
            "showcase_enabled": False,
            "recap_enabled": True,
            "recap_weekday": today.isoweekday(),
            "recap_time": "21:00",
            "recap_mode": "week",
            "recap_top": 5,
        },
    )
    now = plugin._now().replace(hour=21, minute=5, second=0, microsecond=0)
    asyncio.run(plugin._check_scheduled_broadcasts(now))
    assert len(ctx.sent) == 1
    assert "累计上课榜" in ctx.sent[0][1].chain[0]
    assert "甲" in ctx.sent[0][1].chain[0]


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
