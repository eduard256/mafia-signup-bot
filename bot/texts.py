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

    lines.append(f"В деле: {count} {plural_people(count)}")

    if signed:
        lines.append(f"Ты записан. Передумал? /cancel_{event.id}")
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
        f"({format_dt(event.start_dt)}).\n\n"
        f"Можешь записаться и на другие вечера — /start.\n"
        f"Передумаешь — /cancel_{event.id}."
    )


def already_signed_up(event: Event) -> str:
    return (
        f"Ты уже записан на «{escape(event.title)}» "
        f"({format_dt(event.start_dt)}).\n\n"
        f"Отменить запись: /cancel_{event.id}"
    )


def cancelled(event: Event) -> str:
    return (
        f"Запись на «{escape(event.title)}» отменена.\n\n"
        f"Передумаешь — /add_{event.id}."
    )


def not_signed_up(event: Event) -> str:
    return (
        f"Ты и не был записан на «{escape(event.title)}».\n\n"
        f"Записаться: /add_{event.id}"
    )


def event_not_found() -> str:
    return "Такого мероприятия нет — возможно, оно уже прошло. Смотри /start."


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
