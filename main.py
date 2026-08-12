import logging

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from middleware import AdminAuthMiddleware
from crm.customer_mapping import init_customer_mapping, init_business_settings

from api.misc import router as misc_router
from api.businesses import router as businesses_router
from api.auth import router as auth_router

# ==========================================================
# Environment & Initialization
# ==========================================================
from config import (
    DEBUG,
    SESSION_SECRET_KEY,
    ADMIN_USERNAME,
    ADMIN_PASSWORD_HASH,
)

# Fail fast with a clear message if the admin login isn't configured,
# rather than letting the app start and only breaking later - a missing
# SESSION_SECRET_KEY doesn't surface until the first request tries to
# sign a session cookie, and a missing admin username/hash would mean
# every login attempt fails with no indication why (see auth.py's
# verify_admin_login(), which fails closed on either).
if not SESSION_SECRET_KEY or not ADMIN_USERNAME or not ADMIN_PASSWORD_HASH:
    raise RuntimeError(
        "Missing required env vars: SESSION_SECRET_KEY, ADMIN_USERNAME, "
        "and ADMIN_PASSWORD_HASH must all be set before starting the "
        "app - see the comments above ADMIN_USERNAME in config.py for "
        "how to generate ADMIN_PASSWORD_HASH."
    )

app = FastAPI()

# Middleware runs in reverse order of registration (last added = outermost
# = runs first), so SessionMiddleware has to be added AFTER
# AdminAuthMiddleware - it needs to populate request.session before
# AdminAuthMiddleware reads it.
app.add_middleware(AdminAuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    session_cookie="wp_admin_session",
    max_age=60 * 60 * 24 * 30,  # 30 days - re-login shouldn't be needed
                                 # on every visit, only once a session
                                 # actually expires or is logged out.
    same_site="lax",
    https_only=not DEBUG,
)

# customer_mapping.py's tables (customer_numbers/business_settings) hold
# the shared business registry this app reads/writes - the same
# Postgres database the business-portal deployments also connect to.
init_customer_mapping()
init_business_settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)

app.include_router(auth_router)
app.include_router(businesses_router)
app.include_router(misc_router)
