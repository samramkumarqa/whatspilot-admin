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
# provisioning/.

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# The GitHub account new customer repos get created under. GitHub's
# "generate repo from template" API takes a single `owner` login and
# doesn't care whether it's a user account or an org - we're using the
# personal account (github.com/samramkumarqa) rather than a dedicated
# org, so this holds a username, not an org name. Also doubles as the
# owner of the template repo itself, since both live in the same
# account.
GITHUB_OWNER = os.getenv("GITHUB_OWNER")
GITHUB_TEMPLATE_REPO = os.getenv("GITHUB_TEMPLATE_REPO", "whatspilot-business-template")
RENDER_API_KEY = os.getenv("RENDER_API_KEY")
RENDER_OWNER_ID = os.getenv("RENDER_OWNER_ID")

# Same shared Postgres instance every business-portal deployment also
# connects to (see provisioning/orchestrator.py, which forwards this
# value as the new deployment's own DATABASE_URL) - not otherwise used
# by this app itself, which reaches the database through
# database/db.py's own separate os.getenv() read of the same variable.
DATABASE_URL = os.getenv("DATABASE_URL")

# A least-privilege Postgres role for business-portal deployments,
# created by running provisioning/setup_business_portal_role.py once
# against the production database (see that script and PROVISIONING.md).
# Every business-portal deployment is customer-facing and has more
# attack surface than this admin app, so it shouldn't hold the same
# full-access credential this app itself uses. When this is set,
# provisioning/orchestrator.py forwards it as new deployments'
# DATABASE_URL instead of the admin app's own DATABASE_URL above. Falls
# back to DATABASE_URL if unset, so provisioning keeps working exactly
# as before until the setup script has actually been run.
BUSINESS_PORTAL_DATABASE_URL = os.getenv("BUSINESS_PORTAL_DATABASE_URL")

# Shared across every provisioned business-portal deployment for now -
# one Twilio WhatsApp Sandbox number and one Groq account serve every
# customer, same as the two businesses set up manually before this
# pipeline existed. Giving each customer their own WhatsApp Business
# API number is a largely manual, Twilio-side approval process this
# pipeline doesn't attempt to automate - revisit if/when that becomes a
# real requirement, at which point these would need to move from
# "shared config the admin app forwards" to "collected per-business at
# registration time" instead.
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_VERIFY_SERVICE_SID = os.getenv("TWILIO_VERIFY_SERVICE_SID")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OTP_CHANNEL = os.getenv("OTP_CHANNEL", "sms")
