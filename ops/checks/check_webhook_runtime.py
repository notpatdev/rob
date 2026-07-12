from __future__ import annotations

from rob.config.settings import load_webhook_settings


def main() -> None:
    settings = load_webhook_settings()
    print(
        "Loaded webhook settings:",
        f"listen={settings.throne_webhook_host}:{settings.throne_webhook_port}",
        f"public_base={settings.throne_webhook_base_url}",
        f"notify_url={settings.rob_bot_notify_url or 'unset'}",
    )
    if settings.rob_bot_notify_url and not settings.rob_ops_secret:
        print(
            "WARNING: ROB_BOT_NOTIFY_URL is set but ROB_OPS_SECRET is blank; the bot-notify bridge may be unauthenticated."
        )


if __name__ == "__main__":
    main()
