"""Application entry point.

Wires together the dispatcher, routers, bot commands menu and the reminder
scheduler, then starts long polling. Run with ``python -m bot`` or via the
thin ``main.py`` shim at the project root.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from .config import config
from .handlers_admin import router as admin_router
from .handlers_public import router as public_router
from .scheduler import setup_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("mafia-bot")


async def _set_commands(bot: Bot) -> None:
    """Populate the Telegram command menu shown to users."""
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Список мероприятий"),
        ]
    )


async def main() -> None:
    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Admin router first so admin-only handlers take precedence; its filters
    # ensure non-admins fall through to the public router.
    dp.include_router(admin_router)
    dp.include_router(public_router)

    await _set_commands(bot)
    scheduler = setup_scheduler(bot)
    logger.info("Bot started. Admin id=%s, tz=%s", config.admin_id, config.timezone_name)

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
