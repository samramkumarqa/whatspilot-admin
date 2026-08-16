"""
One-off local script that creates a least-privilege Postgres role for
business-portal deployments to use instead of this admin app's own
full-access DATABASE_URL.

Why this exists: every business-portal deployment is a customer-facing
web app (more attack surface than this admin app) and today all of them
share this admin app's own DATABASE_URL - meaning a compromised
business-portal deployment would get a Postgres credential that can do
anything this app can, including things it has no legitimate reason to
touch (drop tables, create other Postgres roles, rewrite the
customer_numbers tenant registry itself). This script creates a new
role, `whatspilot_business_portal`, that:

  - Can SELECT/INSERT/UPDATE/DELETE on the ~13 tables business-portal
    code actually reads and writes (leads, conversations, reminders,
    etc. - see TABLES below).
  - Can only SELECT (never write) on customer_numbers - business-portal
    code reads this table to resolve its own business_id and to look up
    a business-owner login, but should never be able to modify its own
    or another business's registry row (status, business_id,
    provisioning fields).
  - Can CREATE new tables (needed so a future code change that adds a
    genuinely new table keeps self-migrating the same way this app's
    tables do today - see LIMITATION below) but is NOT a superuser and
    cannot CREATEDB or CREATEROLE, so it can't do database- or
    cluster-level damage.

LIMITATION (read before relying on this for a schema change): Postgres
requires *ownership* of a table to run ALTER TABLE on it, and ownership
isn't something you can grant piecemeal - only the original owning role
(whichever role DATABASE_URL already uses) can ALTER TABLE ADD COLUMN
on the tables below going forward. In practice this doesn't affect
day-to-day operation - every business-portal deployment's own
init_*() functions already check whether a column exists before trying
to add it, so once a column has been added once, every other
deployment's startup just sees it's already there and skips the ALTER.
It only matters the NEXT time a code change adds a genuinely new
column to an EXISTING table: that one ALTER TABLE needs to be run once,
by hand, using the original (owner) DATABASE_URL - not by this
restricted role. New TABLES don't have this problem (the restricted
role can create those itself and becomes their owner automatically).

Usage - run locally, once, against production:

    cd whatspilot-admin-repo
    DATABASE_URL="<your production Internal/External Database URL>" python provisioning/setup_business_portal_role.py

This prints a new connection string ONCE at the end. Copy it
immediately into this app's Render environment as
BUSINESS_PORTAL_DATABASE_URL (Render dashboard -> whatspilot-admin ->
Environment) - it is not saved anywhere by this script. New businesses
provisioned after that will use it automatically (see
provisioning/orchestrator.py); already-provisioned businesses keep
using the old DATABASE_URL until you update their own Render env vars
by hand, if you want them covered too.

Safe to re-run: if the role already exists, this skips CREATE ROLE and
only resets its password (printing a new connection string with the
new password) and re-applies the GRANT statements, which are all
idempotent.
"""

import os
import secrets
import sys
from urllib.parse import urlparse, urlunparse, quote

import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    print("Set DATABASE_URL first, e.g.:")
    print(
        '  DATABASE_URL="postgresql://..." python '
        "provisioning/setup_business_portal_role.py"
    )
    sys.exit(1)

ROLE_NAME = "whatspilot_business_portal"

# Every table business-portal code (whatspilot-business-repo) actually
# reads and writes - see each repo's own init_*() functions for the
# CREATE TABLE statements this list is drawn from. Kept as an explicit
# list rather than "everything except customer_numbers" so a future
# admin-only table doesn't silently become writable by this role by
# default.
BUSINESS_PORTAL_TABLES = [
    "conversations",
    "reminders",
    "unread_messages",
    "ai_activity",
    "opportunities",
    "customer_tags",
    "customer_mapping",
    "business_settings",
    "leads",
    "lead_history",
    "ai_followups",
    "automation_rules",
    "automation_rule_executions",
]

# Read-only: the tenant registry. business-portal resolves its own
# business_id and handles business-owner login by reading this table,
# but must never be able to write to it - see this script's module
# docstring.
READ_ONLY_TABLES = [
    "customer_numbers",
]


def _table_exists(conn, table_name):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table_name,),
        )
        return cur.fetchone() is not None


def main():

    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True

    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (ROLE_NAME,)
        )
        role_exists = cur.fetchone() is not None

    password = secrets.token_urlsafe(24)

    with conn.cursor() as cur:

        if role_exists:
            print(f"Role '{ROLE_NAME}' already exists - resetting its password.")
            # Deliberately does NOT restate NOSUPERUSER/NOCREATEDB/NOCREATEROLE/
            # NOREPLICATION here, even though it's harmless in principle (the
            # role already has these attributes from when it was first
            # created below). Postgres 16+ restricts ALTER ROLE ... SUPERUSER/
            # NOSUPERUSER (and REPLICATION/BYPASSRLS) to actual superusers
            # only, regardless of the value being set or who owns/created the
            # role - CREATEROLE isn't enough. Managed Postgres providers
            # (Render, Supabase, RDS, ...) never hand out real superuser, so
            # including those clauses here fails with "permission denied to
            # alter role" even on a role this same script created. Only the
            # password needs to change on a re-run, so only touch that.
            cur.execute(
                f'ALTER ROLE "{ROLE_NAME}" WITH LOGIN PASSWORD %s',
                (password,),
            )
        else:
            print(f"Creating role '{ROLE_NAME}'...")
            cur.execute(
                f'CREATE ROLE "{ROLE_NAME}" WITH LOGIN PASSWORD %s '
                f"NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION",
                (password,),
            )

        print("Granting schema usage + create...")
        cur.execute(f'GRANT USAGE, CREATE ON SCHEMA public TO "{ROLE_NAME}"')

        for table in BUSINESS_PORTAL_TABLES:
            if not _table_exists(conn, table):
                print(f"  (skipping '{table}' - table doesn't exist yet)")
                continue
            print(f"  granting SELECT/INSERT/UPDATE/DELETE on {table}")
            cur.execute(
                f'GRANT SELECT, INSERT, UPDATE, DELETE '
                f'ON TABLE "{table}" TO "{ROLE_NAME}"'
            )

        for table in READ_ONLY_TABLES:
            if not _table_exists(conn, table):
                print(f"  (skipping '{table}' - table doesn't exist yet)")
                continue
            print(f"  granting SELECT (read-only) on {table}")
            cur.execute(
                f'GRANT SELECT ON TABLE "{table}" TO "{ROLE_NAME}"'
            )

        # SERIAL/BIGSERIAL primary key columns are backed by sequences -
        # INSERT needs USAGE+SELECT on the specific sequence, not just
        # the table, or every INSERT into a table with a SERIAL id would
        # fail with "permission denied for sequence ..._id_seq".
        print("Granting sequence usage for INSERT ... RETURNING id columns...")
        cur.execute(
            f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public '
            f'TO "{ROLE_NAME}"'
        )

    conn.close()

    parsed = urlparse(DATABASE_URL)
    new_netloc = (
        f"{ROLE_NAME}:{quote(password)}@{parsed.hostname}"
        f"{':' + str(parsed.port) if parsed.port else ''}"
    )
    restricted_url = urlunparse(
        (parsed.scheme, new_netloc, parsed.path, "", "", "")
    )

    print()
    print("Done. Save this as BUSINESS_PORTAL_DATABASE_URL in the admin")
    print("app's Render environment now - it will not be shown again:")
    print()
    print(f"  {restricted_url}")
    print()
    print(
        "New businesses provisioned after this is set will use it "
        "automatically. See this script's module docstring for what "
        "this role can/can't do."
    )


if __name__ == "__main__":
    main()
