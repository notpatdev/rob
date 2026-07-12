# Deployment: Webhook (Prod-Role Rehearsal)

Use this on the webhook host:

- App path: `/opt/rob-webhook/app`
- Service: `rob-webhook-dev.service`
- Runtime DB user: `prod_rob_webhook`
- Rehearsal DB: `rob_dev_v2`
- Public webhook URL base: `https://throne.robthebot.com`
- Local bind: `127.0.0.1:8080`

`deploy/scripts/install-webhook-dev.sh` is safe-by-default:

- does not overwrite an existing `.env`;
- does not run DB build SQL;
- does not run SQLite import;
- does not create DB users;
- warns if stale values are found in `.env`.

Webhook `.env` should be webhook-only (no `DISCORD_TOKEN`).

```dotenv
APP_ENV=prod
LOG_LEVEL=INFO
DATABASE_URL=postgresql://prod_rob_webhook:replace@replace:25060/rob_dev_v2?sslmode=require
THRONE_WEBHOOK_HOST=127.0.0.1
THRONE_WEBHOOK_PORT=8080
THRONE_WEBHOOK_BASE_URL=https://throne.robthebot.com
THRONE_WEBHOOK_REQUIRE_SIGNATURE=false
THRONE_PUBLIC_KEY_PEM=
THRONE_WEBHOOK_DEBUG_LOG_PAYLOAD=false
THRONE_WEBHOOK_TIMESTAMP_HEADER=X-Signature-Timestamp
THRONE_WEBHOOK_SIGNATURE_HEADER=X-Signature-Ed25519
THRONE_WEBHOOK_SIGNED_MESSAGE_FORMAT=timestamp_dot_body
THRONE_WEBHOOK_MAX_TIMESTAMP_SKEW_SECONDS=300
THRONE_PARSE_TEST_SENDS_AS_REAL_SENDS=false
```

## Canonical install sequence

```bash
sudo bash deploy/scripts/install-webhook-dev.sh
sudo nano /opt/rob-webhook/app/.env
sudo chown "${USER}:rob" /opt/rob-webhook/app/.env
sudo chmod 0640 /opt/rob-webhook/app/.env
cd /opt/rob-webhook/app
set -a
source .env
set +a
PYTHONPATH=. .venv/bin/python -m ops.checks.check_db
sudo systemctl restart rob-webhook-dev.service
sudo systemctl status rob-webhook-dev.service --no-pager
curl -fsS http://127.0.0.1:8080/health
```

## Cloudflared sequence

```bash
sudo bash deploy/scripts/install-cloudflared-webhook.sh
sudo systemctl status cloudflared --no-pager
sudo journalctl -u cloudflared -n 100 --no-pager
curl -I https://throne.robthebot.com/health
```

Cloudflared should route:

- `throne.robthebot.com -> http://127.0.0.1:8080`

Do not expose port `8080` publicly.
