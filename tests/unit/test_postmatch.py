import pytest

from pfbc_bot.domain.postmatch import advance, answer_label, player_labels
from pfbc_bot.services.errors import Conflict


def test_stable_selection_and_backtracking() -> None:
    answers = {}
    for action in ["shot", "pass", "shot", "none"]:
        assert advance(5, answers, action, None) == 5
    assert answers["5"] == ["none"]
    advance(5, answers, "pass", None)
    assert advance(5, answers, "done", None) == 6
    assert answer_label(5, answers) == "Передачи"
    assert advance(6, answers, "back", None) == 5
    advance(5, answers, "skip", None)
    assert "5" not in answers
    assert advance(3, answers, "pain", None) == 30
    assert advance(30, answers, "text", "Колено") == 4
    assert advance(4, answers, "back", None) == 30
    assert advance(30, answers, "back", None) == 3
    assert advance(3, answers, "no", None) == 4
    assert "pain_detail" not in answers


@pytest.mark.parametrize(
    ("step", "action", "text"),
    [
        (1, "0", None),
        (1, "11", None),
        (1, "skip", None),
        (2, "skip", None),
        (3, "skip", None),
        (4, "6", None),
        (4, "skip", None),
        (7, "text", "a" * 1001),
    ],
)
def test_invalid_input(step: int, action: str, text: str | None) -> None:
    with pytest.raises(Conflict):
        advance(step, {}, action, text)


def test_player_labels() -> None:
    assert player_labels({1: "Иванов Сергей", 2: "Иванов Антон", 3: "Петров Андрей"}) == {
        1: "Иванов С.",
        2: "Иванов А.",
        3: "Петров",
    }
