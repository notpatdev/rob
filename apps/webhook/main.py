from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from rob.config.settings import configure_logging, load_webhook_settings
from rob.database.connection import Database
from rob.throne.webhooks import create_webhook_app
from rob.utils.fx import run_rate_refresher

log = logging.getLogger(__name__)


async def main_async() -> None:
    settings = load_webhook_settings()
    configure_logging(settings.log_level)

    database = Database(settings.database_url)
    await database.connect()

    app = create_webhook_app(settings=settings, database=database)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()

    site = web.TCPSite(
        runner,
        host=settings.throne_webhook_host,
        port=settings.throne_webhook_port,
    )
    await site.start()

    log.info(
        "Rob webhook server listening on %s:%s",
        settings.throne_webhook_host,
        settings.throne_webhook_port,
    )

    # Fetch live ECB rates on startup and every 12h; failures fall back to
    # the bundled snapshot (see rob.utils.fx).
    fx_refresher = asyncio.create_task(run_rate_refresher(), name="rob-fx-refresher")

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        fx_refresher.cancel()
        try:
            await fx_refresher
        except asyncio.CancelledError:
            pass
        await runner.cleanup()
        await database.close()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
