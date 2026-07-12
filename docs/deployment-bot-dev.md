# Deployment: Bot (Prod-Role Rehearsal)

Use this on the bot host:

- App path: `/opt/rob-bot/app`
- Service: `rob-bot-dev.service`
- Runtime DB user: `prod_rob_bot`
- Rehearsal DB: `rob_dev_v2`

`deploy/scripts/install-bot-dev.sh` is safe-by-default:

- does not overwrite an existing `.env`;
- does not run DB build SQL;
- does not run SQLite import;
- does not create DB users;
- warns if stale values are found in `.env`.

Bot `.env` should include Discord bot values:

```dotenv
APP_ENV=prod
LOG_LEVEL=INFO
DATABASE_URL=postgresql://prod_rob_bot:replace@replace:25060/rob_dev_v2?sslmode=require
DISCORD_TOKEN=replace
DISCORD_GUILD_ID=replace
BOT_NAME=Rob
THRONE_WEBHOOK_BASE_URL=https://throne.robthebot.com
```

`THRONE_WEBHOOK_BASE_URL` is optional on bot hosts, but recommended when bot flows need to render webhook URLs.

## Canonical install sequence

```bash
sudo bash deploy/scripts/install-bot-dev.sh
sudo nano /opt/rob-bot/app/.env
sudo chown "${USER}:rob" /opt/rob-bot/app/.env
sudo chmod 0640 /opt/rob-bot/app/.env
cd /opt/rob-bot/app
set -a
source .env
set +a
PYTHONPATH=. .venv/bin/python -m ops.checks.check_db
sudo systemctl restart rob-bot-dev.service
sudo systemctl status rob-bot-dev.service --no-pager
sudo journalctl -u rob-bot-dev.service -n 100 --no-pager
```
