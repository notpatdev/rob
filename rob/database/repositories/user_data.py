"""Right-to-erasure: delete all of a user's Rob data, everywhere or per guild.

bot_users is kept (a blocked user must stay blocked) with its PII nulled;
everything else is deleted in one transaction. Tables mirror scripts/db/build.
"""

from __future__ import annotations

from asyncpg import Connection

from rob.database.connection import Database


def _rows_affected(status: str) -> int:
    """Parse the trailing count from an asyncpg command tag like "DELETE 3"."""
    parts = status.split()
    if not parts:
        return 0
    try:
        return int(parts[-1])
    except ValueError:
        return 0


class UserDataRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    # -- public API -----------------------------------------------------------

    async def delete_user_everywhere(self, discord_user_id: int) -> dict[str, int]:
        """Delete every row for the user across all guilds, including
        user_terms_acceptance. Returns {table: rows_deleted}."""
        async with self.database.transaction() as connection:
            deleted = await self._delete_guild_scoped(
                connection,
                discord_user_id=discord_user_id,
                guild_id=None,
            )
            deleted["user_terms_acceptance"] = _rows_affected(
                await connection.execute(
                    "DELETE FROM user_terms_acceptance WHERE discord_user_id = $1",
                    discord_user_id,
                )
            )
        return deleted

    async def delete_user_in_guild(
        self, discord_user_id: int, guild_id: int
    ) -> dict[str, int]:
        """Delete every row for the user within guild_id. user_terms_acceptance
        has no guild column and is left alone here. Returns {table: rows_deleted}."""
        async with self.database.transaction() as connection:
            return await self._delete_guild_scoped(
                connection,
                discord_user_id=discord_user_id,
                guild_id=guild_id,
            )

    async def guilds_with_user_data(self, discord_user_id: int) -> list[int]:
        """Return the distinct guild ids where the user has any Rob data.
        user_terms_acceptance is excluded (no guild column)."""
        async with self.database.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT DISTINCT guild_id FROM (
                    SELECT guild_id FROM dommes WHERE discord_user_id = $1
                    UNION ALL
                    SELECT guild_id FROM subs WHERE discord_user_id = $1
                    UNION ALL
                    SELECT guild_id FROM sends
                        WHERE domme_user_id = $1 OR sub_user_id = $1
                    UNION ALL
                    SELECT guild_id FROM count_blocks WHERE discord_user_id = $1
                    UNION ALL
                    SELECT guild_id FROM count_recovery_windows
                        WHERE failed_user_id = $1 OR required_domme_user_id = $1
                    UNION ALL
                    SELECT guild_id FROM send_change_requests
                        WHERE domme_user_id = $1 OR approved_by_user_id = $1
                    UNION ALL
                    SELECT guild_id FROM domme_onboarding_state
                        WHERE discord_user_id = $1
                    UNION ALL
                    SELECT guild_id FROM inactive_users WHERE discord_user_id = $1
                ) AS guilds
                WHERE guild_id IS NOT NULL
                ORDER BY guild_id
                """,
                discord_user_id,
            )
        return [int(row["guild_id"]) for row in rows]

    # -- internals ------------------------------------------------------------

    async def _delete_guild_scoped(
        self,
        connection: Connection,
        *,
        discord_user_id: int,
        guild_id: int | None,
    ) -> dict[str, int]:
        """Run the guild-scoped deletes for the user; guild_id=None means all guilds.

        Order matters: sends and count_recovery_windows hold restrict FKs into
        dommes/subs, so children go before parents, matching both the
        denormalised user ids and the FK ids.
        """
        # $2 is only referenced when guild_filter is non-empty.
        guild_filter = "" if guild_id is None else " AND guild_id = $2"
        scope: tuple = (discord_user_id,) if guild_id is None else (discord_user_id, guild_id)

        deleted: dict[str, int] = {}

        # sends.domme_id -> dommes(id), sends.sub_id -> subs(id) (restrict).
        deleted["sends"] = _rows_affected(
            await connection.execute(
                "DELETE FROM sends WHERE ("
                "domme_user_id = $1 OR sub_user_id = $1"
                f" OR domme_id IN (SELECT id FROM dommes WHERE discord_user_id = $1{guild_filter})"
                f" OR sub_id IN (SELECT id FROM subs WHERE discord_user_id = $1{guild_filter})"
                f"){guild_filter}",
                *scope,
            )
        )
        # count_recovery_windows.required_domme_id -> dommes(id) (restrict).
        deleted["count_recovery_windows"] = _rows_affected(
            await connection.execute(
                "DELETE FROM count_recovery_windows WHERE ("
                "failed_user_id = $1 OR required_domme_user_id = $1"
                f" OR required_domme_id IN (SELECT id FROM dommes WHERE discord_user_id = $1{guild_filter})"
                f"){guild_filter}",
                *scope,
            )
        )
        # Cascades from subs, but deleted explicitly so the count is accurate.
        deleted["sub_send_names"] = _rows_affected(
            await connection.execute(
                f"DELETE FROM sub_send_names WHERE discord_user_id = $1{guild_filter}",
                *scope,
            )
        )

        # send_change_requests FKs into sends are SET NULL; order doesn't matter.
        deleted["send_change_requests"] = _rows_affected(
            await connection.execute(
                "DELETE FROM send_change_requests "
                f"WHERE (domme_user_id = $1 OR approved_by_user_id = $1){guild_filter}",
                *scope,
            )
        )
        deleted["count_blocks"] = _rows_affected(
            await connection.execute(
                f"DELETE FROM count_blocks WHERE discord_user_id = $1{guild_filter}",
                *scope,
            )
        )
        deleted["domme_onboarding_state"] = _rows_affected(
            await connection.execute(
                f"DELETE FROM domme_onboarding_state WHERE discord_user_id = $1{guild_filter}",
                *scope,
            )
        )

        # Parents last, after every referencing row above is gone.
        deleted["subs"] = _rows_affected(
            await connection.execute(
                f"DELETE FROM subs WHERE discord_user_id = $1{guild_filter}",
                *scope,
            )
        )
        deleted["dommes"] = _rows_affected(
            await connection.execute(
                f"DELETE FROM dommes WHERE discord_user_id = $1{guild_filter}",
                *scope,
            )
        )

        deleted["bot_settings"] = await self._delete_bot_settings(
            connection,
            discord_user_id=discord_user_id,
            guild_id=guild_id,
        )

        deleted["inactive_users"] = _rows_affected(
            await connection.execute(
                f"DELETE FROM inactive_users WHERE discord_user_id = $1{guild_filter}",
                *scope,
            )
        )

        # bot_users stays for block enforcement; only its PII is cleared.
        deleted["bot_users_pii_cleared"] = _rows_affected(
            await connection.execute(
                "UPDATE bot_users SET discord_username = NULL, "
                f"discord_display_name = NULL WHERE discord_user_id = $1{guild_filter}",
                *scope,
            )
        )

        # the_count only stores the last counter's id; null it if it's this user.
        the_count_filter = "" if guild_id is None else " AND guild_id = $2"
        deleted["the_count"] = _rows_affected(
            await connection.execute(
                f"UPDATE the_count SET last_user_id = NULL WHERE last_user_id = $1{the_count_filter}",
                *scope,
            )
        )
        return deleted

    async def _delete_bot_settings(
        self,
        connection: Connection,
        *,
        discord_user_id: int,
        guild_id: int | None,
    ) -> int:
        """Delete per-user bot_settings rows (activity:/inactivity: key prefixes).

        guild_id=None wildcards the guild segment. Both ids are integers, so
        the LIKE patterns carry no stray wildcard characters.
        """
        guild_segment = "%" if guild_id is None else str(int(guild_id))
        uid = int(discord_user_id)
        activity_pattern = f"activity:{guild_segment}:user:{uid}:%"
        inactivity_pattern = f"inactivity:{guild_segment}:user:{uid}:%"
        status = await connection.execute(
            "DELETE FROM bot_settings WHERE key LIKE $1 OR key LIKE $2",
            activity_pattern,
            inactivity_pattern,
        )
        return _rows_affected(status)
