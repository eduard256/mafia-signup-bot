"""Persistent storage layer.

The bot keeps all of its state in two small JSON files inside the ``data/``
directory:

* ``events.json`` — the list of events together with the user ids signed up to
  each of them and the set of reminders that have already been fired.
* ``users.json`` — every Telegram user that has ever interacted with the bot
  (used so the bot can later message them).

JSON is deliberately chosen over a real database: the expected volume is tiny
(a handful of events, a few dozen participants) and a human-readable, easily
backed-up file is the simplest thing that works. All writes are atomic
(write-to-temp + ``os.replace``) so an interrupted write can never corrupt the
existing data, which is what guarantees state survives restarts cleanly.

Times are stored as naive ISO strings representing wall-clock time in the
configured timezone (e.g. Europe/Moscow). They are converted to timezone-aware
``datetime`` objects on the fly via :meth:`Event.start_dt`.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .config import config


@dataclass
class Event:
    """A single scheduled event (a Mafia game, or anything else later)."""

    id: int
    kind: str               # e.g. "Мафия" — the type/category of the event
    title: str              # human-readable title
    description: str        # free-form description shown to users
    link: str               # meeting URL announced when the event starts
    # Naive wall-clock start time (in config.timezone) as ISO string.
    start: str
    participants: list[int] = field(default_factory=list)
    # Reminder offsets (in minutes) that have already been delivered, so the
    # scheduler never sends the same reminder twice across restarts.
    sent_reminders: list[int] = field(default_factory=list)

    @property
    def start_dt(self) -> datetime:
        """Return the start time as a timezone-aware datetime."""
        return datetime.fromisoformat(self.start).replace(tzinfo=config.timezone)

    def is_signed_up(self, user_id: int) -> bool:
        return user_id in self.participants

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "description": self.description,
            "link": self.link,
            "start": self.start,
            "participants": self.participants,
            "sent_reminders": self.sent_reminders,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Event":
        return cls(
            id=data["id"],
            kind=data.get("kind", "Мафия"),
            title=data["title"],
            description=data.get("description", ""),
            link=data["link"],
            start=data["start"],
            participants=list(data.get("participants", [])),
            sent_reminders=list(data.get("sent_reminders", [])),
        )


def _read_json(path: Path, default):
    """Read JSON from ``path`` returning ``default`` if the file is absent."""
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        # A corrupt/empty file should not crash the bot; fall back to default.
        return default


def _write_json_atomic(path: Path, data) -> None:
    """Write ``data`` as JSON atomically to avoid partial/corrupt files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file in the same directory, then atomically replace.
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    finally:
        # Clean up the temp file if replace did not consume it (on error).
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


class Storage:
    """In-memory cache of events/users backed by atomic JSON persistence."""

    def __init__(self) -> None:
        self._events: dict[int, Event] = {}
        self._next_id: int = 1
        self._users: set[int] = set()
        self._load()

    # ----- persistence -------------------------------------------------- #

    def _load(self) -> None:
        raw = _read_json(config.events_file, default={"next_id": 1, "events": []})
        self._next_id = raw.get("next_id", 1)
        self._events = {
            ev["id"]: Event.from_dict(ev) for ev in raw.get("events", [])
        }
        users_raw = _read_json(config.users_file, default=[])
        self._users = set(int(u) for u in users_raw)

    def _save_events(self) -> None:
        _write_json_atomic(
            config.events_file,
            {
                "next_id": self._next_id,
                "events": [ev.to_dict() for ev in self._events.values()],
            },
        )

    def _save_users(self) -> None:
        _write_json_atomic(config.users_file, sorted(self._users))

    # ----- users -------------------------------------------------------- #

    def remember_user(self, user_id: int) -> None:
        """Record a user id (idempotent). Used so we can message them later."""
        if user_id not in self._users:
            self._users.add(user_id)
            self._save_users()

    @property
    def all_user_ids(self) -> list[int]:
        return sorted(self._users)

    # ----- events: queries --------------------------------------------- #

    def get(self, event_id: int) -> Event | None:
        return self._events.get(event_id)

    def upcoming(self, now: datetime) -> list[Event]:
        """Return future events sorted by start time (earliest first)."""
        return sorted(
            (ev for ev in self._events.values() if ev.start_dt > now),
            key=lambda ev: ev.start_dt,
        )

    def all_events(self) -> list[Event]:
        return sorted(self._events.values(), key=lambda ev: ev.start_dt)

    # ----- events: mutations ------------------------------------------- #

    def create_event(
        self, *, kind: str, title: str, description: str, link: str, start: str
    ) -> Event:
        event = Event(
            id=self._next_id,
            kind=kind,
            title=title,
            description=description,
            link=link,
            start=start,
        )
        self._events[event.id] = event
        self._next_id += 1
        self._save_events()
        return event

    def delete_event(self, event_id: int) -> bool:
        if event_id in self._events:
            del self._events[event_id]
            self._save_events()
            return True
        return False

    def add_participant(self, event_id: int, user_id: int) -> bool:
        """Sign a user up. Returns False if already signed up or no event."""
        event = self._events.get(event_id)
        if event is None or user_id in event.participants:
            return False
        event.participants.append(user_id)
        self._save_events()
        return True

    def remove_participant(self, event_id: int, user_id: int) -> bool:
        """Cancel a signup. Returns False if not signed up or no event."""
        event = self._events.get(event_id)
        if event is None or user_id not in event.participants:
            return False
        event.participants.remove(user_id)
        self._save_events()
        return True

    def mark_reminder_sent(self, event_id: int, offset: int) -> None:
        event = self._events.get(event_id)
        if event is not None and offset not in event.sent_reminders:
            event.sent_reminders.append(offset)
            self._save_events()

    def purge_past(self, now: datetime, grace_minutes: int = 0) -> list[int]:
        """Delete events whose start time is in the past.

        A small grace period (in minutes) can be allowed so that the final
        "join now" reminder fires reliably before the event disappears.
        Returns the list of deleted event ids.
        """
        deleted: list[int] = []
        for event_id, event in list(self._events.items()):
            age_minutes = (now - event.start_dt).total_seconds() / 60
            if age_minutes >= grace_minutes:
                del self._events[event_id]
                deleted.append(event_id)
        if deleted:
            self._save_events()
        return deleted


# A single shared instance used across the whole application.
storage = Storage()
