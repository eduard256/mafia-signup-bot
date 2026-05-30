"""Administrator command handlers.

A single administrator (``config.admin_id``) manages events through ``/admin``,
which shows an inline keyboard with two actions:

* Create event — a step-by-step FSM dialog: kind -> title -> date -> time ->
  description -> link, with a confirmation summary at the end.
* Delete event — lists events as buttons; tapping one removes it.
* Send message — pick an audience (everyone, or one event's participants) and
  broadcast a copy of any message to them.
* Send poll — pick an audience the same way, then collect a link, a button
  caption and a body; each recipient gets the body with a link button under it.

The audience picker, recipient resolution and delivery loop are shared between
the message and poll flows. Reminders are fully automatic and have no controls.
"""

from __future__ import annotations

from datetime import datetime

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from . import texts
from .config import config
from .meet import MeetError, create_room
from .storage import storage

logger = logging.getLogger(__name__)

router = Router(name="admin")


def _is_admin(user_id: int | None) -> bool:
    return user_id == config.admin_id


# Restrict every handler in this router to the admin. Non-admins fall through
# to the public router (or get the "not admin" reply on explicit admin cmds).
router.message.filter(F.from_user.func(lambda u: _is_admin(u.id)))
router.callback_query.filter(F.from_user.func(lambda u: _is_admin(u.id)))


# --------------------------------------------------------------------------- #
# FSM for creating an event
# --------------------------------------------------------------------------- #

class NewEvent(StatesGroup):
    kind = State()
    title = State()
    date = State()
    time = State()
    description = State()
    capacity = State()


class Broadcast(StatesGroup):
    # Waiting for the message to forward. The target audience (all users, or a
    # specific event's participants) is stored in FSM data under "target".
    message = State()


class Poll(StatesGroup):
    # Sending a poll: a text message with a single link button under it. The
    # audience is chosen exactly like a Broadcast (stored under "target"); then
    # we collect the link, the button caption, and finally the message body.
    link = State()
    button = State()
    message = State()


def _admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Создать мероприятие", callback_data="adm:new")],
            [InlineKeyboardButton(text="Удалить мероприятие", callback_data="adm:del")],
            [InlineKeyboardButton(text="Отправить сообщение", callback_data="adm:msg")],
            [InlineKeyboardButton(text="Отправить опрос", callback_data="adm:poll")],
        ]
    )


def _cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="adm:abort")]
        ]
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        f"<b>Панель семьи.</b>\nЧто делаем?\n\n{texts.FOOTER_HOME}",
        reply_markup=_admin_menu_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "adm:abort")
async def cb_abort(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(f"Отменено.\n\n{texts.FOOTER_HOME}")
    await callback.answer()


# ----- creation flow ------------------------------------------------------- #

@router.callback_query(F.data == "adm:new")
async def cb_new(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(NewEvent.kind)
    await callback.message.edit_text(
        "Тип мероприятия (например, <b>Мафия</b>):",
        reply_markup=_cancel_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(NewEvent.kind)
async def step_kind(message: Message, state: FSMContext) -> None:
    await state.update_data(kind=message.text.strip())
    await state.set_state(NewEvent.title)
    await message.answer(
        "Название (например, <b>Мафия: вся семья в сборе</b>):",
        reply_markup=_cancel_kb(),
        parse_mode="HTML",
    )


@router.message(NewEvent.title)
async def step_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=message.text.strip())
    await state.set_state(NewEvent.date)
    await message.answer(
        "Дата в формате <b>ДД.ММ.ГГГГ</b> (например, 29.05.2026):",
        reply_markup=_cancel_kb(),
        parse_mode="HTML",
    )


@router.message(NewEvent.date)
async def step_date(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    try:
        date = datetime.strptime(raw, "%d.%m.%Y").date()
    except ValueError:
        await message.answer(
            "Не разобрал дату. Нужен формат ДД.ММ.ГГГГ, например 29.05.2026.",
            reply_markup=_cancel_kb(),
        )
        return
    await state.update_data(date=date.isoformat())
    await state.set_state(NewEvent.time)
    await message.answer(
        "Время в формате <b>ЧЧ:ММ</b> (например, 20:00):",
        reply_markup=_cancel_kb(),
        parse_mode="HTML",
    )


@router.message(NewEvent.time)
async def step_time(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    try:
        t = datetime.strptime(raw, "%H:%M").time()
    except ValueError:
        await message.answer(
            "Не разобрал время. Нужен формат ЧЧ:ММ, например 20:00.",
            reply_markup=_cancel_kb(),
        )
        return

    data = await state.get_data()
    start = datetime.fromisoformat(data["date"]).replace(
        hour=t.hour, minute=t.minute
    )
    # Reject events created in the past — they would be purged immediately.
    if start.replace(tzinfo=config.timezone) <= datetime.now(tz=config.timezone):
        await message.answer(
            "Это время уже прошло. Укажи будущие дату и время заново — /admin.",
        )
        await state.clear()
        return

    await state.update_data(start=start.isoformat())
    await state.set_state(NewEvent.description)
    await message.answer(
        "Описание (одна-две строки). Если не нужно — отправь <b>-</b>:",
        reply_markup=_cancel_kb(),
        parse_mode="HTML",
    )


@router.message(NewEvent.description)
async def step_description(message: Message, state: FSMContext) -> None:
    desc = message.text.strip()
    if desc == "-":
        desc = ""
    await state.update_data(description=desc)
    await state.set_state(NewEvent.capacity)
    await message.answer(
        "Лимит участников — число (например, <b>15</b>). "
        "Если игроков много, поставь большое число, например 100:",
        reply_markup=_cancel_kb(),
        parse_mode="HTML",
    )


@router.message(NewEvent.capacity)
async def step_capacity(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer(
            "Нужно положительное число. Например, 15.",
            reply_markup=_cancel_kb(),
        )
        return
    capacity = int(raw)

    # This is the final step: create a dedicated meeting room sized to the
    # capacity, then persist the event with that room's URL as its link.
    await message.answer("Создаю комнату...")
    try:
        link = await create_room(capacity)
    except MeetError as exc:
        await state.clear()
        logger.error("Room creation failed: %s", exc)
        await message.answer(
            "Не удалось создать комнату для мероприятия. "
            f"Попробуй ещё раз — /admin.\n\nПричина: {texts.escape_title(str(exc))}",
            parse_mode="HTML",
        )
        return

    data = await state.get_data()
    event = storage.create_event(
        kind=data["kind"],
        title=data["title"],
        description=data["description"],
        link=link,
        start=data["start"],
        capacity=capacity,
    )
    await state.clear()

    start_dt = event.start_dt
    await message.answer(
        "<b>Мероприятие создано.</b>\n\n"
        f"{texts.escape_title(event.title)}\n"
        f"{texts.format_dt(start_dt)}\n"
        f"Лимит: {event.capacity}\n"
        f"Комната: {texts.escape_title(event.link)}\n"
        f"Команда записи: /add_{event.id}\n"
        f"Участники: /who_{event.id}\n\n"
        f"{texts.FOOTER_HOME}",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ----- audience selection (shared by broadcast & poll) --------------------- #

# Regex for "/send_<mode>_<id>" used to pick an event's participants as the
# audience. <mode> is "msg" (a broadcast) or "poll" (a poll with a link button).
_SEND_RE = r"^/send_(msg|poll)_(\d+)(?:@\w+)?$"


def _audience_menu_kb(mode: str) -> InlineKeyboardMarkup:
    """Audience picker shared by both flows. ``mode`` is "msg" or "poll" and is
    carried in the callback data so the next step knows what we are sending."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отправить всем", callback_data=f"adm:aud:{mode}:all")],
            [
                InlineKeyboardButton(
                    text="Отправить участникам события",
                    callback_data=f"adm:aud:{mode}:event",
                )
            ],
            [InlineKeyboardButton(text="Отмена", callback_data="adm:abort")],
        ]
    )


def _resolve_recipients(data: dict) -> list[int] | None:
    """Turn stored FSM audience data into a recipient list, or None if a chosen
    event has since vanished."""
    if data.get("target") == "event":
        event = storage.get(data["event_id"])
        if event is None:
            return None
        return list(event.participants)
    return storage.all_user_ids


async def _deliver(recipients: list[int], send_one) -> tuple[int, int]:
    """Run ``send_one(uid)`` for every recipient, tolerating per-user failures
    and staying under Telegram's ~30 msg/sec limit. Returns (sent, failed)."""
    sent = 0
    failed = 0
    for uid in recipients:
        try:
            await send_one(uid)
            sent += 1
        except Exception as exc:  # noqa: BLE001 - one bad recipient must not stop the run
            failed += 1
            logger.warning("Delivery to %s failed: %s", uid, exc)
        await asyncio.sleep(0.05)
    return sent, failed


async def _ask_event_audience(callback: CallbackQuery, mode: str) -> None:
    """List events so the admin picks the target audience via /send_<mode>_<id>.
    Shared by the broadcast and poll flows."""
    events = storage.all_events()
    if not events:
        await callback.message.edit_text(f"Событий нет.\n\n{texts.FOOTER_HOME}")
        await callback.answer()
        return

    lines = ["Кому из участников отправить? Выберите событие:", ""]
    for ev in events:
        count = len(ev.participants)
        lines.append(
            f"<b>{texts.escape_title(ev.title)}</b>\n"
            f"{texts.format_dt(ev.start_dt)} — {count} {texts.plural_people(count)}\n"
            f"/send_{mode}_{ev.id}"
        )
    await callback.message.edit_text(
        "\n\n".join(lines),
        reply_markup=_cancel_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


# ----- broadcast flow ------------------------------------------------------ #

@router.callback_query(F.data == "adm:msg")
async def cb_msg(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Кому отправить сообщение?",
        reply_markup=_audience_menu_kb("msg"),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:aud:msg:all")
async def cb_msg_all(callback: CallbackQuery, state: FSMContext) -> None:
    # Audience "all" = every user that has ever interacted with the bot.
    await state.set_state(Broadcast.message)
    await state.update_data(target="all")
    count = len(storage.all_user_ids)
    await callback.message.edit_text(
        f"Теперь пришлите сообщение, которое нужно отправить всем "
        f"({count}). Можно текст, фото — что угодно.",
        reply_markup=_cancel_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:aud:msg:event")
async def cb_msg_event(callback: CallbackQuery) -> None:
    await _ask_event_audience(callback, "msg")


@router.message(Broadcast.message)
async def step_broadcast(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()

    recipients = _resolve_recipients(data)
    if recipients is None:
        await message.answer(f"Событие пропало.\n\n{texts.FOOTER_HOME}")
        return

    # Copy the admin's message verbatim to each recipient. copy_message sends a
    # standalone copy (not a "forwarded from" header) of any content type.
    async def send_one(uid: int) -> None:
        await bot.copy_message(
            chat_id=uid,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )

    sent, failed = await _deliver(recipients, send_one)
    await message.answer(
        f"Готово. Доставлено: {sent}. Не доставлено: {failed}.\n\n"
        f"{texts.FOOTER_HOME}"
    )


# ----- poll flow ----------------------------------------------------------- #

@router.callback_query(F.data == "adm:poll")
async def cb_poll(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Кому отправить опрос?",
        reply_markup=_audience_menu_kb("poll"),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:aud:poll:all")
async def cb_poll_all(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Poll.link)
    await state.update_data(target="all")
    count = len(storage.all_user_ids)
    await callback.message.edit_text(
        f"Опрос для всех ({count}).\n"
        "Пришлите ссылку на опрос (например, https://t.me/aipolltg_bot/pool?startapp=...):",
        reply_markup=_cancel_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:aud:poll:event")
async def cb_poll_event(callback: CallbackQuery) -> None:
    await _ask_event_audience(callback, "poll")


# Selecting an event audience by command (works for both modes).
@router.message(F.text.regexp(_SEND_RE))
async def cmd_send(message: Message, state: FSMContext) -> None:
    import re

    match = re.match(_SEND_RE, message.text)
    mode = match.group(1)
    event_id = int(match.group(2))
    event = storage.get(event_id)
    if event is None:
        await message.answer(f"Такого события нет.\n\n{texts.FOOTER_HOME}")
        return

    count = len(event.participants)
    if mode == "poll":
        await state.set_state(Poll.link)
        await state.update_data(target="event", event_id=event_id)
        await message.answer(
            f"Опрос для участников «{texts.escape_title(event.title)}» "
            f"({count} {texts.plural_people(count)}).\n"
            "Пришлите ссылку на опрос "
            "(например, https://t.me/aipolltg_bot/pool?startapp=...):",
            reply_markup=_cancel_kb(),
            parse_mode="HTML",
        )
        return

    await state.set_state(Broadcast.message)
    await state.update_data(target="event", event_id=event_id)
    await message.answer(
        f"Теперь пришлите сообщение, которое нужно отправить всем "
        f"участникам «{texts.escape_title(event.title)}» "
        f"({count} {texts.plural_people(count)}).",
        reply_markup=_cancel_kb(),
        parse_mode="HTML",
    )


@router.message(Poll.link, F.text)
async def step_poll_link(message: Message, state: FSMContext) -> None:
    # Link is not validated — Telegram rejects a malformed url:button at send
    # time, which surfaces as a per-recipient failure in the final report.
    await state.update_data(link=message.text.strip())
    await state.set_state(Poll.button)
    await message.answer(
        "Текст на кнопке (например, <b>Пройти опрос</b>):",
        reply_markup=_cancel_kb(),
        parse_mode="HTML",
    )


@router.message(Poll.button, F.text)
async def step_poll_button(message: Message, state: FSMContext) -> None:
    await state.update_data(button=message.text.strip())
    await state.set_state(Poll.message)
    await message.answer(
        "Теперь напишите сообщение к опросу:",
        reply_markup=_cancel_kb(),
    )


@router.message(Poll.message, F.text)
async def step_poll_message(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()

    recipients = _resolve_recipients(data)
    if recipients is None:
        await message.answer(f"Событие пропало.\n\n{texts.FOOTER_HOME}")
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=data["button"], url=data["link"])]
        ]
    )
    # html_text preserves the admin's own formatting (bold, links) for HTML send.
    body = message.html_text

    async def send_one(uid: int) -> None:
        await bot.send_message(
            chat_id=uid,
            text=body,
            reply_markup=kb,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    sent, failed = await _deliver(recipients, send_one)
    await message.answer(
        f"Опрос отправлен. Доставлено: {sent}. Не доставлено: {failed}.\n\n"
        f"{texts.FOOTER_HOME}"
    )


# Every poll step needs plain text; re-prompt instead of crashing on photos etc.
@router.message(Poll.link)
@router.message(Poll.button)
@router.message(Poll.message)
async def step_poll_need_text(message: Message) -> None:
    await message.answer(
        "Нужен текст. Пришлите текстом или нажмите «Отмена».",
        reply_markup=_cancel_kb(),
    )


# ----- deletion flow ------------------------------------------------------- #

@router.callback_query(F.data == "adm:del")
async def cb_del_list(callback: CallbackQuery) -> None:
    events = storage.all_events()
    if not events:
        await callback.message.edit_text(
            f"Удалять нечего — список пуст.\n\n{texts.FOOTER_HOME}"
        )
        await callback.answer()
        return

    rows = [
        [
            InlineKeyboardButton(
                text=f"{texts.format_dt(ev.start_dt)} — {ev.title}",
                callback_data=f"adm:delitem:{ev.id}",
            )
        ]
        for ev in events
    ]
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="adm:abort")])
    await callback.message.edit_text(
        "Какое мероприятие удалить?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:delitem:"))
async def cb_del_item(callback: CallbackQuery) -> None:
    event_id = int(callback.data.split(":")[2])
    event = storage.get(event_id)
    if event is None:
        await callback.message.edit_text(f"Уже удалено.\n\n{texts.FOOTER_HOME}")
        await callback.answer()
        return

    storage.delete_event(event_id)
    await callback.message.edit_text(
        f"Удалено: «{texts.escape_title(event.title)}» "
        f"({texts.format_dt(event.start_dt)}).\n\n{texts.FOOTER_HOME}"
    )
    await callback.answer()
