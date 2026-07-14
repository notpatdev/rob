from __future__ import annotations

from rob.publicapi.cors import parse_allowed_origins, resolve_cors_origin


def test_parse_allowed_origins_splits_and_trims():
    assert parse_allowed_origins("https://a.com, https://b.com ,") == [
        "https://a.com",
        "https://b.com",
    ]
    assert parse_allowed_origins("") == []
    assert parse_allowed_origins(None) == []


def test_exact_origin_is_reflected():
    allowed = ["https://robthebot.com"]
    assert resolve_cors_origin("https://robthebot.com", allowed) == "https://robthebot.com"


def test_unlisted_origin_falls_back_to_first_configured():
    allowed = ["https://robthebot.com", "https://staging.robthebot.com"]
    # An origin that is not allowed gets the canonical origin, so the browser
    # blocks it (ACAO != request origin) rather than silently allowing it.
    assert resolve_cors_origin("https://evil.example", allowed) == "https://robthebot.com"


def test_wildcard_pattern_matches_preview_host():
    allowed = ["https://robthebot.com", "https://*.lovableproject.com"]
    origin = "https://preview--rob-site.lovableproject.com"
    assert resolve_cors_origin(origin, allowed) == origin
    # And the exact site origin still works alongside the wildcard.
    assert resolve_cors_origin("https://robthebot.com", allowed) == "https://robthebot.com"


def test_star_reflects_any_origin():
    assert resolve_cors_origin("https://anything.example", ["*"]) == "https://anything.example"
    # No Origin header (non-browser) with "*" yields a literal "*".
    assert resolve_cors_origin(None, ["*"]) == "*"


def test_no_origin_header_falls_back_to_first_configured():
    assert resolve_cors_origin(None, ["https://robthebot.com"]) == "https://robthebot.com"


def test_empty_allow_list_returns_none():
    assert resolve_cors_origin("https://robthebot.com", []) is None


def test_match_is_case_insensitive_on_scheme_and_host():
    allowed = ["https://Robthebot.com"]
    assert resolve_cors_origin("https://robthebot.com", allowed) == "https://robthebot.com"
