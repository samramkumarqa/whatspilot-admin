import logging

import requests

from config import RENDER_API_KEY, RENDER_OWNER_ID

logger = logging.getLogger(__name__)

RENDER_API_BASE = "https://api.render.com/v1"


class RenderProvisioningError(Exception):
    """
    Raised for any failure creating a customer's web service on Render -
    missing config, a duplicate name, the GitHub repo not being visible
    to Render yet, payment/plan issues, or an unexpected response.
    Callers (the Add Business flow, see api/businesses.py) should catch
    this specifically so they can record a clear provisioning-status
    message instead of a raw traceback.
    """


def _headers():

    if not RENDER_API_KEY:
        raise RenderProvisioningError(
            "RENDER_API_KEY is not configured - set it in this app's "
            "environment before provisioning can create services."
        )

    return {
        "Authorization": f"Bearer {RENDER_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def create_web_service(
    name: str,
    repo_url: str,
    env_vars: list,
    branch: str = "main",
    build_command: str = "pip install -r requirements.txt",
    start_command: str = (
        'uvicorn main:app --host 0.0.0.0 --port $PORT '
        '--proxy-headers --forwarded-allow-ips="*"'
    ),
    health_check_path: str = "/health",
    plan: str = "free",
    region: str = "oregon",
    timeout: int = 30,
) -> dict:
    """
    Creates a new Render web service (Python/native runtime, not Docker)
    from a GitHub repo - the second half of provisioning a new customer's
    portal, after provisioning/github_client.py's
    create_repo_from_template() has already generated their repo.

    env_vars follows Render's own API shape exactly - a list of either
    {"key": ..., "value": ...} or {"key": ..., "generateValue": True}
    (Render generates a random secret for the latter, e.g. for
    SESSION_SECRET_KEY). Deciding *what* env vars a new business-portal
    deployment needs is the caller's job (see the Add Business flow) -
    this function is a thin, faithful wrapper around Render's API and
    doesn't hardcode WhatsPilot-specific env var names.

    plan defaults to "free" deliberately - Render's own API defaults an
    omitted `plan` to "starter" (a paid plan), which would silently
    start billing every customer deployment. Every business-portal
    service should stay on the free tier unless a caller explicitly
    overrides it.

    Returns the subset of Render's response this app actually needs: id,
    name, dashboard_url, url (the live https://*.onrender.com address -
    may not resolve until the first deploy finishes), deploy_id. Raises
    RenderProvisioningError on any failure - see that class's docstring
    for what callers should do with it.
    """

    if not RENDER_OWNER_ID:
        raise RenderProvisioningError(
            "RENDER_OWNER_ID is not configured - set it in this app's "
            "environment before provisioning can create services."
        )

    payload = {
        "type": "web_service",
        "name": name,
        "ownerId": RENDER_OWNER_ID,
        "repo": repo_url,
        "branch": branch,
        "autoDeploy": "yes",
        "envVars": env_vars,
        "serviceDetails": {
            "runtime": "python",
            "plan": plan,
            "region": region,
            "healthCheckPath": health_check_path,
            "envSpecificDetails": {
                "buildCommand": build_command,
                "startCommand": start_command,
            },
        },
    }

    try:
        response = requests.post(
            f"{RENDER_API_BASE}/services",
            headers=_headers(),
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as e:
        raise RenderProvisioningError(
            f"Could not reach Render to create service '{name}': {e}"
        ) from e

    if response.status_code == 201:

        body = response.json()
        service = body.get("service", {})

        return {
            "id": service.get("id"),
            "name": service.get("name"),
            "dashboard_url": service.get("dashboardUrl"),
            "url": service.get("serviceDetails", {}).get("url"),
            "deploy_id": body.get("deployId"),
        }

    # -----------------------------
    # Known failure modes - surfaced with a specific, actionable message
    # rather than a generic "Render returned 4xx".
    # -----------------------------

    if response.status_code == 401:
        raise RenderProvisioningError(
            "Render rejected RENDER_API_KEY as invalid or expired."
        )

    if response.status_code == 402:
        raise RenderProvisioningError(
            "Render requires payment information on file before it will "
            "create this service (this can happen even on the free plan "
            "past a certain number of services) - check the billing "
            "section of the Render dashboard."
        )

    if response.status_code == 404:
        raise RenderProvisioningError(
            f"Render couldn't find the repo '{repo_url}'. Most likely "
            "cause: Render's GitHub integration doesn't have access to "
            "this repo yet - check the connected GitHub account's "
            "repository access under Render's Account Settings -> "
            "GitHub, and make sure it's set to all repositories (or "
            "explicitly includes newly-created ones)."
        )

    if response.status_code == 409:
        raise RenderProvisioningError(
            f"A service named '{name}' already exists in this Render "
            "workspace - choose a different name or remove the "
            "existing service first."
        )

    if response.status_code == 429:
        raise RenderProvisioningError(
            "Render rate-limited this request - try again shortly."
        )

    raise RenderProvisioningError(
        f"Unexpected response creating service '{name}': "
        f"{response.status_code} {response.text}"
    )
