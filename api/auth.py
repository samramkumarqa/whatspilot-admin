import logging

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from auth import verify_admin_login
from rate_limit import is_rate_limited, record_attempt

logger = logging.getLogger(__name__)

router = APIRouter()

templates = Jinja2Templates(directory="templates")


@router.get("/login")
async def login_page(request: Request):

    # AdminAuthMiddleware redirects here with ?error=1 after a failed
    # attempt (PRG pattern - the POST below redirects rather than
    # rendering directly, so refreshing the page after a failed login
    # doesn't resubmit the credentials). error is passed through as the
    # raw query string value (rather than collapsed to a bool) so the
    # template can show a distinct message for ?error=ratelimited vs.
    # the generic ?error=1 bad-credentials case.
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "error": request.query_params.get("error")
        }
    )


@router.post("/login")
async def login_submit(request: Request):

    ip = request.client.host if request.client else "unknown"
    rate_key = f"admin_login:{ip}"

    # Checked (and recorded) before touching verify_admin_login at all -
    # a scripted brute-force loop gets rejected outright once it's over
    # the limit, rather than paying the bcrypt cost (~100-300ms) on every
    # attempt, which is throttling in itself but not a substitute for an
    # actual cap. 5 attempts / 5 minutes per IP - generous enough that a
    # real admin mistyping their password a couple of times never hits
    # it, tight enough to make a brute-force loop impractical.
    if is_rate_limited(rate_key, max_attempts=5, window_seconds=300):
        return RedirectResponse(
            url="/login?error=ratelimited",
            status_code=303
        )

    record_attempt(rate_key)

    form = await request.form()

    username = (form.get("username") or "").strip()
    password = form.get("password") or ""

    valid = await run_in_threadpool(
        verify_admin_login, username, password
    )

    if not valid:
        return RedirectResponse(
            url="/login?error=1",
            status_code=303
        )

    request.session["role"] = "admin"
    request.session["username"] = username

    return RedirectResponse(url="/", status_code=303)


@router.get("/logout")
async def logout(request: Request):

    request.session.clear()

    return RedirectResponse(url="/login", status_code=303)
