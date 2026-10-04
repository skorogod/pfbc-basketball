from aiogram import Router
from aiogram.types import PollAnswer

from pfbc_bot.services.container import Services

router = Router(name="polls")


@router.poll_answer()
async def on_poll_answer(answer: PollAnswer, services: Services) -> None:
    if answer.user is None:
        return
    await services.polls.record_answer(answer.poll_id, answer.user.id, list(answer.option_ids))
