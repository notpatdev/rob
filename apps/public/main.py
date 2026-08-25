from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from rob.config.settings import configure_logging, load_public_api_settings
from rob.database.connection import Database
from rob.publicapi import create_public_api_app

log = logging.getLogger(__name__)


async def main_async() -> None:
    settings = load_public_api_settings()
    configure_logging(settings.log_level)

    database = Database(settings.database_url)
    await database.connect()

    app = create_public_api_app(settings=settings, database=database)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()

    site = web.TCPSite(
        runner,
        host=settings.public_api_host,
        port=settings.public_api_port,
    )
    await site.start()

    log.info(
        "Rob public API listening on %s:%s (allowed origin: %s)",
        settings.public_api_host,
        settings.public_api_port,
        settings.public_api_allowed_origin,
    )

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
        await database.close()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
