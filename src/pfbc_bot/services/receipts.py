"""Квитанции об оплате тренировок за месяц: опрос «Отправил» в группе, напоминания после срока.

Раз в месяц (в receipt_poll_day, receipt_poll_time по времени состава) или по команде /receipts
бот публикует пост «пришлите квитанцию тренеру» и опрос «Отправил / Отправлю позже» — в топике
«Оплата», а если он не назначен, — в общем. Опрос за месяц — один на состав: если его запустили
вручную, по расписанию в этом месяце он не публикуется (это гарантирует и БД).
После срока каждому, кто не отметил «Отправил», — личное сообщение, не дошедших — упоминание в
группе; сколько раз и с каким интервалом — настройки состава, с 00:00 до 08:00 не напоминает.
Платят все участники состава, кроме тренеров.
"""

from collections.abc import Callable
from datetime import date, datetime, timedelta

from sqlalchemy.exc import IntegrityError

from pfbc_bot.db.models import Poll, ReceiptRequest, User
from pfbc_bot.domain.dates import (
    due_payment_reminder,
    format_local,
    local_to_utc,
    to_local,
    utcnow,
)
from pfbc_bot.domain.enums import (
    RECEIPT_POLL_OPTIONS,
    RECEIPT_SENT,
    MembershipStatus,
    PollType,
    SquadRole,
    TopicPurpose,
)
from pfbc_bot.domain.permissions import Permission
from pfbc_bot.logging import get_logger
from pfbc_bot.repositories.uow import UnitOfWork, UowFactory
from pfbc_bot.schemas.settings import MAX_PAYMENT_REMINDER_INTERVAL_HOURS, MAX_PAYMENT_REMINDERS
from pfbc_bot.schemas.squads import UserContext
from pfbc_bot.services.common import (
    mention,
    profile,
    resolve_destination,
    send_direct,
    send_to_squad,
    squad_tz,
)
from pfbc_bot.services.errors import Conflict, NotFound
from pfbc_bot.services.permissions import require
from pfbc_bot.services.ports import Notifier
from pfbc_bot.services.settings import load_squad_settings
from pfbc_bot.texts import ru

log = get_logger(__name__)

RECEIPT_REF = "receipt_request"
# бот был выключен в день опроса — публикуем в течение суток, позже уже нет
PUBLISH_GRACE = timedelta(days=1)
# напоминание давно пропущено (бот был выключен) — не догоняем
REMINDER_GRACE = timedelta(days=2)
# опросы, по которым ещё может быть напоминание: последнее — через (count − 1) × interval
REMINDER_LOOKBACK = (
    timedelta(hours=(MAX_PAYMENT_REMINDERS - 1) * MAX_PAYMENT_REMINDER_INTERVAL_HOURS)
    + REMINDER_GRACE
)


def reminder_key(request_id: int, number: int = 0) -> str:
    base = f"receipt:{request_id}"
    return base if number == 0 else f"{base}:{number}"


def month_of(moment: datetime, tz: str) -> date:
    """Первое число месяца, в который попадает момент (по времени состава)."""
    return to_local(moment, tz).date().replace(day=1)


def month_title(month: date) -> str:
    return f"{ru.MONTHS[month.month - 1]} {month.year}"


class ReceiptService:
    def __init__(self, uow_factory: UowFactory, notifier: Notifier) -> None:
        self._uow_factory = uow_factory
        self._notifier = notifier

    async def default_deadline(
        self, actor: UserContext, squad_id: int, *, now: datetime | None = None
    ) -> datetime:
        """Срок по умолчанию — через receipt_deadline_hours (сутки)."""
        require(actor, squad_id, Permission.RECEIPTS)
        async with self._uow_factory() as uow:
            settings = await load_squad_settings(uow, squad_id)
        return (now or utcnow()) + timedelta(hours=settings.receipt_deadline_hours)

    async def current(
        self, actor: UserContext, squad_id: int, *, now: datetime | None = None
    ) -> str | None:
        """Опрос этого месяца уже опубликован — текст с тем, кто отметился; None — ещё нет."""
        require(actor, squad_id, Permission.RECEIPTS)
        async with self._uow_factory() as uow:
            tz = squad_tz(await uow.squads.get(squad_id))
            request = await uow.receipts.for_month(squad_id, month_of(now or utcnow(), tz))
            return await self._already(uow, request, tz) if request else None

    async def create(
        self,
        actor: UserContext,
        squad_id: int,
        deadline_at: datetime,
        *,
        now: datetime | None = None,
    ) -> str:
        """Опрос по команде. Возвращает месяц («октябрь 2026»)."""
        require(actor, squad_id, Permission.RECEIPTS)
        now = now or utcnow()
        if deadline_at <= now:
            raise Conflict(ru.PAST_DATE)
        month = await self._publish(squad_id, deadline_at, now, created_by=actor.user.id)
        return month_title(month)

    async def publish_due(self, *, now: datetime | None = None) -> int:
        """Опрос по расписанию: в день receipt_poll_day с receipt_poll_time — если в этом месяце
        его ещё не было (в том числе запущенного вручную)."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            squad_ids = [s.id for s in await uow.squads.list_active()]
        published = 0
        for squad_id in squad_ids:
            async with self._uow_factory() as uow:
                settings = await load_squad_settings(uow, squad_id)
                if not settings.features.receipt_reminders:
                    continue
                tz = squad_tz(await uow.squads.get(squad_id))
                month = month_of(now, tz)
                poll_at = local_to_utc(
                    month.replace(day=settings.receipt_poll_day), settings.receipt_poll_time, tz
                )
                if not poll_at <= now < poll_at + PUBLISH_GRACE:
                    continue
                if await uow.receipts.for_month(squad_id, month) is not None:
                    continue
                if await uow.squads.get_chat_for_squad(squad_id) is None:
                    continue
                deadline = now + timedelta(hours=settings.receipt_deadline_hours)
            try:
                await self._publish(squad_id, deadline, now, created_by=None)
            except Conflict as exc:  # не отправилось или уже опубликовали — следующий тик решит
                log.info("receipt_poll_skipped", squad_id=squad_id, reason=str(exc))
                continue
            published += 1
        return published

    async def _publish(
        self, squad_id: int, deadline_at: datetime, now: datetime, *, created_by: int | None
    ) -> date:
        """Пост и опрос в группе, затем запись. Отправка не держит транзакцию; если за это
        время опрос месяца уже появился (ручной и плановый одновременно), наш убирается."""
        async with self._uow_factory() as uow:
            squad = await uow.squads.get(squad_id)
            if squad is None:
                raise NotFound("Состав не найден")
            tz = squad_tz(squad)
            month = month_of(now, tz)
            existing = await uow.receipts.for_month(squad_id, month)
            if existing is not None:
                raise Conflict(await self._already(uow, existing, tz))
            destination = await resolve_destination(uow, squad_id, TopicPurpose.PAYMENTS)
            if destination is None:
                raise Conflict(ru.POLL_NO_CHAT)
            coaches = await _coaches(uow, squad_id)
            post = ru.RECEIPT_POST.format(
                month=month_title(month),
                coaches=_coaches_line(coaches, mention),
                deadline=format_local(deadline_at, tz),
            )
            question = ru.RECEIPT_POLL_QUESTION.format(month=month_title(month))

        post_id = await self._notifier.send_message(
            destination.chat_id, post, thread_id=destination.thread_id
        )
        if post_id is None:
            raise Conflict(ru.RECEIPT_NOT_SENT)
        sent = await self._notifier.send_poll(
            destination.chat_id, question, RECEIPT_POLL_OPTIONS, thread_id=destination.thread_id
        )
        if sent is None:  # пост без опроса в группе не оставляем
            await self._notifier.delete_message(destination.chat_id, post_id)
            raise Conflict(ru.RECEIPT_NOT_SENT)

        async with self._uow_factory() as uow:
            try:
                request = await uow.receipts.add(
                    ReceiptRequest(
                        squad_id=squad_id,
                        month=month,
                        deadline_at=deadline_at,
                        created_by_user_id=created_by,
                    )
                )
            except IntegrityError:
                await uow.rollback()
                await self._notifier.delete_message(destination.chat_id, sent.message_id)
                await self._notifier.delete_message(destination.chat_id, post_id)
                existing = await uow.receipts.for_month(squad_id, month)
                raise Conflict(await self._already(uow, existing, tz)) from None
            await uow.polls.add(
                Poll(
                    type=PollType.RECEIPT,
                    squad_id=squad_id,
                    ref_type=RECEIPT_REF,
                    ref_id=request.id,
                    tg_poll_id=sent.poll_id,
                    chat_id=destination.chat_id,
                    message_id=sent.message_id,
                    question=question,
                    options=list(RECEIPT_POLL_OPTIONS),
                    closes_at=None,  # «Отправил» можно отметить и после срока
                )
            )
            await uow.system.audit(
                actor_user_id=created_by,
                action="receipt.create",
                entity="squad",
                entity_id=squad_id,
                payload={"request_id": request.id, "month": month.isoformat()},
            )
            await uow.commit()
        log.info("receipt_poll_published", squad_id=squad_id, month=month.isoformat())
        return month

    async def _already(self, uow: UnitOfWork, request: ReceiptRequest | None, tz: str) -> str:
        if request is None:  # запись исчезла (состав удалён) — просто «уже есть»
            return ru.RECEIPT_NOT_SENT
        payers, sent = await _payers_and_sent(uow, request)
        return ru.RECEIPT_ALREADY.format(
            month=month_title(request.month),
            when=format_local(request.created_at, tz),
            sent=len([u for u in payers.values() if u.id in sent]),
            total=len(payers),
            missing=", ".join(profile(u) for u in payers.values() if u.id not in sent) or "—",
        )

    async def remind_due(self, *, now: datetime | None = None) -> int:
        """Срок прошёл: не отметившим «Отправил» — в личку (не дошло — в группу). Сколько раз
        и с каким интервалом — настройки состава, не в тихие часы. Каждое напоминание — один
        раз; отметка — до рассылки. Итог тренеру не шлётся: он виден в опросе."""
        now = now or utcnow()
        async with self._uow_factory() as uow:
            due = [r.id for r in await uow.receipts.list_due(now - REMINDER_LOOKBACK, now)]
        reminded = 0
        for request_id in due:
            async with self._uow_factory() as uow:
                request = await uow.receipts.get(request_id)
                if request is None:
                    continue
                settings = await load_squad_settings(uow, request.squad_id)
                number = due_payment_reminder(
                    deadline_at=request.deadline_at,
                    tz=squad_tz(await uow.squads.get(request.squad_id)),
                    count=settings.receipt_reminder_count,
                    interval=timedelta(hours=settings.receipt_reminder_interval_hours),
                    now=now,
                    grace=REMINDER_GRACE,
                )
                if number is None:
                    continue
                key = reminder_key(request.id, number)
                if not await uow.system.claim_reminder(key, "receipt"):
                    continue
                await uow.commit()
                await self._remind(uow, request)
                await uow.commit()  # dm_available
                reminded += 1
        return reminded

    async def _remind(self, uow: UnitOfWork, request: ReceiptRequest) -> None:
        squad = await uow.squads.get(request.squad_id)
        squad_name = squad.name if squad else "?"
        month = month_title(request.month)
        coaches = await _coaches(uow, request.squad_id)
        payers, sent = await _payers_and_sent(uow, request)
        missing = [u for u in payers.values() if u.id not in sent]
        text = ru.RECEIPT_REMINDER_DM.format(
            month=month, squad=squad_name, coaches=_coaches_line(coaches, profile)
        )
        not_reached = [u for u in missing if not await send_direct(self._notifier, u, text)]
        if not_reached:
            await send_to_squad(
                uow,
                self._notifier,
                request.squad_id,
                TopicPurpose.PAYMENTS,
                ru.RECEIPT_REMINDER_GROUP.format(
                    names=", ".join(mention(u) for u in not_reached), month=month
                ),
            )
        log.info("receipt_reminded", request_id=request.id, missing=len(missing))


async def _coaches(uow: UnitOfWork, squad_id: int) -> list[User]:
    roles = await uow.members.roles_in_squad(squad_id)
    return [
        user
        for member, user in await uow.members.list_active_for_squad(squad_id)
        if member.status == MembershipStatus.ACTIVE and SquadRole.COACH in roles.get(user.id, set())
    ]


def _coaches_line(coaches: list[User], render: Callable[[User | None], str]) -> str:
    """«тренеру: Иван, Пётр» — пусто, если тренер не назначен."""
    if not coaches:
        return ""
    return ru.RECEIPT_COACHES.format(names=", ".join(render(u) for u in coaches))


async def _payers_and_sent(
    uow: UnitOfWork, request: ReceiptRequest
) -> tuple[dict[int, User], set[int]]:
    """Кто присылает квитанцию — активные участники состава, кроме тренеров; и кто из всех
    отметил «Отправил»."""
    roles = await uow.members.roles_in_squad(request.squad_id)
    payers = {
        user.id: user
        for member, user in await uow.members.list_active_for_squad(request.squad_id)
        if member.status == MembershipStatus.ACTIVE
        and SquadRole.COACH not in roles.get(user.id, set())
    }
    poll = await uow.polls.get_for_ref(PollType.RECEIPT, RECEIPT_REF, request.id)
    if poll is None:
        return payers, set()
    options = [str(o) for o in poll.options]
    if RECEIPT_SENT not in options:
        return payers, set()
    idx = options.index(RECEIPT_SENT)
    answers = await uow.polls.answers(poll.id)
    return payers, {uid for uid, chosen in answers.items() if idx in chosen}
