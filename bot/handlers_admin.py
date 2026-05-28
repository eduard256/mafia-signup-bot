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

from aiogram import F, Router
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
    link = State()


def _admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Создать мероприятие", callback_data="adm:new")],
            [InlineKeyboardButton(text="Удалить мероприятие", callback_data="adm:del")],
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
        "<b>Панель семьи.</b>\nЧто делаем?",
        reply_markup=_admin_menu_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "adm:abort")
async def cb_abort(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Отменено.")
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
    )
    await state.clear()

    start_dt = event.start_dt
    await message.answer(
        "<b>Мероприятие создано.</b>\n\n"
        f"{texts.escape_title(event.title)}\n"
        f"{texts.format_dt(start_dt)}\n"
        f"Команда записи: /add_{event.id}\n"
        f"Участники: /who_{event.id}",
        parse_mode="HTML",
    )


# ----- deletion flow ------------------------------------------------------- #

@router.callback_query(F.data == "adm:del")
async def cb_del_list(callback: CallbackQuery) -> None:
    events = storage.all_events()
    if not events:
        await callback.message.edit_text("Удалять нечего — список пуст.")
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
        await callback.message.edit_text("Уже удалено.")
        await callback.answer()
        return

    storage.delete_event(event_id)
    await callback.message.edit_text(
        f"Удалено: «{texts.escape_title(event.title)}» "
        f"({texts.format_dt(event.start_dt)})."
    )
    await callback.answer()
