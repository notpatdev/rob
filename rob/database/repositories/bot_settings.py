from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from rob.database.connection import Database


def _to_text(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        value = raw.get("value")
        if value is None:
            return None
        return str(value)
    if isinstance(raw, str):
        cleaned = raw.strip()
        if cleaned.startswith("{") and cleaned.endswith("}"):
            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError:
                return raw
            if isinstance(parsed, dict):
                value = parsed.get("value")
                if value is None:
                    return None
                return str(value)
    return str(raw)


class BotSettingsRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def get_value(self, key: str) -> tuple[str | None, datetime | None]:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT value, updated_at
                FROM bot_settings
                WHERE key = $1
                """,
                key,
            )
        if row is None:
            return None, None
        return _to_text(row["value"]), row["updated_at"]

    async def get_text(self, key: str) -> str | None:
        value, _updated_at = await self.get_value(key)
        return value

    async def get_bool(self, key: str, *, default: bool = False) -> bool:
        value = await self.get_text(key)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    async def get_values(self, keys: list[str]) -> dict[str, str]:
        if not keys:
            return {}
        async with self.database.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT key, value
                FROM bot_settings
                WHERE key = ANY($1::text[])
                """,
                keys,
            )
        output: dict[str, str] = {}
        for row in rows:
            text = _to_text(row["value"])
            if text is not None:
                output[str(row["key"])] = text
        return output

    async def set_value(self, key: str, value: str) -> None:
        async with self.database.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO bot_settings (key, value)
                VALUES ($1, jsonb_build_object('value', $2::text))
                ON CONFLICT (key) DO UPDATE SET
                    value = EXCLUDED.value,
                    updated_at = now()
                """,
                key,
                value,
            )

    async def set_bool(self, key: str, value: bool) -> None:
        await self.set_value(key, "true" if value else "false")

    async def set_values(self, values: dict[str, str | None]) -> None:
        async with self.database.transaction() as connection:
            for key, value in values.items():
                if value is None:
                    await connection.execute(
                        "DELETE FROM bot_settings WHERE key = $1",
                        key,
                    )
                    continue
                await connection.execute(
                    """
                    INSERT INTO bot_settings (key, value)
                    VALUES ($1, jsonb_build_object('value', $2::text))
                    ON CONFLICT (key) DO UPDATE SET
                        value = EXCLUDED.value,
                        updated_at = now()
                    """,
                    key,
                    value,
                )

