"""Public read-only API service (``api.robthebot.com``).

A tiny aiohttp app that serves the website's home-page stats and "grab your
data" page. It runs as its own service with a SELECT-only database role (never
the webhook writer role), and reflects allowed CORS origins so the browser can
call it from the Rob site and from ephemeral preview hosts.
"""

from __future__ import annotations

import logging

from aiohttp import web

from rob.config.settings import PublicApiSettings
from rob.database.connection import Database
from rob.database.repositories.public_sends import PublicSendsRepository
from rob.database.repositories.public_summary import PublicSummaryRepository
from rob.publicapi.cors import parse_allowed_origins, resolve_cors_origin
from rob.publicapi.sends import handle_public_sends
from rob.publicapi.summary import handle_guild_summary

log = logging.getLogger(__name__)


async def handle_health(request: web.Request) -> web.Response:
    database: Database = request.app["database"]
    ok = await database.health_check()
    return web.json_response({"ok": ok}, status=200 if ok else 503)


def _cors_middleware(allowed_raw: str):
    """Middleware that stamps CORS headers on every response (errors included)
    and answers preflight ``OPTIONS`` requests directly. The allowed origin is
    reflected from the request when it matches the configured allow-list."""

    allowed = parse_allowed_origins(allowed_raw)

    @web.middleware
    async def middleware(request: web.Request, handler):
        acao = resolve_cors_origin(request.headers.get("Origin"), allowed)

        if request.method == "OPTIONS":
            response: web.StreamResponse = web.Response(status=204)
        else:
            try:
                response = await handler(request)
            except web.HTTPException as exc:
                # Attach CORS headers to error responses too, so the browser can
                # read a 404/400 body instead of a masked CORS failure.
                response = exc

        if acao is not None:
            response.headers["Access-Control-Allow-Origin"] = acao
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Max-Age"] = "86400"
        response.headers["Vary"] = "Origin"
        if isinstance(response, web.HTTPException):
            raise response
        return response

    return middleware


def create_public_api_app(
    *,
    settings: PublicApiSettings,
    database: Database,
) -> web.Application:
    app = web.Application(middlewares=[_cors_middleware(settings.public_api_allowed_origin)])
    app["settings"] = settings
    app["database"] = database
    app["public_sends_repository"] = PublicSendsRepository(database)
    app["public_summary_repository"] = PublicSummaryRepository(database)

    app.router.add_get("/health", handle_health)
    app.router.add_get("/public/sends", handle_public_sends)
    app.router.add_get("/public/guild-summary", handle_guild_summary)
    # Preflight OPTIONS is answered by the CORS middleware for every path.

    return app
