"""Работа с датами и временем: разбор ввода, часовые пояса, расчёт сроков напоминаний.

Все функции чистые (время передаётся явно), поэтому легко тестируются.
"""

import calendar
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

WEEKDAY_SHORT: tuple[str, ...] = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
_WEEKDAY_ALIASES: dict[str, int] = {
    **{name: idx for idx, name in enumerate(WEEKDAY_SHORT)},
    "понедельник": 0,
    "вторник": 1,
    "среда": 2,
    "четверг": 3,
    "пятница": 4,
    "суббота": 5,
    "воскресенье": 6,
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_local(moment: datetime, tz: str) -> datetime:
    return moment.astimezone(ZoneInfo(tz))


def local_to_utc(day: date, at: time, tz: str) -> datetime:
    return datetime.combine(day, at, tzinfo=ZoneInfo(tz)).astimezone(UTC)


def localize(naive: datetime, tz: str) -> datetime:
    """Локальное время состава (без tzinfo) → UTC."""
    return naive.replace(tzinfo=ZoneInfo(tz)).astimezone(UTC)


def format_local(moment: datetime, tz: str) -> str:
    """«пн 06.10.2026 19:00» в часовом поясе состава."""
    local = to_local(moment, tz)
    return f"{WEEKDAY_SHORT[local.weekday()]} {local:%d.%m.%Y %H:%M}"


def format_interval(start: datetime, end: datetime, tz: str) -> str:
    """«пн 06.10.2026 19:00–20:30»; если интервал переходит через полночь — обе даты."""
    local_end = to_local(end, tz)
    if to_local(start, tz).date() == local_end.date():
        return f"{format_local(start, tz)}–{local_end:%H:%M}"
    return f"{format_local(start, tz)} – {format_local(end, tz)}"


@dataclass(frozen=True, slots=True)
class ScheduleLine:
    weekday: int
    start_time: time
    duration_minutes: int
    venue: str


_SCHEDULE_RE = re.compile(
    r"^\s*(?P<day>[а-яё]+)\s+(?P<h>\d{1,2})[:.](?P<m>\d{2})\s+(?P<dur>\d{2,3})\s+(?P<venue>.+?)\s*$",
    re.IGNORECASE,
)


def parse_schedule_line(text: str) -> ScheduleLine:
    """Разбирает строку вида «Пн 19:00 90 Зал школы №5»."""
    match = _SCHEDULE_RE.match(text)
    if not match:
        raise ValueError("Формат: «Пн 19:00 90 Название зала»")
    day = match["day"].lower()
    if day not in _WEEKDAY_ALIASES:
        raise ValueError(f"Неизвестный день недели: {match['day']}")
    duration = int(match["dur"])
    if not 15 <= duration <= 600:
        raise ValueError("Длительность — от 15 до 600 минут")
    return ScheduleLine(
        weekday=_WEEKDAY_ALIASES[day],
        start_time=_make_time(int(match["h"]), int(match["m"])),
        duration_minutes=duration,
        venue=match["venue"],
    )


def parse_date(text: str) -> date:
    """«дд.мм.гггг» → date."""
    try:
        return datetime.strptime(text.strip(), "%d.%m.%Y").date()
    except ValueError as exc:
        raise ValueError("Формат даты: дд.мм.гггг, например 21.03.2005") from exc


def parse_local_datetime(text: str, *, today: date) -> datetime:
    """«дд.мм.гггг чч:мм» или «дд.мм чч:мм» (ближайшая такая дата не раньше today) → naive."""
    text = text.strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%y %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        parsed = datetime.strptime(f"{text} {today.year}", "%d.%m %H:%M %Y")
    except ValueError as exc:
        raise ValueError("Формат: дд.мм.гггг чч:мм, например 12.10.2026 19:30") from exc
    if parsed.date() < today:
        parsed = parsed.replace(year=today.year + 1)
    return parsed


def parse_time(text: str) -> time:
    match = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", text)
    if not match:
        raise ValueError("Формат времени: чч:мм")
    return _make_time(int(match[1]), int(match[2]))


def _make_time(hour: int, minute: int) -> time:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Некорректное время")
    return time(hour, minute)


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """Интервалы пересекаются по времени. Встык (конец одного = начало другого) — не пересечение."""
    return a_start < b_end and b_start < a_end


def is_birthday(birth_date: date, today: date) -> bool:
    """День рождения сегодня? 29 февраля в невисокосный год празднуется 28-го."""
    if (birth_date.month, birth_date.day) == (today.month, today.day):
        return True
    return (
        (birth_date.month, birth_date.day) == (2, 29)
        and (today.month, today.day) == (2, 28)
        and not calendar.isleap(today.year)
    )


def birthday_offsets(birth_date: date, today: date, offsets_days: Iterable[int]) -> list[int]:
    """Через сколько дней из offsets_days наступает день рождения (с учётом правила 29 февраля)."""
    return [d for d in offsets_days if is_birthday(birth_date, today + timedelta(days=d))]


def dates_for_weekday(start: date, days: int, weekday: int) -> list[date]:
    """Все даты с нужным днём недели в интервале [start, start + days)."""
    end = start + timedelta(days=days)
    current = start + timedelta(days=(weekday - start.weekday()) % 7)
    result: list[date] = []
    while current < end:
        result.append(current)
        current += timedelta(weeks=1)
    return result


def due_reminder_offsets(
    *,
    starts_at: datetime,
    tz: str,
    offsets_days: Iterable[int],
    reminder_time: time,
    now: datetime,
    grace: timedelta,
    same_day_lead: timedelta = timedelta(hours=2),
) -> list[int]:
    """Какие напоминания (смещения в днях) пора отправить прямо сейчас.

    Напоминание за N дней отправляется в reminder_time по местному времени в дату (событие − N).
    Для напоминания в день события время не позже чем за same_day_lead до начала.
    Напоминания старше grace не догоняются (например, событие создали позже срока).
    """
    if now >= starts_at:
        return []
    event_day = to_local(starts_at, tz).date()
    due: list[int] = []
    for offset in sorted(set(offsets_days), reverse=True):
        fire_at = local_to_utc(event_day - timedelta(days=offset), reminder_time, tz)
        if offset == 0:
            fire_at = min(fire_at, starts_at - same_day_lead)
        if fire_at <= now < fire_at + grace:
            due.append(offset)
    return due
