import random
from datetime import UTC, datetime, timedelta

from pfbc_bot.domain.duty import DutyCandidate, rank_duty_candidates, select_duty

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
LONG_AGO = NOW - timedelta(days=365)


def cand(
    user_id: int, *, joined_days_ago: int = 365, last_duty_days_ago: int | None = None
) -> DutyCandidate:
    return DutyCandidate(
        user_id=user_id,
        first_joined_at=NOW - timedelta(days=joined_days_ago),
        last_duty_at=None
        if last_duty_days_ago is None
        else NOW - timedelta(days=last_duty_days_ago),
    )


def test_oldest_last_duty_goes_first() -> None:
    candidates = [
        cand(1, last_duty_days_ago=3),
        cand(2, last_duty_days_ago=30),
        cand(3, last_duty_days_ago=10),
    ]
    assert select_duty(candidates, count=2, now=NOW, grace_days=14) == [2, 3]


def test_never_on_duty_goes_before_everyone() -> None:
    candidates = [cand(1, last_duty_days_ago=300), cand(2)]
    assert select_duty(candidates, count=1, now=NOW, grace_days=14) == [2]


def test_returning_player_is_first_regardless_of_duty_count() -> None:
    """Количество дежурств не учитывается: важна только давность последнего."""
    # игрок 1 дежурил много раз, но давно (долго не ходил); игрок 2 — один раз, но недавно
    candidates = [cand(1, last_duty_days_ago=120), cand(2, last_duty_days_ago=7)]
    assert select_duty(candidates, count=1, now=NOW, grace_days=14) == [1]


def test_newcomers_within_grace_period_are_excluded() -> None:
    candidates = [
        cand(1, joined_days_ago=13),
        cand(2, joined_days_ago=14),
        cand(3, joined_days_ago=1),
    ]
    assert select_duty(candidates, count=2, now=NOW, grace_days=14) == [2]


def test_excluded_users_are_skipped() -> None:
    candidates = [cand(1), cand(2, last_duty_days_ago=5), cand(3, last_duty_days_ago=1)]
    assert select_duty(candidates, count=2, now=NOW, grace_days=14, exclude={1}) == [2, 3]


def test_returns_fewer_when_not_enough_candidates() -> None:
    assert select_duty([cand(1)], count=2, now=NOW, grace_days=14) == [1]
    assert select_duty([], count=2, now=NOW, grace_days=14) == []


def test_ties_are_broken_randomly() -> None:
    candidates = [cand(i) for i in range(1, 6)]
    seen_first = {
        rank_duty_candidates(candidates, now=NOW, grace_days=14, rng=random.Random(seed))[0]
        for seed in range(50)
    }
    assert len(seen_first) > 1
