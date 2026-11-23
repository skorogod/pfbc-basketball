"""Диалог одной анкеты. Ответы в БД используют стабильные коды."""

from collections import Counter
from html import escape
from typing import Any

from pfbc_bot.services.errors import Conflict
from pfbc_bot.services.ports import InlineButton, Keyboard

PLAYING_TIME = 9
PLAYING_TIME_KEY = "playing_time_satisfaction"
PLAYING_TIME_OPTIONS = {
    "more": "Хочу играть больше",
    "less": "Хочу играть меньше",
    "satisfied": "Удовлетворён",
}
QUESTION_ORDER = (PLAYING_TIME, 1, 2, 3, 4, 5, 6, 7)

QUESTIONS = {
    PLAYING_TIME: "Удовлетворены ли вы сыгранным временем?",
    1: "Какой была физическая нагрузка? (1 — очень легко, 10 — предельная)",
    2: "Как вы себя чувствуете после игры?",
    3: "Испытываете боль или дискомфорт?",
    4: "Как оцениваете свою игру? (1 — очень плохо, 5 — отлично)",
    5: "Какие индивидуальные навыки хотите отработать?",
    6: "Какие командные элементы хотите отработать?",
    7: "Есть ли комментарий? Введите до 1000 символов.",
}
QUESTION_LABELS = {
    PLAYING_TIME: "⏳ Игровое время",
    1: "💪 Нагрузка",
    2: "🙂 Самочувствие",
    3: "🩹 Боль и дискомфорт",
    4: "⭐ Самооценка",
    5: "🏀 Индивидуальные навыки",
    6: "🤝 Командные элементы",
    7: "💬 Комментарий",
}

OPTIONS = {
    PLAYING_TIME: PLAYING_TIME_OPTIONS,
    1: {str(i): str(i) for i in range(1, 11)},
    2: {
        "great": "Отлично",
        "tired": "Умеренная усталость",
        "very_tired": "Сильно устал",
        "spent": "Вымотан",
    },
    3: {"no": "Нет", "fatigue": "Мышечная усталость", "pain": "Есть боль"},
    4: {str(i): str(i) for i in range(1, 6)},
    5: {
        "shot": "Бросок",
        "dribble": "Дриблинг",
        "pass": "Передачи",
        "defense": "Защита",
        "finish": "Завершение",
        "rebound": "Подборы",
        "offball": "Движение без мяча",
    },
    6: {
        "spacing": "Расположение",
        "break": "Быстрый отрыв",
        "pick": "Пик-н-ролл",
        "help": "Подстраховка",
        "transition": "Возврат в защиту",
        "talk": "Коммуникация",
    },
}
STATUS = {
    "invited": "Приглашён",
    "started": "Заполняет",
    "submitted": "Ответил",
    "absent": "Не участвовал в матче",
    "declined": "Не будет проходить опрос",
    "removed": "Убран штабом",
}
WAITING = {"invited", "started"}
EXCLUDED = {"absent", "declined", "removed"}
PAIN_DETAIL = 30
REVIEW = 8


def player_labels(names: dict[int, str]) -> dict[int, str]:
    """Фамилия для выбора игрока и легенды графика; при совпадении — инициал."""
    parts = {uid: name.split() for uid, name in names.items()}
    counts = Counter(words[0].casefold() for words in parts.values() if words)
    labels = {}
    for uid, words in parts.items():
        surname = words[0] if words else str(uid)
        if counts[surname.casefold()] > 1 and len(words) > 1:
            surname += " " + words[1][0] + "."
        labels[uid] = surname
    return labels


def answer_label(step: int, answers: dict[str, Any]) -> str:
    value = answers.get(PLAYING_TIME_KEY if step == PLAYING_TIME else str(step))
    if isinstance(value, list):
        return ", ".join(OPTIONS[step].get(v, "Ничего") for v in value) or "Пропущено"
    if value is None:
        return "Нет данных" if step == PLAYING_TIME else "Пропущено"
    label = OPTIONS.get(step, {}).get(str(value), str(value))
    if step == 3 and answers.get("pain_detail"):
        label += " — " + answers["pain_detail"]
    return label


def invitation(token: str) -> Keyboard:
    return [
        [InlineButton(label, f"ps:{token}:0:{action}")]
        for label, action in (
            ("Ответить", "start"),
            ("Не участвовал в матче", "absent"),
            ("Не буду проходить опрос", "declined"),
        )
    ]


def form(token: str, step: int, answers: dict[str, Any]) -> tuple[str, Keyboard]:
    def button(label: str, action: str) -> InlineButton:
        return InlineButton(label, f"ps:{token}:{step}:{action}")

    rows = []
    if step == REVIEW:
        blocks = ["📋 <b>Проверьте ответы</b>"]
        for question in QUESTION_ORDER:
            answer = answer_label(question, answers)
            if question in (1, 4) and str(question) in answers:
                answer += " из " + ("10" if question == 1 else "5")
            blocks.append(f"<b>{QUESTION_LABELS[question]}:</b> {escape(answer)}")
        blocks.append("После отправки изменить ответы нельзя.")
        text = "\n\n".join(blocks)
        rows.append([button("✅ Отправить", "submit")])
    elif step == PAIN_DETAIL:
        text = "📋 <b>Вопрос 4 из 8</b>\n\n🩹 <b>Где ощущается боль?</b>\n\nМожно пропустить."
    else:
        emoji = QUESTION_LABELS[step].split()[0]
        text = (
            f"📋 <b>Вопрос {QUESTION_ORDER.index(step) + 1} из 8</b>\n\n"
            f"{emoji} <b>{QUESTIONS[step]}</b>"
        )
        if step in (5, 6):
            text += "\n\nВыберите несколько вариантов и нажмите «Готово»."
        elif step == 7:
            text += "\n\nМожно пропустить."
        else:
            text += "\n\nВыберите один вариант."
        buttons = []
        for code, label in OPTIONS.get(step, {}).items():
            if step in (5, 6) and code in answers.get(str(step), []):
                label = "✅ " + label
            buttons.append(button(label, code))
        width = 5 if step in (1, 4) else 1 if step == PLAYING_TIME else 2
        rows.extend(buttons[i : i + width] for i in range(0, len(buttons), width))
        if step in (5, 6):
            none = "✅ Ничего" if answers.get(str(step)) == ["none"] else "Ничего"
            rows.append([button(none, "none"), button("✅ Готово", "done")])
    if step in (5, 6, 7, PAIN_DETAIL):
        rows.append([button("⏭ Пропустить", "skip")])
    if step != PLAYING_TIME:
        rows.append([button("⬅️ Назад", "back")])
    return text, rows


def advance(step: int, answers: dict[str, Any], action: str, text: str | None) -> int:
    """Изменяет копию черновика. Возвращает следующий шаг."""
    if action == "back" and step != PLAYING_TIME:
        if step == 1:
            return PLAYING_TIME
        if step == PAIN_DETAIL:
            return 3
        if step == 4 and answers.get("3") == "pain":
            return PAIN_DETAIL
        return step - 1
    if step == PLAYING_TIME and action in PLAYING_TIME_OPTIONS:
        answers[PLAYING_TIME_KEY] = action
        return 1
    if step in (PAIN_DETAIL, 7) and action in ("text", "skip"):
        key = "pain_detail" if step == PAIN_DETAIL else "7"
        if action == "text":
            if not text or not 1 <= len(text.strip()) <= 1000:
                raise Conflict("Введите от 1 до 1000 символов.")
            answers[key] = text.strip()
        else:
            answers.pop(key, None)
        return 4 if step == PAIN_DETAIL else REVIEW
    if step in (5, 6):
        key = str(step)
        if action == "skip":
            answers.pop(key, None)
            return step + 1
        if action == "done" and answers.get(key):
            return step + 1
        if action == "none":
            answers[key] = ["none"]
            return step
        if action in OPTIONS[step]:
            selected = set(answers.get(key, [])) - {"none"}
            answers[key] = sorted(selected ^ {action})
            return step
    elif action in OPTIONS.get(step, {}):
        answers[str(step)] = action
        if step == 3:
            if action == "pain":
                return PAIN_DETAIL
            answers.pop("pain_detail", None)
        return step + 1
    raise Conflict("Выберите один из предложенных вариантов.")
