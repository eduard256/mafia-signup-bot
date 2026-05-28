"""User-facing text and message formatting.

Everything the user reads lives here so wording can be tuned in one place and
the handler code stays focused on behaviour. All copy is in Russian, uses HTML
parse mode, and intentionally contains no emoji per project style.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

from .storage import Event

# Russian month names in the genitive case ("29 мая", "1 июня", ...).
_MONTHS_GENITIVE = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def format_dt(dt: datetime) -> str:
    """Return a human-friendly Russian date/time like '29 мая в 20:00'."""
    return f"{dt.day} {_MONTHS_GENITIVE[dt.month - 1]} в {dt:%H:%M}"


def escape_title(text: str) -> str:
    """HTML-escape arbitrary text for safe inclusion in HTML messages."""
    return escape(text)


def plural_people(n: int) -> str:
    """Return the correctly pluralised Russian word for a count of people."""
    if 11 <= n % 100 <= 14:
        return "участников"
    last = n % 10
    if last == 1:
        return "участник"
    if 2 <= last <= 4:
        return "участника"
    return "участников"


# --------------------------------------------------------------------------- #
# Static copy
# --------------------------------------------------------------------------- #

START_GREETING = (
    "<b>MAFIA</b>\n"
    "<i>Вся семья в сборе.</i>\n\n"
    "Здесь собирается семья на партию в мафию. Выбирай вечер, жми команду "
    "записи под мероприятием — и место за столом твоё.\n\n"
    "За несколько часов до игры пришлю напоминание, а в назначенный час — "
    "ссылку на комнату."
)

NO_EVENTS = (
    "<b>Пока тихо.</b>\n\n"
    "Ни одного назначенного дела. Загляни позже — семья что-нибудь "
    "придумает."
)

# Shown to non-admins who try to use admin commands.
NOT_ADMIN = "Эта команда только для того, кто держит семью."

# Plain-text footers (not buttons) appended to the bottom of messages. Each is
# just a hint that points back to /start, which refreshes the listing.
FOOTER_REFRESH = "Обновить данные: /start"
FOOTER_SIGNUP_MORE = "Записаться ещё: /start"
FOOTER_HOME = "Главная: /start"


# --------------------------------------------------------------------------- #
# Event rendering
# --------------------------------------------------------------------------- #

def render_event_card(event: Event, *, user_id: int) -> str:
    """Render a single event block for the public /start listing."""
    count = len(event.participants)
    signed = event.is_signed_up(user_id)

    lines = [
        f"<b>{escape(event.title)}</b>",
        format_dt(event.start_dt),
    ]
    if event.description:
        lines.append(escape(event.description))

    if event.capacity > 0:
        lines.append(
            f"В деле: {count}/{event.capacity} — /who_{event.id}"
        )
    else:
        lines.append(f"В деле: {count} {plural_people(count)} — /who_{event.id}")

    if signed:
        lines.append(f"Ты записан. Передумал? /cancel_{event.id}")
    elif event.is_full:
        lines.append("Регистрация закончилась — мест нет.")
    else:
        lines.append(f"Записаться: /add_{event.id}")

    return "\n".join(lines)


def render_event_list(events: list[Event], *, user_id: int) -> str:
    """Render the full public listing of upcoming events."""
    blocks = [render_event_card(ev, user_id=user_id) for ev in events]
    # A thin separator keeps consecutive cards readable.
    return "\n\n— — —\n\n".join(blocks)


# --------------------------------------------------------------------------- #
# Signup feedback
# --------------------------------------------------------------------------- #

def signed_up(event: Event) -> str:
    return (
        f"Готово. Ты в деле на «{escape(event.title)}» "
        f"({format_dt(event.start_dt)}).\n"
        f"Передумаешь — /cancel_{event.id}.\n\n"
        f"{FOOTER_SIGNUP_MORE}"
    )


def already_signed_up(event: Event) -> str:
    return (
        f"Ты уже записан на «{escape(event.title)}» "
        f"({format_dt(event.start_dt)}).\n"
        f"Отменить запись: /cancel_{event.id}\n\n"
        f"{FOOTER_SIGNUP_MORE}"
    )


def cancelled(event: Event) -> str:
    return (
        f"Запись на «{escape(event.title)}» отменена.\n"
        f"Передумаешь — /add_{event.id}.\n\n"
        f"{FOOTER_REFRESH}"
    )


def not_signed_up(event: Event) -> str:
    return (
        f"Ты и не был записан на «{escape(event.title)}».\n"
        f"Записаться: /add_{event.id}\n\n"
        f"{FOOTER_REFRESH}"
    )


def event_not_found() -> str:
    return (
        "Такого мероприятия нет — возможно, оно уже прошло.\n\n"
        f"{FOOTER_REFRESH}"
    )


def event_full(event: Event) -> str:
    return (
        f"Регистрация на «{escape(event.title)}» закончилась — "
        f"все {event.capacity} мест заняты.\n\n"
        f"{FOOTER_REFRESH}"
    )


# --------------------------------------------------------------------------- #
# Participants list
# --------------------------------------------------------------------------- #

def format_participant(
    user_id: int, *, username: str | None, full_name: str | None
) -> str:
    """Render one participant line.

    Prefers a public ``@username``; otherwise falls back to the display name
    rendered as a clickable ``tg://user`` mention so it is tappable even without
    a username. The numeric id is used only if nothing else is available.
    """
    if username:
        return f"@{escape(username)}"
    name = full_name or str(user_id)
    return f'<a href="tg://user?id={user_id}">{escape(name)}</a>'


def render_participants(event: Event, lines: list[str]) -> str:
    """Assemble the full participants message for an event."""
    count = len(event.participants)
    if event.capacity > 0:
        counter = f"Записано: {count}/{event.capacity}"
        if event.is_full:
            counter += " — мест нет"
    else:
        counter = f"Записано: {count} {plural_people(count)}"
    header = (
        f"<b>{escape(event.title)}</b>\n"
        f"{format_dt(event.start_dt)}\n"
        f"{counter}"
    )
    body = "\n".join(f"{i}. {line}" for i, line in enumerate(lines, start=1))
    return f"{header}\n\n{body}\n\n{FOOTER_REFRESH}"


def no_participants(event: Event) -> str:
    return (
        f"<b>{escape(event.title)}</b>\n"
        f"{format_dt(event.start_dt)}\n\n"
        f"Пока никто не записан. Будь первым: /add_{event.id}\n\n"
        f"{FOOTER_REFRESH}"
    )


# --------------------------------------------------------------------------- #
# Reminders
# --------------------------------------------------------------------------- #

def reminder(event: Event, minutes_before: int) -> str:
    """Reminder text for the 4h/3h/2h/1h notifications."""
    hours = minutes_before // 60
    return (
        f"<b>Скоро игра.</b>\n\n"
        f"«{escape(event.title)}» начнётся через {hours} ч "
        f"({format_dt(event.start_dt)}).\n"
        f"Семья ждёт. Не подведи."
    )


def starting_now(event: Event) -> str:
    """Final notification sent at the event start, with the join link."""
    return (
        f"<b>Начинаем.</b>\n\n"
        f"«{escape(event.title)}» — заходи в комнату:\n"
        f"{escape(event.link)}\n\n"
        f"Кто не зашёл — тот лох."
    )
