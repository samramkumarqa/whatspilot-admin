from database.db import get_crm_connection

def init_customer_mapping():

    conn = get_crm_connection()

    # Business registration table
    # NOTE: business_id was missing here - the live production data/app.db
    # has it (added via a manual ALTER TABLE at some point), and
    # save_customer_number() below writes to it unconditionally. A fresh
    # database would fail with "table customer_numbers has no column named
    # business_id" on the very first call.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS customer_numbers (
            user_id TEXT PRIMARY KEY,
            whatsapp_number TEXT NOT NULL,
            business_id TEXT
        )
    """)

    # customer_numbers is this app's tenant registry - one row per
    # business. These three columns turn it from a lookup table into
    # something an activation flow and per-business automation can
    # actually use:
    #
    #   status              - 'active' businesses are the ones
    #                          automation/runner.py evaluates rules for
    #                          (see get_active_businesses() below) and,
    #                          eventually, the ones allowed to log in.
    #                          Existing rows default to 'active' so the
    #                          current single tenant keeps working
    #                          unchanged through this migration.
    #   owner_whatsapp_number - a *separate* personal WhatsApp number for
    #                          the business owner/admin, distinct from
    #                          `whatsapp_number` (the Twilio-connected
    #                          number the bot sends/receives from). A
    #                          WhatsApp Business API number generally
    #                          isn't readable in a normal WhatsApp client,
    #                          so a login OTP has to go to a real personal
    #                          number instead.
    #   created_at          - when this business was registered.
    existing_business_columns = {
        row[0] for row in
        conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'customer_numbers'"
        ).fetchall()
    }

    if "status" not in existing_business_columns:
        conn.execute(
            "ALTER TABLE customer_numbers ADD COLUMN status TEXT DEFAULT 'active'"
        )

    if "owner_whatsapp_number" not in existing_business_columns:
        conn.execute(
            "ALTER TABLE customer_numbers ADD COLUMN owner_whatsapp_number TEXT"
        )

    if "created_at" not in existing_business_columns:
        conn.execute(
            "ALTER TABLE customer_numbers ADD COLUMN created_at "
            "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        )

    # Customer → Business mapping table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS customer_mapping (
            customer_phone TEXT PRIMARY KEY,
            business_phone TEXT NOT NULL,
            customer_name TEXT
        )
    """)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_customer_mapping_business_phone "
        "ON customer_mapping(business_phone)"
    )

    # customer_numbers is queried by whatsapp_number on every incoming
    # webhook message (get_customer_by_number()) and by whatsapp_number
    # OR owner_whatsapp_number on every business-login attempt
    # (get_business_by_login_number()) - both hot paths, and both were
    # running full table scans with no index to support them.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_customer_numbers_whatsapp_number "
        "ON customer_numbers(whatsapp_number)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_customer_numbers_owner_whatsapp_number "
        "ON customer_numbers(owner_whatsapp_number)"
    )

    # customer_name existed in a schema created before this column was
    # added - patch it in for any database created by an older version.
    existing_columns = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'customer_mapping'"
        ).fetchall()
    }

    if "customer_name" not in existing_columns:
        conn.execute(
            "ALTER TABLE customer_mapping ADD COLUMN customer_name TEXT"
        )

    conn.commit()
    conn.close()


# --------------------------------------------------
# BUSINESS REGISTRATION
# user_id -> business whatsapp number
# --------------------------------------------------
def get_customers(user_id):

    conn = get_crm_connection()
    cursor = conn.execute(
        """
        SELECT customer_phone
        FROM customer_mapping
        WHERE business_phone = (
            SELECT whatsapp_number
            FROM customer_numbers
            WHERE user_id = ?
        )
        """,
        (user_id,)
    )

    customers = [
        row[0]
        for row in cursor.fetchall()
    ]

    # BUG FIX: conn.close() used to sit after `return customers`, so it
    # was unreachable dead code - every call to this function permanently
    # checked a pooled connection out of database/db.py's 5-connection
    # pool and never returned it. Enough calls (this is exercised by
    # tests/test_crm_managers.py) would exhaust the pool and start
    # blocking/erroring on get_crm_connection().
    conn.close()

    return customers
def get_user_id_by_business_id(business_id):

    conn = get_crm_connection()

    cursor = conn.execute(
        """
        SELECT user_id
        FROM customer_numbers
        WHERE business_id = ?
        """,
        (business_id,)
    )

    row = cursor.fetchone()

    conn.close()

    if not row:
        return None

    return row[0]

def get_business_id(user_id):

    conn = get_crm_connection()

    cursor = conn.execute(
        """
        SELECT business_id
        FROM customer_numbers
        WHERE user_id = ?
        """,
        (user_id,)
    )

    row = cursor.fetchone()

    conn.close()

    if not row:
        return None

    return row[0]

def get_active_businesses():
    """
    Every registered business with status='active' - the tenant list
    automation/runner.py loops over to evaluate each business's own rules
    against its own customers, instead of the old hardcoded single
    business_id. Rows with business_id or whatsapp_number missing are
    skipped (not yet fully registered) rather than raising - a partially
    set up business just doesn't run automation until it's complete.
    """

    conn = get_crm_connection()

    rows = conn.execute(
        """
        SELECT user_id, whatsapp_number, business_id
        FROM customer_numbers
        WHERE status = 'active'
        """
    ).fetchall()

    conn.close()

    return [
        {
            "user_id": row[0],
            "whatsapp_number": row[1],
            "business_id": row[2]
        }
        for row in rows
        if row[2]
    ]


def list_businesses():
    """
    Every registered business, active or not - backs the admin
    Businesses page (GET /businesses, templates/businesses.html). Unlike
    get_active_businesses(), this includes inactive/incomplete rows too,
    since the admin page's whole job is showing what's registered and
    letting an admin activate/deactivate/delete it.
    """

    conn = get_crm_connection()

    rows = conn.execute(
        """
        SELECT
            user_id,
            whatsapp_number,
            business_id,
            status,
            owner_whatsapp_number,
            created_at
        FROM customer_numbers
        ORDER BY created_at DESC
        """
    ).fetchall()

    conn.close()

    return [
        {
            "user_id": row[0],
            "whatsapp_number": row[1],
            "business_id": row[2],
            "status": row[3],
            "owner_whatsapp_number": row[4],
            "created_at": row[5],
        }
        for row in rows
    ]


def get_business_by_login_number(phone: str):
    """
    Resolves a business-owner login attempt (see api/auth.py's
    /business-login routes): given the phone number an owner entered,
    finds the *active* business it belongs to. A number matches either
    of two columns - whatsapp_number (the Twilio-connected bot number
    itself) or owner_whatsapp_number (a separate personal number, for
    businesses that want OTPs to go somewhere other than the bot's own
    number - see register_business()'s owner_whatsapp_number param).

    Login doesn't require a separate personal number to be registered:
    a solo owner running the bot from their own everyday WhatsApp
    number just logs in with that same number, and
    owner_whatsapp_number can be left blank at registration. It only
    needs to be set when the bot's number is a WhatsApp Business API
    number that can't itself receive normal WhatsApp app messages (only
    matters once OTP_CHANNEL is "whatsapp" - see config.py - since SMS
    OTPs can reach either kind of number).

    Only 'active' businesses can log in - a newly registered business
    stays inactive until an admin activates it from the Businesses
    page, same gate that controls whether automation runs for it (see
    get_active_businesses()). Returns None if there's no match, which
    the login route treats as "no business found for this number"
    without revealing whether the number belongs to an inactive/unknown
    business.
    """

    conn = get_crm_connection()

    cursor = conn.execute(
        """
        SELECT user_id, business_id, whatsapp_number
        FROM customer_numbers
        WHERE (whatsapp_number = ? OR owner_whatsapp_number = ?)
          AND status = 'active'
        """,
        (phone, phone)
    )

    row = cursor.fetchone()

    conn.close()

    if not row:
        return None

    return {
        "user_id": row[0],
        "business_id": row[1],
        "whatsapp_number": row[2],
    }


def _generate_business_id(conn):
    """
    "business_001", "business_002", ... - matches the naming the one real
    business in production already has (business_001, assigned manually
    before this registry existed). Looks at the highest existing
    business_NNN number rather than just COUNT(*), so a deleted business
    in the middle of the sequence doesn't get its number reused and
    collide with a business_id some other table (automation_rules,
    customer_mapping) might still reference.
    """

    rows = conn.execute(
        "SELECT business_id FROM customer_numbers WHERE business_id LIKE 'business\\_%' ESCAPE '\\'"
    ).fetchall()

    highest = 0

    for row in rows:

        suffix = row[0].replace("business_", "", 1)

        if suffix.isdigit():
            highest = max(highest, int(suffix))

    return f"business_{highest + 1:03d}"


def register_business(
    user_id: str,
    whatsapp_number: str,
    owner_whatsapp_number: str = None
):
    """
    The "add business WhatsApp number" step of the admin activation flow -
    generates a business_id and inserts a new row with status explicitly
    set to 'inactive'. Deliberately not save_customer_number() (which
    INSERT OR REPLACEs and leans on the status column's schema DEFAULT
    'active' - the right behavior for the pre-registry single-tenant
    migration, wrong here: a newly registered business shouldn't start
    running automation before an admin has actually clicked Activate).

    Returns None if user_id is already registered - the caller (see
    api/businesses.py) turns that into a 409 rather than silently
    overwriting an existing business's row.
    """

    conn = get_crm_connection()

    # register_business() still needs an upfront existence check (unlike
    # set_business_status()/delete_business() below) - it has to know
    # *before* generating a business_id whether this is a genuinely new
    # business, since an UPDATE/INSERT-based rowcount check can't tell
    # "already registered" apart from "just inserted".
    existing = conn.execute(
        "SELECT 1 FROM customer_numbers WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    if existing:
        conn.close()
        return None

    business_id = _generate_business_id(conn)

    conn.execute(
        """
        INSERT INTO customer_numbers
        (user_id, whatsapp_number, business_id, status, owner_whatsapp_number)
        VALUES (?, ?, ?, 'inactive', ?)
        """,
        (user_id, whatsapp_number, business_id, owner_whatsapp_number)
    )

    conn.commit()
    conn.close()

    return {
        "user_id": user_id,
        "whatsapp_number": whatsapp_number,
        "business_id": business_id,
        "status": "inactive",
        "owner_whatsapp_number": owner_whatsapp_number,
    }


def set_business_status(user_id: str, status: str):
    """
    Flips a registered business active/inactive - 'active' is what makes
    get_active_businesses() (and therefore automation/runner.py) pick it
    up. Returns False if user_id isn't registered, so the route can 404
    instead of silently no-opping.
    """

    conn = get_crm_connection()

    # One round trip instead of a SELECT-then-UPDATE: cursor.rowcount
    # after the UPDATE already says whether a matching row existed,
    # without a separate existence query first.
    cursor = conn.execute(
        "UPDATE customer_numbers SET status = ? WHERE user_id = ?",
        (status, user_id)
    )

    updated = cursor.rowcount > 0

    conn.commit()
    conn.close()

    return updated


def delete_business(user_id: str):
    """
    Removes a business from the tenant registry only - it does not touch
    that business's automation rules, leads, opportunities, or
    conversations (see automation/database.py, crm/lead_manager.py, etc.),
    which stay in place but become unreachable through the normal
    business_id-scoped routes once this row is gone. Returns False if
    user_id isn't registered.
    """

    conn = get_crm_connection()

    # Same one-round-trip approach as set_business_status() above -
    # cursor.rowcount after the DELETE says whether a row existed.
    cursor = conn.execute(
        "DELETE FROM customer_numbers WHERE user_id = ?",
        (user_id,)
    )

    deleted = cursor.rowcount > 0

    conn.commit()
    conn.close()

    return deleted


def save_customer_number(
    user_id: str,
    whatsapp_number: str,
    business_id: str = None
):

    conn = get_crm_connection()

    conn.execute(
        """
        INSERT INTO customer_numbers
        (
            user_id,
            whatsapp_number,
            business_id
        )
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            whatsapp_number = excluded.whatsapp_number,
            business_id = excluded.business_id
        """,
        (
            user_id,
            whatsapp_number,
            business_id
        )
    )

    conn.commit()
    conn.close()

def get_customer_by_number(
    whatsapp_number: str
):

    conn = get_crm_connection()
    cursor = conn.execute(
        """
        SELECT user_id
        FROM customer_numbers
        WHERE whatsapp_number = ?
        """,
        (whatsapp_number,)
    )

    row = cursor.fetchone()

    conn.close()

    return row[0] if row else None


def get_business_phone_by_user(
    user_id: str
):

    conn = get_crm_connection()
    cursor = conn.execute(
        """
        SELECT whatsapp_number
        FROM customer_numbers
        WHERE user_id = ?
        """,
        (user_id,)
    )

    row = cursor.fetchone()

    conn.close()

    return row[0] if row else None


# --------------------------------------------------
# CUSTOMER -> BUSINESS ROUTING
# --------------------------------------------------

def save_mapping(
    customer_phone: str,
    business_phone: str,
    customer_name: str = None
):

    conn = get_crm_connection()

    # Upsert rather than INSERT OR REPLACE: the latter replaces the whole
    # row, which would wipe out an already-known customer_name (captured
    # from a previous message's WhatsApp ProfileName, or entered manually)
    # on every subsequent incoming message. Keep whichever name is already
    # on file; only fill it in from `customer_name` when nothing is set
    # yet, so manual edits and previously-captured names both stick.
    conn.execute(
        """
        INSERT INTO customer_mapping
            (customer_phone, business_phone, customer_name)
        VALUES (?, ?, ?)
        ON CONFLICT(customer_phone) DO UPDATE SET
            business_phone = excluded.business_phone,
            customer_name = COALESCE(
                customer_mapping.customer_name,
                excluded.customer_name
            )
        """,
        (
            customer_phone,
            business_phone,
            customer_name
        )
    )

    conn.commit()
    conn.close()


def get_owning_business_user_id(customer_phone: str):
    """
    Which business's user_id a given customer_phone belongs to, in one
    query - used by auth.py's enforce_tenant_access_for_customer() to
    check whether a business_owner session is allowed to reach a
    customer's lead/activity/timeline/opportunities data. Replaces what
    used to be two separate calls (get_business_phone_by_customer() then
    get_customer_by_number()) with a single JOIN, since this now runs on
    every one of those customer-detail routes for every business_owner
    request. Returns None for an unknown customer_phone, same as the
    two-call version did.
    """

    conn = get_crm_connection()

    cursor = conn.execute(
        """
        SELECT customer_numbers.user_id
        FROM customer_mapping
        JOIN customer_numbers
            ON customer_numbers.whatsapp_number = customer_mapping.business_phone
        WHERE customer_mapping.customer_phone = ?
        """,
        (customer_phone,)
    )

    row = cursor.fetchone()

    conn.close()

    return row[0] if row else None


def set_customer_name(
    customer_phone: str,
    customer_name: str
):
    """
    Explicit manual override (e.g. from the Customer Profile panel).
    Unlike the auto-capture in save_mapping(), this always overwrites -
    it's a deliberate user action, not a best-effort default.
    """

    conn = get_crm_connection()

    conn.execute(
        """
        UPDATE customer_mapping
        SET customer_name = ?
        WHERE customer_phone = ?
        """,
        (
            customer_name,
            customer_phone
        )
    )

    conn.commit()
    conn.close()


def get_customer_name(
    customer_phone: str
):

    conn = get_crm_connection()

    cursor = conn.execute(
        """
        SELECT customer_name
        FROM customer_mapping
        WHERE customer_phone = ?
        """,
        (customer_phone,)
    )

    row = cursor.fetchone()

    conn.close()

    return row[0] if row else None


def get_business_phone_by_customer(
    customer_phone: str
):

    conn = get_crm_connection()
    cursor = conn.execute(
        """
        SELECT business_phone
        FROM customer_mapping
        WHERE customer_phone = ?
        """,
        (customer_phone,)
    )

    row = cursor.fetchone()

    conn.close()

    return row[0] if row else None


def delete_mapping(
    customer_phone: str
):

    conn = get_crm_connection()
    conn.execute(
        """
        DELETE FROM customer_mapping
        WHERE customer_phone = ?
        """,
        (customer_phone,)
    )

    conn.commit()
    conn.close()

def init_business_settings():

    conn = get_crm_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS business_settings (

            user_id TEXT PRIMARY KEY,

            business_name TEXT,

            welcome_message TEXT,

            ai_instructions TEXT,

            phone TEXT,

            email TEXT,

            website TEXT
        )
    """)

    conn.commit()
    conn.close()

def save_business_settings(
    user_id,
    business_name,
    welcome_message,
    ai_instructions,
    phone=None,
    email=None,
    website=None
):

    conn = get_crm_connection()

    conn.execute(
        """
        INSERT INTO business_settings
        (
            user_id,
            business_name,
            welcome_message,
            ai_instructions,
            phone,
            email,
            website
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            business_name = excluded.business_name,
            welcome_message = excluded.welcome_message,
            ai_instructions = excluded.ai_instructions,
            phone = excluded.phone,
            email = excluded.email,
            website = excluded.website
        """,
        (
            user_id,
            business_name,
            welcome_message,
            ai_instructions,
            phone,
            email,
            website
        )
    )

    conn.commit()
    conn.close()

def get_business_settings(
    user_id
):

    conn = get_crm_connection()

    cursor = conn.execute(
        """
        SELECT
            business_name,
            welcome_message,
            ai_instructions,
            phone,
            email,
            website
        FROM business_settings
        WHERE user_id = ?
        """,
        (user_id,)
    )

    row = cursor.fetchone()

    conn.close()

    if not row:
        return None

    return {
        "business_name": row[0],
        "welcome_message": row[1],
        "ai_instructions": row[2],
        "phone": row[3],
        "email": row[4],
        "website": row[5]
    }

