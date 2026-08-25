# Components V2 research

## Button placement research

### Discord docs findings
- Discord Components reference says buttons must be inside an Action Row or in a Section `accessory`.
- Container is a layout wrapper and can hold layout/content components; interactive buttons still must follow button placement rules.

### discord.py findings
- Runtime verified on discord.py 2.7.1 (`discord.__version__ == 2.7.1`).
- `discord.ui.Container`, `ActionRow`, `Section`, `Button`, `TextDisplay`, and `LayoutView` are present.
- Valid, supported pattern for two buttons is `Container` + `ActionRow(button1, button2)` in the same `LayoutView`.
- `Section(..., accessory=Button(...))` is valid when you need one accessory button.

### notpatdev/rob-the-bot findings
- Directly inspected `/Users/patfaint/Documents/rob-the-bot-legacy`.
- Relevant references:
  - `bot/ui/components.py`: `action_section(text, button)` uses `Section(..., accessory=Button(...))`.
  - `bot/event_views.py`: container-first views with section accessories and separators.
  - `bot/event_cog.py`: `SendRequestDecisionView` and leaderboard upsert flows.
- Old bot patterns confirm container-first layouts with button accessories were used successfully.

### Final Rob implementation decision
- Rob keeps container-first cards and never places raw top-level `Button` objects in `LayoutView`.
- Two validated patterns are used:
  - `ActionRow` for button groups (`add_card_actions(...)` helper).
  - `Section(..., accessory=Button(...))` for compact inline action prompts.
- `/sendrequest` Dom/me review cards currently use the validated `Section` accessory button pattern.


## 2026-05 legacy parity update
- Adopted container-first card templates with action rows (no raw top-level buttons).
- Documented fallback policy and old-Rob copy parity targets.

- 2026-05-23: Public send card now uses compact Components V2 layout with real `discord.ui.Separator()` and purple accent constants from `rob/ui/theme.py`; rank lines/footer removed.
- 2026-05-23: Added NEW LEADER ALERT card (purple accent, separator-based sections) with bot-state dedupe.
- 2026-05-23: Leaderboard and stats cards now use explicit separator components; stats include Unclaimed Sends section.
- 2026-05-23: `/sendrequest` DM review cards use container + section accessory buttons; helper support for container + action-row remains available where grouped buttons are preferred.
