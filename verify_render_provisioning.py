"""
One-off local verification for provisioning/render_client.py - creates a
throwaway Render web service to confirm your RENDER_API_KEY/RENDER_OWNER_ID
actually work against the real Render API, before this gets wired into
the "Add Business" admin flow.

If RENDER_OWNER_ID isn't set, this looks up your available workspaces
for you first and asks you to pick one - there's no field in the Render
dashboard UI labeled "Owner ID", so this is the easiest way to find it.

This test service is intentionally minimal - it points at a repo of your
choosing (ideally a throwaway one, not whatspilot-business-template
itself) and doesn't pass real app secrets (DATABASE_URL, TWILIO_*, etc).
It's normal and expected for the deploy itself to fail once it boots -
we're only verifying that Render's API accepts the create-service call,
not that the app actually runs. Delete the service afterward from
dashboard.render.com once you've confirmed the API call succeeded.

Usage:
    cd whatspilot-admin-repo
    RENDER_API_KEY="rnd_..." GITHUB_TEST_REPO_URL="https://github.com/samramkumarqa/whatspilot-business-template" python verify_render_provisioning.py

    # If you don't know your RENDER_OWNER_ID yet, omit it the first run -
    # the script will list your workspaces and their IDs, then exit.
"""

import os
import sys
import time

import requests

if not os.getenv("RENDER_API_KEY"):
    print("Set RENDER_API_KEY first, e.g.:")
    print('  RENDER_API_KEY="rnd_..." GITHUB_TEST_REPO_URL="https://github.com/samramkumarqa/whatspilot-business-template" python verify_render_provisioning.py')
    sys.exit(1)

if not os.getenv("GITHUB_TEST_REPO_URL"):
    print("Set GITHUB_TEST_REPO_URL to a repo Render can see, e.g. your")
    print("whatspilot-test-provisioning-<timestamp> repo from the GitHub")
    print("verification step, or whatspilot-business-template itself.")
    sys.exit(1)

RENDER_API_BASE = "https://api.render.com/v1"
headers = {
    "Authorization": f"Bearer {os.environ['RENDER_API_KEY']}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

if not os.getenv("RENDER_OWNER_ID"):

    print("RENDER_OWNER_ID not set - looking up your workspaces...")
    response = requests.get(f"{RENDER_API_BASE}/owners", headers=headers, timeout=15)

    if response.status_code != 200:
        print(f"Couldn't list workspaces: {response.status_code} {response.text}")
        sys.exit(1)

    for item in response.json():
        owner = item.get("owner", item)
        label = owner.get("name") or owner.get("email") or "(unnamed)"
        print(f"  {owner.get('id')}  -  {label}")

    print("\nSet RENDER_OWNER_ID to one of the ids above and rerun.")
    sys.exit(0)

# config.py reads these via os.getenv() at import time, so they must be
# set in the environment before importing anything that pulls in config.
from provisioning.render_client import create_web_service, RenderProvisioningError

test_name = f"whatspilot-test-provisioning-{int(time.time())}"

print(f"Creating Render web service '{test_name}' from {os.environ['GITHUB_TEST_REPO_URL']}...")

try:
    result = create_web_service(
        test_name,
        os.environ["GITHUB_TEST_REPO_URL"],
        env_vars=[
            {"key": "SESSION_SECRET_KEY", "generateValue": True},
            {"key": "BUSINESS_ID", "value": "test"},
        ],
    )
except RenderProvisioningError as e:
    print(f"FAILED: {e}")
    sys.exit(1)

print("Service created:")
for key, value in result.items():
    print(f"  {key}: {value}")

print(
    f"\nDone. The app itself will likely fail to boot (no DATABASE_URL/"
    f"TWILIO_* set) - that's expected and fine for this test. Check "
    f"{result['dashboard_url']} to confirm the service exists, then "
    f"delete it once you're satisfied the API call worked."
)
