import asyncio
import json
from datetime import date

from schedule_lite_core.importers import detect, generic, ics, shiguang, wakeup


# ---------------------------------------------------------------- WakeUp


def _wakeup_payload() -> str:
    nodes = [
        {"node": 1, "startTime": "08:00", "endTime": "08:45"},
        {"node": 2, "startTime": "08:55", "endTime": "09:40"},
        {"node": 3, "startTime": "10:00", "endTime": "10:45"},
    ]
    info = {"tableName": "2026 秋", "startDate": "2026-09-07", "totalWeek": 20}
    infos = [{"id": 1, "courseName": "高等数学", "color": "#FF0000"}]
    instances = [
        {
            "id": 1, "day": 1, "startWeek": 1, "endWeek": 8, "startNode": 1,
            "step": 2, "room": "A101", "teacher": "张老师", "type": 0,
        },
        {
            "id": 1, "day": 1, "startWeek": 9, "endWeek": 16, "startNode": 1,
            "step": 2, "room": "A101", "teacher": "张老师", "type": 0,
        },
        {
            "id": 1, "day": 3, "startWeek": 1, "endWeek": 16, "startNode": 1,
            "step": 3, "room": "A101", "teacher": "张老师", "type": 1,
        },
        {
            "id": 1, "day": 3, "startWeek": 2, "endWeek": 16, "startNode": 1,
            "step": 3, "room": "A101", "teacher": "张老师", "type": 2,
        },
    ]
    lines = [
        json.dumps([1]),
        json.dumps(nodes, ensure_ascii=False),
        json.dumps(info, ensure_ascii=False),
        json.dumps(infos, ensure_ascii=False),
        json.dumps(instances, ensure_ascii=False),
    ]
    return "\n".join(lines)


def test_wakeup_parse():
    result = wakeup.parse_backup(_wakeup_payload(), store_teacher=True, store_location=True)
    assert result.term_start == date(2026, 9, 7)
    assert result.total_weeks == 20
    monday = [c for c in result.courses if c.day == 1]
    assert len(monday) == 1
    assert monday[0].weeks == list(range(1, 17))
    assert (monday[0].start, monday[0].end) == ("08:00", "09:40")
    assert monday[0].location == "A101"
    assert monday[0].teacher == "张老师"

    wednesday = [c for c in result.courses if c.day == 3]
    assert len(wednesday) == 1
    assert wednesday[0].weeks == list(range(1, 17))  # 单双周两行合并
    assert (wednesday[0].start, wednesday[0].end) == ("08:00", "10:45")


def test_wakeup_own_time():
    payload = _wakeup_payload().splitlines()
    instances = json.loads(payload[-1])
    instances.append(
        {
            "id": 1, "day": 5, "startWeek": 1, "endWeek": 2, "startNode": 1,
            "step": 1, "room": "线上", "teacher": "", "type": 0,
            "ownTime": True, "startTime": "19:00", "endTime": "20:30",
        }
    )
    payload[-1] = json.dumps(instances, ensure_ascii=False)
    result = wakeup.parse_backup("\n".join(payload))
    friday = [c for c in result.courses if c.day == 5][0]
    assert (friday.start, friday.end) == ("19:00", "20:30")
    assert friday.start_section is None


# ---------------------------------------------------------------- iCalendar


ICS_SAMPLE = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:math-1
SUMMARY:高等数学
LOCATION:A101
DTSTART;TZID=Asia/Shanghai:20260907T080000
DTEND;TZID=Asia/Shanghai:20260907T094000
RRULE:FREQ=WEEKLY;UNTIL=20261220T235959Z
EXDATE;TZID=Asia/Shanghai:20261005T080000
END:VEVENT
BEGIN:VEVENT
UID:english-1
SUMMARY:大学英语
DTSTART;TZID=Asia/Shanghai:20260908T100000
DTEND;TZID=Asia/Shanghai:20260908T114000
END:VEVENT
END:VCALENDAR
"""


def test_ics_weekly_with_exdate():
    result = ics.parse_ics(ICS_SAMPLE)
    assert result.term_start == date(2026, 9, 7)
    math = [c for c in result.courses if c.name == "高等数学"][0]
    assert math.day == 1
    assert 5 not in math.weeks  # EXDATE 停课
    assert math.weeks == [1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]
    assert (math.start, math.end) == ("08:00", "09:40")
    english = [c for c in result.courses if c.name == "大学英语"][0]
    assert english.day == 2
    assert english.weeks == [1]


def test_ics_utc_converted_to_local():
    text = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:utc-1
SUMMARY:跨时区课程
DTSTART:20260907T000000Z
DTEND:20260907T014000Z
END:VEVENT
END:VCALENDAR
"""
    result = ics.parse_ics(text)
    course = result.courses[0]
    assert (course.start, course.end) == ("08:00", "09:40")


def test_ics_recurrence_override():
    text = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:x
SUMMARY:调课
DTSTART;TZID=Asia/Shanghai:20260907T100000
DTEND;TZID=Asia/Shanghai:20260907T114000
RRULE:FREQ=WEEKLY;UNTIL=20260928T235959Z
END:VEVENT
BEGIN:VEVENT
UID:x
RECURRENCE-ID;TZID=Asia/Shanghai:20260914T100000
SUMMARY:调课
DTSTART;TZID=Asia/Shanghai:20260916T140000
DTEND;TZID=Asia/Shanghai:20260916T154000
END:VEVENT
END:VCALENDAR
"""
    result = ics.parse_ics(text)
    monday = [c for c in result.courses if c.day == 1][0]
    wednesday = [c for c in result.courses if c.day == 3][0]
    assert monday.weeks == [1, 3, 4]
    assert wednesday.weeks == [2]
    assert (wednesday.start, wednesday.end) == ("14:00", "15:40")


# ---------------------------------------------------------------- 拾光


SHIGUANG_SAMPLE = {
    "courses": [
        {
            "name": "大学物理",
            "teacher": "王老师",
            "position": "C301",
            "day": 2,
            "startSection": 1,
            "endSection": 2,
            "weeks": [1, 2, 3],
            "isCustomTime": False,
        },
        {
            "name": "体育",
            "position": "操场",
            "day": 4,
            "weeks": [1, 2],
            "isCustomTime": True,
            "customStartTime": "15:00",
            "customEndTime": "16:30",
        },
    ],
    "timeSlots": [
        {"number": 1, "startTime": "08:00", "endTime": "08:45"},
        {"number": 2, "startTime": "08:55", "endTime": "09:40"},
    ],
    "config": {"semesterStartDate": "2026-09-07", "semesterTotalWeeks": 18},
}


def test_shiguang_parse():
    result = shiguang.parse_export(SHIGUANG_SAMPLE)
    assert result.name
    assert result.term_start == date(2026, 9, 7)
    assert result.total_weeks == 18
    physics = [c for c in result.courses if c.name == "大学物理"][0]
    assert (physics.start, physics.end) == ("08:00", "09:40")
    sport = [c for c in result.courses if c.name == "体育"][0]
    assert (sport.start, sport.end) == ("15:00", "16:30")


# ---------------------------------------------------------------- 通用


def test_generic_json():
    obj = {
        "name": "通用课表",
        "term_start": "2026-09-07",
        "total_weeks": 20,
        "courses": [
            {
                "课程": "线性代数",
                "星期": "周二",
                "开始": "08:00",
                "结束": "09:40",
                "周次": "1-16",
                "地点": "A301",
            },
            {
                "courseName": "实验",
                "weekday": "3",
                "startTime": "14:00-15:40",
                "weeks": "1,3,5",
                "room": "实验楼",
            },
        ],
    }
    result = generic.parse_json(obj)
    assert len(result.courses) == 2
    algebra = [c for c in result.courses if c.name == "线性代数"][0]
    assert algebra.day == 2 and algebra.weeks == list(range(1, 17))
    lab = [c for c in result.courses if c.name == "实验"][0]
    assert (lab.start, lab.end) == ("14:00", "15:40")


def test_generic_csv():
    text = "课程,星期,开始,结束,周次,地点\n线性代数,周三,10:00,11:40,1-8,A301\n"
    result = generic.parse_csv(text, store_location=True)
    assert len(result.courses) == 1
    course = result.courses[0]
    assert course.day == 3
    assert course.weeks == list(range(1, 9))
    assert course.location == "A301"


# ---------------------------------------------------------------- 自动识别


def test_detect_routes():
    assert detect.parse_text(ICS_SAMPLE).source == "iCalendar"
    shiguang_text = json.dumps(SHIGUANG_SAMPLE, ensure_ascii=False)
    assert detect.parse_text(shiguang_text).source == "拾光课程表"
    wakeup_text = _wakeup_payload()
    assert detect.parse_text(wakeup_text).source == "WakeUp 课程表"
    assert detect.parse_text("课程,星期,开始,结束,周次\n物理,周一,08:00,09:40,1-8\n").source == "CSV 表格"


def test_xiaoai_support_removed():
    import importlib.util

    assert "小爱" not in detect.HELP
    assert importlib.util.find_spec("schedule_lite_core.importers.xiaoai") is None


def test_ics_biweekly_byday():
    text = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:a
SUMMARY:多天课
DTSTART;TZID=Asia/Shanghai:20260907T080000
DTEND;TZID=Asia/Shanghai:20260907T094000
RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;COUNT=6
END:VEVENT
END:VCALENDAR
"""
    result = ics.parse_ics(text)
    weeks = {c.day: c.weeks for c in result.courses}
    assert weeks == {1: [1, 3, 5], 3: [1, 3, 5]}


def test_ics_windows_timezone_name():
    text = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:w
SUMMARY:时区课
DTSTART;TZID=China Standard Time:20260907T140000
DTEND;TZID=China Standard Time:20260907T153000
END:VEVENT
END:VCALENDAR
"""
    result = ics.parse_ics(text)
    assert (result.courses[0].start, result.courses[0].end) == ("14:00", "15:30")


def test_ics_metadata_from_summary_and_description():
    text = """BEGIN:VCALENDAR
X-WR-CALNAME:Nexio 课表
BEGIN:VEVENT
UID:n1
SUMMARY:高等数学@A101
DESCRIPTION:任课教师：张三\n周次：1-8周
DTSTART;TZID=Asia/Shanghai:20260907T080000
DTEND;TZID=Asia/Shanghai:20260907T094000
END:VEVENT
END:VCALENDAR
"""
    result = ics.parse_ics(text, store_teacher=True, store_location=True)
    assert result.name == "Nexio 课表"
    course = result.courses[0]
    assert course.name == "高等数学"
    assert course.location == "A101"
    assert course.teacher == "张三"
    assert course.weeks == list(range(1, 9))
    assert (course.start, course.end) == ("08:00", "09:40")


def test_ics_rrule_filtered_by_summary_weeks():
    text = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:n2
SUMMARY:大学英语(1-8周)
DTSTART;TZID=Asia/Shanghai:20260907T100000
DTEND;TZID=Asia/Shanghai:20260907T114000
RRULE:FREQ=WEEKLY;UNTIL=20261231T235959Z
END:VEVENT
END:VCALENDAR
"""
    result = ics.parse_ics(text)
    assert result.courses[0].name == "大学英语"
    assert result.courses[0].weeks == list(range(1, 9))


def test_ics_odd_week_hint():
    text = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:n3
SUMMARY:体育(1-16周 单)
DTSTART;TZID=Asia/Shanghai:20260908T140000
DTEND;TZID=Asia/Shanghai:20260908T153000
RRULE:FREQ=WEEKLY;UNTIL=20261231T235959Z
END:VEVENT
END:VCALENDAR
"""
    result = ics.parse_ics(text)
    assert result.courses[0].weeks == list(range(1, 17, 2))


def test_ics_single_line_description_fields():
    text = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:n4
SUMMARY:操作系统
DESCRIPTION:教师：李四，地点：B202，周次：1-4周
DTSTART;TZID=Asia/Shanghai:20260909T140000
DTEND;TZID=Asia/Shanghai:20260909T153000
END:VEVENT
END:VCALENDAR
"""
    result = ics.parse_ics(text, store_teacher=True, store_location=True)
    course = result.courses[0]
    assert course.teacher == "李四"
    assert course.location == "B202"
    assert course.weeks == [1, 2, 3, 4]


def test_ics_floating_time_uses_xwr_timezone():
    text = """BEGIN:VCALENDAR
X-WR-TIMEZONE:Asia/Shanghai
BEGIN:VEVENT
UID:n5
SUMMARY:编译原理
DTSTART:20260910T080000
DTEND:20260910T094000
END:VEVENT
END:VCALENDAR
"""
    result = ics.parse_ics(text)
    assert (result.courses[0].start, result.courses[0].end) == ("08:00", "09:40")


def test_ics_multiple_occurrence_events_without_rrule():
    def event(day: str, uid: str) -> str:
        return f"""BEGIN:VEVENT
UID:{uid}
SUMMARY:数据结构
LOCATION:C301
DTSTART;TZID=Asia/Shanghai:{day}T080000
DTEND;TZID=Asia/Shanghai:{day}T094000
END:VEVENT
"""

    text = (
        "BEGIN:VCALENDAR\n"
        + event("20260907", "a")
        + event("20260914", "b")
        + "END:VCALENDAR\n"
    )
    result = ics.parse_ics(text)
    assert len(result.courses) == 1
    assert result.courses[0].weeks == [1, 2]



# ---------------------------------------------------------------- Nexio


def _nexio_payload() -> dict:
    return {
        "protocol": "nexio.schedule",
        "version": 4,
        "schedule_name": "示例课表",
        "settings": {
            "class_start_time": "2026/09/07",
            "current_week": 3,
            "total_weeks": 18,
            "morning_sections": 4,
            "afternoon_sections": 4,
            "evening_sections": 4,
        },
        "times": {
            "morning": {"1": "08:00-08:45", "2": "08:55-09:40", "3": "10:00-10:45", "4": "10:55-11:40"},
            "afternoon": {"1": "14:00-14:45", "2": "14:55-15:40", "3": "16:00-16:45", "4": "16:55-17:40"},
            "evening": {"1": "19:00-19:45", "2": "19:55-20:40", "3": "20:50-21:35", "4": "21:45-22:30"},
        },
        "courses": [
            {
                "name": "高等数学", "classroom": "A101", "teacher": "张老师",
                "dayOfWeek": 1, "startSection": 1, "endSection": 2,
                "startWeek": 1, "endWeek": 16, "weekType": 0, "selectedWeeks": [],
            },
            {
                "name": "大学英语", "classroom": "B202", "teacher": "李老师",
                "dayOfWeek": 2, "startSection": 3, "endSection": 4,
                "startWeek": 1, "endWeek": 16, "weekType": 1, "selectedWeeks": [],
            },
            {
                "name": "晚自习", "classroom": "图书馆",
                "dayOfWeek": 3, "isCustomTime": True,
                "customStartTime": "19:30", "customEndTime": "20:30",
                "startWeek": 1, "endWeek": 2, "weekType": 0,
                "selectedWeeks": [1, 3, 5],
            },
        ],
    }


def test_nexio_json_export():
    result = detect.parse_text(json.dumps(_nexio_payload(), ensure_ascii=False))
    assert result.source_kind == "nexio"
    assert result.term_start == date(2026, 9, 7)
    assert result.total_weeks == 18
    # 下午、晚上的节次接着上午编号
    assert [(s.index, s.start) for s in result.sections][:8] == [
        (1, "08:00"), (2, "08:55"), (3, "10:00"), (4, "10:55"),
        (5, "14:00"), (6, "14:55"), (7, "16:00"), (8, "16:55"),
    ]
    math = next(c for c in result.courses if c.name == "高等数学")
    assert (math.start, math.end) == ("08:00", "09:40")
    assert math.weeks == list(range(1, 17))
    english = next(c for c in result.courses if c.name == "大学英语")
    assert english.weeks == list(range(1, 17, 2))
    evening = next(c for c in result.courses if c.name == "晚自习")
    assert (evening.start, evening.end) == ("19:30", "20:30")
    assert evening.weeks == [1, 3, 5]


def test_nexio_share_code_via_detect():
    payload = {
        "ok": True,
        "code": "示例课表已上线",
        "schedule_name": "默认课表",
        "schedule_data": _nexio_payload(),
    }
    seen = []

    async def fake_fetch(url):
        seen.append(url)
        return json.dumps(payload, ensure_ascii=False)

    result = asyncio.run(
        detect.import_any("分享口令为：示例课表已上线", fetch=fake_fetch)
    )
    assert result.source_kind == "nexio"
    assert result.name == "默认课表"
    assert "code=%E7%A4%BA%E4%BE%8B" in seen[0]


def test_nexio_expired_code_message():
    async def fake_fetch(url):
        return '{"ok":false,"error":"share code not found"}'

    try:
        asyncio.run(detect.import_any("示例课表已上线", fetch=fake_fetch))
    except Exception as exc:
        assert "share code not found" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("过期口令应当报错")


def test_ics_location_contains_teacher():
    text = """BEGIN:VCALENDAR
PRODID:-//Nexio Schedule//Course Schedule//CN
BEGIN:VEVENT
UID:n1
SUMMARY:大学物理实验
LOCATION:A101 张老师,李老师
DESCRIPTION:第10周
DTSTART:20261102T083000
DTEND:20261102T100500
END:VEVENT
END:VCALENDAR
"""
    result = ics.parse_ics(text, store_teacher=True, store_location=True)
    course = result.courses[0]
    assert course.location == "A101"
    assert course.teacher == "张老师、李老师"
    assert course.weeks == [10]


def test_default_import_drops_teacher_and_location():
    text = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:privacy
SUMMARY:信息安全
LOCATION:A101 张三
DESCRIPTION:任课教师：张三，地点：A101，周次：1-4周
DTSTART;TZID=Asia/Shanghai:20260907T080000
DTEND;TZID=Asia/Shanghai:20260907T094000
END:VEVENT
END:VCALENDAR
"""
    result = ics.parse_ics(text)
    course = result.courses[0]
    assert course.teacher == ""
    assert course.location == ""
