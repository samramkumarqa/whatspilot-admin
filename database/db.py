import os
import threading

import psycopg2
import psycopg2.pool
from psycopg2.extras import DictCursor

# WhatsPilot now runs on Postgres instead of two local SQLite files
# (data/app.db for CRM data, conversations.db for chat history). Both
# logical "databases" live in the same Postgres instance now - table
# names don't collide between them - so get_crm_connection() and
# get_conversation_connection() below both draw from the one pool below.
# They're kept as two separate functions purely so none of the ~22
# downstream modules that import them need to change.
#
# DATABASE_URL should be a standard Postgres connection string, e.g.
# Render's "Internal Database URL" for the production deploy, or a local
# Postgres instance for dev (see README/docs for a docker run one-liner).
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. WhatsPilot requires Postgres - point "
        "this at your Render Postgres instance's Internal Database URL "
        "in production, or a local Postgres instance for dev."
    )


def _timestamp_as_sqlite_style_string(value, cursor):
    """
    psycopg2 normally parses Postgres TIMESTAMP/TIMESTAMPTZ/DATE columns
    into native datetime.datetime/date objects. This entire codebase was
    written against sqlite3, where the same "TIMESTAMP DEFAULT
    CURRENT_TIMESTAMP" columns always came back as plain strings like
    "2026-08-01 11:00:00" - analytics/revenue_stats.py slices them
    (won_at[:7]) for a month key, analytics/customer_health.py and
    analytics/customer_stats.py parse them with strptime/fromisoformat,
    timeline_manager.py sorts and compares them as strings, and
    dashboard.html's JS does msg.created_at.split(" ")[0] to group
    messages by day. Overriding the type caster here (once, globally)
    keeps every one of those call sites working exactly as before,
    instead of hunting down and patching each one individually - and
    protects any similar call site this migration didn't happen to touch.

    Trimmed to 19 chars ("YYYY-MM-DD HH:MM:SS") because Postgres's raw
    text form can include up to 6 fractional-second digits (its
    CURRENT_TIMESTAMP has real microsecond precision, unlike SQLite's) -
    without this, a value like "2026-08-01 11:00:00.482913" would reach
    code that never expected fractional seconds (e.g. strptime with a
    "%Y-%m-%d %H:%M:%S" format, or exact string-equality checks).
    """
    return None if value is None else value[:19]


psycopg2.extensions.register_type(
    psycopg2.extensions.new_type(
        (1082, 1114, 1184),  # date, timestamp, timestamptz
        "SQLITE_STYLE_TIMESTAMP",
        _timestamp_as_sqlite_style_string,
    )
)

_POOL_MIN = 1
_POOL_MAX = 10

_pg_pool = psycopg2.pool.ThreadedConnectionPool(
    _POOL_MIN,
    _POOL_MAX,
    dsn=DATABASE_URL,
    cursor_factory=DictCursor,
)
_pool_lock = threading.Lock()


class _PGCursor:
    """
    Wraps a real psycopg2 cursor so the ~100 existing call sites written
    for sqlite3 keep working unchanged: sqlite3 queries use '?' positional
    placeholders, psycopg2 needs '%s' - this translates on the way in.
    Everything else (fetchone/fetchall/fetchmany/rowcount/description) is
    forwarded straight through to the real cursor.

    Assumes no query string contains a literal '?' character outside of a
    placeholder position (true for every query in this codebase as of the
    Postgres migration audit - none of the CRM/conversation text columns
    are queried with a literal '?' anywhere).

    execute() only forwards params to psycopg2 when there actually are
    some. psycopg2 always treats '%' in the query text as the start of a
    substitution once a vars argument is supplied - even an empty tuple -
    so a query with no placeholders but a literal '%' in it (e.g.
    crm/customer_mapping.py's business_id LIKE 'business\\_%' pattern)
    would raise "IndexError: tuple index out of range" if we always
    passed params through. sqlite3 has no such restriction (it never
    interprets '%'), which is why this didn't matter before.
    """

    __slots__ = ("_cursor",)

    def __init__(self, cursor):
        object.__setattr__(self, "_cursor", cursor)

    def execute(self, sql, params=()):
        pg_sql = sql.replace("?", "%s")
        if params:
            self._cursor.execute(pg_sql, params)
        else:
            self._cursor.execute(pg_sql)
        return self

    def executemany(self, sql, seq_of_params):
        self._cursor.executemany(sql.replace("?", "%s"), seq_of_params)
        return self

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def __iter__(self):
        return iter(self._cursor)


class _PGConnection:
    """
    Wraps a pooled psycopg2 connection so it matches the calling
    convention every module in this codebase already uses:

        conn = get_crm_connection() / get_conversation_connection()
        conn.execute(sql, params)   # sqlite3.Connection.execute() shortcut
        conn.commit()
        conn.close()

    close() returns the connection to the pool instead of really closing
    it, exactly like the old SQLite _PooledConnection this replaces.
    """

    def __init__(self, conn, pool):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_pool", pool)
        object.__setattr__(self, "_closed", False)

    def execute(self, sql, params=()):
        return _PGCursor(self._conn.cursor()).execute(sql, params)

    def executemany(self, sql, seq_of_params):
        return _PGCursor(self._conn.cursor()).executemany(sql, seq_of_params)

    def cursor(self):
        return _PGCursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        if not self._closed:
            object.__setattr__(self, "_closed", True)
            # A connection returned mid-transaction (e.g. after an
            # exception skipped commit()) would poison the pool for the
            # next borrower - roll back defensively before releasing it.
            # Also reset search_path back to the default in case this
            # connection had a test schema set (see _test_schema above) -
            # otherwise the next borrower (a different test, or real
            # request traffic) could inherit a search_path pointing at a
            # schema that's since been dropped.
            try:
                self._conn.rollback()
                self._conn.cursor().execute("SET search_path TO public")
            except Exception:
                pass
            self._pool.putconn(self._conn)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


# Test-only hook (see tests/conftest.py's isolated_db fixture): when set
# to a schema name, every connection checked out below is pointed at that
# throwaway Postgres schema via search_path instead of "public" - the
# Postgres equivalent of the old "fresh SQLite file per test" isolation.
# None (the default) in normal production/dev operation.
_test_schema = None


def get_crm_connection():
    with _pool_lock:
        conn = _pg_pool.getconn()
    if _test_schema:
        conn.cursor().execute(f'SET search_path TO "{_test_schema}", public')
    return _PGConnection(conn, _pg_pool)


def get_conversation_connection():
    with _pool_lock:
        conn = _pg_pool.getconn()
    if _test_schema:
        conn.cursor().execute(f'SET search_path TO "{_test_schema}", public')
    return _PGConnection(conn, _pg_pool)


def execute_crm(query, params=()):
    conn = get_crm_connection()
    cursor = conn.execute(query, params)
    conn.commit()
    conn.close()
    return cursor


def fetchone_crm(query, params=()):
    conn = get_crm_connection()
    row = conn.execute(query, params).fetchone()
    conn.close()
    return row


def fetchall_crm(query, params=()):
    conn = get_crm_connection()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return rows


def execute_conversation(query, params=()):
    conn = get_conversation_connection()
    cursor = conn.execute(query, params)
    conn.commit()
    conn.close()
    return cursor


def fetchone_conversation(query, params=()):
    conn = get_conversation_connection()
    row = conn.execute(query, params).fetchone()
    conn.close()
    return row


def fetchall_conversation(query, params=()):
    conn = get_conversation_connection()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return rows


def commit_and_close(conn):
    conn.commit()
    conn.close()


def close_connection(conn):
    conn.close()
