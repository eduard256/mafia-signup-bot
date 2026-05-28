"""Administrator command handlers.

A single administrator (``config.admin_id``) manages events through ``/admin``,
which shows an inline keyboard with two actions:

* Create event — a step-by-step FSM dialog: kind -> title -> date -> time ->
  description -> link, with a confirmation summary at the end.
* Delete event — lists events as buttons; tapping one removes it.

Broadcasts and reminders are fully automatic and intentionally have no admin
controls.
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
    link = State()


class Broadcast(StatesGroup):
    # Waiting for the message to forward. The target audience (all users, or a
    # specific event's participants) is stored in FSM data under "target".
    message = State()


def _admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Создать мероприятие", callback_data="adm:new")],
            [InlineKeyboardButton(text="Удалить мероприятие", callback_data="adm:del")],
            [InlineKeyboardButton(text="Отправить сообщение", callback_data="adm:msg")],
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
    await state.update_data(capacity=int(raw))
    await state.set_state(NewEvent.link)
    await message.answer(
        "Ссылка на комнату (её разошлю в момент старта):",
        reply_markup=_cancel_kb(),
    )


@router.message(NewEvent.link)
async def step_link(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    event = storage.create_event(
        kind=data["kind"],
        title=data["title"],
        description=data["description"],
        link=message.text.strip(),
        start=data["start"],
        capacity=data["capacity"],
    )
    await state.clear()

    start_dt = event.start_dt
    await message.answer(
        "<b>Мероприятие создано.</b>\n\n"
        f"{texts.escape_title(event.title)}\n"
        f"{texts.format_dt(start_dt)}\n"
        f"Лимит: {event.capacity}\n"
        f"Команда записи: /add_{event.id}\n"
        f"Участники: /who_{event.id}\n\n"
        f"{texts.FOOTER_HOME}",
        parse_mode="HTML",
    )


# ----- broadcast flow ------------------------------------------------------ #

# Regex for "/send_<id>" used to pick an event's participants as the audience.
_SEND_RE = r"^/send_(\d+)(?:@\w+)?$"


def _broadcast_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отправить всем", callback_data="adm:msgall")],
            [
                InlineKeyboardButton(
                    text="Отправить участникам события",
                    callback_data="adm:msgevent",
                )
            ],
            [InlineKeyboardButton(text="Отмена", callback_data="adm:abort")],
        ]
    )


@router.callback_query(F.data == "adm:msg")
async def cb_msg(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Кому отправить сообщение?",
        reply_markup=_broadcast_menu_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:msgall")
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


@router.callback_query(F.data == "adm:msgevent")
async def cb_msg_event(callback: CallbackQuery) -> None:
    events = storage.all_events()
    if not events:
        await callback.message.edit_text(
            f"Событий нет.\n\n{texts.FOOTER_HOME}"
        )
        await callback.answer()
        return

    # List events so the admin picks the target audience via /send_<id>.
    lines = ["Кому из участников отправить? Выберите событие:", ""]
    for ev in events:
        count = len(ev.participants)
        lines.append(
            f"<b>{texts.escape_title(ev.title)}</b>\n"
            f"{texts.format_dt(ev.start_dt)} — {count} {texts.plural_people(count)}\n"
            f"/send_{ev.id}"
        )
    await callback.message.edit_text(
        "\n\n".join(lines),
        reply_markup=_cancel_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(F.text.regexp(_SEND_RE))
async def cmd_send(message: Message, state: FSMContext) -> None:
    import re

    event_id = int(re.match(_SEND_RE, message.text).group(1))
    event = storage.get(event_id)
    if event is None:
        await message.answer(f"Такого события нет.\n\n{texts.FOOTER_HOME}")
        return

    await state.set_state(Broadcast.message)
    await state.update_data(target="event", event_id=event_id)
    count = len(event.participants)
    await message.answer(
        f"Теперь пришлите сообщение, которое нужно отправить всем "
        f"участникам «{texts.escape_title(event.title)}» "
        f"({count} {texts.plural_people(count)}).",
        reply_markup=_cancel_kb(),
        parse_mode="HTML",
    )


@router.message(Broadcast.message)
async def step_broadcast(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()

    if data.get("target") == "event":
        event = storage.get(data["event_id"])
        if event is None:
            await message.answer(f"Событие пропало.\n\n{texts.FOOTER_HOME}")
            return
        recipients = list(event.participants)
    else:
        recipients = storage.all_user_ids

    # Copy the admin's message verbatim to each recipient. copy_message sends a
    # standalone copy (not a "forwarded from" header) of any content type.
    sent = 0
    failed = 0
    for uid in recipients:
        try:
            await bot.copy_message(
                chat_id=uid,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
            sent += 1
        except Exception as exc:  # noqa: BLE001 - one bad recipient must not stop the run
            failed += 1
            logger.warning("Broadcast to %s failed: %s", uid, exc)
        # Stay comfortably under Telegram's ~30 msg/sec broadcast limit.
        await asyncio.sleep(0.05)

    await message.answer(
        f"Готово. Доставлено: {sent}. Не доставлено: {failed}.\n\n"
        f"{texts.FOOTER_HOME}"
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
