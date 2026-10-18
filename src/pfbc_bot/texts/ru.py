"""Тексты сообщений бота. Вынесены из кода — подготовка к редактированию и i18n.

Все тексты — HTML (parse_mode=HTML); подставляемые пользовательские данные экранируются
вызывающим кодом через html.escape.
"""

# --- доступ и онбординг ---
ACCESS_DENIED = (
    "⛔️ Бот доступен только участникам команд клуба PFBC.\n"
    "Если вы в команде — попросите тренера добавить вас в группу состава."
)
ACCESS_DENIED_CALLBACK = "Доступ только для участников команд PFBC"
NO_PERMISSION = "Недостаточно прав для этого действия."
NO_PERMISSION_FOR = (
    "Недостаточно прав: «{title}». Права ролей настраивает админ — обратитесь к нему."
)
UNEXPECTED_ERROR = (
    "Что-то пошло не так — действие не выполнено. Попробуйте ещё раз; если повторится, "
    "сообщите админу."
)
DM_UNAVAILABLE = "Не могу написать вам в личку: откройте бота и нажмите /start."


CLUB_INFO = (
    "🏀 <b>PFBC</b> — любительский баскетбольный клуб, участник и победитель московских "
    "чемпионатов лиги ABL в разных возрастах.\n\n"
    "Этот бот помогает команде: опросы на тренировки и игры, дежурства по залу, "
    "поздравления, напоминания о событиях и оплате."
)
REG_ASK_NAME = "Как вас зовут? Напишите <b>ФИО</b> (минимум — имя)."
REG_ASK_BIRTH = (
    "Дата рождения в формате <b>дд.мм.гггг</b> — чтобы команда могла поздравить вас.\n"
    "Можно пропустить."
)
REG_ASK_MTGAME = (
    'Ссылка на ваш профиль на <a href="https://mtgame.ru/">mtgame.ru</a>. Можно пропустить.'
)
REG_ASK_PHONE = (
    "Телефон, например <code>+79991234567</code> — чтобы тренер мог с вами связаться. "
    "Можно пропустить."
)
REG_ASK_CONSENT = (
    "Последний шаг: согласие на обработку персональных данных (ФИО, дата рождения, ссылка на "
    "профиль, телефон) для организации деятельности клуба. Данные можно удалить командой "
    "/delete_me."
)
REG_ASK_CONSENT_YOUTH = (
    REG_ASK_CONSENT
    + "\n\nВы состоите в детском составе: согласие должен дать <b>родитель или законный "
    "представитель</b>."
)
REG_DONE = "✅ Регистрация завершена. Спасибо, {name}!\nПрофиль: /profile"
REG_DECLINED = "Без согласия регистрация невозможна. Вы можете вернуться к ней командой /start."
REG_ALREADY = "Вы уже зарегистрированы. Профиль: /profile"
BTN_SKIP = "Пропустить"
BTN_CLEAR = "Очистить"
BTN_CONSENT = "✅ Согласен(на)"
BTN_CONSENT_GUARDIAN = "✅ Я законный представитель, согласен(на)"
BTN_DECLINE = "Отказаться"

PROFILE = (
    "<b>Ваш профиль</b>\n"
    "ФИО: {full_name}\n"
    "Дата рождения: {birth_date}\n"
    "mtgame: {mtgame}\n"
    "Телефон: {phone}\n"
    "Составы: {squads}{payment}"
)
PROFILE_PAYMENT = "\nРеквизиты для сбора денег: {details}"
PROFILE_BTN_PAYMENT = "💳 Реквизиты"
PROFILE_UPDATED = "Сохранено ✅"
DELETE_ME_CONFIRM = (
    "Удалить ваши персональные данные (ФИО, дату рождения, ссылку mtgame, телефон, "
    "реквизиты)? "
    "Членство в составах сохранится, но вы перестанете получать поздравления."
)
DELETE_ME_DONE = "Данные удалены. Зарегистрироваться снова можно командой /start."
NOT_SET = "—"

HELP_PLAYER = (
    "<b>Команды</b>\n"
    "/profile — профиль\n"
    "/events — ближайшие события\n"
    "/delete_me — удалить персональные данные"
)
HELP_FUNCTIONS = "\n\n<b>Ваши функции</b>"
# право → строки справки (ключ — код права из domain.permissions)
HELP_BY_PERMISSION: dict[str, str] = {
    "schedule": (
        "/schedule — расписание тренировок\n"
        "/trainings — ближайшие тренировки (отмена, перенос)\n"
        "/training_add — разовая тренировка"
    ),
    "training_poll": "/poll — опрос на тренировку (раньше времени или заново)",
    "attendance": "/attendance — кто придёт: тренировки, матчи, турниры, разборы",
    "duty": "/duty — дежурные: замена и ручной выбор",
    "meetings": ("/event_add — собрание команды\n/events — ближайшие события: перенос и отмена"),
    "events": (
        "/event_add — матч, турнир, разбор, сборы и другие события\n"
        "/events — ближайшие события: перенос и отмена\n"
        "/review — назначить разбор игры (опрос «Буду / Не смогу»)\n"
        "/tournaments — турниры: привязка матчей, стоимость игры"
    ),
    "game_payments": "/game_pay — сбор денег за игру: сумма, получатель, опрос «Оплатил»",
    "invite": "/invite — ссылка на регистрацию для новичка",
    "receipts": "/receipts — опрос «Прислали квитанцию за тренировки тренеру?» за месяц",
}
HELP_ADMIN = (
    "\n\n<b>Админ</b>\n"
    "/squads — составы\n"
    "/roles — назначить тренера или капитана\n"
    "/member — карточка участника: профиль, реквизиты\n"
    "/admins — админы бота\n"
    "/permissions — что могут тренер, капитан и игрок\n"
    "/settings, /set &lt;ключ&gt; &lt;значение&gt; — настройки\n"
    "В группе состава (разово): /bind &lt;название&gt;, "
    "/bind_topic &lt;назначение&gt; внутри топика"
)

# --- членство ---
STAFF_LEFT_ADMIN_NOTICE = "ℹ️ {name} покинул(а) группу состава <b>{squad}</b>. Сняты роли: {roles}."

# --- админка ---
BIND_USAGE = "Использование: /bind &lt;название состава&gt; — в группе состава."
BIND_DONE = "✅ Группа «{chat}» привязана к составу <b>{squad}</b>."
BIND_CONFLICT = "Эта группа уже привязана к составу <b>{squad}</b>."
BIND_SQUAD_TAKEN = "Состав <b>{squad}</b> уже привязан к другой группе."
BIND_TOPIC_USAGE = "Использование: /bind_topic {purposes} — внутри нужного топика."
BIND_TOPIC_DONE = "✅ Группа «{chat}»: топик назначен — <b>{purpose}</b>."
CHAT_NOT_BOUND = "Группа не привязана к составу. Админ: /bind &lt;название&gt;."
MEMBER_NOT_FOUND = "Участник не найден в составе."
ADMINS_HEADER = "<b>Админы бота</b>"
ADMIN_LINE_CONFIG = "• {name} — из настроек сервера (.env), снять нельзя"
ADMIN_LINE = "• {name}"
ADMIN_BTN_ADD = "➕ Назначить админа"
ADMIN_BTN_REVOKE = "Снять: {name}"
ADMIN_CHOOSE_MEMBER = "<b>{squad}</b> — кого назначить админом?"
ADMIN_NO_CANDIDATES = "В этом составе все участники уже админы."
ADMIN_GRANTED = "✅ {name} теперь админ."
ADMIN_REVOKED = "✅ {name} больше не админ."
ADMIN_ALREADY = "Этот пользователь уже админ."
ADMIN_NOT_FOUND = "Админ не найден."
ADMIN_NOT_MEMBER = "Админом можно назначить только участника состава."
ADMIN_FROM_CONFIG = (
    "Этот админ задан в настройках сервера (ADMIN_TG_IDS) — снять его можно только там."
)
ADMIN_SELF_REVOKE = "Нельзя снять права админа с самого себя — попросите другого админа."
ADMIN_LOST_MEMBERSHIP = "ℹ️ {name} больше не состоит ни в одном составе — права админа сняты."
ROLES_HEADER = "<b>Роли: {squad}</b>\nВыберите участника:"
ROLES_EMPTY = "В составе пока нет участников."
ROLE_CARD = "<b>{name}</b> — состав {squad}\nРоли: {roles}"
ROLE_BTN = "{title}: {mark}"
CARD_TELEGRAM = "Telegram: {username} (id <code>{tg_id}</code>)"
CARD_NO_USERNAME = "без username"
CARD_REGISTERED = "Регистрация: пройдена {date}"
CARD_NOT_REGISTERED = "Регистрация: ⚠️ не пройдена — данных профиля нет"
CARD_FIELD = "{title}: {value}"
CARD_JOINED_UNKNOWN = "В составе: до подключения бота"
CARD_BOT_ADMIN = " · админ бота"
CARD_DETAILS = "Реквизиты:\n{details}"
CARD_DM_OPEN = "Личка с ботом: открыта"
CARD_DM_CLOSED = "Личка с ботом: ⚠️ закрыта — бот не может написать (нужен /start)"
CARD_NO_LINK = (
    "ℹ️ Ссылки на профиль нет: у пользователя нет username и он не писал боту — Telegram "
    "в таком случае не даёт открыть профиль из лички. Написать ему можно из группы состава."
)
SETTINGS_USAGE = (
    "Использование: /set &lt;ключ&gt; &lt;значение&gt;, например "
    "<code>/set training_poll_time 08:30</code>. Текущие настройки: /settings"
)
SETTINGS_CHOOSE = "Чьи настройки показать?"
SETTINGS_GLOBAL = "🌐 Глобальные"
SETTINGS_UNKNOWN = "Неизвестная настройка: {key}. Список ключей: /settings"
SETTINGS_SAVED = "✅ Настройка {key} = {value} сохранена ({scope})."
SQUAD_MISSING_ROLES = "\n  ⚠️ Не назначен: {roles} — их сообщения получают админы (/roles)."
SQUAD_MEMBERS_BTN = "👥 Участники: {squad}"
SQUAD_MEMBERS_HEADER = "<b>Участники состава {squad}</b> — {count}:"
MEMBER_CHOOSE = "<b>{squad}</b> — чья карточка участника?"
SQUAD_MEMBERS_EMPTY = "В составе <b>{squad}</b> пока нет участников."
SQUAD_MEMBER_ADMIN = "админ"
SQUAD_MEMBER_UNREGISTERED = "⚠️ не зарегистрирован(а)"
SQUADS_EMPTY = (
    "Составов пока нет. Добавьте бота в группу состава и выполните там /bind &lt;название&gt;."
)
INVITE = (
    "Ссылка для регистрации в составе <b>{squad}</b> — перешлите её новичку:\n{link}\n\n"
    "Бот откроется только у тех, кто уже состоит в группе состава."
)

# --- тренировки ---
CHOOSE_SQUAD = "Выберите состав:"
CHOOSE_TRAINING = "Выберите тренировку:"
NO_SQUADS_FOR_ACTION = "Нет составов, где у вас есть права на это действие."
SCHEDULE_HEADER = "<b>Расписание: {squad}</b>"
SCHEDULE_EMPTY = "Регулярных тренировок нет."
SCHEDULE_ADD_PROMPT = (
    "Отправьте строку: <b>день время длительность зал</b>\n"
    "Например: <code>Пн 19:00 90 Зал школы №5</code>"
)
SCHEDULE_ADDED = "✅ Добавлено. Тренировки на ближайшие 2 недели созданы."
SCHEDULE_REMOVED = "✅ Удалено из расписания, будущие тренировки по нему отменены."
TRAININGS_HEADER = "<b>Ближайшие тренировки: {squad}</b>"
TRAININGS_EMPTY = "Ближайших тренировок нет."
TRAINING_CARD = "⛹️ <b>Тренировка</b>\n🕒 {when}\n📍 {venue}"
TRAINING_BTN_MOVE = "🔁 Перенести"
TRAINING_BTN_CANCEL = "❌ Отменить"
TRAINING_CONFIRM_MOVE = "Перенести тренировку {when}? Команда получит сообщение в группе."
TRAINING_BTN_CONFIRM_MOVE = "Да, перенести"
TRAINING_ADD_PROMPT = (
    "Отправьте: <b>дата время длительность зал</b>\n"
    "Например: <code>12.10.2026 19:00 90 Зал школы №5</code>"
)
TRAINING_MOVE_PROMPT = "Новые дата и время: <code>дд.мм.гггг чч:мм</code>"
TRAINING_ADDED = "✅ Тренировка добавлена: {when}, {venue}"
TRAINING_CANCELLED_GROUP = "❌ Тренировка {when} ({venue}) <b>отменена</b>."
TRAINING_MOVED_GROUP = "🔁 Тренировка перенесена: {old} → <b>{new}</b> ({venue})."
TRAINING_DONE = "Готово ✅"
TRAINING_ALREADY_STARTED = "Тренировка уже началась — отменить или перенести её нельзя."
TRAINING_CONFIRM_CANCEL = "Отменить тренировку {when}? Команда получит сообщение в группе."
TRAINING_BTN_CONFIRM_CANCEL = "Да, отменить"
SCHEDULE_CONFIRM_REMOVE = (
    "Удалить из расписания «{label}»? Будущие тренировки по нему будут отменены."
)
SCHEDULE_BTN_CONFIRM_REMOVE = "Да, удалить"
TRAINING_POLL_QUESTION = "🏀 Тренировка {when}, {venue}. Придёшь?"
ATTENDANCE = "<b>Тренировка {when}</b>\n{lines}\nНе ответили: {missing}"
ATTENDANCE_NO_POLL = "Опрос на эту тренировку ещё не публиковался."
POLL_CONFIRM_START = (
    "Опубликовать опрос на тренировку {when} сейчас? Бот сам его на эту тренировку "
    "уже не опубликует."
)
POLL_CONFIRM_RESTART = (
    "Опрос на тренировку {when} уже опубликован. Запустить новый? Старый будет закрыт, "
    "ответы в нём учитываться не будут — участникам нужно ответить заново."
)
POLL_BTN_CONFIRM = "Да, опубликовать"
POLL_STARTED = "✅ Опрос опубликован в группе."
POLL_TRAINING_STARTED = "Тренировка уже началась — опрос на неё запустить нельзя."
POLL_NO_CHAT = "Группа состава не привязана — опубликовать опрос некуда."
POLL_NOT_SENT = "Не удалось опубликовать опрос в группе. Попробуйте ещё раз."

# --- дежурства ---
DUTY_ANNOUNCE = (
    "🧹 Дежурные по залу на тренировку {when}:\n{names}\n"
    "Пожалуйста, приходите за {minutes} минут до начала и помойте паркет."
)
DUTY_NOBODY = "• уточняется — назначит капитан"
DUTY_ASSIGNED_DM = (
    "🧹 Вы дежурный по залу на тренировку <b>{when}</b> ({venue}){instead}.\n"
    "Приходите за {minutes} минут до начала и помойте паркет.\n\n"
    "Если не можете — нажмите кнопку ниже, бот назначит следующего по очереди."
)
DUTY_INSTEAD = " вместо {name}"
DUTY_REPLACED_GROUP = (
    "🔁 Замена дежурного на тренировку {when}: {old} → {new}.\n"
    "Пожалуйста, приходите за {minutes} минут до начала."
)
DUTY_REMOVED_GROUP = "🔁 {old} не дежурит на тренировке {when}. Замену назначит капитан."
DUTY_PICKED_GROUP = (
    "🧹 Дежурный на тренировку {when}: {new}.\nПожалуйста, приходите за {minutes} минут до начала."
)
DUTY_NOT_NOTIFIED_MARK = " ⚠️ не получил(а) уведомление"
DUTY_NOT_NOTIFIED_CAPTAIN = (
    "\n⚠️ {name} не получил(а) уведомление (личка закрыта, в группу отправить не удалось) — "
    "предупредите лично."
)
DUTY_MOVED_DM = (
    "ℹ️ Тренировка {old} перенесена на <b>{new}</b> — ваше дежурство снято. "
    "Дежурных назначат заново по опросу на новое время."
)
DUTY_UNASSIGNED_DM = "ℹ️ Вы больше не дежурный на тренировку {when}: вас заменил(а) {name}."
DUTY_STAFF_DM = "🧹 <b>Дежурные на тренировку {when}</b> ({squad}):\n{names}"
DUTY_MANAGE_EMPTY = (
    "Ближайших тренировок с назначенными дежурными нет. "
    "Дежурные назначаются автоматически за {hours} ч до начала тренировки."
)
DUTY_SHORTAGE_CAPTAIN = (
    "⚠️ На тренировку {when} ({squad}) не хватает дежурных: назначено {assigned} из {need}.\n"
    "Выберите вручную:"
)
DUTY_NO_REPLACEMENT = "Свободных по очереди нет — выберите вручную:"
DUTY_AUTO_REPLACED_CAPTAIN = (
    "ℹ️ {old} ответил(а) «не приду» на тренировку {when} — дежурный заменён на {new}."
)
DUTY_LEFT_CAPTAIN = "ℹ️ {old} вышел(а) из группы — дежурный на тренировку {when} заменён на {new}."
DUTY_COACH_CAPTAIN = (
    "ℹ️ {old} теперь тренер и не дежурит — дежурный на тренировку {when} заменён на {new}."
)
DUTY_SELF_DECLINED_CAPTAIN = (
    "ℹ️ {old} не может дежурить на тренировке {when} — дежурный заменён на {new}."
)
DUTY_PICKED = "✅ Назначен(а): {name}"
DUTY_BTN_REPLACE = "🔁 Заменить: {name}"
DUTY_BTN_MANUAL = "✋ Выбрать вручную"
DUTY_BTN_DECLINE = "🙅 Не могу дежурить"
DUTY_BTN_CONFIRM_REPLACE = "Да, заменить"
DUTY_BTN_CONFIRM_PICK = "Да, назначить"
DUTY_BTN_CONFIRM_DECLINE = "Да, не могу"
DUTY_CONFIRM_REPLACE = "Заменить дежурного {old} на тренировке {when}?\n{next}"
DUTY_CONFIRM_NEXT = "Следующий по очереди: <b>{name}</b>."
DUTY_CONFIRM_NO_NEXT = "Свободных по очереди нет — после замены выберите дежурного вручную."
DUTY_CONFIRM_PICK = "Назначить <b>{name}</b> дежурным на тренировку {when}?"
DUTY_CONFIRM_DECLINE = (
    "Отказаться от дежурства на тренировке {when}? Бот назначит следующего по очереди, "
    "капитан получит уведомление."
)
DUTY_DECLINED = "Готово: вы больше не дежурный. Спасибо, что предупредили!"
DUTY_PICK_PROMPT = "Выберите дежурного:"
DUTY_MANUAL_SENT = "Список для выбора отправлен вам в личные сообщения."
DUTY_ALREADY_CHANGED = "Это назначение уже изменено."
DUTY_ALREADY_ASSIGNED = "Этот участник уже назначен дежурным."
DUTY_COACH_NOT_ALLOWED = "Тренер не дежурит — выберите другого участника."
DUTY_TRAINING_PASSED = "Тренировка уже началась или отменена."
DUTY_NOT_YOURS = "Это не ваше дежурство."
DUTY_PAST_DM = (
    "🧹 <b>Дежурили на тренировке {when}</b> ({squad}):\n{names}\n"
    "Если дежурил(а) кто-то другой — исправьте запись."
)
DUTY_BTN_FIX = "✏️ Исправить: {name}"
DUTY_FIX_PICK_PROMPT = "Кто дежурил на тренировке {when} вместо {old}?"
DUTY_CONFIRM_FIX = "Записать дежурство на тренировке {when} на <b>{new}</b> вместо {old}?"
DUTY_BTN_CONFIRM_FIX = "Да, записать"
DUTY_FIXED = "✅ Дежурство записано на {name}."
DUTY_FIX_UNAVAILABLE = (
    "Исправить можно только дежурство на состоявшейся тренировке за последние {days} дн."
)

# --- дни рождения ---
BIRTHDAY_DEFAULT_TEMPLATES: tuple[str, ...] = (
    "🎉 Сегодня день рождения у {name}! Поздравляем — побед, здоровья и точных бросков! 🏀",
    "🎂 {name}, с днём рождения! Пусть каждый матч приносит радость, а мяч летит точно в цель!",
    "🥳 Команда поздравляет {name} с днём рождения! Отличного настроения и ярких игр!",
)

BIRTHDAY_STAFF_HEADER = "🎂 Скоро дни рождения в составе <b>{squad}</b>:"
BIRTHDAY_STAFF_LINE = "• {name} — {date} ({when})"
BIRTHDAY_IN_DAYS = {1: "завтра"}
BIRTHDAY_IN_N_DAYS = "через {days} дн."

# --- журнал событий ---
EVENT_ASK_TYPE = "Тип события:"
EVENT_ASK_TITLE = "Название события:"
EVENT_ASK_WHEN = "Дата и время начала: <code>дд.мм.гггг чч:мм</code>"
EVENT_ASK_DURATION = "Длительность в минутах (например, 120) или выберите кнопкой:"
EVENT_BAD_DURATION = "Длительность — целое число минут от 15 до 1440."
EVENT_ASK_PLACE = "Место (или «Пропустить»):"
EVENT_ASK_DESCRIPTION = "Описание (или «Пропустить»):"
EVENT_ASK_PUBLISH = (
    "Опубликовать событие в группе состава? Пост появится в соответствующем топике, "
    "туда же будут приходить напоминания для команды. К матчу, турниру и разбору игры "
    "добавится опрос участия."
)
EVENT_CONFLICT_HEADER = "⚠️ Время пересекается с уже запланированным:"
EVENT_CONFLICT_LINE = "• {title} — {when}"
EVENT_CONFLICT_QUESTION = "Всё равно продолжить?"
BTN_CONTINUE = "Продолжить"
BTN_CREATE_ANYWAY = "Создать всё равно"
BTN_CHANGE_TIME = "Изменить время"
CONFLICT_TRAINING = "Тренировка, {venue}"
CONFLICT_MATCH = "Матч с {opponent}"
CONFLICT_EVENT = "{type}: {title}"
EVENT_CREATED = "✅ Событие добавлено:\n\n{event}"
EVENT_PUBLISHED = "Опубликовано в группе состава."
EVENT_PUBLISHED_WITH_POLL = "Опубликовано в группе состава вместе с опросом участия."
EVENT_MOVED_GROUP = (
    "🔁 <b>Перенесено — {type}: {title}</b>\n🕒 {old} → <b>{new}</b>{place}{tournament}"
)
EVENT_PLACE_CHANGED = "📍 <b>Новое место — {type}: {title}</b>\n🕒 {when}\n📍 <b>{place}</b>"
EVENT_MOVE_PAST = "Перенести можно только будущее событие и только на будущее время."
EVENT_BTN_MOVE = "🔁 Перенести"
EVENT_BTN_CANCEL = "❌ Отменить"
EVENT_CONFIRM_MOVE = (
    "Перенести {type}: <b>{title}</b>, {when}?\n"
    "Если событие публиковалось в группе, команда получит сообщение о переносе, а опрос участия "
    "будет опубликован заново."
)
EVENT_CONFIRM_CANCEL = (
    "Отменить {type}: <b>{title}</b>, {when}?\n"
    "Если событие публиковалось в группе, команда получит сообщение об отмене."
)
EVENT_BTN_CONFIRM_MOVE = "Да, перенести"
EVENT_BTN_CONFIRM_CANCEL = "Да, отменить"
EVENT_MOVE_ASK_WHEN = "Новые дата и время: <code>дд.мм.гггг чч:мм</code>"
EVENT_MOVE_ASK_DATE = "Новая дата старта турнира: <code>дд.мм.гггг</code>"
EVENT_MOVED = "✅ Перенесено: {when}"
EVENT_NOT_PUBLISHED = (
    "⚠️ В группу опубликовать не удалось: проверьте, что бот состоит в группе состава и "
    "топик существует. Событие сохранено — его видно в /events."
)
EVENT_PUBLISHED_POLL_FAILED = (
    "⚠️ Пост опубликован, а опрос участия — нет (Telegram не принял). Событие сохранено."
)
EVENT_DELETED = "✅ Событие отменено."
EVENTS_HEADER = "<b>Ближайшие события</b>"
EVENTS_EMPTY = "Ближайших событий нет."
# карточка события в списках (/events, «событие добавлено», «матч прошёл») — как пост о нём
EVENT_CARD = "{emoji} <b>{type}: {title}</b>\n🕒 {when}{place}{tournament}{squad}"
EVENT_POLL_QUESTION = "{type}: {title}{tournament}, {when}. Будешь?"
TOURNAMENT_POLL_QUESTION = "🏆 Турнир: {title}, {when}. Участвуешь?"
EVENT_ANNOUNCE = "{emoji} <b>{type}: {title}</b>\n🕒 {when}{place}{tournament}{description}"
EVENT_CANCELLED = "❌ <b>Отменено — {type}: {title}</b>\n🕒 {when}{tournament}"
EVENT_REMINDER = "⏰ Напоминание{when_left}: <b>{title}</b>\n{when}{place}{tournament}{description}"
EVENT_TOURNAMENT = "🏆 Турнир: {title}"
EVENT_POLL_TOURNAMENT = " (турнир «{title}»)"
PERIOD_SEASON = "сезон, дата старта уточняется"
PERIOD_SEASON_FROM = "сезон, с {start}"
PERIOD_DAYS = "{start} – {end} ({days} дн.)"
PERIOD_DAYS_TBD = "дата уточняется, {days} дн."
EVENT_IN_DAYS = {0: " — сегодня", 1: " — завтра"}
EVENT_IN_N_DAYS = " — через {days} дн."
EVENT_ASK_WHEN_REVIEW = (
    "Дата и время разбора: <code>дд.мм.гггг чч:мм</code> — или время по умолчанию кнопкой "
    "(за час до первой тренировки недели)."
)
EVENT_ASK_WHEN_REVIEW_NO_DEFAULT = (
    "Дата и время разбора: <code>дд.мм.гггг чч:мм</code>\n"
    "(Ближайших тренировок нет — время по умолчанию предложить не могу.)"
)
EVENT_BTN_REVIEW_DEFAULT = "🗓 {when} — за час до тренировки"
REVIEW_ASK_TITLE = "Матч или соперник (например, PFBC — Динамо):"
TOURNAMENT_ASK_START = (
    "Дата старта турнира: <code>дд.мм.гггг</code> — или кнопка, если она пока неизвестна."
)
MATCH_ASK_TOURNAMENT = "В рамках какого турнира матч?"
MATCH_BTN_NO_TOURNAMENT = "Без турнира"
TOURNAMENT_BTN_START_UNKNOWN = "Дата пока неизвестна"
TOURNAMENT_ASK_LENGTH = "Сколько дней длится турнир? Введите число — или «Сезон» для лиги (ABL)."
TOURNAMENT_BTN_SEASON = "🏆 Сезон"
TOURNAMENT_BAD_LENGTH = "Число дней — от 1 до 366, или кнопка «Сезон»."
EVENT_ATTENDANCE = "<b>{type}: {title}</b>, {when}\n{lines}\nНе ответили: {missing}"
EVENT_NO_POLL = "Опрос по этому событию не публиковался."
CHOOSE_TRAINING_OR_EVENT = "Чью сводку ответов показать?"
ATTENDANCE_NO_POLL_MARK = " · опроса ещё нет"
ATTENDANCE_TRAININGS = "🏀 <b>Тренировки</b> — чью сводку показать?"
ATTENDANCE_EVENTS = "🗓 <b>Игры и события</b> — чью сводку показать?"
EVENT_SUMMARY = "Проверьте: <b>{type}: {title}</b>\n🕒 {when}"
ATTENDANCE_NOTHING = "Нет ближайших тренировок и событий с опросом."

# --- турниры ---
TOURNAMENTS_HEADER = "<b>Турниры</b> — выберите, чтобы привязать матчи:"
TOURNAMENTS_EMPTY = "Турниров нет. Создайте событие «Турнир»: /event_add"
TOURNAMENT_CARD = (
    "🏆 <b>{title}</b> ({squad}), {when}\n"
    "Дивизион: {division}\n"
    "Стоимость одной игры: {price}\n"
    "Матчи турнира отмечены ✅. Нажмите на матч, чтобы привязать или отвязать."
)
TOURNAMENT_BTN_GAME_PRICE = "💰 Изменить стоимость игры"
TOURNAMENT_BTN_TITLE = "✏️ Изменить название"
TOURNAMENT_BTN_DIVISION = "🏷 Изменить дивизион"
TOURNAMENT_ASK_TITLE_EDIT = "Новое название турнира:"
TOURNAMENT_BAD_TITLE = "Название — от 1 до 200 символов."
TOURNAMENT_ASK_DIVISION = (
    "Дивизион лиги, например <code>Москвич</code>, — как на mtgame.ru. По нему (или по названию "
    "турнира) бот привязывает к турниру матчи, найденные на mtgame. Можно пропустить."
)
TOURNAMENT_ASK_DIVISION_EDIT = (
    "Новый дивизион лиги, например <code>Москвич</code> (как на mtgame.ru):"
)
TOURNAMENT_BTN_CLEAR_DIVISION = "Убрать дивизион"
TOURNAMENT_BAD_DIVISION = "Дивизион — до 100 символов."
TOURNAMENT_ASK_GAME_PRICE = (
    "Стоимость одной игры в турнире (вся сумма за игру, например <code>15000</code>) — "
    "по ней бот посчитает, сколько платит каждый игрок. Можно пропустить."
)
TOURNAMENT_ASK_GAME_PRICE_EDIT = (
    "Новая стоимость одной игры (вся сумма за игру, например <code>15000</code>):"
)
TOURNAMENT_BTN_CLEAR_PRICE = "Убрать стоимость"
TOURNAMENT_NO_MATCHES = "\n\nМатчей для привязки нет — создайте событие «Матч»: /event_add"
TOURNAMENT_NOT_FOUND = "Турнир не найден."
TOURNAMENT_MATCH_NOT_FOUND = "Матч не найден."
TOURNAMENT_OTHER_SQUAD = "Матч другого состава — к этому турниру его не привязать."
TOURNAMENT_MATCH_TAKEN = "Матч уже привязан к другому турниру — сначала отвяжите его там."

# --- разбор игры после матча ---
REVIEW_OFFER = (
    "🏀 Матч прошёл:\n{match}\n\nНазначить разбор игры? Команда получит опрос «Буду / Не смогу»."
)
REVIEW_BTN_YES = "✅ Да, назначить разбор"
REVIEW_BTN_NO = "Не нужно"
REVIEW_PROPOSAL = (
    "Разбор игры «{title}» — {when} (за час до первой тренировки недели). "
    "Опубликовать в группе с опросом?"
)
REVIEW_PROPOSAL_NO_DEFAULT = (
    "Ближайших тренировок нет — время по умолчанию не предложить. "
    "Введите дату и время разбора: <code>дд.мм.гггг чч:мм</code>"
)
REVIEW_BTN_PUBLISH = "✅ Да, {when}"
REVIEW_BTN_OTHER_TIME = "🕒 Другое время"
REVIEW_ASK_TIME = "Дата и время разбора: <code>дд.мм.гггг чч:мм</code>"
REVIEW_CREATED = "✅ Разбор назначен: {when}. Опрос опубликован в группе и закреплён."
REVIEW_ALREADY = "Разбор по этому матчу уже назначен."
BTN_YES = "Да"
BTN_NO = "Нет"
BTN_CANCEL = "Отмена"
INPUT_CANCELLED = "Ввод прерван."
PAST_DATE = "Дата уже прошла."

# --- квитанции об оплате тренировок за месяц ---
MONTHS = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)  # fmt: skip
RECEIPT_POST = (
    "🧾 <b>Квитанция об оплате тренировок за {month}</b>\n"
    "Пришлите квитанцию (фото или PDF) тренеру{coaches} в личные сообщения и отметьтесь "
    "в опросе ниже.\n⏰ До {deadline}"
)
RECEIPT_COACHES = ": {names}"
RECEIPT_ALL_SENT = (
    "✅ Все прислали квитанции за {month} ({squad}): {count} из {count}.\n\n"
    "Пожалуйста, проверьте, что все игроки прислали квитанции."
)
RECEIPT_POLL_QUESTION = "🧾 Квитанция за {month}: отправили тренеру?"
RECEIPT_REMINDER_DM = (
    "⏰ <b>Напоминание: квитанция за {month}</b> ({squad})\n"
    "Пришлите тренеру{coaches} квитанцию об оплате тренировок. Если уже отправили — "
    "отметьте «Отправил» в опросе в группе."
)
RECEIPT_REMINDER_GROUP = (
    "⏰ {names}, напоминание: пришлите тренеру квитанцию об оплате тренировок за {month} "
    "и отметьтесь в опросе."
)
RECEIPT_ALREADY = (
    "Опрос о квитанциях за {month} уже опубликован {when}. Повторно в этом месяце "
    "бот его не публикует.\nОтметили «Отправил»: {sent} из {total}\nНе отметили: {missing}"
)
RECEIPT_ASK_DEADLINE = (
    "До какого времени прислать квитанцию? Кнопка — срок по умолчанию, или введите "
    "<code>дд.мм.гггг чч:мм</code>. После срока бот напомнит тем, кто не отметил «Отправил»."
)
RECEIPT_BTN_DEFAULT = "⏰ До {when}"
RECEIPT_CREATED = "✅ Опрос о квитанциях за {month} опубликован в группе."
RECEIPT_NOT_FOUND = "Сбор квитанций не найден."
RECEIPT_NOT_SENT = "Не удалось опубликовать опрос в группе — проверьте, что бот может там писать."

# --- сбор денег за игру (не путать с оплатой тренировок по квитанциям) ---
GAME_PAYMENT_EVENT = "{type}: <b>{title}</b>, {when}{tournament}"
GAME_PAYMENT_POST = (
    "💰 <b>Сбор за игру</b>\n<b>{title}</b>\n🗓 {when}\n\n"
    "<b>Сумма: {amount}</b>\n👤 Получатель: {collector}\n{details}{deadline}\n\n"
    "Платят игроки из состава выше ({players}). После перевода отметьте «Оплатил» в опросе ниже."
)
GAME_PAYMENT_DEADLINE = "\n⏰ Оплатить до {when}"
GAME_PAYMENT_ALL_PAID = (
    "✅ Все игроки оплатили игру\n{event}\nОплатили {count} из {count} по {amount}: {names}\n\n"
    "Пожалуйста, проверьте, что все игроки внесли сумму."
)
# дописывается в «Все оплатили / все прислали», когда сбор завершён (кнопка снимается)
COLLECTION_CLOSED_MARK = "\n\n🏁 Сбор завершён."
GAME_PAYMENT_POLL_QUESTION = "💰 Оплата игры «{title}»: {amount}"
GAME_PAYMENT_REMINDER_DM = (
    "💰 Напоминание об оплате игры\n{event}\n\n<b>Сумма: {amount}</b>\n{details}\n\n"
    "Вы в составе на эту игру, но ещё не отметили «Оплатил» в опросе в группе. "
    "После перевода отметьте, пожалуйста."
)
GAME_PAYMENT_REMINDER_GROUP = (
    "💰 {names}, напоминание об оплате игры\n{event}\n<b>Сумма: {amount}</b>\n{details}\n"
    "После перевода отметьте «Оплатил» в опросе."
)
GAME_PAYMENT_STATUS = (
    "\n\n💰 <b>Оплата: {amount}</b>{deadline}{collector}\nОплатили ({paid_count}): {paid}\n"
    "Не оплатили ({unpaid_count}): {unpaid}{others}"
)
PAYMENT_BTN_CLOSE = "🏁 Завершить сбор"
PAYMENT_BTN_CONFIRM_CLOSE = "✅ Да, завершить"
PAYMENT_ALREADY_CLOSED = "Сбор уже завершён."
PAYMENT_CLOSED_MARK = "\n🏁 Сбор завершён — напоминаний нет."
GAME_PAYMENT_CONFIRM_CLOSE = (
    "Завершить сбор денег за игру? Опрос «Оплатил» в группе закроется, напоминаний больше "
    "не будет. Открыть его снова нельзя."
)
GAME_PAYMENT_CLOSED = "🏁 Сбор денег за игру завершён: опрос закрыт, напоминаний больше не будет."
RECEIPT_CONFIRM_CLOSE = (
    "Завершить сбор квитанций за месяц? Опрос в группе закроется, напоминаний больше не будет. "
    "Открыть его снова нельзя."
)
RECEIPT_CLOSED = "🏁 Сбор квитанций за {month} завершён: опрос закрыт, напоминаний больше не будет."
GAME_PAYMENT_NOT_FOUND = "Сбор денег не найден или отменён."
GAME_PAYMENT_STATUS_OTHERS = "\nОплатили, но не в составе: {names}"
GAME_PAYMENT_CHOOSE_MATCH = "За какую игру собираем деньги?"
GAME_PAYMENT_NO_MATCHES = "Нет матчей для сбора денег. Создайте матч: /event_add"
GAME_PAYMENT_ASK_AMOUNT = "Сумма с каждого игрока в рублях, например <code>2500</code>:"
GAME_PAYMENT_BTN_SUGGESTED = "{amount} — {price} ÷ {players} игр."
GAME_PAYMENT_ASK_DEADLINE = (
    "До какого времени оплатить? Кнопка — срок по умолчанию, или введите "
    "<code>дд.мм.гггг чч:мм</code>. После срока бот напомнит тем, кто не отметил оплату."
)
GAME_PAYMENT_BTN_DAY = "⏰ До {when}"
GAME_PAYMENT_BTN_NO_DEADLINE = "Без срока"
GAME_PAYMENT_ASK_COLLECTOR = "Кто собирает деньги? Переводить будут на его реквизиты."
GAME_PAYMENT_ME = " (я)"
GAME_PAYMENT_COLLECTOR_DETAILS = (
    "Ваши реквизиты:\n{details}\n\nПроверьте и выберите — или введите новые."
)
GAME_PAYMENT_COLLECTOR_NO_DETAILS = "У {name} нет сохранённых реквизитов — введите их."
GAME_PAYMENT_BTN_USE = "✅ Использовать"
GAME_PAYMENT_BTN_USE_N = "✅ {n}. {details}"
GAME_PAYMENT_ASK_COLLECTOR_HINT = (
    "\nВ списке — вы и тренеры и админы состава, которые сами указали реквизиты в /profile."
)
GAME_PAYMENT_OTHER_DETAILS = (
    "Реквизиты получателя {name}:\n{details}\n\n"
    "Их задаёт и меняет только сам получатель (в /profile). Проверьте и используйте:"
)
GAME_PAYMENT_COLLECTOR_NOT_ALLOWED = (
    "Получателем можно выбрать себя или тренера/админа состава, который сам указал реквизиты."
)
GAME_PAYMENT_BTN_NEW_DETAILS = "✏️ Задать реквизиты"
GAME_PAYMENT_BTN_DETAILS_OK = "✅ Реквизиты подтверждаю"
GAME_PAYMENT_ASK_SAVE_DETAILS = (
    "Сохранить эти реквизиты в профиле, чтобы в следующий раз не вводить?\n{details}"
)
GAME_PAYMENT_DETAILS_SAVED = "💾 Реквизиты сохранены в профиле."
GAME_PAYMENT_ALREADY = "Сбор денег за эту игру уже объявлен."
GAME_PAYMENT_AMOUNT_CALC = (
    "В заявке {players} игр. Стоимость игры {price} ÷ {players} = <b>{amount}</b> с игрока "
    "(округлено вверх до рубля)."
)

# --- заявка на игру и «Состав на матч» ---
ROSTER_POST = (
    "🏀 <b>Состав на матч</b>\n<b>{title}</b>\n🗓 {when}\n\n<b>Игроки ({count}):</b>\n{players}"
)
ROSTER_NOTICE = (
    "🏀 <b>{title}</b>\n🗓 {when}\n\n«Буду (игрок)» отметили <b>{count}</b>.{hints}\n\n"
    "<i>Количество игроков обновляется здесь же, обновлено {updated}</i>"
)
ROSTER_NOTICE_TOO_MANY = "В заявку войдут не больше {max} — выберете при начале сбора."
ROSTER_NOTICE_SHORT = "До полного состава ({size}) не хватает {missing}."
ROSTER_NOTICE_NO_PRICE = "Стоимость игры у турнира не указана — сумму с игрока введёте сами."
ROSTER_BTN_START = "💰 Начать сбор"
ROSTER_CONFIRM_START = (
    "<b>{title}</b>, {when}\n«Буду (игрок)» отметили {count}.\n\n"
    "Голосование об участии будет закрыто — снова открыть его нельзя. Дальше: выбор заявки "
    "(до {max} игроков), сумма, срок, реквизиты. Если прервётесь — нажмите «Начать сбор» ещё "
    "раз. Подтвердить?"
)
ROSTER_BTN_CONFIRM = "✅ Подтвердить"
ROSTER_CHOOSE = (
    "<b>Заявка на игру</b> {title}\nОтмечено: <b>{count}</b> из {max}.\n"
    "✅ — в заявке. Нажмите на игрока, чтобы включить или убрать. Платят только игроки заявки."
)
ROSTER_OTHERS = "Не отвечали «Буду (игрок)»:"
ROSTER_BTN_MORE = "➕ Добавить из состава"
ROSTER_BTN_LESS = "➖ Только ответившие «Буду (игрок)»"
ROSTER_BTN_DONE = "Готово ({count}) →"
ROSTER_LIMIT = "В заявке уже {max} — сначала уберите кого-нибудь."
ROSTER_EMPTY = "Отметьте хотя бы одного игрока."
ROSTER_TOO_MANY = "В заявке больше {max} игроков — уберите лишних и попробуйте снова."
ROSTER_NOT_MEMBERS = "Кто-то из заявки уже не в группе состава — выберите заявку заново: /game_pay"
GAME_PAYMENT_PREVIEW = (
    "Так будет в группе — состав, пост и опрос «Оплатил»:\n\n{roster}\n\n➖➖➖\n\n{post}\n\n"
    "Реквизиты и сумма верны? Опубликовать?"
)
GAME_PAYMENT_BTN_PUBLISH = "✅ Опубликовать"
GAME_PAYMENT_CREATED = "✅ Сбор опубликован в группе."
GAME_PAYMENT_NOT_SENT = (
    "Не удалось опубликовать сбор в группе (пост или опрос не приняты Telegram). "
    "Сбор не создан — попробуйте ещё раз."
)

# --- права ролей ---
PERMISSIONS_CHOOSE_ROLE = (
    "<b>Права ролей</b> — что может каждая роль во всех составах. Админ может всё.\nВыберите роль:"
)
PERMISSIONS_ROLE = (
    "<b>Роль: {role}</b>\nНажмите на функцию, чтобы включить ✅ или выключить ▫️. "
    "Изменения действуют сразу."
)
PERMISSIONS_BTN_RESET = "↩️ Сбросить по умолчанию"
PERMISSION_LOCKED = "Это право закреплено за тренером — другой роли его не выдать."
PERMISSION_LOCKED_MARK = "🔒"

# --- реквизиты ---
DETAILS_ASK_METHOD = (
    "Как переводить деньги? Реквизиты увидят все участники группы — в посте о сборе."
)
DETAILS_BTN_PHONE = "📱 По номеру телефона"
DETAILS_BTN_CARD = "💳 По номеру карты"
DETAILS_ASK_PHONE = "Номер телефона для перевода, например <code>+79991234567</code>:"
DETAILS_BTN_MY_PHONE = "Телефон из профиля: {phone}"
DETAILS_ASK_CARD = "Номер карты, например <code>2202 2000 1234 5678</code>:"
DETAILS_ASK_BANK = "Банк, например <code>Сбербанк</code> или <code>Т-Банк</code>:"
DETAILS_SAVED = "✅ Реквизиты сохранены:\n{details}"
DETAILS_MINE = "<b>Ваши реквизиты для сбора денег</b>\n{details}"
DETAILS_EMPTY = "Пока нет — добавьте, чтобы вас можно было выбрать получателем сбора."
DETAILS_BTN_ADD = "➕ Добавить реквизиты"
DETAILS_BTN_EDIT = "✏️ Изменить {n}"
DETAILS_BTN_DELETE = "🗑 Удалить {n}"
DETAILS_EDITING = "Изменяем реквизиты:\n{details}\n\nВведите новые."
DETAILS_CONFIRM_DELETE = "Удалить реквизиты?\n{details}"
DETAILS_BTN_CONFIRM_DELETE = "Да, удалить"
DETAILS_DELETED = "Реквизиты удалены."
DETAILS_LIMIT = "Можно сохранить не больше {limit} реквизитов — удалите ненужные в /profile."
DETAILS_NOT_FOUND = "Реквизиты не найдены — возможно, их уже удалили."

# --- матчи из mtgame (ФТ-4) ---
# описание матча: «Москвич, тур 7»; если матч привязан к турниру журнала — «Тур 7»
MATCH_TOUR = "тур {tour}"
MATCH_STAGE = "плей-офф: {stage}"
MATCH_SOURCE_FAILED = (
    "⚠️ Не удалось получить матчи состава «{squad}» с mtgame.ru (команда {team_id}): {error}\n"
    "Пока сайт недоступен, матч можно добавить вручную: /event_add."
)
MATCH_FALLBACK = (
    "🏀 <b>{squad}</b>: матч на эти выходные не найден автоматически — на mtgame.ru (команда "
    "{team_id}) его нет или сайт недоступен.\n\nЕсли игра будет — введите её вручную: "
    "ручная запись главная, синхронизация её не перезапишет."
)
MATCH_FALLBACK_BTN_ADD = "✏️ Ввести матч"
MATCH_FALLBACK_BTN_NONE = "Игры нет"
MATCH_FALLBACK_NONE_DONE = "Отмечено: игры на этих выходных нет."
MATCH_DIFF = (
    "⚠️ <b>{title}</b>: на mtgame.ru данные отличаются от введённых вручную.\n"
    "В журнале: {ours}\nНа mtgame: {theirs}\n"
    "Матч в журнале не изменён. Если верно mtgame — перенесите его: /events"
)
MATCH_GONE = (
    "⚠️ <b>{title}</b>, {when}: на mtgame.ru игра пропала из расписания или у неё сняли дату — "
    "возможно, её отменили или переносят. Уточните у лиги; если игры не будет — отмените матч: "
    "/events"
)
