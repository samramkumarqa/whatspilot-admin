import logging

from config import (
    DATABASE_URL,
    BUSINESS_PORTAL_DATABASE_URL,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_VERIFY_SERVICE_SID,
    TWILIO_WHATSAPP_NUMBER,
    GROQ_API_KEY,
    OTP_CHANNEL,
)
from crm.customer_mapping import update_provisioning_result, get_business
from provisioning.github_client import (
    create_repo_from_template,
    wait_for_repo_ready,
    GitHubProvisioningError,
)
from provisioning.render_client import (
    create_web_service,
    RenderProvisioningError,
)

logger = logging.getLogger(__name__)


def _repo_name_for_business(business_id: str) -> str:
    return f"whatspilot-{business_id}"


def _env_vars_for_business(
    business_id: str, twilio_whatsapp_number: str = None,
    groq_api_key: str = None
) -> list:
    """
    Env vars every new business-portal deployment needs, matching
    whatspilot-business-repo's own render.yaml.

    TWILIO_WHATSAPP_NUMBER: uses the business's own number (see
    crm/customer_mapping.py's customer_numbers.twilio_whatsapp_number
    column and register_business()) when one has been set, falling back
    to this admin app's shared TWILIO_WHATSAPP_NUMBER (the Twilio
    Sandbox number every business used before this parameter existed)
    otherwise. A business's own number only gets set once an admin has
    actually finished registering it with Twilio/Meta and pointing its
    webhook at that business's Render URL by hand - neither of which
    this pipeline automates (see PROVISIONING.md). Until that's done,
    every business keeps working exactly as before, sharing the one
    Sandbox number.

    TWILIO_ACCOUNT_SID/AUTH_TOKEN/VERIFY_SERVICE_SID stay shared across
    every customer deliberately, not as a limitation to fix later: one
    Twilio account can host many independent WhatsApp senders (see
    Twilio's own docs on registering additional senders under a single
    WABA), so there's no need for each business to have separate account
    credentials - only the number itself needs to differ per business,
    which is exactly what the parameter above does. Verify Service SID
    is even less business-specific - it sends OTPs to any phone number
    regardless of which business it belongs to, so splitting it per
    business would add complexity without fixing anything.

    GROQ_API_KEY: same fallback pattern as the WhatsApp number above -
    uses the business's own individually-allocated Groq key (see
    customer_numbers.groq_api_key and register_business()) when one has
    been set, falling back to this admin app's shared GROQ_API_KEY
    otherwise. Isolates one business's AI usage/rate limits from every
    other business's once they have their own key, and lets AI cost be
    attributed per business instead of all landing on this app's shared
    account. Unlike the Twilio number, a business's own key can be set
    straight from the Add Business form - no manual out-of-band Twilio/Meta
    step is needed first.

    DATABASE_URL: uses BUSINESS_PORTAL_DATABASE_URL (a least-privilege
    Postgres role - see provisioning/setup_business_portal_role.py) when
    it's configured, falling back to this admin app's own full-access
    DATABASE_URL otherwise. Every business-portal deployment is
    customer-facing and has more attack surface than this admin app, so
    it shouldn't hold the same credential this app uses for itself -
    the fallback just means provisioning keeps working exactly as
    before until an admin has actually run that setup script once.
    """

    business_portal_database_url = (
        BUSINESS_PORTAL_DATABASE_URL or DATABASE_URL
    )

    return [
        {"key": "BUSINESS_ID", "value": business_id},
        {"key": "SESSION_SECRET_KEY", "generateValue": True},
        {"key": "DATABASE_URL", "value": business_portal_database_url},
        {"key": "TWILIO_ACCOUNT_SID", "value": TWILIO_ACCOUNT_SID},
        {"key": "TWILIO_AUTH_TOKEN", "value": TWILIO_AUTH_TOKEN},
        {"key": "TWILIO_VERIFY_SERVICE_SID", "value": TWILIO_VERIFY_SERVICE_SID},
        {
            "key": "TWILIO_WHATSAPP_NUMBER",
            "value": twilio_whatsapp_number or TWILIO_WHATSAPP_NUMBER,
        },
        {"key": "OTP_CHANNEL", "value": OTP_CHANNEL},
        {
            "key": "GROQ_API_KEY",
            "value": groq_api_key or GROQ_API_KEY,
        },
        {"key": "DEBUG", "value": "false"},
        {"key": "PYTHON_VERSION", "value": "3.11.9"},
    ]


def provision_business(user_id: str, business_id: str) -> dict:
    """
    Creates a new customer's own repo + live Render deployment - the
    automated version of the manual "create repo, push, deploy on
    Render, set env vars" steps this project used to require per
    customer. Called right after a business is registered (see
    api/businesses.py's create_business()), and re-callable on an
    already-registered business to retry a failed attempt (see that
    same file's provision_business_route()).

    Runs synchronously - GitHub repo creation, a short content-readiness
    wait, then Render service creation, roughly 15-20 seconds total -
    rather than as a background job. That's the simplest thing that
    works for an admin-initiated, one-at-a-time action, and matches how
    the rest of this app already handles slower operations (no task
    queue exists anywhere else in the codebase). The admin's "Add
    Business" button will appear to hang for that long; a background
    job with polling is a reasonable future improvement if that becomes
    annoying, but isn't needed for correctness.

    Always records a provisioning_status on the business row before
    returning, success or failure, via update_provisioning_result() -
    including the partial-failure case (repo created but the Render
    call then fails), so an admin always has something concrete to see
    and a repo to reuse on retry, rather than provisioning silently
    losing track of what it already created.

    Returns the same dict passed to update_provisioning_result(), for
    the caller to include in its response. Does not raise - failures
    are reported through the returned dict's status/error fields, since
    a provisioning failure shouldn't fail the business *registration*
    that already succeeded.
    """

    repo_name = _repo_name_for_business(business_id)

    # Retry-after-partial-failure support: repo names are deterministic
    # (_repo_name_for_business()), so if an earlier attempt already
    # created the GitHub repo but failed before or during Render service
    # creation, calling create_repo_from_template() again on retry always
    # failed with a "name already exists" GitHubProvisioningError - making
    # retry a dead end for exactly the case it exists to handle. Reuse the
    # repo already on file for this business (from that earlier attempt's
    # update_provisioning_result() call) instead of creating a new one.
    existing_business = get_business(user_id)
    existing_repo_url = (
        existing_business.get("github_repo_url")
        if existing_business else None
    )

    if existing_repo_url:

        full_name = existing_repo_url.replace(
            "https://github.com/", ""
        ).rstrip("/")

        logger.info(
            "Provisioning %s: reusing existing repo %s from a prior "
            "attempt instead of creating a new one.",
            user_id, full_name
        )

        repo = {
            "full_name": full_name,
            "html_url": existing_repo_url,
        }

    else:

        try:
            repo = create_repo_from_template(
                repo_name,
                description=f"WhatsPilot business portal for {user_id}",
            )
        except GitHubProvisioningError as e:

            logger.error(
                "Provisioning failed for %s at repo creation: %s", user_id, e
            )

            # render_service_id/url fall back to whatever was already on
            # file (None for a genuinely first attempt, but not for a
            # retry or a race with a concurrent attempt) rather than
            # being hardcoded to None - see get_business()'s docstring
            # for why unconditionally nulling these out here would wipe
            # the record of an already-live, still-running Render
            # service that this particular failed attempt never touched.
            result = {
                "provisioning_status": "failed",
                "provisioning_error": f"GitHub: {e}",
                "github_repo_url": None,
                "render_service_id": (
                    existing_business.get("render_service_id")
                    if existing_business else None
                ),
                "render_service_url": (
                    existing_business.get("render_service_url")
                    if existing_business else None
                ),
            }
            update_provisioning_result(user_id, **result)
            return result

    # Deliberately broad: this used to sit outside any try/except, so any
    # exception here (not just the requests.RequestException
    # wait_for_repo_ready() already retries past internally - e.g. a bug
    # in its own response handling) propagated straight out of this
    # function despite its docstring promising it never raises, leaving
    # the business stuck at provisioning_status='pending' forever and
    # returning an unhandled 500 to the admin UI instead of a clear
    # failure message.
    try:
        if not wait_for_repo_ready(repo["full_name"]):
            logger.warning(
                "Provisioning %s: repo content wasn't confirmed ready in "
                "time - proceeding anyway, Render's own clone may just "
                "need a bit longer.",
                user_id
            )
    except Exception as e:
        logger.exception(
            "Provisioning %s: unexpected error while waiting for repo "
            "content to be ready - proceeding anyway: %s", user_id, e
        )

    try:
        service = create_web_service(
            repo_name,
            repo["html_url"],
            env_vars=_env_vars_for_business(
                business_id,
                twilio_whatsapp_number=(
                    existing_business.get("twilio_whatsapp_number")
                    if existing_business else None
                ),
                groq_api_key=(
                    existing_business.get("groq_api_key")
                    if existing_business else None
                ),
            ),
        )
    except RenderProvisioningError as e:

        logger.error(
            "Provisioning failed for %s at service creation: %s", user_id, e
        )

        # Same fallback-instead-of-hardcoded-None reasoning as the
        # GitHub-failure branch above.
        result = {
            "provisioning_status": "failed",
            "provisioning_error": f"Render: {e}",
            "github_repo_url": repo["html_url"],
            "render_service_id": (
                existing_business.get("render_service_id")
                if existing_business else None
            ),
            "render_service_url": (
                existing_business.get("render_service_url")
                if existing_business else None
            ),
        }
        update_provisioning_result(user_id, **result)
        return result

    result = {
        "provisioning_status": "live",
        "provisioning_error": None,
        "github_repo_url": repo["html_url"],
        "render_service_id": service["id"],
        "render_service_url": service["url"],
    }
    update_provisioning_result(user_id, **result)
    return result
