"""Сборка сервисов (composition root). Единственное место, где создаются зависимости."""

from collections.abc import Iterable
from dataclasses import dataclass

from pfbc_bot.repositories.uow import UowFactory
from pfbc_bot.services.access import AccessService
from pfbc_bot.services.admins import AdminService
from pfbc_bot.services.birthdays import BirthdayService
from pfbc_bot.services.duty import DutyService
from pfbc_bot.services.events import EventService
from pfbc_bot.services.membership import MembershipService
from pfbc_bot.services.polls import PollService
from pfbc_bot.services.ports import Notifier
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


def build_services(
    uow_factory: UowFactory, notifier: Notifier, admin_tg_ids: Iterable[int]
) -> Services:
    admin_ids = frozenset(admin_tg_ids)
    membership = MembershipService(uow_factory, notifier, admin_ids)
    duty = DutyService(uow_factory, notifier)
    return Services(
        access=AccessService(uow_factory, admin_ids),
        admins=AdminService(uow_factory, admin_ids),
        membership=membership,
        registration=RegistrationService(uow_factory, notifier),
        squads=SquadService(uow_factory),
        settings=SettingsService(uow_factory),
        trainings=TrainingService(uow_factory, notifier),
        duty=duty,
        polls=PollService(uow_factory, notifier, duty),
        birthdays=BirthdayService(uow_factory, notifier),
        events=EventService(uow_factory, notifier),
    )
