"""Администрирование (ФТ-10).

В группе состава — только разовая привязка (/bind, /bind_topic): лишь оттуда бот узнаёт ID группы
и топика. Сообщение с командой удаляется, ответ уходит админу в личку, чтобы не засорять группу.
Всё остальное (роли, настройки, обзор) — в личке бота.
"""

import html
import json

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from pfbc_bot.bot.helpers import ask_squad, callback_message
from pfbc_bot.bot.keyboards.common import Choice, SquadPick
from pfbc_bot.bot.middlewares.access import to_telegram_user
from pfbc_bot.bot.states import SettingsEdit
from pfbc_bot.domain.dates import to_local
from pfbc_bot.domain.enums import ROLE_TITLES, STAFF_ROLES, SquadRole, TopicPurpose, UserStatus
from pfbc_bot.logging import get_logger
from pfbc_bot.schemas.payments import parse_details_list
from pfbc_bot.schemas.squads import SquadDTO, SquadMemberView, UserContext
from pfbc_bot.services.container import Services
from pfbc_bot.services.errors import DomainError
from pfbc_bot.services.membership import DISCOVERED_FIRST_JOINED_AT
from pfbc_bot.texts import ru

router = Router(name="admin")
log = get_logger(__name__)

in_group = F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP})
in_private = F.chat.type == ChatType.PRIVATE
ROLES_FLOW, SETTINGS_SHOW_FLOW, SETTINGS_SET_FLOW = "roles", "settings_show", "settings_set"
ADMIN_ADD_FLOW = "admin_add"
MEMBERS_FLOW = "squad_members"
MESSAGE_LIMIT = 4000  # лимит сообщения Telegram — 4096 символов
BUTTONS_LIMIT = 90  # кнопок в одном сообщении (лимит Telegram — 100)


class RoleAction(CallbackData, prefix="rl"):
    squad_id: int
    user_id: int
    action: str  # "open" | "<role>-1" (выдать) | "<role>-0" (снять)


async def _admin_only(callback: CallbackQuery, ctx: UserContext) -> bool:
    """Кнопки админки — только админу (кнопку можно подделать в неофициальном клиенте)."""
    if ctx.is_admin:
        return True
    await callback.answer(ru.NO_PERMISSION, show_alert=True)
    return False


# --- разовая привязка в группе ---
@router.message(Command("bind"), in_group)
async def cmd_bind(
    message: Message, command: CommandObject, ctx: UserContext, services: Services
) -> None:
    if not command.args:
        await _reply_privately(message, ru.BIND_USAGE)
        return
    try:
        squad = await services.squads.bind_chat(
            ctx, message.chat.id, message.chat.title, command.args
        )
        if message.from_user is not None:  # привязавший админ — участник группы и состава
            await services.membership.on_join(
                message.chat.id, to_telegram_user(message.from_user), discovered=True
            )
        text = ru.BIND_DONE.format(
            chat=html.escape(message.chat.title or ""), squad=html.escape(squad.name)
        )
    except DomainError as exc:
        text = str(exc)
    await _reply_privately(message, text)


@router.message(Command("bind_topic"), in_group)
async def cmd_bind_topic(
    message: Message, command: CommandObject, ctx: UserContext, services: Services
) -> None:
    try:
        purpose = TopicPurpose((command.args or "").strip().lower())
    except ValueError:
        purposes = "|".join(p.value for p in TopicPurpose)
        await _reply_privately(message, ru.BIND_TOPIC_USAGE.format(purposes=purposes))
        return
    thread_id = message.message_thread_id if message.is_topic_message else None
    try:
        await services.squads.bind_topic(ctx, message.chat.id, purpose, thread_id)
        text = ru.BIND_TOPIC_DONE.format(
            chat=html.escape(message.chat.title or ""), purpose=purpose.value
        )
    except DomainError as exc:
        text = str(exc)
    await _reply_privately(message, text)


async def _reply_privately(message: Message, text: str) -> None:
    """Удаляет команду из группы и отвечает в личку; если личка закрыта — один ответ в группе."""
    try:
        await message.delete()
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        log.info("command_delete_failed", chat_id=message.chat.id, error=str(exc))
    if message.from_user is None or message.bot is None:
        return
    try:
        await message.bot.send_message(message.from_user.id, text)
    except (TelegramBadRequest, TelegramForbiddenError):
        await message.answer(f"{text}\n\n{ru.DM_UNAVAILABLE}")


def _missing_roles(members: list[SquadMemberView]) -> str:
    """Без капитана сообщения о дежурствах уйдут только админам, без тренера — предложения
    разбора и итоги сборов; админу стоит назначить роли."""
    present = {r for m in members for r in m.roles}
    missing = [ROLE_TITLES[r] for r in (SquadRole.COACH, SquadRole.CAPTAIN) if r not in present]
    return ru.SQUAD_MISSING_ROLES.format(roles=", ".join(missing)) if missing else ""


# --- обзор составов ---
@router.message(Command("squads"), in_private)
async def cmd_squads(message: Message, ctx: UserContext, services: Services) -> None:
    if not ctx.is_admin:
        await message.answer(ru.NO_PERMISSION)
        return
    overview = await services.squads.overview()
    if not overview:
        await message.answer(ru.SQUADS_EMPTY)
        return
    lines = []
    builder = InlineKeyboardBuilder()
    for item in overview:
        members = await services.squads.members(item.squad.id)
        staff = [
            f"{m.user.profile_html} ({_roles_text(m.roles & STAFF_ROLES)})"
            for m in members
            if m.roles & STAFF_ROLES
        ]
        lines.append(
            f"<b>{html.escape(item.squad.name)}</b> — участников: {item.members}, "
            f"группа: {html.escape(item.chat_title or '—')}\n"
            f"  {'; '.join(staff) or 'тренер и капитан не назначены'}" + _missing_roles(members)
        )
        builder.button(
            text=ru.SQUAD_MEMBERS_BTN.format(squad=item.squad.name)[:60],
            callback_data=SquadPick(flow=MEMBERS_FLOW, squad_id=item.squad.id),
        )
    builder.adjust(1)
    await message.answer("\n\n".join(lines), reply_markup=builder.as_markup())


@router.callback_query(SquadPick.filter(F.flow == MEMBERS_FLOW))
async def on_squad_members(
    callback: CallbackQuery, callback_data: SquadPick, ctx: UserContext, services: Services
) -> None:
    """Список участников состава: тренеры и капитаны первыми, затем по имени."""
    if not await _admin_only(callback, ctx):
        return
    await callback.answer()
    message = callback_message(callback)
    if message is None:
        return
    squad = await services.squads.get(callback_data.squad_id)
    members = await services.squads.members(squad.id)
    name = html.escape(squad.name)
    if not members:
        await message.answer(ru.SQUAD_MEMBERS_EMPTY.format(squad=name))
        return
    members.sort(key=lambda m: (not m.roles & STAFF_ROLES, m.user.display_name.lower()))
    lines = [ru.SQUAD_MEMBERS_HEADER.format(squad=name, count=len(members))]
    lines += [
        f"{i}. {_member_line(m, services.admins.is_admin_user(m.user))}"
        for i, m in enumerate(members, 1)
    ]
    for chunk in _chunks(lines):
        await message.answer(chunk)
    # карточка участника — по кнопке (та же, что в /roles)
    for start in range(0, len(members), BUTTONS_LIMIT):
        await message.answer(
            ru.SQUAD_MEMBERS_OPEN_CARD,
            reply_markup=_member_buttons(members[start : start + BUTTONS_LIMIT], squad),
        )


def _member_line(view: SquadMemberView, is_admin: bool) -> str:
    marks = [_roles_text(view.roles & STAFF_ROLES)]
    if is_admin:
        marks.append(ru.SQUAD_MEMBER_ADMIN)
    if view.user.status == UserStatus.UNREGISTERED:
        marks.append(ru.SQUAD_MEMBER_UNREGISTERED)
    extra = " · ".join(m for m in marks if m)
    return view.user.profile_html + (f" — {extra}" if extra else "")


def _chunks(lines: list[str]) -> list[str]:
    """Строки в сообщения не длиннее лимита Telegram (длинный состав — несколько сообщений)."""
    chunks: list[str] = []
    current = ""
    for line in lines:
        if current and len(current) + len(line) + 1 > MESSAGE_LIMIT:
            chunks.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    return [*chunks, current] if current else chunks


# --- роли ---
@router.message(Command("roles"), in_private)
async def cmd_roles(message: Message, ctx: UserContext, services: Services) -> None:
    if not ctx.is_admin:
        await message.answer(ru.NO_PERMISSION)
        return
    if squad := await ask_squad(message, ctx, services, ROLES_FLOW):
        await _show_members(message, squad, services)


@router.callback_query(SquadPick.filter(F.flow == ROLES_FLOW))
async def on_roles_squad(
    callback: CallbackQuery, callback_data: SquadPick, ctx: UserContext, services: Services
) -> None:
    if not await _admin_only(callback, ctx):
        return
    await callback.answer()
    if message := callback_message(callback):
        await _show_members(message, await services.squads.get(callback_data.squad_id), services)


async def _show_members(message: Message, squad: SquadDTO, services: Services) -> None:
    members = await services.squads.members(squad.id)
    if not members:
        await message.answer(ru.ROLES_EMPTY)
        return
    await message.answer(
        ru.ROLES_HEADER.format(squad=html.escape(squad.name)),
        reply_markup=_member_buttons(members, squad),
    )


def _member_buttons(members: list[SquadMemberView], squad: SquadDTO) -> InlineKeyboardMarkup:
    """По кнопке на участника — открывает его карточку."""
    builder = InlineKeyboardBuilder()
    for m in members:
        staff = m.roles & STAFF_ROLES
        label = m.user.display_name + (f" · {_roles_text(staff)}" if staff else "")
        builder.button(
            text=label[:60],
            callback_data=RoleAction(squad_id=squad.id, user_id=m.user.id, action="open"),
        )
    builder.adjust(1)
    return builder.as_markup()


@router.callback_query(RoleAction.filter())
async def on_role_action(
    callback: CallbackQuery, callback_data: RoleAction, ctx: UserContext, services: Services
) -> None:
    if not await _admin_only(callback, ctx):
        return
    message = callback_message(callback)
    squad = await services.squads.get(callback_data.squad_id)
    if callback_data.action == "open":
        members = await services.squads.members(squad.id)
        view = next((m for m in members if m.user.id == callback_data.user_id), None)
        if view is None:
            await callback.answer(ru.MEMBER_NOT_FOUND, show_alert=True)
            return
        await callback.answer()
        if message is not None:
            await message.answer(
                _role_card(view, squad, services.admins.is_admin_user(view.user)),
                reply_markup=_role_keyboard(view, squad),
            )
        return

    role_value, _, grant = callback_data.action.partition("-")
    view = await services.squads.set_role(
        ctx, squad.id, callback_data.user_id, SquadRole(role_value), grant=grant == "1"
    )
    await callback.answer(ru.PROFILE_UPDATED)
    if message is not None:
        await message.edit_text(
            _role_card(view, squad, services.admins.is_admin_user(view.user)),
            reply_markup=_role_keyboard(view, squad),
        )


def _role_card(view: SquadMemberView, squad: SquadDTO, is_admin: bool) -> str:
    """Карточка участника для админа: роли (и админ бота) и все данные профиля."""
    user = view.user
    roles = _roles_text(view.roles) or ROLE_TITLES[SquadRole.PLAYER]
    lines = [
        ru.ROLE_CARD.format(
            name=user.profile_html,
            squad=html.escape(squad.name),
            roles=roles + (ru.CARD_BOT_ADMIN if is_admin else ""),
        ),
        ru.CARD_TELEGRAM.format(
            username=f"@{html.escape(user.username)}" if user.username else ru.CARD_NO_USERNAME,
            tg_id=user.tg_user_id,
        ),
        ru.CARD_REGISTERED.format(date=f"{user.pd_consent_at:%d.%m.%Y}")
        if user.pd_consent_at
        else ru.CARD_NOT_REGISTERED,
        ru.CARD_FIELD.format(title="ФИО", value=html.escape(user.full_name or ru.NOT_SET)),
        ru.CARD_FIELD.format(title="Телефон", value=user.phone or ru.NOT_SET),
        ru.CARD_FIELD.format(
            title="Дата рождения",
            value=f"{user.birth_date:%d.%m.%Y}" if user.birth_date else ru.NOT_SET,
        ),
        ru.CARD_FIELD.format(title="mtgame", value=html.escape(user.mtgame_url or ru.NOT_SET)),
        _joined_line(view, squad.timezone),
        _details_line(user.payment_details),
        ru.CARD_DM_OPEN if user.dm_available else ru.CARD_DM_CLOSED,
    ]
    if not user.username and not user.knows_bot:
        lines.append(ru.CARD_NO_LINK)
    return "\n".join(lines)


def _joined_line(view: SquadMemberView, tz: str) -> str:
    """Был в группе до подключения бота — дата вступления неизвестна (Telegram её не сообщает);
    в БД для дежурств у такого стоит заведомо старая дата — её не показываем."""
    membership = view.membership
    if membership.first_joined_at == DISCOVERED_FIRST_JOINED_AT:
        return ru.CARD_JOINED_UNKNOWN
    return ru.CARD_FIELD.format(
        title="В составе с", value=f"{to_local(membership.first_joined_at, tz):%d.%m.%Y}"
    )


def _details_line(raw: object) -> str:
    """Реквизиты для сборов — только просмотр: менять их может лишь сам участник."""
    details = parse_details_list(raw)
    if not details:
        return ru.CARD_FIELD.format(title="Реквизиты", value=ru.NOT_SET)
    return ru.CARD_DETAILS.format(details="\n".join(f"• {d.text}" for d in details))


def _role_keyboard(view: SquadMemberView, squad: SquadDTO) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for role in (SquadRole.CAPTAIN, SquadRole.COACH):
        has = role in view.roles
        builder.button(
            text=ru.ROLE_BTN.format(title=ROLE_TITLES[role].capitalize(), mark="✓" if has else "✗"),
            callback_data=RoleAction(
                squad_id=squad.id, user_id=view.user.id, action=f"{role.value}-{0 if has else 1}"
            ),
        )
    builder.adjust(2)
    return builder.as_markup()


def _roles_text(roles: frozenset[SquadRole]) -> str:
    return ", ".join(ROLE_TITLES[r] for r in sorted(roles))


# --- админы бота ---
class AdminAction(CallbackData, prefix="adm"):
    action: str  # "add" | "grant" | "revoke"
    user_id: int = 0


@router.message(Command("admins"), in_private)
async def cmd_admins(message: Message, ctx: UserContext, services: Services) -> None:
    await _show_admins(message, ctx, services)


async def _show_admins(message: Message, ctx: UserContext, services: Services) -> None:
    admins = await services.admins.list_admins(ctx)
    lines = [ru.ADMINS_HEADER]
    builder = InlineKeyboardBuilder()
    for admin in admins:
        name = admin.profile_html
        lines.append(
            (ru.ADMIN_LINE_CONFIG if admin.from_config else ru.ADMIN_LINE).format(name=name)
        )
        if not admin.from_config and admin.user is not None and admin.user.id != ctx.user.id:
            builder.button(
                text=ru.ADMIN_BTN_REVOKE.format(name=admin.display_name)[:60],
                callback_data=AdminAction(action="revoke", user_id=admin.user.id),
            )
    builder.button(text=ru.ADMIN_BTN_ADD, callback_data=AdminAction(action="add"))
    builder.adjust(1)
    await message.answer("\n".join(lines), reply_markup=builder.as_markup())


@router.callback_query(AdminAction.filter())
async def on_admin_action(
    callback: CallbackQuery, callback_data: AdminAction, ctx: UserContext, services: Services
) -> None:
    message = callback_message(callback)
    if callback_data.action == "add":
        await callback.answer()
        if message is not None and (
            squad := await ask_squad(message, ctx, services, ADMIN_ADD_FLOW)
        ):
            await _show_admin_candidates(message, squad, services)
        return
    if callback_data.action == "grant":
        user = await services.admins.grant(ctx, callback_data.user_id)
        text = ru.ADMIN_GRANTED.format(name=user.profile_html)
    else:
        user = await services.admins.revoke(ctx, callback_data.user_id)
        text = ru.ADMIN_REVOKED.format(name=user.profile_html)
    await callback.answer()
    if message is not None:
        await message.answer(text)
        await _show_admins(message, ctx, services)


@router.callback_query(SquadPick.filter(F.flow == ADMIN_ADD_FLOW))
async def on_admin_squad(
    callback: CallbackQuery, callback_data: SquadPick, ctx: UserContext, services: Services
) -> None:
    if not await _admin_only(callback, ctx):
        return
    await callback.answer()
    if message := callback_message(callback):
        squad = await services.squads.get(callback_data.squad_id)
        await _show_admin_candidates(message, squad, services)


async def _show_admin_candidates(message: Message, squad: SquadDTO, services: Services) -> None:
    candidates = [
        m
        for m in await services.squads.members(squad.id)
        if not services.admins.is_admin_user(m.user)
    ]
    if not candidates:
        await message.answer(ru.ADMIN_NO_CANDIDATES)
        return
    builder = InlineKeyboardBuilder()
    for m in candidates:
        builder.button(
            text=m.user.display_name[:60],
            callback_data=AdminAction(action="grant", user_id=m.user.id),
        )
    builder.adjust(1)
    await message.answer(
        ru.ADMIN_CHOOSE_MEMBER.format(squad=html.escape(squad.name)),
        reply_markup=builder.as_markup(),
    )


# --- настройки ---
@router.message(Command("settings"), in_private)
async def cmd_settings(message: Message, ctx: UserContext, services: Services) -> None:
    if not ctx.is_admin:
        await message.answer(ru.NO_PERMISSION)
        return
    builder = InlineKeyboardBuilder()
    for item in await services.squads.overview():
        builder.button(
            text=item.squad.name,
            callback_data=SquadPick(flow=SETTINGS_SHOW_FLOW, squad_id=item.squad.id),
        )
    builder.button(
        text=ru.SETTINGS_GLOBAL, callback_data=Choice(flow=SETTINGS_SHOW_FLOW, value="global")
    )
    builder.adjust(1)
    await message.answer(ru.SETTINGS_CHOOSE, reply_markup=builder.as_markup())


@router.callback_query(SquadPick.filter(F.flow == SETTINGS_SHOW_FLOW))
async def on_settings_squad(
    callback: CallbackQuery, callback_data: SquadPick, ctx: UserContext, services: Services
) -> None:
    await callback.answer()
    dump = (await services.settings.get_squad(ctx, callback_data.squad_id)).model_dump(mode="json")
    if message := callback_message(callback):
        await message.answer(_settings_text(dump))


@router.callback_query(Choice.filter((F.flow == SETTINGS_SHOW_FLOW) & (F.value == "global")))
async def on_settings_global(callback: CallbackQuery, ctx: UserContext, services: Services) -> None:
    await callback.answer()
    dump = (await services.settings.get_global(ctx)).model_dump(mode="json")
    if message := callback_message(callback):
        await message.answer(_settings_text(dump))


def _settings_text(dump: dict[str, object]) -> str:
    pretty = html.escape(json.dumps(dump, ensure_ascii=False, indent=2))
    return f"<pre>{pretty}</pre>\n{ru.SETTINGS_USAGE}"


@router.message(Command("set"), in_private)
async def cmd_set(
    message: Message,
    command: CommandObject,
    ctx: UserContext,
    services: Services,
    state: FSMContext,
) -> None:
    if not ctx.is_admin:
        await message.answer(ru.NO_PERMISSION)
        return
    key, _, value = (command.args or "").strip().partition(" ")
    value = value.strip()
    if not key or not value:
        await message.answer(ru.SETTINGS_USAGE)
        return
    if services.settings.is_global_key(key):
        await services.settings.set_global_value(ctx, key, value)
        await message.answer(_saved(key, value, ru.SETTINGS_GLOBAL))
        return
    if not services.settings.is_squad_key(key):
        await message.answer(ru.SETTINGS_UNKNOWN.format(key=html.escape(key)))
        return
    await state.set_state(SettingsEdit.squad)
    await state.update_data(key=key, value=value)
    if squad := await ask_squad(message, ctx, services, SETTINGS_SET_FLOW):
        await _apply_squad_setting(message, squad, ctx, services, state)


@router.callback_query(SettingsEdit.squad, SquadPick.filter(F.flow == SETTINGS_SET_FLOW))
async def on_set_squad(
    callback: CallbackQuery,
    callback_data: SquadPick,
    ctx: UserContext,
    services: Services,
    state: FSMContext,
) -> None:
    await callback.answer()
    if message := callback_message(callback):
        squad = await services.squads.get(callback_data.squad_id)
        await _apply_squad_setting(message, squad, ctx, services, state)


async def _apply_squad_setting(
    message: Message, squad: SquadDTO, ctx: UserContext, services: Services, state: FSMContext
) -> None:
    data = await state.get_data()
    await state.clear()
    key, value = str(data["key"]), str(data["value"])
    await services.settings.set_squad_value(ctx, squad.id, key, value)
    await message.answer(_saved(key, value, squad.name))


def _saved(key: str, value: str, scope: str) -> str:
    return ru.SETTINGS_SAVED.format(
        key=html.escape(key), value=html.escape(value), scope=html.escape(scope)
    )
