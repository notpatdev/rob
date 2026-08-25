from __future__ import annotations

from rob.config.settings import load_bot_settings


def main() -> None:
    settings = load_bot_settings()
    print(
        "Loaded bot settings:",
        f"bot_name={settings.bot_name}",
        f"ops_host={settings.rob_ops_host}:{settings.rob_ops_port}",
        f"notify_url={settings.rob_bot_notify_url or 'unset'}",
    )
    if not settings.rob_ops_secret:
        print(
            "WARNING: ROB_OPS_SECRET is blank; bot ops health checks and webhook-to-bot sync are unauthenticated."
        )


if __name__ == "__main__":
    main()
