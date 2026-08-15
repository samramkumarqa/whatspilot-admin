import logging

from config import (
    DATABASE_URL,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_VERIFY_SERVICE_SID,
    TWILIO_WHATSAPP_NUMBER,
    GROQ_API_KEY,
    OTP_CHANNEL,
)
from crm.customer_mapping import update_provisioning_result
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


def _env_vars_for_business(business_id: str) -> list:
    """
    Env vars every new business-portal deployment needs, matching
    whatspilot-business-repo's own render.yaml. Twilio/Groq credentials
    are shared across every customer for now - a single WhatsApp
    Sandbox number and one Groq account serve all of them, same as the
    two businesses provisioned manually before this pipeline existed.
    Giving each customer their own WhatsApp Business API number is a
    largely manual, Twilio-side approval process this pipeline doesn't
    attempt to automate - revisit if/when that becomes a real
    requirement (see config.py's comment on these same variables).
    """

    return [
        {"key": "BUSINESS_ID", "value": business_id},
        {"key": "SESSION_SECRET_KEY", "generateValue": True},
        {"key": "DATABASE_URL", "value": DATABASE_URL},
        {"key": "TWILIO_ACCOUNT_SID", "value": TWILIO_ACCOUNT_SID},
        {"key": "TWILIO_AUTH_TOKEN", "value": TWILIO_AUTH_TOKEN},
        {"key": "TWILIO_VERIFY_SERVICE_SID", "value": TWILIO_VERIFY_SERVICE_SID},
        {"key": "TWILIO_WHATSAPP_NUMBER", "value": TWILIO_WHATSAPP_NUMBER},
        {"key": "OTP_CHANNEL", "value": OTP_CHANNEL},
        {"key": "GROQ_API_KEY", "value": GROQ_API_KEY},
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

    try:
        repo = create_repo_from_template(
            repo_name,
            description=f"WhatsPilot business portal for {user_id}",
        )
    except GitHubProvisioningError as e:

        logger.error(
            "Provisioning failed for %s at repo creation: %s", user_id, e
        )

        result = {
            "provisioning_status": "failed",
            "provisioning_error": f"GitHub: {e}",
            "github_repo_url": None,
            "render_service_id": None,
            "render_service_url": None,
        }
        update_provisioning_result(user_id, **result)
        return result

    if not wait_for_repo_ready(repo["full_name"]):
        logger.warning(
            "Provisioning %s: repo content wasn't confirmed ready in "
            "time - proceeding anyway, Render's own clone may just "
            "need a bit longer.",
            user_id
        )

    try:
        service = create_web_service(
            repo_name,
            repo["html_url"],
            env_vars=_env_vars_for_business(business_id),
        )
    except RenderProvisioningError as e:

        logger.error(
            "Provisioning failed for %s at service creation: %s", user_id, e
        )

        result = {
            "provisioning_status": "failed",
            "provisioning_error": f"Render: {e}",
            "github_repo_url": repo["html_url"],
            "render_service_id": None,
            "render_service_url": None,
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
