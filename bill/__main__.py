from __future__ import annotations

import logging

from bill.bot import BillBot
from bill.settings import Settings, SettingsError


def main() -> None:
    try:
        settings = Settings.from_env()
    except SettingsError as exc:
        raise SystemExit(f"Bill configuration error: {exc}") from exc
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    BillBot(settings).run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
