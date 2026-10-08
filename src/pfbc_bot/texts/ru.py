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
REG_ASK_CONSENT = (
    "Последний шаг: согласие на обработку персональных данных (ФИО, дата рождения, ссылка на "
    "профиль) для организации деятельности клуба. Данные можно удалить командой /delete_me."
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
BTN_CONSENT = "✅ Согласен(на)"
BTN_CONSENT_GUARDIAN = "✅ Я законный представитель, согласен(на)"
BTN_DECLINE = "Отказаться"

PROFILE = (
    "<b>Ваш профиль</b>\n"
    "ФИО: {full_name}\n"
    "Дата рождения: {birth_date}\n"
    "mtgame: {mtgame}\n"
    "Составы: {squads}"
)
PROFILE_UPDATED = "Сохранено ✅"
DELETE_ME_CONFIRM = (
    "Удалить ваши персональные данные (ФИО, дату рождения, ссылку mtgame)? "
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
HELP_STAFF = (
    "\n\n<b>Тренер / капитан</b>\n"
    "/schedule — расписание тренировок\n"
    "/trainings — ближайшие тренировки (отмена, перенос)\n"
    "/training_add — разовая тренировка\n"
    "/attendance — кто придёт на тренировку\n"
    "/duty — дежурные: замена и ручной выбор\n"
    "/event_add — добавить событие в журнал\n"
    "/event_del — удалить событие\n"
    "/invite — ссылка на регистрацию для новичка"
)
HELP_ADMIN = (
    "\n\n<b>Админ</b>\n"
    "/squads — составы\n"
    "/roles — назначить тренера или капитана\n"
    "/admins — админы бота\n"
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
SETTINGS_USAGE = (
    "Использование: /set &lt;ключ&gt; &lt;значение&gt;, например "
    "<code>/set training_poll_time 08:30</code>. Текущие настройки: /settings"
)
SETTINGS_CHOOSE = "Чьи настройки показать?"
SETTINGS_GLOBAL = "🌐 Глобальные"
SETTINGS_UNKNOWN = "Неизвестная настройка: {key}. Список ключей: /settings"
SETTINGS_SAVED = "✅ Настройка {key} = {value} сохранена ({scope})."
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
TRAINING_ADD_PROMPT = (
    "Отправьте: <b>дата время длительность зал</b>\n"
    "Например: <code>12.10.2026 19:00 90 Зал школы №5</code>"
)
TRAINING_MOVE_PROMPT = "Новые дата и время: <code>дд.мм.гггг чч:мм</code>"
TRAINING_ADDED = "✅ Тренировка добавлена: {when}, {venue}"
TRAINING_CANCELLED_GROUP = "❌ Тренировка {when} ({venue}) <b>отменена</b>."
TRAINING_MOVED_GROUP = "🔁 Тренировка перенесена: {old} → <b>{new}</b> ({venue})."
TRAINING_DONE = "Готово ✅"
TRAINING_POLL_QUESTION = "🏀 Тренировка {when}, {venue}. Придёшь?"
ATTENDANCE = "<b>Тренировка {when}</b>\n{lines}\nНе ответили: {missing}"
ATTENDANCE_NO_POLL = "Опрос на эту тренировку ещё не публиковался."

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
DUTY_ASSIGNED_FALLBACK = (
    "🧹 {mention}, вы назначены дежурным на тренировку {when}{instead}. "
    "Приходите за {minutes} минут до начала."
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
    "туда же будут приходить напоминания для команды."
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
EVENT_CREATED = "✅ Событие добавлено:\n{event}"
EVENT_PUBLISHED = "Опубликовано в группе состава."
EVENT_DELETED = "✅ Событие удалено."
EVENTS_HEADER = "<b>Ближайшие события</b>"
EVENTS_EMPTY = "Ближайших событий нет."
EVENT_LINE = "• {when} — <b>{title}</b> ({type}, {squad}){place}"
EVENT_ANNOUNCE = "📅 <b>{type}: {title}</b>\n🕒 {when}{place}{description}"
EVENT_REMINDER = "⏰ Напоминание{when_left}: <b>{title}</b>\n{when}{place}{description}"
EVENT_IN_DAYS = {0: " — сегодня", 1: " — завтра"}
EVENT_IN_N_DAYS = " — через {days} дн."
BTN_YES = "Да"
BTN_NO = "Нет"
BTN_CANCEL = "Отмена"
CANCELLED = "Отменено."
PAST_DATE = "Дата уже прошла."
