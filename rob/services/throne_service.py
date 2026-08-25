from __future__ import annotations

import aiohttp

from rob.throne.scraper import (
    CreatorInfo,
    WishlistItemRecord,
    fetch_public_wishlist_items,
    match_wishlist_item_price,
    resolve_creator_info,
)


class ThroneService:
    def __init__(self, *, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._http: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._http is not None and not self._http.closed:
            await self._http.close()
        self._http = None

    async def get_http(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession()
        return self._http

    async def resolve_creator(self, throne_reference: str) -> CreatorInfo | None:
        return await resolve_creator_info(
            throne_reference,
            http=await self.get_http(),
            timeout_seconds=self.timeout_seconds,
        )

    async def fetch_wishlist_items(self, creator_id: str) -> list[WishlistItemRecord] | None:
        return await fetch_public_wishlist_items(
            creator_id,
            http=await self.get_http(),
            timeout_seconds=self.timeout_seconds,
        )

    async def match_item(
        self,
        *,
        creator_id: str,
        item_name: str | None,
        item_image_url: str | None,
    ) -> WishlistItemRecord | None:
        items = await self.fetch_wishlist_items(creator_id)
        if items is None:
            return None
        return match_wishlist_item_price(
            items,
            item_name=item_name,
            item_image_url=item_image_url,
        )
