from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class ExternalGame:
    """Будущая игра команды из внешнего источника (mtgame). Дата и место появляются не сразу:
    сначала лига публикует пару соперников, время и зал — обычно за несколько дней."""

    external_id: str
    home: str  # хозяева — первыми в названии матча
    away: str
    opponent: str
    tournament: str
    starts_at: datetime | None = None
    venue: str | None = None
    tour: int | None = None
    stage: str | None = None  # стадия плей-офф («1 / 2», «Финал»); None — регулярный сезон
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return f"{self.home} — {self.away}"
