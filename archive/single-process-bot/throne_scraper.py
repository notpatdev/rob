"""Fetch recent Throne sends from stream-alert overlays or public pages.

The preferred source is Throne's browser-source alert backing store. The
browser-source URL contains a creator id, and public Firestore reads can also
resolve a creator id from a Throne username. Overlay documents contain the same
payload Throne uses for stream alerts, including ``overlayId``, gifter name,
item name, item image, and timestamp.

The page-scraping fallback (``__NEXT_DATA__`` / HTML) is retained in this module
but is no longer called by the polling loop. Throne migrated the main profile
page away from Next.js, so ``__NEXT_DATA__`` no longer appears there. The code
is kept for reference and potential future use.

This module is pure-functional and easy to unit-test against saved fixtures.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

import aiohttp

log = logging.getLogger(__name__)

_FIRESTORE_DOCUMENTS_URL = (
    "https://firestore.googleapis.com/v1/projects/onlywish-9d17b/"
    "databases/(default)/documents"
)
_OVERLAY_SEND_TYPES = {"item-purchased-stream-alert"}
_OVERLAY_QUERY_LIMIT = 25


@dataclass(frozen=True)
class ScrapedSend:
    external_id: str
    sender_name: str | None  # None = anonymous
    amount_usd: float | None  # None = sender hid the amount ("private")
    item_name: str | None
    item_image_url: str | None
    sent_at: str  # ISO-8601 UTC


@dataclass(frozen=True)
class CreatorInfo:
    """Resolved Throne creator metadata."""

    creator_id: str
    throne_handle: str
    hide_own_purchases: (
        bool | None
    )  # None = unknown (e.g. resolved from stream-alert URL)


@dataclass(frozen=True)
class PageFetchResult:
    sends: list[ScrapedSend] | None
    status: str


@dataclass(frozen=True)
class RecentSendsFetchResult:
    sends: list[ScrapedSend] | None
    overlay_succeeded: bool
    page_status: str


@dataclass(frozen=True)
class WishlistItemPrice:
    amount_usd: float
    currency: str | None


@dataclass(frozen=True)
class WishlistItemRecord:
    wishlist_item_id: str
    item_name: str | None
    item_image_url: str | None
    amount_usd: float
    currency: str | None
    is_available: bool | None


_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL,
)

_SEND_ID_KEYS = ("id", "_id", "uuid", "sendId", "send_id", "transactionId")
_SENDER_NAME_KEYS = (
    "senderName",
    "sender_name",
    "fromName",
    "from_name",
    "displayName",
    "username",
    "supporterName",
    "name",
)
_AMOUNT_KEYS = (
    "amountUsd",
    "amount_usd",
    "amountUSD",
    "amount",
    "value",
    "valueUsd",
    "totalUsd",
    "total_usd",
    "priceUsd",
    "price",
)
_PRIVATE_KEYS = (
    "isPrivate",
    "is_private",
    "amountHidden",
    "hideAmount",
    "private",
)
_ANONYMOUS_KEYS = (
    "isAnonymous",
    "is_anonymous",
    "anonymous",
)
_ITEM_NAME_KEYS = ("itemName", "item_name", "productName", "title", "name")
_ITEM_IMAGE_KEYS = (
    "itemImageUrl",
    "item_image_url",
    "imageUrl",
    "image_url",
    "thumbnail",
    "thumbnailUrl",
    "image",
)
_TIMESTAMP_KEYS = (
    "sentAt",
    "sent_at",
    "createdAt",
    "created_at",
    "timestamp",
    "date",
    "time",
)


def normalize_throne_url(throne_url: str) -> str | None:
    """Normalize a user-supplied Throne URL.

    Accepts either ``throne.com/<handle>`` or ``throne.gifts/<handle>``,
    with or without scheme, with or without trailing slash / query / fragment.
    Returns ``None`` if the URL is not a recognisable Throne URL.
    """
    if not throne_url:
        return None
    url = throne_url.strip()
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if host not in {"throne.com", "throne.gifts"}:
        return None
    path = parsed.path.rstrip("/")
    if not path or path == "/":
        return None
    # Drop query and fragment; force https.
    return urlunparse(("https", host, path, "", "", ""))


def normalize_throne_registration_input(value: str) -> str | None:
    """Normalize a Domme signup value into a Throne profile URL.

    Accepts either a full Throne URL or a bare username such as ``pattyboy03``
    or ``@pattyboy03``.
    """
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if "://" in cleaned or cleaned.startswith("www."):
        return normalize_throne_url(cleaned)

    username = cleaned.lstrip("@").strip()
    if not username or any(char.isspace() for char in username):
        return None
    return normalize_throne_url(f"https://throne.com/{quote(username, safe='._-')}")


async def resolve_creator_id(
    throne_reference: str,
    *,
    http: aiohttp.ClientSession,
    timeout_seconds: float = 10.0,
) -> str | None:
    normalized = normalize_throne_registration_input(throne_reference)
    if normalized is None:
        return None
    return await _resolve_creator_id(
        normalized,
        http=http,
        timeout_seconds=timeout_seconds,
    )


async def resolve_creator_info(
    throne_reference: str,
    *,
    http: aiohttp.ClientSession,
    timeout_seconds: float = 10.0,
) -> CreatorInfo | None:
    """Resolve Throne creator metadata (id, handle, hideOwnPurchases).

    Returns ``None`` if the reference cannot be resolved to a Throne creator.
    """
    normalized = normalize_throne_registration_input(throne_reference)
    if normalized is None:
        return None

    # Fast path: stream-alert URLs embed the creator id directly.
    direct_creator_id = _creator_id_from_stream_alert_url(normalized)
    if direct_creator_id:
        # Stream-alert URLs don't carry the handle — resolve via Firestore.
        return await _resolve_creator_info_by_id(
            direct_creator_id, http=http, timeout_seconds=timeout_seconds
        )

    username = _username_from_throne_url(normalized)
    if username is None:
        return None
    return await _resolve_creator_info_by_username(
        username, http=http, timeout_seconds=timeout_seconds
    )


async def has_overlay_data(
    creator_id: str,
    *,
    http: aiohttp.ClientSession,
    timeout_seconds: float = 10.0,
) -> bool:
    """Return True if the creator has any overlay documents in Firestore."""
    documents = await _query_overlay_documents(
        creator_id, http=http, timeout_seconds=timeout_seconds
    )
    return bool(documents)


async def fetch_wishlist_item_price(
    creator_id: str,
    *,
    item_name: str | None,
    item_image_url: str | None,
    http: aiohttp.ClientSession,
    timeout_seconds: float = 10.0,
    page_size: int = 100,
    max_pages: int = 5,
) -> WishlistItemPrice | None:
    items = await fetch_public_wishlist_items(
        creator_id,
        http=http,
        timeout_seconds=timeout_seconds,
        page_size=page_size,
        max_pages=max_pages,
    )
    if items is None:
        return None
    return match_wishlist_item_price(
        items,
        item_name=item_name,
        item_image_url=item_image_url,
    )


async def fetch_public_wishlist_items(
    creator_id: str,
    *,
    http: aiohttp.ClientSession,
    timeout_seconds: float = 10.0,
    page_size: int = 100,
    max_pages: int = 5,
) -> list[WishlistItemRecord] | None:
    """Look up a creator's public wishlist items and best-match a purchased gift."""
    if not creator_id:
        return None

    next_page_token: str | None = None
    results: list[WishlistItemRecord] = []
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    for _ in range(max_pages):
        params = {"pageSize": str(page_size)}
        if next_page_token:
            params["pageToken"] = next_page_token

        url = f"{_FIRESTORE_DOCUMENTS_URL}/creators/{quote(creator_id, safe='')}/wishlistItems"
        try:
            async with http.get(url, params=params, timeout=timeout) as resp:
                if resp.status == 404:
                    return None
                if resp.status != 200:
                    text = await resp.text()
                    log.warning(
                        "Wishlist item lookup for creator %s returned HTTP %s: %s",
                        creator_id,
                        resp.status,
                        text[:300],
                    )
                    return None
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as exc:
            log.warning(
                "Failed to fetch public wishlist items for creator %s: %s",
                creator_id,
                exc,
            )
            return None

        documents = data.get("documents", [])
        if not isinstance(documents, list):
            documents = []

        for document in documents:
            if not isinstance(document, dict):
                continue
            fields = _firestore_fields_to_python(document.get("fields"))
            price = _coerce_wishlist_item_price(fields.get("price"))
            if price is None:
                continue
            wishlist_item_id = _coerce_str(fields.get("id")) or _document_id(document)
            if wishlist_item_id is None:
                continue
            results.append(
                WishlistItemRecord(
                    wishlist_item_id=wishlist_item_id,
                    item_name=_coerce_str(fields.get("name")),
                    item_image_url=_coerce_str(fields.get("imgLink"))
                    or _coerce_str(fields.get("imageUrl")),
                    amount_usd=price,
                    currency=_coerce_str(fields.get("currency")),
                    is_available=_coerce_bool(fields.get("isAvailable")),
                )
            )

        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break

    return results


def match_wishlist_item_price(
    items: list[WishlistItemRecord],
    *,
    item_name: str | None,
    item_image_url: str | None,
) -> WishlistItemPrice | None:
    wanted_name = _normalize_match_text(item_name)
    wanted_image = _normalize_image_key(item_image_url)
    if wanted_name is None and wanted_image is None:
        return None

    if wanted_image is not None:
        exact_image_matches = [
            item
            for item in items
            if _image_match_strength(wanted_image, item.item_image_url) == 2
        ]
        if exact_image_matches:
            return _pick_wishlist_price_candidate(
                exact_image_matches, wanted_name=wanted_name
            )

        loose_image_matches = [
            item
            for item in items
            if _image_match_strength(wanted_image, item.item_image_url) == 1
        ]
        if loose_image_matches:
            return _pick_wishlist_price_candidate(
                loose_image_matches, wanted_name=wanted_name
            )

    if wanted_name is not None:
        exact_name_matches = [
            item
            for item in items
            if _normalize_match_text(item.item_name) == wanted_name
        ]
        if len(exact_name_matches) == 1:
            return _wishlist_item_price_from_record(exact_name_matches[0])
        if len(exact_name_matches) > 1:
            return None

        partial_name_matches = [
            item
            for item in items
            if (
                (candidate_name := _normalize_match_text(item.item_name)) is not None
                and (wanted_name in candidate_name or candidate_name in wanted_name)
            )
        ]
        if len(partial_name_matches) == 1:
            return _wishlist_item_price_from_record(partial_name_matches[0])
        if len(partial_name_matches) > 1:
            return None

    scored_candidates = [
        (
            _wishlist_item_match_score(
                wanted_name=wanted_name,
                wanted_image=wanted_image,
                candidate_name=item.item_name,
                candidate_image=item.item_image_url,
            ),
            item,
        )
        for item in items
    ]
    scored_candidates = [
        (score, item) for score, item in scored_candidates if score > 0
    ]
    if not scored_candidates:
        return None

    scored_candidates.sort(key=lambda pair: pair[0], reverse=True)
    top_score = scored_candidates[0][0]
    top_items = [item for score, item in scored_candidates if score == top_score]
    if len(top_items) != 1:
        return None
    return _wishlist_item_price_from_record(top_items[0])


def _pick_wishlist_price_candidate(
    items: list[WishlistItemRecord],
    *,
    wanted_name: str | None,
) -> WishlistItemPrice | None:
    if not items:
        return None
    if len(items) == 1:
        return _wishlist_item_price_from_record(items[0])

    if wanted_name is not None:
        exact_name_matches = [
            item
            for item in items
            if _normalize_match_text(item.item_name) == wanted_name
        ]
        if len(exact_name_matches) == 1:
            return _wishlist_item_price_from_record(exact_name_matches[0])
        if len(exact_name_matches) > 1:
            return None

    return None


def _wishlist_item_price_from_record(item: WishlistItemRecord) -> WishlistItemPrice:
    return WishlistItemPrice(
        amount_usd=item.amount_usd,
        currency=item.currency,
    )


async def fetch_recent_sends(
    throne_url: str,
    *,
    http: aiohttp.ClientSession,
    user_agent: str,
    timeout_seconds: float = 10.0,
) -> list[ScrapedSend] | None:
    result = await fetch_recent_sends_with_status(
        throne_url,
        http=http,
        user_agent=user_agent,
        timeout_seconds=timeout_seconds,
    )
    return result.sends


async def fetch_recent_sends_with_status(
    throne_url: str,
    *,
    http: aiohttp.ClientSession,
    user_agent: str,
    timeout_seconds: float = 10.0,
    allow_page_enrichment: bool = True,
) -> RecentSendsFetchResult:
    """Fetch recent sends for a Throne profile.

    Returns ``None`` on HTTP / network / parse failure, and ``[]`` when the
    request succeeded but there are no visible sends. The polling loop relies
    on this distinction so empty-but-valid overlays are not treated as failures.
    """
    normalized = normalize_throne_url(throne_url)

    overlay_sends = await fetch_recent_overlay_sends(
        throne_url,
        http=http,
        timeout_seconds=timeout_seconds,
    )
    page_sends: list[ScrapedSend] | None = None
    page_status = "skipped"
    should_fetch_page = (
        allow_page_enrichment
        and normalized is not None
        and (
            overlay_sends is None
            or any(send.amount_usd is None for send in overlay_sends)
        )
    )

    if should_fetch_page:
        page_result = await _fetch_page_sends(
            normalized,
            http=http,
            user_agent=user_agent,
            timeout_seconds=timeout_seconds,
        )
        page_sends = page_result.sends
        page_status = page_result.status
    elif overlay_sends is None and normalized is None:
        log.warning("Skipping unrecognised Throne URL: %r", throne_url)
        return RecentSendsFetchResult(
            sends=None, overlay_succeeded=False, page_status="skipped"
        )

    if overlay_sends is not None:
        if page_sends:
            return RecentSendsFetchResult(
                sends=_merge_overlay_and_page_sends(overlay_sends, page_sends),
                overlay_succeeded=True,
                page_status=page_status,
            )
        return RecentSendsFetchResult(
            sends=overlay_sends,
            overlay_succeeded=True,
            page_status=page_status,
        )
    return RecentSendsFetchResult(
        sends=page_sends,
        overlay_succeeded=False,
        page_status=page_status,
    )


async def fetch_recent_overlay_sends(
    throne_url: str,
    *,
    http: aiohttp.ClientSession,
    timeout_seconds: float = 10.0,
) -> list[ScrapedSend] | None:
    """Fetch recent browser-source overlay sends for a Throne profile.

    Returns ``None`` if the URL cannot be resolved to a Throne creator id or if
    Firestore cannot be read. Returns ``[]`` when the creator resolves but no
    purchased-item overlays are currently available.
    """
    creator_id = await _resolve_creator_id(
        throne_url,
        http=http,
        timeout_seconds=timeout_seconds,
    )
    if creator_id is None:
        return None

    documents = await _query_overlay_documents(
        creator_id,
        http=http,
        timeout_seconds=timeout_seconds,
    )
    if documents is None:
        return None

    sends: list[ScrapedSend] = []
    seen_ids: set[str] = set()
    for document in documents:
        send = _overlay_document_to_send(document)
        if send is None or send.external_id in seen_ids:
            continue
        seen_ids.add(send.external_id)
        sends.append(send)
    sends.sort(key=lambda s: s.sent_at)
    return sends


async def _fetch_page_sends(
    normalized_url: str,
    *,
    http: aiohttp.ClientSession,
    user_agent: str,
    timeout_seconds: float,
) -> PageFetchResult:
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    try:
        async with http.get(normalized_url, headers=headers, timeout=timeout) as resp:
            if resp.status == 429:
                return PageFetchResult(sends=None, status="rate_limited")
            if resp.status != 200:
                log.warning(
                    "Throne page %s returned HTTP %s", normalized_url, resp.status
                )
                return PageFetchResult(sends=None, status=f"http_{resp.status}")
            html = await resp.text()
    except (aiohttp.ClientError, TimeoutError) as exc:
        log.warning("Failed to fetch Throne page %s: %s", normalized_url, exc)
        return PageFetchResult(sends=None, status="request_failed")

    try:
        return PageFetchResult(sends=parse_sends_from_html(html), status="ok")
    except Exception:  # noqa: BLE001 - never let parsing kill the poller
        log.exception("Failed to parse Throne page %s", normalized_url)
        return PageFetchResult(sends=None, status="parse_failed")


def _merge_overlay_and_page_sends(
    overlay_sends: list[ScrapedSend],
    page_sends: list[ScrapedSend],
) -> list[ScrapedSend]:
    """Use page data to enrich overlay rows with prices when possible."""
    enriched: list[ScrapedSend] = []
    remaining = page_sends.copy()

    for overlay_send in overlay_sends:
        best_index: int | None = None
        best_score = -1
        for index, page_send in enumerate(remaining):
            score = _send_match_score(overlay_send, page_send)
            if score > best_score:
                best_index = index
                best_score = score

        if best_index is None or best_score < 2:
            enriched.append(overlay_send)
            continue

        matched = remaining.pop(best_index)
        enriched.append(
            ScrapedSend(
                external_id=overlay_send.external_id,
                sender_name=overlay_send.sender_name or matched.sender_name,
                amount_usd=matched.amount_usd
                if matched.amount_usd is not None
                else overlay_send.amount_usd,
                item_name=overlay_send.item_name or matched.item_name,
                item_image_url=overlay_send.item_image_url or matched.item_image_url,
                sent_at=overlay_send.sent_at,
            )
        )

    return enriched


def _wishlist_item_match_score(
    *,
    wanted_name: str | None,
    wanted_image: str | None,
    candidate_name: str | None,
    candidate_image: str | None,
) -> int:
    score = 0

    normalized_candidate_name = _normalize_match_text(candidate_name)
    if wanted_name and normalized_candidate_name:
        if wanted_name == normalized_candidate_name:
            score += 4
        elif (
            wanted_name in normalized_candidate_name
            or normalized_candidate_name in wanted_name
        ):
            score += 2

    normalized_candidate_image = _normalize_image_key(candidate_image)
    if wanted_image and normalized_candidate_image:
        if wanted_image == normalized_candidate_image:
            score += 4
        elif (
            wanted_image.rsplit("/", 1)[-1]
            == normalized_candidate_image.rsplit("/", 1)[-1]
        ):
            score += 3

    return score


def _image_match_strength(
    wanted_image: str,
    candidate_image: str | None,
) -> int:
    normalized_candidate_image = _normalize_image_key(candidate_image)
    if normalized_candidate_image is None:
        return 0
    if wanted_image == normalized_candidate_image:
        return 2
    if wanted_image.rsplit("/", 1)[-1] == normalized_candidate_image.rsplit("/", 1)[-1]:
        return 1
    return 0


def _send_match_score(left: ScrapedSend, right: ScrapedSend) -> int:
    score = 0
    if (
        left.sender_name
        and right.sender_name
        and left.sender_name.casefold() == right.sender_name.casefold()
    ):
        score += 2
    elif left.sender_name is None and right.sender_name is None:
        score += 1

    if (
        left.item_name
        and right.item_name
        and left.item_name.casefold() == right.item_name.casefold()
    ):
        score += 2

    try:
        left_ts = datetime.fromisoformat(left.sent_at.replace("Z", "+00:00"))
        right_ts = datetime.fromisoformat(right.sent_at.replace("Z", "+00:00"))
    except ValueError:
        return score

    delta_seconds = abs((left_ts - right_ts).total_seconds())
    if delta_seconds <= 60:
        score += 2
    elif delta_seconds <= 300:
        score += 1
    return score


async def _resolve_creator_id(
    throne_url: str,
    *,
    http: aiohttp.ClientSession,
    timeout_seconds: float,
) -> str | None:
    direct_creator_id = _creator_id_from_stream_alert_url(throne_url)
    if direct_creator_id:
        return direct_creator_id

    username = _username_from_throne_url(throne_url)
    if username is None:
        return None

    payload = {
        "structuredQuery": {
            "from": [{"collectionId": "creators"}],
            "where": {
                "fieldFilter": {
                    "field": {"fieldPath": "username"},
                    "op": "EQUAL",
                    "value": {"stringValue": username},
                }
            },
            "limit": 1,
        }
    }
    rows = await _run_firestore_query(
        payload, http=http, timeout_seconds=timeout_seconds
    )
    if rows is None:
        return None
    for row in rows:
        document = row.get("document")
        if not isinstance(document, dict):
            continue
        fields = _firestore_fields_to_python(document.get("fields"))
        raw_id = fields.get("_id") or _document_id(document)
        if isinstance(raw_id, str) and raw_id:
            return raw_id
    log.info("No Throne creator found for username %s.", username)
    return None


async def _resolve_creator_info_by_username(
    username: str,
    *,
    http: aiohttp.ClientSession,
    timeout_seconds: float,
) -> CreatorInfo | None:
    """Fetch creator info (id, handle, hideOwnPurchases) by Throne username."""
    payload = {
        "structuredQuery": {
            "from": [{"collectionId": "creators"}],
            "where": {
                "fieldFilter": {
                    "field": {"fieldPath": "username"},
                    "op": "EQUAL",
                    "value": {"stringValue": username},
                }
            },
            "limit": 1,
        }
    }
    rows = await _run_firestore_query(
        payload, http=http, timeout_seconds=timeout_seconds
    )
    if rows is None:
        return None
    for row in rows:
        document = row.get("document")
        if not isinstance(document, dict):
            continue
        fields = _firestore_fields_to_python(document.get("fields"))
        raw_id = fields.get("_id") or _document_id(document)
        if not isinstance(raw_id, str) or not raw_id:
            continue
        handle = fields.get("username") or username
        if not isinstance(handle, str):
            handle = username
        raw_hop = fields.get("hideOwnPurchases")
        # hideOwnPurchases is a Firestore boolean; None means the field is absent.
        hide_own_purchases: bool | None = bool(raw_hop) if raw_hop is not None else None
        return CreatorInfo(
            creator_id=raw_id,
            throne_handle=handle,
            hide_own_purchases=hide_own_purchases,
        )
    log.info("No Throne creator found for username %s.", username)
    return None


async def _resolve_creator_info_by_id(
    creator_id: str,
    *,
    http: aiohttp.ClientSession,
    timeout_seconds: float,
) -> CreatorInfo | None:
    """Fetch creator info (handle, hideOwnPurchases) for a known creator id."""
    payload = {
        "structuredQuery": {
            "from": [{"collectionId": "creators"}],
            "where": {
                "fieldFilter": {
                    "field": {"fieldPath": "_id"},
                    "op": "EQUAL",
                    "value": {"stringValue": creator_id},
                }
            },
            "limit": 1,
        }
    }
    rows = await _run_firestore_query(
        payload, http=http, timeout_seconds=timeout_seconds
    )
    if rows is None:
        return None
    for row in rows:
        document = row.get("document")
        if not isinstance(document, dict):
            continue
        fields = _firestore_fields_to_python(document.get("fields"))
        handle = fields.get("username")
        if not isinstance(handle, str) or not handle:
            handle = creator_id
        raw_hop = fields.get("hideOwnPurchases")
        hide_own_purchases: bool | None = bool(raw_hop) if raw_hop is not None else None
        return CreatorInfo(
            creator_id=creator_id,
            throne_handle=handle,
            hide_own_purchases=hide_own_purchases,
        )
    # Creator id is valid (came from stream-alert URL) but not in Firestore.
    return CreatorInfo(
        creator_id=creator_id, throne_handle=creator_id, hide_own_purchases=None
    )


async def _query_overlay_documents(
    creator_id: str,
    *,
    http: aiohttp.ClientSession,
    timeout_seconds: float,
) -> list[dict[str, Any]] | None:
    payload = {
        "structuredQuery": {
            "from": [{"collectionId": "overlays"}],
            "where": {
                "fieldFilter": {
                    "field": {"fieldPath": "creatorId"},
                    "op": "EQUAL",
                    "value": {"stringValue": creator_id},
                }
            },
            "orderBy": [
                {
                    "field": {"fieldPath": "createdAt"},
                    "direction": "DESCENDING",
                }
            ],
            "limit": _OVERLAY_QUERY_LIMIT,
        }
    }
    rows = await _run_firestore_query(
        payload, http=http, timeout_seconds=timeout_seconds
    )
    if rows is None:
        return None
    documents: list[dict[str, Any]] = []
    for row in rows:
        document = row.get("document")
        if isinstance(document, dict):
            documents.append(document)
    return documents


async def _run_firestore_query(
    payload: dict[str, Any],
    *,
    http: aiohttp.ClientSession,
    timeout_seconds: float,
) -> list[dict[str, Any]] | None:
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    try:
        async with http.post(
            f"{_FIRESTORE_DOCUMENTS_URL}:runQuery",
            json=payload,
            timeout=timeout,
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                log.warning(
                    "Throne Firestore query returned HTTP %s: %s",
                    resp.status,
                    text[:500],
                )
                return None
            data = await resp.json()
    except (aiohttp.ClientError, TimeoutError) as exc:
        log.warning("Failed to query Throne Firestore: %s", exc)
        return None
    if not isinstance(data, list):
        log.warning(
            "Unexpected Throne Firestore query response shape: %r", type(data).__name__
        )
        return None
    return [row for row in data if isinstance(row, dict)]


def _overlay_document_to_send(document: dict[str, Any]) -> ScrapedSend | None:
    fields = _firestore_fields_to_python(document.get("fields"))
    overlay_info = fields.get("overlayInformation")
    if not isinstance(overlay_info, dict):
        return None
    overlay_type = overlay_info.get("type")
    if overlay_type not in _OVERLAY_SEND_TYPES:
        return None

    overlay_id = fields.get("overlayId") or _document_id(document)
    if not isinstance(overlay_id, str) or not overlay_id:
        return None
    sent_at = _normalize_timestamp(fields.get("createdAt"))
    if sent_at is None:
        return None

    sender_name = _coerce_name(overlay_info.get("gifterUsername"))
    item_name = _coerce_str(overlay_info.get("itemName"))
    item_image_url = _coerce_str(overlay_info.get("itemImage"))
    if item_image_url and not item_image_url.lower().startswith(
        ("http://", "https://")
    ):
        item_image_url = None

    return ScrapedSend(
        external_id=f"throne-overlay:{overlay_id}",
        sender_name=sender_name,
        amount_usd=None,
        item_name=item_name,
        item_image_url=item_image_url,
        sent_at=sent_at,
    )


def _creator_id_from_stream_alert_url(throne_url: str) -> str | None:
    parsed = _parse_throne_url(throne_url)
    if parsed is None:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "stream-alerts":
        return parts[1]
    return None


def _username_from_throne_url(throne_url: str) -> str | None:
    parsed = _parse_throne_url(throne_url)
    if parsed is None:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    if parts[0] == "stream-alerts":
        return None
    if parts[0] in {"u", "wishlist"} and len(parts) >= 2:
        return parts[1]
    return parts[0]


def _parse_throne_url(throne_url: str):
    if not throne_url:
        return None
    url = throne_url.strip()
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if host not in {"throne.com", "throne.gifts"}:
        return None
    return parsed


def _document_id(document: dict[str, Any]) -> str | None:
    name = document.get("name")
    if not isinstance(name, str) or "/" not in name:
        return None
    return name.rsplit("/", 1)[-1] or None


def _firestore_fields_to_python(fields: Any) -> dict[str, Any]:
    if not isinstance(fields, dict):
        return {}
    return {key: _firestore_value_to_python(value) for key, value in fields.items()}


def _firestore_value_to_python(value: Any) -> Any:
    if not isinstance(value, dict):
        return None
    if "stringValue" in value:
        return value["stringValue"]
    if "integerValue" in value:
        try:
            return int(value["integerValue"])
        except (TypeError, ValueError):
            return None
    if "doubleValue" in value:
        try:
            return float(value["doubleValue"])
        except (TypeError, ValueError):
            return None
    if "booleanValue" in value:
        return bool(value["booleanValue"])
    if "timestampValue" in value:
        return value["timestampValue"]
    if "mapValue" in value:
        return _firestore_fields_to_python(value["mapValue"].get("fields"))
    if "arrayValue" in value:
        values = value["arrayValue"].get("values", [])
        if not isinstance(values, list):
            return []
        return [_firestore_value_to_python(item) for item in values]
    if "nullValue" in value:
        return None
    return None


def parse_sends_from_html(html: str) -> list[ScrapedSend]:
    """Extract sends from a rendered Throne profile page."""
    payload = _extract_next_data(html)
    if payload is not None:
        sends = _extract_sends_from_payload(payload)
        if sends:
            return sends
    # JSON path produced nothing usable — try the HTML fallback (best effort).
    return _extract_sends_from_html_fallback(html)


def _extract_next_data(html: str) -> Any:
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return None
    raw = match.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Failed to JSON-decode __NEXT_DATA__ payload")
        return None


def _extract_sends_from_payload(payload: Any) -> list[ScrapedSend]:
    """Walk the Next.js payload and harvest anything that looks like a send."""
    seen_ids: set[str] = set()
    sends: list[ScrapedSend] = []

    for obj in _walk_objects(payload):
        if not _looks_like_send(obj):
            continue
        scraped = _build_scraped_send(obj)
        if scraped is None:
            continue
        if scraped.external_id in seen_ids:
            continue
        seen_ids.add(scraped.external_id)
        sends.append(scraped)

    # Sort by timestamp (oldest first) for deterministic processing order.
    sends.sort(key=lambda s: s.sent_at)
    return sends


def _walk_objects(node: Any):
    """Yield every dict found anywhere inside a JSON-like structure."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk_objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_objects(v)


def _looks_like_send(obj: dict[str, Any]) -> bool:
    """Heuristic: object has a stable id AND (an amount-ish field OR an item)."""
    if not _first_key(obj, _SEND_ID_KEYS):
        return False
    has_amount = any(k in obj for k in _AMOUNT_KEYS)
    has_item = any(k in obj for k in _ITEM_NAME_KEYS) or any(
        k in obj for k in _ITEM_IMAGE_KEYS
    )
    has_timestamp = any(k in obj for k in _TIMESTAMP_KEYS)
    # Require a timestamp + (amount or item) to avoid matching unrelated
    # objects like users / pages that happen to have an "id".
    return has_timestamp and (has_amount or has_item)


def _build_scraped_send(obj: dict[str, Any]) -> ScrapedSend | None:
    raw_id = _first_value(obj, _SEND_ID_KEYS)
    if raw_id is None:
        return None
    external_id = str(raw_id)

    raw_ts = _first_value(obj, _TIMESTAMP_KEYS)
    sent_at = _normalize_timestamp(raw_ts)
    if sent_at is None:
        return None

    is_anonymous = _first_truthy(obj, _ANONYMOUS_KEYS)
    sender_name_raw = _first_value(obj, _SENDER_NAME_KEYS)
    sender_name: str | None
    if is_anonymous:
        sender_name = None
    else:
        sender_name = _coerce_name(sender_name_raw)

    is_private = bool(_first_truthy(obj, _PRIVATE_KEYS))
    amount_usd: float | None
    if is_private:
        amount_usd = None
    else:
        amount_usd = _coerce_amount(_first_value(obj, _AMOUNT_KEYS))
        if amount_usd is None:
            # Treat missing amount as private rather than dropping the send.
            is_private = True

    item_name = _coerce_str(_first_value(obj, _ITEM_NAME_KEYS))
    item_image_url = _coerce_str(_first_value(obj, _ITEM_IMAGE_KEYS))
    if item_image_url and not item_image_url.lower().startswith(
        ("http://", "https://")
    ):
        item_image_url = None

    return ScrapedSend(
        external_id=external_id,
        sender_name=sender_name,
        amount_usd=amount_usd if not is_private else None,
        item_name=item_name,
        item_image_url=item_image_url,
        sent_at=sent_at,
    )


def _first_key(obj: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        if k in obj:
            return k
    return None


def _first_value(obj: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in obj:
            return obj[k]
    return None


def _first_truthy(obj: dict[str, Any], keys: tuple[str, ...]) -> bool:
    for k in keys:
        if k in obj and bool(obj[k]):
            return True
    return False


def _coerce_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        return s or None
    return None


def _coerce_name(value: Any) -> str | None:
    s = _coerce_str(value)
    if s is None:
        return None
    if s.lower() in {"anonymous", "anon", "someone"}:
        return None
    return s


def _coerce_amount(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        amount = float(value)
    elif isinstance(value, str):
        cleaned = value.strip().lstrip("$").replace(",", "")
        if not cleaned:
            return None
        try:
            amount = float(cleaned)
        except ValueError:
            return None
    else:
        return None
    if amount < 0:
        return None
    # Throne stores some amounts in cents — heuristically detect that. If the
    # raw value is an int and >= 1000 it's almost certainly cents (a $10+ tip).
    # We can't be 100% sure, so leave as-is; downstream only formats the value.
    return amount


def _coerce_wishlist_item_price(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(value, 0) / 100.0
    if isinstance(value, float):
        return max(value, 0.0)
    if isinstance(value, str):
        cleaned = value.strip().replace("$", "").replace(",", "")
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _coerce_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return None


def _normalize_match_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.casefold().split())
    return normalized or None


def _normalize_image_key(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return value.casefold()
    if parsed.scheme and parsed.netloc:
        path = parsed.path or ""
        return f"{parsed.netloc.casefold()}{path}"
    return value.casefold()


def _normalize_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Epoch seconds vs milliseconds — > year 3000 in seconds means ms.
        ts = float(value)
        if ts > 32503680000:  # ~year 3000 in seconds
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except (OverflowError, ValueError, OSError):
            return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Handle trailing 'Z'
        candidate = s.replace("Z", "+00:00") if s.endswith("Z") else s
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    return None


def _extract_sends_from_html_fallback(html: str) -> list[ScrapedSend]:
    """Best-effort HTML fallback when ``__NEXT_DATA__`` is missing/unusable.

    We don't know the exact DOM layout up-front, so this is intentionally
    conservative: it returns ``[]`` rather than guessing wrong. When a real
    Throne fixture is available, this can be expanded with BeautifulSoup-based
    parsing without affecting the rest of the pipeline.
    """
    log.debug("HTML-fallback parser not implemented; returning no sends.")
    return []
