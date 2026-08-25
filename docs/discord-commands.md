# Discord Commands

## Public commands

- `/register domme` (no options; starts Dom/me DM setup flow and collects Throne profile in modal)
- `/register sub` (no options; opens modal for up to 3 Throne usernames/send names)
- `/leaderboard`
- `/achievements`
- `/report`

## Dom/me commands

- `/add`

## Counting commands

- `/count set {number}`

## Developer test commands

- `/test achievements` (requires owner/mod/manage-guild permissions)

## Owner commands

- `/winddown` (owner-only; main + test guilds) — view or control the scheduled
  wind-down phase (`phase:0-3`, `auto:true|false`). See [`wind-down.md`](wind-down.md).
- `/shutdown` (owner-only; main + test guilds; requires Manage Server to see it)
  — DMs Rob's farewell/shutdown announcement. The announcement carries three
  link buttons: **FinBot** (https://www.thefinbot.xyz/), **Grab your data**
  (https://www.robthebot.com/sends/), and **Pigeon** (https://pigeonbot.xyz).
  Recipients are currently limited to the bot owner only for testing — see
  `ANNOUNCEMENT_RECIPIENT_USER_IDS` in `rob/discord/cogs/shutdown.py` to widen
  the audience before going live.

## Inactivity commands

- `/inactivitytest`
- `/inactivelist`

## Moderator prefix commands

- `!rob-blacklist <discord_user_id_or_mention> [reason]`
- `!rob-unblacklist <discord_user_id_or_mention>`
- `!throne-blacklist <discord_user_id_or_mention>`

## Removed in rebuild

- `/sendrequest`
- `/privacy`
- `/broadcast`

## Automatic behaviours

- Voice-message transcription: when `VOICE_TRANSCRIBE_ENABLED=true`, Rob replies
  to every voice message (without pinging) with a transcript; replying to an
  older voice message while @mentioning Rob transcribes it on demand. See
  [`voice-transcription.md`](voice-transcription.md).

## Notes

- Registration role checks are runtime-validated from `vib_settings`.
- During maintenance, `/register domme` and `/register sub` are intentionally unavailable while counting stays active.
- `/leaderboard` is for user-facing stats and should not create schema changes or admin side effects.
- After deploy, Discord command sync removes retired commands. Guild removals usually appear quickly; global command removal can take longer to propagate.
