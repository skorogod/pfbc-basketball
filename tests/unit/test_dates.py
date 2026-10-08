from datetime import UTC, date, datetime, time, timedelta

import pytest

from pfbc_bot.domain.dates import (
    birthday_offsets,
    dates_for_weekday,
    due_reminder_offsets,
    duty_assign_at,
    is_birthday,
    local_to_utc,
    overlaps,
    parse_date,
    parse_local_datetime,
    parse_schedule_line,
    training_poll_at,
)

MSK = "Europe/Moscow"


def test_parse_schedule_line() -> None:
    line = parse_schedule_line("Пн 19:00 90 Зал школы №5")
    assert (line.weekday, line.start_time, line.duration_minutes, line.venue) == (
        0,
        time(19, 0),
        90,
        "Зал школы №5",
    )
    assert parse_schedule_line("суббота 9.30 120 ФОК").weekday == 5


@pytest.mark.parametrize(
    "text", ["Пн 25:00 90 Зал", "Xx 19:00 90 Зал", "Пн 19:00 5 Зал", "Пн 19:00"]
)
def test_parse_schedule_line_rejects_invalid(text: str) -> None:
    with pytest.raises(ValueError):
        parse_schedule_line(text)


def test_parse_date() -> None:
    assert parse_date("21.03.2005") == date(2005, 3, 21)
    with pytest.raises(ValueError):
        parse_date("2005-03-21")


def test_parse_local_datetime_without_year_rolls_forward() -> None:
    today = date(2026, 12, 20)
    assert parse_local_datetime("12.01 19:30", today=today) == datetime(2027, 1, 12, 19, 30)
    assert parse_local_datetime("25.12 10:00", today=today) == datetime(2026, 12, 25, 10, 0)
    assert parse_local_datetime("01.02.2027 08:00", today=today) == datetime(2027, 2, 1, 8, 0)


@pytest.mark.parametrize(
    ("birth", "today", "expected"),
    [
        (date(2000, 5, 10), date(2026, 5, 10), True),
        (date(2000, 5, 10), date(2026, 5, 11), False),
        (date(2004, 2, 29), date(2027, 2, 28), True),  # невисокосный год
        (date(2004, 2, 29), date(2028, 2, 28), False),  # високосный — празднуем 29-го
        (date(2004, 2, 29), date(2028, 2, 29), True),
    ],
)
def test_is_birthday(birth: date, today: date, expected: bool) -> None:
    assert is_birthday(birth, today) is expected


def test_birthday_offsets() -> None:
    today = date(2026, 10, 5)
    assert birthday_offsets(date(2000, 10, 12), today, [7, 3, 1]) == [7]
    assert birthday_offsets(date(2000, 10, 6), today, [7, 3, 1]) == [1]
    assert (
        birthday_offsets(date(2000, 10, 5), today, [7, 3, 1]) == []
    )  # сегодня — не предупреждение
    # через Новый год и 29 февраля в невисокосный год (празднуется 28-го)
    assert birthday_offsets(date(2000, 1, 2), date(2026, 12, 30), [3]) == [3]
    assert birthday_offsets(date(2004, 2, 29), date(2027, 2, 21), [7]) == [7]


def test_dates_for_weekday() -> None:
    start = date(2026, 10, 5)  # понедельник
    assert dates_for_weekday(start, 14, 0) == [date(2026, 10, 5), date(2026, 10, 12)]
    assert dates_for_weekday(start, 14, 2) == [date(2026, 10, 7), date(2026, 10, 14)]
    assert dates_for_weekday(start, 1, 1) == []


class TestDueReminderOffsets:
    starts_at = local_to_utc(date(2026, 10, 10), time(18, 0), MSK)

    def due(self, now: datetime) -> list[int]:
        return due_reminder_offsets(
            starts_at=self.starts_at,
            tz=MSK,
            offsets_days=[3, 1, 0],
            reminder_time=time(9, 0),
            now=now,
            grace=timedelta(hours=12),
        )

    def test_three_days_before_at_reminder_time(self) -> None:
        assert self.due(local_to_utc(date(2026, 10, 7), time(8, 59), MSK)) == []
        assert self.due(local_to_utc(date(2026, 10, 7), time(9, 0), MSK)) == [3]

    def test_one_day_and_same_day(self) -> None:
        assert self.due(local_to_utc(date(2026, 10, 9), time(9, 5), MSK)) == [1]
        assert self.due(local_to_utc(date(2026, 10, 10), time(9, 5), MSK)) == [0]

    def test_stale_reminders_are_not_backfilled(self) -> None:
        assert self.due(local_to_utc(date(2026, 10, 8), time(9, 0), MSK)) == []

    def test_nothing_after_start(self) -> None:
        assert self.due(self.starts_at + timedelta(minutes=1)) == []

    def test_same_day_early_event_reminds_before_start(self) -> None:
        early = local_to_utc(date(2026, 10, 10), time(8, 0), MSK)
        offsets = due_reminder_offsets(
            starts_at=early,
            tz=MSK,
            offsets_days=[0],
            reminder_time=time(9, 0),
            now=early - timedelta(hours=2),
            grace=timedelta(hours=12),
        )
        assert offsets == [0]


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ((10, 12), (11, 13), True),  # частичное пересечение
        ((10, 14), (11, 12), True),  # вложенность
        ((10, 12), (12, 13), False),  # встык
        ((12, 13), (10, 12), False),  # встык с другой стороны
        ((10, 11), (12, 13), False),
    ],
)
def test_overlaps(a: tuple[int, int], b: tuple[int, int], expected: bool) -> None:
    def t(hour: int) -> datetime:
        return datetime(2026, 10, 5, hour, tzinfo=UTC)

    assert overlaps(t(a[0]), t(a[1]), t(b[0]), t(b[1])) is expected


def test_local_to_utc_uses_squad_timezone() -> None:
    assert local_to_utc(date(2026, 10, 10), time(12, 0), MSK) == datetime(
        2026, 10, 10, 9, 0, tzinfo=UTC
    )


@pytest.mark.parametrize(
    ("hour", "minute", "poll_day_offset", "poll_hour"),
    [
        (19, 0, 0, 9),  # обычная — опрос в 09:00 в день тренировки
        (14, 1, 0, 9),  # позже 14:00 — обычная
        (14, 0, -1, 21),  # до 14:00 включительно — ранняя: опрос накануне в 21:00
        (11, 59, -1, 21),
        (8, 0, -1, 21),
    ],
)
def test_training_poll_at(hour: int, minute: int, poll_day_offset: int, poll_hour: int) -> None:
    day = date(2026, 10, 6)
    starts = local_to_utc(day, time(hour, minute), MSK)
    expected = local_to_utc(day + timedelta(days=poll_day_offset), time(poll_hour), MSK)
    assert (
        training_poll_at(
            starts_at=starts,
            tz=MSK,
            poll_time=time(9),
            eve_time=time(21),
            duty_lead=timedelta(hours=3),
            early_until=time(14),
        )
        == expected
    )


@pytest.mark.parametrize(
    ("hour", "minute", "assign_hour", "assign_minute"),
    [
        (19, 0, 16, 0),  # обычная — за 3 часа
        (14, 1, 11, 1),  # позже 14:00 — по обычному правилу
        (14, 0, 8, 0),  # до 14:00 включительно — утром в 08:00
        (11, 0, 8, 0),
        (8, 15, 8, 0),  # успевают прийти за 15 минут
        (8, 10, 5, 10),  # слишком рано для утреннего назначения — за 3 часа
    ],
)
def test_duty_assign_at(hour: int, minute: int, assign_hour: int, assign_minute: int) -> None:
    day = date(2026, 10, 6)
    assert duty_assign_at(
        starts_at=local_to_utc(day, time(hour, minute), MSK),
        tz=MSK,
        poll_time=time(9),
        duty_lead=timedelta(hours=3),
        early_until=time(14),
        morning_time=time(8),
        arrive_before=timedelta(minutes=15),
    ) == local_to_utc(day, time(assign_hour, assign_minute), MSK)


def test_long_duty_lead_makes_training_early() -> None:
    """Дежурных пришлось бы назначать раньше опроса — тренировка тоже «ранняя»."""
    starts = local_to_utc(date(2026, 10, 6), time(17, 0), MSK)
    assert training_poll_at(
        starts_at=starts,
        tz=MSK,
        poll_time=time(9),
        eve_time=time(21),
        duty_lead=timedelta(hours=10),
        early_until=time(14),
    ) == local_to_utc(date(2026, 10, 5), time(21), MSK)
