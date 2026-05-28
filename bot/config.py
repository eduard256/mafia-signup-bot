"""Application configuration.

All runtime configuration is read from environment variables (loaded from a
local ``.env`` file in development). The module exposes a single ``Config``
instance, :data:`config`, that the rest of the application imports.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Resolve important filesystem paths relative to the project root so the bot
# behaves identically regardless of the current working directory it is
# launched from (locally, via systemd, or inside Docker).
BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BASE_DIR / "assets"
DATA_DIR = BASE_DIR / "data"

# Load variables from .env if present. In production (Docker) the variables are
# injected by the environment, in which case load_dotenv is simply a no-op.
load_dotenv(BASE_DIR / ".env")


def _require(name: str) -> str:
    """Return a mandatory environment variable or raise a clear error."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable {name!r}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


@dataclass(frozen=True)
class Config:
    """Immutable container for all runtime settings."""

    bot_token: str
    admin_id: int
    timezone: ZoneInfo
    timezone_name: str

    # Reminder offsets in minutes before the event start. ``0`` means a final
    # notification sent exactly at the event start time.
    reminder_offsets: tuple[int, ...] = (240, 180, 120, 60, 0)

    @property
    def events_file(self) -> Path:
        """Path to the JSON file that persists all events and signups."""
        return DATA_DIR / "events.json"

    @property
    def users_file(self) -> Path:
        """Path to the JSON file that persists known bot users."""
        return DATA_DIR / "users.json"


def _load() -> Config:
    tz_name = os.getenv("TZ", "Europe/Moscow")
    return Config(
        bot_token=_require("BOT_TOKEN"),
        admin_id=int(_require("ADMIN_ID")),
        timezone=ZoneInfo(tz_name),
        timezone_name=tz_name,
    )


# Ensure the data directory exists before anything tries to write to it.
DATA_DIR.mkdir(parents=True, exist_ok=True)

config = _load()
