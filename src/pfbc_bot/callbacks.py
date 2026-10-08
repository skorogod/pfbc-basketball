"""Формат callback_data кнопок — единое место для сервисов (создают кнопки) и роутеров (разбирают).

Формат: «<prefix>:<action>:<arg1>[:<arg2>]», не длиннее 64 байт (ограничение Telegram).
"""

from dataclasses import dataclass

# Все кнопки дежурств — только в личке бота. Действия, меняющие назначение, — в два шага:
# кнопка → вопрос «точно?» → подтверждение (…ok). Случайное нажатие ничего не меняет.
DUTY = "duty"
DUTY_REPLACE = "rep"  # duty:rep:<assignment_id> — капитан/тренер: заменить (спросить)
DUTY_REPLACE_OK = "repok"  # duty:repok:<assignment_id> — подтверждено
DUTY_MANUAL = "man"  # duty:man:<training_id> — список для ручного выбора
DUTY_PICK = "pick"  # duty:pick:<training_id>:<user_id> — назначить выбранного (спросить)
DUTY_PICK_OK = "pickok"  # duty:pickok:<training_id>:<user_id> — подтверждено
DUTY_DECLINE = "decl"  # duty:decl:<assignment_id> — дежурный: не могу (спросить)
DUTY_DECLINE_OK = "declok"  # duty:declok:<assignment_id> — подтверждено
# Исправление прошедшего дежурства (экстренная замена, о которой бот не знал):
DUTY_FIX = "fix"  # duty:fix:<assignment_id> — список: кто дежурил на самом деле
DUTY_FIX_PICK = "fixp"  # duty:fixp:<assignment_id>:<user_id> — записать на выбранного (спросить)
DUTY_FIX_OK = "fixok"  # duty:fixok:<assignment_id>:<user_id> — подтверждено
DUTY_CANCEL = "no"  # duty:no — «Отмена» в вопросе подтверждения


@dataclass(frozen=True, slots=True)
class ParsedCallback:
    prefix: str
    action: str
    args: tuple[int, ...]


def pack(prefix: str, action: str, *args: int) -> str:
    return ":".join([prefix, action, *(str(a) for a in args)])


def parse(data: str) -> ParsedCallback:
    prefix, action, *rest = data.split(":")
    return ParsedCallback(prefix=prefix, action=action, args=tuple(int(x) for x in rest))
