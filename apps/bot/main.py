from __future__ import annotations

import asyncio

from rob.config.settings import configure_logging, load_bot_settings
from rob.discord.client import RobBot


async def main_async() -> None:
    settings = load_bot_settings()
    configure_logging(settings.log_level)

    bot = RobBot(settings)
    try:
        await bot.start(settings.discord_token, reconnect=True)
    finally:
        await bot.close()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
