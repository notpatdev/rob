from __future__ import annotations

import rob.database.repositories.vib_settings as _vib_settings

# Backward-compat alias while services transition to v2 naming.
GuildSettingsRepository = _vib_settings.VibSettingsRepository
CHANNEL_FIELD_NAMES = _vib_settings.CHANNEL_FIELD_NAMES
ROLE_FIELD_NAMES = _vib_settings.ROLE_FIELD_NAMES

__all__ = [
    "GuildSettingsRepository",
    "CHANNEL_FIELD_NAMES",
    "ROLE_FIELD_NAMES",
]
