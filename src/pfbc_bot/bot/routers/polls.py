"""Ответы на опросы бота.

Доступ проверяет сам PollService (участник ли состава этого опроса), а не AccessMiddleware:
голос может прийти от участника, о котором бот ещё не знал, — его нужно найти, а не отбросить.
"""

from aiogram import Router
from aiogram.types import Poll, PollAnswer

from pfbc_bot.bot.middlewares.access import to_telegram_user
from pfbc_bot.services.container import Services

router = Router(name="polls")


@router.poll()
async def on_poll(poll: Poll, services: Services) -> None:
    """Состояние опроса бота. Закрыт — по сбору денег или квитанций больше не напоминаем."""
    if poll.is_closed:
        await services.polls.mark_closed(poll.id)


@router.poll_answer()
async def on_poll_answer(answer: PollAnswer, services: Services) -> None:
    if answer.user is None or answer.user.is_bot:
        return
    await services.polls.record_answer(
        answer.poll_id,
        answer.user.id,
        list(answer.option_ids),
        profile=to_telegram_user(answer.user),
    )
