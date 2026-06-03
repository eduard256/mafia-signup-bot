"""Client for the meet.webaweba.com room API.

A meeting room is created automatically for every event at creation time, sized
to the event's participant capacity. The room is permanent (no expiry) and one
fresh room is allocated per event.

Example API exchange::

    POST https://meet.webaweba.com/api/rooms
    {"maxParticipants": 15}

    -> 201 Created
       {"slug": "wz8-h32-d9l",
        "url": "https://meet.webaweba.com/r/wz8-h32-d9l",
        "maxParticipants": 15}
"""

from __future__ import annotations

import aiohttp

# Base endpoint for room creation.
_ROOMS_URL = "https://meet.webaweba.com/api/rooms"

# The API accepts capacities in the 2..100 range.
_MIN_PARTICIPANTS = 2
_MAX_PARTICIPANTS = 100


class MeetError(Exception):
    """Raised when a meeting room could not be created."""


async def create_room(max_participants: int) -> str:
    """Create a meeting room and return its public URL.

    ``max_participants`` is clamped into the API-supported 2..100 range so an
    event capacity outside that window still yields a usable room.

    Raises :class:`MeetError` on any network, timeout, or response problem.
    """
    capacity = max(_MIN_PARTICIPANTS, min(_MAX_PARTICIPANTS, max_participants))
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                _ROOMS_URL, json={"maxParticipants": capacity}
            ) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    raise MeetError(f"HTTP {resp.status}: {body[:200]}")
                data = await resp.json()
    except aiohttp.ClientError as exc:
        raise MeetError(f"Сеть: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - surface any unexpected failure cleanly
        raise MeetError(str(exc)) from exc

    url = data.get("url")
    if not url:
        raise MeetError(f"В ответе нет url: {data!r}")
    return url
