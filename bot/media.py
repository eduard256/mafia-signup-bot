"""Image helpers.

The bot ships with a few moody Mafia-themed images in ``assets/``. To keep
messages pretty without re-uploading the same file on every send, Telegram
``file_id`` values are cached in memory after the first upload: the first send
uploads the bytes, every subsequent send reuses the lightweight file_id.
"""

from __future__ import annotations

from aiogram import Bot
from aiogram.types import FSInputFile, Message

from .config import ASSETS_DIR

# Logical names mapped to files in assets/. Using names (not paths) elsewhere
# keeps call sites readable and makes it trivial to swap artwork later.
IMAGES = {
    "welcome": ASSETS_DIR / "mafia_collage_2.jpg",   # shown on /start
    "cards": ASSETS_DIR / "mafia_cards.jpg",         # shown on successful signup
    "call": ASSETS_DIR / "mafia_collage_1.jpg",      # shown with the start/reminder
}

# Cache of uploaded file_ids keyed by logical image name.
_file_id_cache: dict[str, str] = {}


async def send_photo(
    bot: Bot, chat_id: int, image: str, caption: str
) -> Message:
    """Send one of the bundled images with an HTML caption.

    Reuses a cached Telegram file_id when available; otherwise uploads the
    local file and caches the resulting file_id for next time.
    """
    cached = _file_id_cache.get(image)
    if cached is not None:
        photo = cached
    else:
        path = IMAGES[image]
        photo = FSInputFile(path)

    msg = await bot.send_photo(
        chat_id=chat_id, photo=photo, caption=caption, parse_mode="HTML"
    )

    # Cache the largest photo size's file_id for subsequent sends.
    if cached is None and msg.photo:
        _file_id_cache[image] = msg.photo[-1].file_id

    return msg
