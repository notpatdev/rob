from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

import aiohttp

log = logging.getLogger(__name__)

_FIRESTORE_DOCUMENTS_URL = (
    "https://firestore.googleapis.com/v1/projects/onlywish-9d17b/"
    "databases/(default)/documents"
)


@dataclass(frozen=True)
class CreatorInfo:
    creator_id: str
    throne_handle: str
    hide_own_purchases: bool | None


@dataclass(frozen=True)
class WishlistItemRecord:
    wishlist_item_id: str
    item_name: str | None
    item_image_url: str | None
    amount_cents: int
    currency: str | None
    is_available: bool | None


def normalize_throne_url(throne_url: str) -> str | None:
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
    return urlunparse(("https", host, path, "", "", ""))


def normalize_throne_registration_input(value: str) -> str | None:
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


async def resolve_creator_info(
    throne_reference: str,
    *,
    http: aiohttp.ClientSession,
    timeout_seconds: float = 10.0,
) -> CreatorInfo | None:
    normalized = normalize_throne_registration_input(throne_reference)
    if normalized is None:
        return None
    username = _username_from_throne_url(normalized)
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
    rows = await _run_firestore_query(payload, http=http, timeout_seconds=timeout_seconds)
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
        raw_hide = fields.get("hideOwnPurchases")
        return CreatorInfo(
            creator_id=raw_id,
            throne_handle=handle,
            hide_own_purchases=bool(raw_hide) if raw_hide is not None else None,
        )
    return None


async def fetch_public_wishlist_items(
    creator_id: str,
    *,
    http: aiohttp.ClientSession,
    timeout_seconds: float = 10.0,
    page_size: int = 100,
    max_pages: int = 3,
) -> list[WishlistItemRecord] | None:
    if not creator_id:
        return None

    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    next_page_token: str | None = None
    results: list[WishlistItemRecord] = []

    for _ in range(max_pages):
        params = {"pageSize": str(page_size)}
        if next_page_token:
            params["pageToken"] = next_page_token
        url = f"{_FIRESTORE_DOCUMENTS_URL}/creators/{quote(creator_id, safe='')}/wishlistItems"
        try:
            async with http.get(url, params=params, timeout=timeout) as response:
                if response.status == 404:
                    return None
                if response.status != 200:
                    text = await response.text()
                    log.warning(
                        "Wishlist lookup for %s returned HTTP %s: %s",
                        creator_id,
                        response.status,
                        text[:300],
                    )
                    return None
                data = await response.json()
        except (aiohttp.ClientError, TimeoutError) as exc:
            log.warning("Failed to fetch wishlist items for %s: %s", creator_id, exc)
            return None

        documents = data.get("documents", [])
        if not isinstance(documents, list):
            documents = []
        for document in documents:
            if not isinstance(document, dict):
                continue
            fields = _firestore_fields_to_python(document.get("fields"))
            item_id = fields.get("_id") or _document_id(document)
            if not isinstance(item_id, str) or not item_id:
                continue
            item_name = fields.get("name")
            image_url = fields.get("image")
            amount = fields.get("price")
            amount_cents = 0
            if isinstance(amount, (int, float)):
                amount_cents = int(round(float(amount) * 100))
            currency = fields.get("currency")
            is_available = fields.get("available")
            results.append(
                WishlistItemRecord(
                    wishlist_item_id=item_id,
                    item_name=item_name if isinstance(item_name, str) else None,
                    item_image_url=image_url if isinstance(image_url, str) else None,
                    amount_cents=amount_cents,
                    currency=currency if isinstance(currency, str) else None,
                    is_available=bool(is_available) if is_available is not None else None,
                )
            )
        next_page_token = data.get("nextPageToken")
        if not isinstance(next_page_token, str) or not next_page_token:
            break

    return results


def match_wishlist_item_price(
    items: list[WishlistItemRecord],
    *,
    item_name: str | None,
    item_image_url: str | None,
) -> WishlistItemRecord | None:
    if not items:
        return None

    normalized_name = item_name.casefold().strip() if item_name else None
    normalized_image = item_image_url.strip() if item_image_url else None

    for item in items:
        if normalized_image and item.item_image_url == normalized_image:
            return item
    for item in items:
        if normalized_name and item.item_name and item.item_name.casefold().strip() == normalized_name:
            return item
    return None


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
        ) as response:
            if response.status != 200:
                text = await response.text()
                log.warning(
                    "Throne Firestore query returned HTTP %s: %s",
                    response.status,
                    text[:500],
                )
                return None
            data = await response.json()
    except (aiohttp.ClientError, TimeoutError) as exc:
        log.warning("Failed to query Throne Firestore: %s", exc)
        return None
    if not isinstance(data, list):
        return None
    return [row for row in data if isinstance(row, dict)]


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
    if "mapValue" in value:
        return _firestore_fields_to_python(value["mapValue"].get("fields"))
    if "nullValue" in value:
        return None
    return None


def _document_id(document: dict[str, Any]) -> str | None:
    name = document.get("name")
    if not isinstance(name, str) or "/" not in name:
        return None
    return name.rsplit("/", 1)[-1] or None


def _username_from_throne_url(throne_url: str) -> str | None:
    parsed = urlparse(throne_url)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    if parts[0] in {"u", "wishlist"} and len(parts) >= 2:
        return parts[1]
    return parts[0]
