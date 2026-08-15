import logging
import time

import requests

from config import GITHUB_TOKEN, GITHUB_OWNER, GITHUB_TEMPLATE_REPO

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"


class GitHubProvisioningError(Exception):
    """
    Raised for any failure creating a customer repo from the template -
    missing config, the repo name already existing, auth/permission
    issues, or an unexpected response from GitHub. Callers (the Add
    Business flow, see api/businesses.py) should catch this specifically
    so they can record a clear provisioning-status message instead of a
    raw traceback.
    """


def _headers():

    if not GITHUB_TOKEN:
        raise GitHubProvisioningError(
            "GITHUB_TOKEN is not configured - set it in this app's "
            "environment before provisioning can create repos."
        )

    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }


def create_repo_from_template(
    repo_name: str,
    private: bool = True,
    description: str = None,
    timeout: int = 15,
) -> dict:
    """
    Creates a new repo under GITHUB_OWNER by generating it from the
    whatspilot-business-template repo (GITHUB_TEMPLATE_REPO) - the same
    thing "Use this template" does in GitHub's UI. Used by the Add
    Business admin flow to spin up each new customer's own portal repo.

    Returns the subset of GitHub's response this app actually needs:
    full_name, html_url, clone_url, default_branch. Raises
    GitHubProvisioningError on any failure - see that class's docstring
    for what callers should do with it.

    Note: GitHub creates the repo object synchronously, but copying the
    template's file content over happens asynchronously afterward and
    can lag by a few seconds for larger templates. Anything that
    immediately needs the repo to be non-empty (e.g. pointing a Render
    deployment at it) should call wait_for_repo_ready() first.
    """

    if not GITHUB_OWNER:
        raise GitHubProvisioningError(
            "GITHUB_OWNER is not configured - set it in this app's "
            "environment before provisioning can create repos."
        )

    url = (
        f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_TEMPLATE_REPO}"
        "/generate"
    )

    payload = {
        "owner": GITHUB_OWNER,
        "name": repo_name,
        "private": private,
    }

    if description:
        payload["description"] = description

    try:
        response = requests.post(
            url,
            headers=_headers(),
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as e:
        raise GitHubProvisioningError(
            f"Could not reach GitHub to create repo '{repo_name}': {e}"
        ) from e

    if response.status_code == 201:

        # A 201 with a non-JSON body would otherwise crash with an
        # unhandled ValueError here instead of the clear
        # GitHubProvisioningError callers expect from this function.
        try:
            body = response.json()
        except ValueError as e:
            raise GitHubProvisioningError(
                f"GitHub returned a 201 creating repo '{repo_name}' but "
                f"the response body wasn't valid JSON: {e}"
            ) from e

        return {
            "full_name": body.get("full_name"),
            "html_url": body.get("html_url"),
            "clone_url": body.get("clone_url"),
            "default_branch": body.get("default_branch", "main"),
        }

    # -----------------------------
    # Known failure modes - surfaced with a specific, actionable message
    # rather than a generic "GitHub returned 4xx".
    # -----------------------------

    if response.status_code == 404:
        raise GitHubProvisioningError(
            f"Template repo '{GITHUB_OWNER}/{GITHUB_TEMPLATE_REPO}' "
            "wasn't found, isn't marked as a template repo, or "
            "GITHUB_TOKEN doesn't have access to it."
        )

    if response.status_code == 401:
        raise GitHubProvisioningError(
            "GitHub rejected GITHUB_TOKEN as invalid or expired."
        )

    if response.status_code == 403:
        raise GitHubProvisioningError(
            "GITHUB_TOKEN doesn't have permission to create repos under "
            f"'{GITHUB_OWNER}' - it needs the 'repo' scope (classic "
            "token) or equivalent fine-grained Administration access."
        )

    if response.status_code == 422:

        # GitHub's `errors` array isn't consistently shaped - some
        # validation failures (including "name already exists", the
        # common case here) come back as plain strings, others as
        # {"resource", "field", "code", "message"} objects. Handling
        # only the dict shape crashed with AttributeError the first
        # time a real string-shaped error came through.
        body = response.json()
        errors = body.get("errors", [])

        def _error_text(err):
            if isinstance(err, str):
                return err
            if isinstance(err, dict):
                return err.get("message", "")
            return str(err)

        error_texts = [_error_text(err) for err in errors]

        if any("already exists" in text.lower() for text in error_texts):
            raise GitHubProvisioningError(
                f"A repo named '{repo_name}' already exists under "
                f"'{GITHUB_OWNER}' - choose a different name or remove "
                "the existing repo first."
            )

        raise GitHubProvisioningError(
            f"GitHub rejected the repo creation request: "
            f"{'; '.join(t for t in error_texts if t) or body.get('message') or response.text}"
        )

    raise GitHubProvisioningError(
        f"Unexpected response creating repo '{repo_name}': "
        f"{response.status_code} {response.text}"
    )


def wait_for_repo_ready(
    full_name: str,
    attempts: int = 6,
    delay_seconds: float = 2.0,
) -> bool:
    """
    Polls GET /repos/{full_name}/contents (the root directory listing)
    until it returns actual files or the attempt budget runs out. See
    create_repo_from_template()'s docstring for why this matters: the
    repo object exists immediately, but its content lags by a few
    seconds. Only needed by callers that act on the repo's *contents*
    right away (e.g. deploying it) - anything that just links to the
    repo doesn't need this.

    Deliberately checks the file listing itself rather than the repo
    object's `size` field (KB, computed by a separate background job)
    - `size` can stay 0 for well over a minute after the files are
    already there and fully usable, which made this report "not ready"
    for repos that were actually fine. The contents listing reflects
    reality immediately.

    Returns True once content is detected, False if it never showed up
    within the attempt budget (caller decides whether that's fatal).
    """

    url = f"{GITHUB_API_BASE}/repos/{full_name}/contents"

    for attempt in range(attempts):

        try:
            response = requests.get(url, headers=_headers(), timeout=15)
        except requests.RequestException as e:
            logger.warning(
                "wait_for_repo_ready: request failed (attempt %d/%d): %s",
                attempt + 1, attempts, e
            )
            time.sleep(delay_seconds)
            continue

        if response.status_code == 200:
            body = response.json()
            if isinstance(body, list) and len(body) > 0:
                return True

        time.sleep(delay_seconds)

    return False
