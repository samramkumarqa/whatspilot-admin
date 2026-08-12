"""
Global session gate for the admin app. Unlike the business-portal app,
there's only one role here - "admin" - the single shared admin account
(see api/auth.py's /login routes). Everything except a small allowlist
of endpoints that can't go through login at all requires an admin
session - the health check, used by Render for liveness probing.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse, JSONResponse

EXEMPT_PATHS = {
    "/login",
    "/logout",
    "/health",
}


class AdminAuthMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request, call_next):

        path = request.url.path

        if path in EXEMPT_PATHS:
            return await call_next(request)

        role = request.session.get("role")

        if role == "admin":
            return _no_store(await call_next(request))

        # Not authenticated. A real browser navigating to a page sends
        # "text/html" in Accept; this app's own fetch() calls don't set
        # an Accept header at all (defaults to "*/*" in every browser),
        # so this reliably tells a full page load apart from an XHR/
        # fetch call.
        accept = request.headers.get("accept", "")

        if "text/html" in accept:

            return RedirectResponse(
                url="/login",
                status_code=302
            )

        return JSONResponse(
            {
                "status": "error",
                "detail": "Not authenticated"
            },
            status_code=401
        )


def _no_store(response):
    """
    The Businesses page is rendered per-session. Without an explicit
    no-store, the browser could replay a cached copy after the session
    changes (e.g. a different admin logs in on the same browser).
    """

    response.headers["Cache-Control"] = "no-store"

    return response
