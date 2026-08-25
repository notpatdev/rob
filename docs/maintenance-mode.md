# Maintenance Mode

Maintenance mode is stored in PostgreSQL `bot_settings` using:

- `maintenance_mode`
- `maintenance_reason`

It is controlled from backend shell commands, not from broad Discord admin commands.

## During Maintenance

- Incoming sends are still saved to PostgreSQL.
- Webhook sends are inserted as `queued_maintenance`.
- The bot does not post queued sends to Discord.
- Leaderboard messages continue to reflect posted sends only.
- Legacy imported sends that were seeded as `posted` stay out of the queue.

## After Maintenance

When maintenance mode is disabled, the bot queue worker:

1. Releases `queued_maintenance` sends back to `pending`.
2. Processes them oldest-first.
3. Marks successful posts as `posted`.
4. Marks failures as `failed`.
5. Refreshes leaderboard messages.

## Commands

```bash
ops/cli/rob maintenance status
ops/cli/rob maintenance on "reason"
ops/cli/rob maintenance off
ops/cli/rob queue status
ops/cli/rob queue flush
```

## Rob Offline Mode

Rob Offline Mode is a separate bare-features mode for the production VIB guild only. It is stored in PostgreSQL `bot_settings` using:

- `rob_offline_mode`

When enabled for the main guild:

- Discord slash commands are blocked except `/add`.
- `/leaderboard` and `/register` are blocked with the Rob offline embed.
- Manual sends from `/add` are saved without a Discord send notification.
- Throne webhook sends continue to be saved by the webhook server without Discord send-tracking posts.
- Discord leaderboard refreshes render the Rob offline embed instead of live stats.
- Counting messages continue to work, but count recovery windows are not started or resolved by sends.

The dev/test guild is intentionally unaffected by this mode.

```bash
ops/cli/rob offline status
ops/cli/rob offline on
ops/cli/rob offline off
```
