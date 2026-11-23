import pytest

from pfbc_bot.domain.postmatch import advance, answer_label
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


async def test_report_player_buttons_show_profile_names(monkeypatch) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from pfbc_bot.bot.routers import postmatch

    names = {1: "Иванов Сергей", 2: "Иванов Антон", 3: "Петров Андрей Ильич", 4: "Никита"}
    reports = SimpleNamespace(report_players=AsyncMock(return_value=names))
    services = SimpleNamespace(postmatch=SimpleNamespace(reports=reports))
    cb = SimpleNamespace(data="psm:9:players", answer=AsyncMock())
    monkeypatch.setattr(postmatch, "callback_message", lambda _: object())
    send = AsyncMock()
    monkeypatch.setattr(postmatch, "send_options", send)
    await postmatch.manage(cb, AsyncMock(), services, SimpleNamespace())
    assert send.await_args.args[2] == [(name, f"psm:9:period:{uid}") for uid, name in names.items()]


@pytest.mark.parametrize("code", ["more", "less", "satisfied"])
def test_playing_time_question(code: str) -> None:
    from pfbc_bot.domain.postmatch import PLAYING_TIME, PLAYING_TIME_KEY, form

    answers = {"1": "7", "4": "4"}
    assert advance(PLAYING_TIME, answers, code, None) == 1
    assert answers == {"1": "7", "4": "4", PLAYING_TIME_KEY: code}
    assert advance(1, answers, "back", None) == PLAYING_TIME
    assert "Вопрос 1 из 8" in form("token", PLAYING_TIME, answers)[0]
    assert "Вопрос 2 из 8" in form("token", 1, answers)[0]
    with pytest.raises(Conflict):
        advance(PLAYING_TIME, answers, "skip", None)


def test_form_format_and_review_labels() -> None:
    from pfbc_bot.domain.postmatch import REVIEW, form

    text, buttons = form("token", 5, {"5": ["shot"]})
    assert "<b>Вопрос 6 из 8</b>" in text
    assert buttons[0][0].text == "✅ Бросок"
    text, _ = form("token", REVIEW, {"1": "7", "4": "4", "7": "<tag> & текст"})
    assert "<b>💪 Нагрузка:</b> 7 из 10" in text
    assert "<b>⭐ Самооценка:</b> 4 из 5" in text
    assert "&lt;tag&gt; &amp; текст" in text


@pytest.mark.parametrize("error", [None, "message is not modified", "message to edit not found"])
async def test_multiselect_edits_existing_message(monkeypatch, error) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import EditMessageReplyMarkup

    from pfbc_bot.bot.routers import postmatch
    from pfbc_bot.domain.postmatch import form

    message = SimpleNamespace(edit_reply_markup=AsyncMock(), answer=AsyncMock())
    if error:
        message.edit_reply_markup.side_effect = TelegramBadRequest(
            method=EditMessageReplyMarkup(chat_id=1, message_id=1), message=error
        )
    monkeypatch.setattr(postmatch, "callback_message", lambda cb: message)
    result = (*form("token", 5, {"5": ["shot"]}), 5)
    services = SimpleNamespace(
        postmatch=SimpleNamespace(player_action=AsyncMock(return_value=result))
    )
    cb = SimpleNamespace(data="ps:token:5:shot", answer=AsyncMock())
    await postmatch.play(cb, AsyncMock(), services, SimpleNamespace())
    message.edit_reply_markup.assert_awaited_once()
    assert message.answer.await_count == (1 if error == "message to edit not found" else 0)


async def test_long_review_is_split_with_buttons_only_at_end() -> None:
    from html import unescape
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from pfbc_bot.bot.routers.postmatch import player_reply
    from pfbc_bot.domain.postmatch import REVIEW, form

    result = (
        *form("token", REVIEW, {"3": "pain", "pain_detail": "😀" * 1000, "7": "😀" * 1000}),
        REVIEW,
    )
    message = SimpleNamespace(answer=AsyncMock())
    await player_reply(message, AsyncMock(), "token", result)
    calls = message.answer.await_args_list
    assert len(calls) > 1
    assert all(len(unescape(call.args[0]).encode("utf-16-le")) // 2 <= 4096 for call in calls)
    assert all(call.kwargs["reply_markup"] is None for call in calls[:-1])
    assert calls[-1].kwargs["reply_markup"] is not None


@pytest.mark.parametrize("step", [7, 30])
async def test_text_question_remembers_sent_message(step) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from pfbc_bot.bot.routers.postmatch import player_reply
    from pfbc_bot.domain.postmatch import form

    sent = SimpleNamespace(chat=SimpleNamespace(id=41), message_id=123)
    message = SimpleNamespace(answer=AsyncMock(return_value=sent))
    state = AsyncMock()
    await player_reply(message, state, "token", (*form("token", step, {}), step))
    state.update_data.assert_any_await(token="token", step=step)
    state.update_data.assert_any_await(question_chat_id=41, question_message_id=123)


@pytest.mark.parametrize("scenario", ["accepted", "invalid", "legacy", "deleted"])
async def test_text_answer_removes_question_keyboard(scenario) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import EditMessageReplyMarkup

    from pfbc_bot.bot.routers.postmatch import free_text

    data = {"token": "token", "step": 7}
    if scenario != "legacy":
        data.update(question_chat_id=41, question_message_id=123)
    state = AsyncMock()
    state.get_data.return_value = data
    edit = AsyncMock()
    if scenario == "deleted":
        edit.side_effect = TelegramBadRequest(
            method=EditMessageReplyMarkup(chat_id=41, message_id=123),
            message="message to edit not found",
        )
    msg = SimpleNamespace(
        text="Комментарий", answer=AsyncMock(), bot=SimpleNamespace(edit_message_reply_markup=edit)
    )
    action = AsyncMock(return_value=("Проверьте ответы", None, 8))
    services = SimpleNamespace(postmatch=SimpleNamespace(player_action=action))
    if scenario == "invalid":
        action.side_effect = Conflict("Некорректный ответ")
        with pytest.raises(Conflict):
            await free_text(msg, state, services, SimpleNamespace())
        edit.assert_not_awaited()
        msg.answer.assert_not_awaited()
        state.clear.assert_not_awaited()
    else:
        await free_text(msg, state, services, SimpleNamespace())
        if scenario == "legacy":
            edit.assert_not_awaited()
        else:
            edit.assert_awaited_once_with(chat_id=41, message_id=123, reply_markup=None)
        msg.answer.assert_awaited_once()
