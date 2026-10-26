from datetime import date

from schedule_lite_core.model import Course, Section
from schedule_lite_core.normalize import (
    compress_weeks,
    describe_weeks,
    normalize_courses,
    parse_date,
    parse_day,
    parse_section_text,
    parse_section_times,
    parse_time,
    parse_weeks,
)


def test_parse_weeks_basic():
    assert parse_weeks("1-16周") == list(range(1, 17))
    assert parse_weeks("第1~16周") == list(range(1, 17))
    assert parse_weeks("1，3，5") == [1, 3, 5]
    assert parse_weeks([1, "2", 3.0]) == [1, 2, 3]
    assert parse_weeks(5) == [5]


def test_parse_weeks_odd_even():
    assert parse_weeks("1-16周(单)") == list(range(1, 17, 2))
    assert parse_weeks("1-16单周") == list(range(1, 17, 2))
    assert parse_weeks("1-16双周") == list(range(2, 17, 2))
    assert parse_weeks("1-8单,10-16双") == [1, 3, 5, 7, 10, 12, 14, 16]


def test_parse_weeks_step_and_noise():
    assert parse_weeks("1-16/2") == list(range(1, 17, 2))
    assert parse_weeks("1-16周，第3周") == list(range(1, 17))
    assert parse_weeks("") == []
    assert parse_weeks("没有周次") == []


def test_describe_weeks():
    assert describe_weeks(range(1, 17)) == "1-16周"
    assert describe_weeks([1, 2, 3, 5, 6]) == "1-3,5-6周"
    assert describe_weeks([1, 3, 5, 7]) == "1-7周(单)"
    assert compress_weeks([1, 3, 4, 5, 9]) == "1,3-5,9"


def test_parse_time_variants():
    assert parse_time("8:00") == "08:00"
    assert parse_time("08：30") == "08:30"
    assert parse_time("0830") == "08:30"
    assert parse_time("8点") == "08:00"
    assert parse_time("8点30") == "08:30"
    assert parse_time("08:00:00") == "08:00"
    assert parse_time("25:00") is None
    assert parse_time("") is None


def test_parse_day_variants():
    assert parse_day("周一") == 1
    assert parse_day("星期日") == 7
    assert parse_day("星期天") == 7
    assert parse_day("MO") == 1
    assert parse_day("Sun") == 7
    assert parse_day(0) == 7
    assert parse_day(3) == 3
    assert parse_day("8") is None


def test_parse_date_variants():
    assert parse_date("2026-09-07") == date(2026, 9, 7)
    assert parse_date("2026/9/7") == date(2026, 9, 7)
    assert parse_date("20260907") == date(2026, 9, 7)
    assert parse_date("2026年9月7日") == date(2026, 9, 7)
    assert parse_date(1757000000000) == date(2025, 9, 4)


def test_parse_sections():
    text = "1 08:00-08:45\n2 08:55 - 09:40\n# 注释\n"
    sections = parse_section_text(text)
    assert [(s.index, s.start, s.end) for s in sections] == [
        (1, "08:00", "08:45"),
        (2, "08:55", "09:40"),
    ]

    sections = parse_section_times(
        [{"i": 1, "s": "08:00", "e": "08:45"}, {"node": 2, "startTime": "08:55", "endTime": "09:40"}]
    )
    assert [s.index for s in sections] == [1, 2]

    sections = parse_section_times('{"1": "08:00-08:45", "2": "08:55-09:40"}')
    assert [s.index for s in sections] == [1, 2]


def test_normalize_courses_merges_and_fills_times():
    sections = [
        Section(index=1, start="08:00", end="08:45"),
        Section(index=2, start="08:55", end="09:40"),
    ]
    courses = [
        Course(name="高等数学", day=1, weeks=[1, 2, 3, 4], start_section=1, end_section=2),
        Course(name="高等数学", day=1, weeks=[5, 6], start_section=1, end_section=2),
        Course(name="坏数据", day=1, weeks=[1]),  # 没有时间，应被丢弃
        Course(name="星期错误", day=9, weeks=[1], start="08:00", end="09:00"),
    ]
    result, merged, dropped, warnings = normalize_courses(courses, sections)
    assert len(result) == 1
    assert result[0].weeks == [1, 2, 3, 4, 5, 6]
    assert (result[0].start, result[0].end) == ("08:00", "09:40")
    assert merged == 1
    assert dropped == 2
    assert len(warnings) == 2


def test_parse_time_minutes_of_day():
    assert parse_time("480") == "08:00"
    assert parse_time("1439") == "14:39"
    assert parse_time("999") == "16:39"
    assert parse_time("0830") == "08:30"


def test_parse_section_times_python_repr():
    text = "[{'i': 1, 's': '08:00', 'e': '08:45'}, {'i': 2, 's': '08:55', 'e': '09:40'}]"
    sections = parse_section_times(text)
    assert [(s.index, s.start, s.end) for s in sections] == [
        (1, "08:00", "08:45"),
        (2, "08:55", "09:40"),
    ]
