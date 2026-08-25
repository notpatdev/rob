# Cloudflared: Webhook Routing

This guide configures Cloudflared for the production webhook host only.

Target routing:

- `https://throne.robthebot.com` -> `http://127.0.0.1:8080`
- Route the hostname to the local webhook origin so `/health`, `/webhook/*`, and `/throne/webhook/*` stay reachable through the same tunnel host.

Safety rules:

- Do not open port `8080` publicly.
- Do not commit tunnel tokens or credential JSON files.
- Do not copy random credential JSON files; the installer now performs a named-tunnel login flow and installs the exact tunnel credentials at `/etc/cloudflared/rob-webhook.json`.
- On Ubuntu/Debian, use the official Cloudflare apt repository (the installer handles this).

## Install sequence

```bash
sudo bash deploy/scripts/install-webhook.sh
sudo nano /opt/rob-webhook/app/.env
sudo chown "${USER}:rob" /opt/rob-webhook/app/.env
sudo chmod 0640 /opt/rob-webhook/app/.env
cd /opt/rob-webhook/app
set -a
source .env
set +a
PYTHONPATH=. .venv/bin/python -m ops.checks.check_db
sudo systemctl restart rob-webhook.service
curl -fsS http://127.0.0.1:8080/health
sudo bash deploy/scripts/install-cloudflared-webhook.sh
curl -I https://throne.robthebot.com/health
```

The Cloudflared installer now uses the browser-based `cloudflared tunnel login` flow and named tunnel routing. It does not prompt for a token-managed tunnel key.

## Moving to a new server without changing tracking links

Keep the same named tunnel and hostname route. The public URLs stay the same as long as the DNS record still points at the same tunnel UUID.

Recommended cutover:

1. On the old webhook host, copy `/etc/cloudflared/rob-webhook.json` somewhere safe.
2. Copy that JSON file to the new webhook host over SSH or another secure channel.
3. Install the webhook app on the new host and confirm `http://127.0.0.1:8080/health` works locally.
4. Run the Cloudflared installer on the new host with the copied credentials:

```bash
sudo SOURCE_CREDENTIALS_FILE=/root/rob-webhook.json bash deploy/scripts/install-cloudflared-webhook.sh
```

That reuses the same tunnel UUID and existing DNS route, so you do not need to create a new tunnel or reissue tracking links.
If the new host does not also have a Cloudflare login `cert.pem`, the installer will now skip the DNS API step automatically. That is fine when the hostname is already routed to the existing tunnel.

After the new host is healthy:

```bash
curl -I https://throne.robthebot.com/health
sudo systemctl status cloudflared --no-pager
```

Once traffic is confirmed on the new host, stop `cloudflared` on the old host.

## Verify services

```bash
sudo systemctl status cloudflared --no-pager
sudo journalctl -u cloudflared -n 100 --no-pager
sudo systemctl status rob-webhook.service --no-pager
sudo journalctl -u rob-webhook.service -n 100 --no-pager
```
