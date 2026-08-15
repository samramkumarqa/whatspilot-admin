"""
One-off local verification for provisioning/github_client.py - creates a
throwaway test repo from the whatspilot-business-template to confirm your
GITHUB_TOKEN/GITHUB_OWNER actually work against the real GitHub API,
before this gets wired into the "Add Business" admin flow.

This does NOT delete the test repo afterward (on purpose - keeps this
script from needing repo-delete permissions). Delete it yourself from
github.com/<owner>/<repo> -> Settings -> Delete this repository once
you've confirmed it worked.

Usage:
    cd whatspilot-admin-repo
    GITHUB_TOKEN="ghp_..." GITHUB_OWNER="samramkumarqa" python verify_github_provisioning.py
"""

import os
import sys
import time

# config.py reads these via os.getenv() at import time, so they must be
# set in the environment before importing anything that pulls in config.
if not os.getenv("GITHUB_TOKEN") or not os.getenv("GITHUB_OWNER"):
    print("Set GITHUB_TOKEN and GITHUB_OWNER first, e.g.:")
    print('  GITHUB_TOKEN="ghp_..." GITHUB_OWNER="samramkumarqa" python verify_github_provisioning.py')
    sys.exit(1)

from provisioning.github_client import (
    create_repo_from_template,
    wait_for_repo_ready,
    GitHubProvisioningError,
)

test_repo_name = f"whatspilot-test-provisioning-{int(time.time())}"

print(f"Creating '{test_repo_name}' from the template...")

try:
    result = create_repo_from_template(
        test_repo_name,
        private=True,
        description="Throwaway repo - created by verify_github_provisioning.py, safe to delete.",
    )
except GitHubProvisioningError as e:
    print(f"FAILED: {e}")
    sys.exit(1)

print("Repo created:")
for key, value in result.items():
    print(f"  {key}: {value}")

print("\nWaiting for the template's file content to finish copying over...")
ready = wait_for_repo_ready(result["full_name"])

if ready:
    print("Content is ready - the repo is a real clone of the template.")
else:
    print(
        "Content didn't show up within the wait window - check the repo "
        "on GitHub directly; it may just need a bit longer."
    )

print(f"\nDone. Remember to delete {result['html_url']} when you're satisfied it worked.")
