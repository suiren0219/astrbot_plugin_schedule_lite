from datetime import datetime

import pytest

from schedule_lite_core.importers import ics
from schedule_lite_core.model import Timetable
from schedule_lite_core.normalize import get_tz
from schedule_lite_core.service import (
    day_courses,
    everyone_report,
    format_day,
    format_rank,
    parse_query_date,
    rank_hours,
    status_now,
)
from schedule_lite_core.storage import TimetableStore

from .test_importers import ICS_SAMPLE

TZ = get_tz("Asia/Shanghai")


def make_timetable() -> Timetable:
    result = ics.parse_ics(ICS_SAMPLE)
    return Timetable(
        owner_id="u1",
        owner_name="张三",
        name=result.name,
        term_start=result.term_start.isoformat(),
        total_weeks=result.total_weeks,
        timezone="Asia/Shanghai",
        sections=result.sections,
        courses=result.courses,
    )


def test_format_day():
    tt = make_timetable()
    text = format_day(tt, datetime(2026, 9, 7).date())
    assert "2026/09/07 周一" in text
    assert "第 1 周" in text
    assert "08:00-09:40 高等数学" in text


def test_format_day_no_course_and_before_term():
    tt = make_timetable()
    empty = format_day(tt, datetime(2026, 9, 10).date(), today=datetime(2026, 9, 8).date())
    assert "这天没有课" in empty
    before = format_day(tt, datetime(2026, 8, 1).date())
    assert "学期还没开始" in before


def test_status_now_in_class_and_next():
    tt = make_timetable()
    in_class = status_now(tt, datetime(2026, 9, 7, 8, 30, tzinfo=TZ))
    assert in_class["type"] == 1
    assert in_class["main"] == "高等数学"
    assert "剩余" in in_class["sub"]

    upcoming = status_now(tt, datetime(2026, 9, 7, 7, 30, tzinfo=TZ))
    assert upcoming["type"] == 3
    assert "后" in upcoming["sub"]

    # 上午的课上完、下午没课时，晚上按「下午没课」处理，不再冒充已结束
    finished = status_now(tt, datetime(2026, 9, 7, 20, 0, tzinfo=TZ))
    assert finished["type"] == 5
    assert finished["main"] == "下午没有课"


def test_status_now_abandoned_only_current_course():
    tt = make_timetable()
    tt.abandon_date = "2026-09-07"
    tt.abandon_course = "高等数学"
    tt.abandon_start = "08:00"
    status = status_now(tt, datetime(2026, 9, 7, 8, 30, tzinfo=TZ))
    assert status["type"] == 2
    assert status["main"] == "高等数学"
    assert "翘课" in status["sub"]

    before = status_now(tt, datetime(2026, 9, 7, 7, 30, tzinfo=TZ))
    assert before["type"] == 3
    assert before["main"] == "高等数学"

    after = status_now(tt, datetime(2026, 9, 7, 20, 0, tzinfo=TZ))
    assert after["type"] == 5
    assert after["main"] == "下午没有课"


def test_status_half_day_scoping():
    from schedule_lite_core.model import Course

    tt = make_timetable()
    tt.courses = [
        Course(name="高等数学", day=1, weeks=[1], start="08:00", end="09:40"),
        Course(name="大学物理", day=1, weeks=[1], start="14:00", end="15:40"),
    ]

    # 上午：上午课上完 → 已结束，只统计上午课时；不预告下午的课
    morning = status_now(tt, datetime(2026, 9, 7, 11, 0, tzinfo=TZ))
    assert morning["type"] == 4
    assert morning["main"] == "上午课程已上完"
    assert morning["hours"] == 1.7

    # 下午：下午课上完 → 已结束，只统计下午课时
    afternoon = status_now(tt, datetime(2026, 9, 7, 16, 30, tzinfo=TZ))
    assert afternoon["type"] == 4
    assert afternoon["main"] == "下午课程已上完"
    assert afternoon["hours"] == 1.7

    # 只有上午课的人，下午查 → 下午没课（无课程，可被隐藏）
    tt.courses = [
        Course(name="高等数学", day=1, weeks=[1], start="08:00", end="09:40"),
    ]
    tt.invalidate_index()
    night = status_now(tt, datetime(2026, 9, 7, 20, 0, tzinfo=TZ))
    assert night["type"] == 5
    assert night["main"] == "下午没有课"

    # 只有下午课的人，上午查 → 上午没课
    tt.courses = [
        Course(name="大学物理", day=1, weeks=[1], start="14:00", end="15:40"),
    ]
    tt.invalidate_index()
    early = status_now(tt, datetime(2026, 9, 7, 9, 0, tzinfo=TZ))
    assert early["type"] == 5
    assert early["main"] == "上午没有课"

    # 跨中午的课两边都算：12:10 仍显示进行中
    tt.courses = [
        Course(name="实验", day=1, weeks=[1], start="11:00", end="12:30"),
    ]
    tt.invalidate_index()
    crossing = status_now(tt, datetime(2026, 9, 7, 12, 10, tzinfo=TZ))
    assert crossing["type"] == 1


def test_format_day_marks_only_skipped_course():
    tt = make_timetable()
    tt.abandon_date = "2026-09-07"
    tt.abandon_course = "高等数学"
    tt.abandon_start = "08:00"
    text = format_day(tt, datetime(2026, 9, 7).date())
    assert "08:00-09:40 高等数学（翘课）" in text
    assert "全部课程" not in text


def test_day_courses_week_filter():
    tt = make_timetable()
    week1 = day_courses(tt, datetime(2026, 9, 7).date())
    assert [c.name for c, _, _ in week1] == ["高等数学"]
    week5 = day_courses(tt, datetime(2026, 10, 5).date())  # EXDATE 停课
    assert week5 == []


def test_everyone_report():
    tt = make_timetable()
    tt2 = make_timetable()
    tt2.owner_id = "u2"
    tt2.owner_name = "李四"
    tt2.courses = []
    text = everyone_report([tt, tt2], datetime(2026, 9, 7, 8, 30, tzinfo=TZ))
    assert "张三" in text and "李四" in text
    assert "进行中" in text
    assert "无课程" in text


def test_rank():
    tt = make_timetable()
    # 高等数学 1 周 1 次 100 分钟 + 英语 1 周 1 次 100 分钟
    assert rank_hours(tt, "week", datetime(2026, 9, 8, 12, tzinfo=TZ)) == 200 / 60
    text = format_rank([tt], "week", datetime(2026, 9, 8, 12, tzinfo=TZ), title="测试榜")
    assert "测试榜" in text and "3.3小时" in text


def test_parse_query_date():
    today = datetime(2026, 10, 26).date()  # 周一
    assert parse_query_date("", today) == today
    assert parse_query_date("明天", today).isoformat() == "2026-10-27"
    assert parse_query_date("周三", today).isoformat() == "2026-10-28"
    assert parse_query_date("下周三", today).isoformat() == "2026-11-04"
    assert parse_query_date("2026/9/1", today).isoformat() == "2026-09-01"
    assert parse_query_date("看不懂", today) is None


def test_storage_roundtrip(tmp_path):
    path = tmp_path / "timetables.json"
    store = TimetableStore(path)
    tt = make_timetable()
    tt.notes["高等数学"] = "带课本"
    store.put("aiocqhttp:1", tt)
    store.save()

    second = TimetableStore(path)
    loaded = second.get("aiocqhttp:1")
    assert loaded is not None
    assert loaded.owner_name == "张三"
    assert loaded.courses[0].name == "高等数学"
    assert loaded.notes["高等数学"] == "带课本"

    second.delete("aiocqhttp:1")
    second.save()
    assert TimetableStore(path).get("aiocqhttp:1") is None


def test_holiday_and_makeup_day():
    from datetime import date

    from schedule_lite_core.holiday import HolidayCalendar
    from schedule_lite_core.model import Course

    calendar = HolidayCalendar(
        year=2026,
        days={
            "2026-10-01": {"name": "国庆节", "off": True},
            "2026-10-10": {"name": "国庆节后补班", "off": False},
        },
    )
    tt = Timetable(
        owner_id="u2",
        owner_name="李四",
        term_start="2026-08-31",
        total_weeks=20,
    )
    tt.courses = [
        Course(name="高等数学", day=4, weeks=[6], start="08:00", end="09:40"),
        Course(name="大学英语", day=1, weeks=[6], start="10:00", end="11:40"),
    ]

    holiday = format_day(tt, date(2026, 10, 1), holiday=calendar)
    assert "国庆节放假" in holiday
    assert "今天放假" in holiday

    # 补班日默认按实际周六，没有课
    assert "调休补班" in format_day(tt, date(2026, 10, 10), holiday=calendar)
    # 明确映射成周四后显示周四课程
    tt.makeup_days["2026-10-10"] = 4
    makeup = format_day(tt, date(2026, 10, 10), holiday=calendar)
    assert "按周四课表" in makeup
    assert "08:00-09:40 高等数学" in makeup


def test_leave_and_adjustment():
    from datetime import date

    from schedule_lite_core.model import Course

    tt = make_timetable()
    tt.courses = [Course(name="高等数学", day=1, weeks=[1], start="08:00", end="09:40")]
    tt.leaves = [{"start": "2026-09-07", "end": "2026-09-07", "reason": "病假"}]
    leave_text = format_day(tt, date(2026, 9, 7))
    assert "已请假：病假" in leave_text
    assert "（请假）" in leave_text
    status = status_now(tt, datetime(2026, 9, 8 - 1, 8, 30, tzinfo=TZ))
    assert status["type"] == 6
    assert "请假" in status["sub"]

    tt.leaves = []
    tt.adjustments = [
        {
            "from_date": "2026-09-07",
            "to_date": "2026-09-08",
            "course": "高等数学",
            "courses": [{"name": "高等数学", "start": "08:00", "end": "09:40"}],
        }
    ]
    assert day_courses(tt, date(2026, 9, 7)) == []
    moved = day_courses(tt, date(2026, 9, 8))
    assert [(c.name, s, e) for c, s, e in moved] == [("高等数学", "08:00", "09:40")]
    text = format_day(tt, date(2026, 9, 8))
    assert "（调课）" in text


def test_format_day_half_filter():
    from datetime import date

    from schedule_lite_core.model import Course

    tt = make_timetable()
    tt.courses = [
        Course(name="高等数学", day=1, weeks=[1], start="08:00", end="09:40"),
        Course(name="大学物理", day=1, weeks=[1], start="14:00", end="15:40"),
    ]
    tt.invalidate_index()

    # 上午查询：只看上午，下午的课隐藏
    morning = format_day(tt, date(2026, 9, 7), now_min=9 * 60)
    assert "高等数学" in morning
    assert "大学物理" not in morning
    assert "已隐藏下午的 1 节课" in morning

    # 下午查询：只看下午，上午的课隐藏
    afternoon = format_day(tt, date(2026, 9, 7), now_min=15 * 60)
    assert "大学物理" in afternoon
    assert "高等数学" not in afternoon
    assert "已隐藏上午的 1 节课" in afternoon


def test_format_day_after_morning_only_day():
    from datetime import date

    from schedule_lite_core.model import Course

    tt = make_timetable()
    tt.courses = [
        Course(name="高等数学", day=1, weeks=[1], start="08:00", end="09:40"),
    ]
    tt.invalidate_index()

    # 上午结课、下午没有课：下午再查直接提示已上完
    text = format_day(tt, date(2026, 9, 7), now_min=15 * 60)
    assert "上午的课已经上完" in text
    assert "高等数学" not in text

    # 上午还在上课时正常显示
    during = format_day(tt, date(2026, 9, 7), now_min=8 * 60 + 30)
    assert "高等数学" in during


def test_format_day_defaults_to_full_day():
    from datetime import date

    from schedule_lite_core.model import Course

    tt = make_timetable()
    tt.courses = [
        Course(name="高等数学", day=1, weeks=[1], start="08:00", end="09:40"),
        Course(name="大学物理", day=1, weeks=[1], start="14:00", end="15:40"),
    ]
    tt.invalidate_index()
    text = format_day(tt, date(2026, 9, 7))
    assert "高等数学" in text and "大学物理" in text
    assert "已隐藏" not in text


def test_course_in_half_boundaries():
    from schedule_lite_core.service import course_in_half

    # 正好 12:00 结课算上午
    assert course_in_half("10:00", "12:00", "morning") is True
    assert course_in_half("10:00", "12:00", "afternoon") is False
    # 跨中午的课上午和下午都能看到，不会整节消失
    assert course_in_half("11:00", "12:30", "morning") is True
    assert course_in_half("11:00", "12:30", "afternoon") is True
    assert course_in_half("14:00", "15:40", "morning") is False


def test_rank_shows_my_position():
    tt = make_timetable()
    others = []
    for index, (name, start, end) in enumerate(
        [("班长", "08:00", "12:00"), ("学委", "13:00", "18:00")], start=2
    ):
        other = make_timetable()
        other.owner_id = f"u{index}"
        other.owner_name = name
        from schedule_lite_core.model import Course

        other.courses = [
            Course(name="课", day=1, weeks=[1], start=start, end=end)
        ]
        others.append(other)
    text = format_rank(
        [tt, *others],
        "week",
        datetime(2026, 9, 8, 12, tzinfo=TZ),
        title="测试榜",
        highlight_owner="u1",
    )
    assert "你当前排第" in text
    assert "未进前九" not in text

    # 自己不在前九时给出具体名次
    many = [tt]
    for index in range(12):
        other = make_timetable()
        other.owner_id = f"x{index}"
        other.owner_name = f"同学{index}"
        from schedule_lite_core.model import Course

        other.courses = [
            Course(name="课", day=1, weeks=[1], start="08:00", end="18:00")
        ]
        many.append(other)
    long_text = format_rank(
        many,
        "week",
        datetime(2026, 9, 8, 12, tzinfo=TZ),
        title="测试榜",
        highlight_owner="u1",
    )
    assert "另有 4 人未显示" in long_text
    assert "你当前排第 13 名" in long_text


def test_everyone_priority_and_hide_idle():
    from datetime import date

    from schedule_lite_core.model import Course
    from schedule_lite_core.service import everyone_entries

    def tt(uid, name, start, end, leave=False):
        item = Timetable(
            owner_id=uid,
            owner_name=name,
            term_start="2026-08-31",
            total_weeks=20,
        )
        item.courses = [Course(name="课程", day=1, weeks=[5], start=start, end=end)]
        if leave:
            item.leaves = [{"start": "2026-09-28", "end": "2026-09-28", "reason": "事假"}]
        return item

    now = datetime(2026, 9, 28, 8, 30, tzinfo=TZ)
    timetables = [
        Timetable(owner_id="1", owner_name="无课", term_start="2026-08-31", total_weeks=20),
        tt("2", "请假", "08:00", "09:40", leave=True),
        tt("3", "在上", "08:00", "09:40"),
        tt("4", "下一节", "10:00", "11:40"),
    ]
    entries = everyone_entries(timetables, now)
    assert [entry["name"] for entry in entries] == ["请假", "在上", "下一节", "无课"]
    assert entries[0]["type"] == 6
    assert entries[-1]["type"] == 5

    hidden = everyone_entries(timetables, now, hide_idle_over=2)
    assert [entry["name"] for entry in hidden] == ["请假", "在上", "下一节"]


def test_rank_month_mode():
    from datetime import date

    from schedule_lite_core.model import Course

    tt = Timetable(
        owner_id="u1",
        term_start="2026-09-07",
        total_weeks=20,
        timezone="Asia/Shanghai",
        courses=[
            Course(name="高等数学", day=1, weeks=[1, 2, 3, 4], start="08:00", end="09:40")
        ],
    )
    now = datetime(2026, 9, 15, 12, 0, tzinfo=TZ)
    assert rank_hours(tt, "month", now) == pytest.approx(200 / 60)
    assert rank_hours(tt, "week", now) == pytest.approx(100 / 60)


def test_global_makeup_weekday_map():
    from datetime import date

    from schedule_lite_core.holiday import HolidayCalendar
    from schedule_lite_core.model import Course

    calendar = HolidayCalendar(
        year=2026,
        days={"2026-10-10": {"name": "国庆节后补班", "off": False}},
        makeup_weekdays={"2026-10-10": 4},
    )
    tt = Timetable(
        owner_id="u1",
        term_start="2026-09-07",
        total_weeks=20,
        timezone="Asia/Shanghai",
        courses=[
            Course(name="大学英语", day=4, weeks=[5], start="10:00", end="11:40")
        ],
    )
    text = format_day(tt, date(2026, 10, 10), holiday=calendar)
    assert "按周四课表" in text
    assert "大学英语" in text

    tt.makeup_days["2026-10-10"] = 5
    personal = format_day(tt, date(2026, 10, 10), holiday=calendar)
    assert "按周五课表" in personal
