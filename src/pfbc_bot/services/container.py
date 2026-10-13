"""Сборка сервисов (composition root). Единственное место, где создаются зависимости."""

from collections.abc import Iterable
from dataclasses import dataclass

from pfbc_bot.repositories.uow import UowFactory
from pfbc_bot.services.access import AccessService
from pfbc_bot.services.admins import AdminService
from pfbc_bot.services.birthdays import BirthdayService
from pfbc_bot.services.duty import DutyService
from pfbc_bot.services.events import EventService
from pfbc_bot.services.game_payments import GamePaymentService
from pfbc_bot.services.matches import MatchService
from pfbc_bot.services.membership import MembershipService
from pfbc_bot.services.permissions import PermissionService, Recipients
from pfbc_bot.services.polls import PollService
from pfbc_bot.services.ports import MatchSource, Notifier
from pfbc_bot.services.receipts import ReceiptService
from pfbc_bot.services.registration import RegistrationService
from pfbc_bot.services.settings import SettingsService
from pfbc_bot.services.squads import SquadService
from pfbc_bot.services.trainings import TrainingService


@dataclass(frozen=True, slots=True)
class Services:
    access: AccessService
    admins: AdminService
    membership: MembershipService
    registration: RegistrationService
    squads: SquadService
    settings: SettingsService
    trainings: TrainingService
    duty: DutyService
    polls: PollService
    birthdays: BirthdayService
    events: EventService
    matches: MatchService
    game_payments: GamePaymentService
    receipts: ReceiptService
    permissions: PermissionService


def build_services(
    uow_factory: UowFactory,
    notifier: Notifier,
    admin_tg_ids: Iterable[int],
    match_source: MatchSource | None = None,
) -> Services:
    admin_ids = frozenset(admin_tg_ids)
    recipients = Recipients(admin_ids)
    duty = DutyService(uow_factory, notifier, recipients)
    game_payments = GamePaymentService(uow_factory, notifier, recipients)
    receipts = ReceiptService(uow_factory, notifier)
    membership = MembershipService(uow_factory, notifier, admin_ids, duty=duty)
    events = EventService(uow_factory, notifier, recipients)
    return Services(
        access=AccessService(uow_factory, admin_ids),
        admins=AdminService(uow_factory, admin_ids),
        membership=membership,
        registration=RegistrationService(uow_factory, notifier),
        squads=SquadService(uow_factory, duty),
        settings=SettingsService(uow_factory),
        trainings=TrainingService(uow_factory, notifier),
        duty=duty,
        polls=PollService(uow_factory, notifier, duty, membership, game_payments, receipts),
        birthdays=BirthdayService(uow_factory, notifier),
        events=events,
        matches=MatchService(uow_factory, notifier, events, match_source, recipients, admin_ids),
        game_payments=game_payments,
        receipts=receipts,
        permissions=PermissionService(uow_factory),
    )
