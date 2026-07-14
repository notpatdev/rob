"""Public read-only API service (``api.robthebot.com``).

A tiny aiohttp app that serves the website's "grab your data" page. It runs as
its own service with a SELECT-only database role (never the webhook writer
role), and adds permissive-but-scoped CORS so the browser can call it from the
Rob website.
"""

from __future__ import annotations

import logging

from aiohttp import web

from rob.config.settings import PublicApiSettings
from rob.database.connection import Database
from rob.database.repositories.public_sends import PublicSendsRepository
from rob.publicapi.sends import handle_public_sends

log = logging.getLogger(__name__)


async def handle_health(request: web.Request) -> web.Response:
    database: Database = request.app["database"]
    ok = await database.health_check()
    return web.json_response({"ok": ok}, status=200 if ok else 503)


def _cors_middleware(allowed_origin: str):
    """Middleware that stamps CORS headers on every response (errors included)
    and answers preflight ``OPTIONS`` requests directly."""

    @web.middleware
    async def middleware(request: web.Request, handler):
        if request.method == "OPTIONS":
            response: web.StreamResponse = web.Response(status=204)
        else:
            try:
                response = await handler(request)
            except web.HTTPException as exc:
                # Attach CORS headers to error responses too, so the browser can
                # read a 404/400 body instead of a masked CORS failure.
                response = exc
        response.headers["Access-Control-Allow-Origin"] = allowed_origin
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
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

    app.router.add_get("/health", handle_health)
    app.router.add_get("/public/sends", handle_public_sends)
    # Preflight OPTIONS is answered by the CORS middleware for every path.

    return app
