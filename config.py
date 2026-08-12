import os
from dotenv import load_dotenv

# Load .env once
load_dotenv()

# ----------------------------------------
# App
# ----------------------------------------

DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# ----------------------------------------
# Admin login
# ----------------------------------------
# Gates every page/route in this app except /login, /logout, and /health
# - see middleware.py's AdminAuthMiddleware. Single shared admin account
# rather than a user table - this app has exactly one user type.

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH")
SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY")

# ----------------------------------------
# Logging
# ----------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ----------------------------------------
# Customer-portal provisioning (GitHub + Render APIs)
# ----------------------------------------
# Used when a business is added in the registry to spin up that
# customer's own portal repo + live deployment automatically. See
# provisioning/ (added in a later pass of the repo split).

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_ORG = os.getenv("GITHUB_ORG")
GITHUB_TEMPLATE_REPO = os.getenv("GITHUB_TEMPLATE_REPO", "whatspilot-business-template")
RENDER_API_KEY = os.getenv("RENDER_API_KEY")
RENDER_OWNER_ID = os.getenv("RENDER_OWNER_ID")
