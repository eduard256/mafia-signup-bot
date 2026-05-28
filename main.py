"""Convenience entry point so the bot can be started with ``python main.py``.

The real application lives in the ``bot`` package; this simply delegates to it.
"""

from bot.__main__ import main
import asyncio

if __name__ == "__main__":
    asyncio.run(main())
