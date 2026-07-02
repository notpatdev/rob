# Discord Commands

## Public commands

- `/register domme` (no options; starts Dom/me DM setup flow and collects Throne profile in modal)
- `/register sub` (no options; opens modal for up to 3 Throne usernames/send names)
- `/leaderboard`
- `/achievements`
- `/report`
- `/tldr` (options: `timeframe`, `topic`, `channel`; summarises recent chat as a public plain-text reply — see [`tldr-and-voice-transcription.md`](tldr-and-voice-transcription.md))

## Dom/me commands

- `/add`

## Counting commands

- `/count set {number}`

## Developer test commands

- `/test achievements` (requires owner/mod/manage-guild permissions)

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
  [`tldr-and-voice-transcription.md`](tldr-and-voice-transcription.md).

## Notes

- Registration role checks are runtime-validated from `vib_settings`.
- During maintenance, `/register domme` and `/register sub` are intentionally unavailable while counting stays active.
- `/leaderboard` is for user-facing stats and should not create schema changes or admin side effects.
- After deploy, Discord command sync removes retired commands. Guild removals usually appear quickly; global command removal can take longer to propagate.
