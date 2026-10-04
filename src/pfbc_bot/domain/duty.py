"""Алгоритм выбора дежурных по залу (ФТ-3).

Правила:
- кандидаты — только подтвердившие участие в тренировке (готовит вызывающий код);
- новички, состоящие в составе меньше grace_days от первого вступления, не назначаются;
- очередь — по давности последнего фактического дежурства (никогда не дежуривший — первым);
- количество дежурств не учитывается: вернувшийся после перерыва игрок идёт первым;
- при равенстве — случайный порядок.
"""

import random
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class DutyCandidate:
    user_id: int
    first_joined_at: datetime
    last_duty_at: datetime | None


def rank_duty_candidates(
    candidates: Iterable[DutyCandidate],
    *,
    now: datetime,
    grace_days: int,
    exclude: Collection[int] = (),
    rng: random.Random | None = None,
) -> list[int]:
    """Возвращает user_id подходящих кандидатов в порядке очереди."""
    rng = rng or random.Random()
    grace = timedelta(days=grace_days)
    eligible = [
        c for c in candidates if c.user_id not in exclude and now - c.first_joined_at >= grace
    ]
    rng.shuffle(eligible)  # случайный порядок при равенстве (сортировка ниже стабильная)
    eligible.sort(key=lambda c: (c.last_duty_at is not None, c.last_duty_at or now))
    return [c.user_id for c in eligible]


def select_duty(
    candidates: Iterable[DutyCandidate],
    *,
    count: int,
    now: datetime,
    grace_days: int,
    exclude: Collection[int] = (),
    rng: random.Random | None = None,
) -> list[int]:
    """Первые `count` кандидатов очереди (может вернуть меньше, если кандидатов не хватает)."""
    ranked = rank_duty_candidates(
        candidates, now=now, grace_days=grace_days, exclude=exclude, rng=rng
    )
    return ranked[:count]
