from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

log = logging.getLogger(__name__)


class Database:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        if self.pool is not None:
            return

        self.pool = await asyncpg.create_pool(
            dsn=self.database_url,
            min_size=1,
            max_size=10,
            command_timeout=30,
        )

        log.info("Connected to PostgreSQL.")

    async def close(self) -> None:
        if self.pool is None:
            return

        await self.pool.close()
        self.pool = None

        log.info("Closed PostgreSQL connection pool.")

    def require_pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("Database connection pool has not been opened.")

        return self.pool

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        pool = self.require_pool()

        async with pool.acquire() as connection:
            yield connection

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        async with self.acquire() as connection:
            async with connection.transaction():
                yield connection

    async def health_check(self) -> bool:
        try:
            async with self.acquire() as connection:
                result = await connection.fetchval("SELECT 1;")
        except Exception:
            log.exception("PostgreSQL health check failed.")
            return False

        return result == 1
