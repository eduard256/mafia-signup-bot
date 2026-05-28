"""Reminder scheduler.

A single background job runs every minute and, for each upcoming event,
delivers any reminder whose offset window has been reached:

    240, 180, 120, 60 minutes before  -> "starts in N hours" notification
    0 minutes before (at start time)   -> final notification with the join link

Each fired reminder is recorded in the event (``sent_reminders``) and persisted,
so reminders survive restarts and are never sent twice. After the final
notification has gone out, the event is purged a few minutes later so it stops
appearing in /start.

The one-minute tick is intentionally simple and robust: even if the bot is
offline for a while, on restart it will immediately send any reminders that
became due during the downtime (skipping ones whose window has fully passed via
the per-offset guard), which is the desired catch-up behaviour.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from . import texts
from .config import config
from .media import send_photo
from .storage import storage

logger = logging.getLogger(__name__)

# How long after start an event is kept before being purged. Gives the final
# "join now" reminder room to be delivered, then cleans up.
_PURGE_GRACE_MINUTES = 5

# A reminder is considered "due" within this many minutes after its exact
# moment. Wider than the 1-minute tick so a slightly late tick never skips one.
_DUE_WINDOW_MINUTES = 10


async def _send_reminder(bot: Bot, event, offset: int) -> None:
    """Deliver one reminder for ``offset`` to every participant of ``event``."""
    is_final = offset == 0
    image = "call"
    caption = (
        texts.starting_now(event) if is_final else texts.reminder(event, offset)
    )

    for user_id in list(event.participants):
        try:
            await send_photo(bot, user_id, image, caption)
        except Exception as exc:  # noqa: BLE001 - never let one user break the loop
            logger.warning("Failed to notify %s for event %s: %s", user_id, event.id, exc)
        # Gentle pacing to stay well under Telegram rate limits.
        await asyncio.sleep(0.05)

    storage.mark_reminder_sent(event.id, offset)
    logger.info("Sent reminder offset=%s for event %s", offset, event.id)


async def _tick(bot: Bot) -> None:
    """One scheduler iteration: send due reminders, then purge stale events."""
    now = datetime.now(tz=config.timezone)

    for event in storage.all_events():
        minutes_to_start = (event.start_dt - now).total_seconds() / 60
        for offset in config.reminder_offsets:
            if offset in event.sent_reminders:
                continue
            # Due when we are within the window at or after the offset moment,
            # i.e. minutes_to_start has dropped to <= offset (but not long past).
            if offset - _DUE_WINDOW_MINUTES <= minutes_to_start <= offset:
                await _send_reminder(bot, event, offset)

    # Remove events that finished long enough ago to stop showing them.
    deleted = storage.purge_past(now, grace_minutes=_PURGE_GRACE_MINUTES)
    if deleted:
        logger.info("Purged past events: %s", deleted)


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    """Create and start the AsyncIO scheduler with the one-minute tick."""
    scheduler = AsyncIOScheduler(timezone=config.timezone)
    scheduler.add_job(
        _tick,
        trigger="interval",
        minutes=1,
        args=[bot],
        # Coalesce missed runs into one and never run two ticks concurrently.
        coalesce=True,
        max_instances=1,
        # Fire once shortly after startup for immediate catch-up.
        next_run_time=datetime.now(tz=config.timezone),
    )
    scheduler.start()
    return scheduler
