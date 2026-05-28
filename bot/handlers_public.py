"""Public command handlers available to every user.

Covers the visitor-facing flow:

* ``/start``           — greeting image + the list of upcoming events.
* ``/add_<id>``        — sign up for an event.
* ``/cancel_<id>``     — cancel a previous signup.

Event ids are sequential integers, so signup/cancel are matched by a regex on
the command rather than declared as static commands (which Telegram cannot do
for an open-ended set of ids).
"""

from __future__ import annotations

import re
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from . import texts
from .config import config
from .media import send_photo
from .storage import storage

router = Router(name="public")

# Matches "/add_12" and "/cancel_12", optionally with an @botname suffix that
# Telegram appends in group chats (e.g. "/add_12@MyBot").
_ADD_RE = re.compile(r"^/add_(\d+)(?:@\w+)?$")
_CANCEL_RE = re.compile(r"^/cancel_(\d+)(?:@\w+)?$")


def _now() -> datetime:
    return datetime.now(tz=config.timezone)


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot) -> None:
    """Greet the user and show all upcoming events."""
    storage.remember_user(message.chat.id)

    events = storage.upcoming(_now())
    if not events:
        await send_photo(
            bot, message.chat.id, "welcome",
            f"{texts.START_GREETING}\n\n{texts.NO_EVENTS}",
        )
        return

    listing = texts.render_event_list(events, user_id=message.chat.id)
    await send_photo(
        bot, message.chat.id, "welcome",
        f"{texts.START_GREETING}\n\n{listing}",
    )


@router.message(F.text.regexp(_ADD_RE))
async def cmd_add(message: Message, bot: Bot) -> None:
    """Sign the user up for the event encoded in the command."""
    storage.remember_user(message.chat.id)

    event_id = int(_ADD_RE.match(message.text).group(1))
    event = storage.get(event_id)
    if event is None or event.start_dt <= _now():
        await message.answer(texts.event_not_found(), parse_mode="HTML")
        return

    if event.is_signed_up(message.chat.id):
        await message.answer(texts.already_signed_up(event), parse_mode="HTML")
        return

    storage.add_participant(event_id, message.chat.id)
    await send_photo(bot, message.chat.id, "cards", texts.signed_up(event))


@router.message(F.text.regexp(_CANCEL_RE))
async def cmd_cancel(message: Message) -> None:
    """Cancel the user's signup for the event encoded in the command."""
    event_id = int(_CANCEL_RE.match(message.text).group(1))
    event = storage.get(event_id)
    if event is None:
        await message.answer(texts.event_not_found(), parse_mode="HTML")
        return

    if not event.is_signed_up(message.chat.id):
        await message.answer(texts.not_signed_up(event), parse_mode="HTML")
        return

    storage.remove_participant(event_id, message.chat.id)
    await message.answer(texts.cancelled(event), parse_mode="HTML")
