"""
Admin credential check - the single shared admin account (see
middleware.py's AdminAuthMiddleware and api/auth.py's /login routes).
There's no user table for this - just one username/bcrypt-hash pair in
config.py (ADMIN_USERNAME/ADMIN_PASSWORD_HASH env vars).
"""

import secrets

import bcrypt

from config import ADMIN_USERNAME, ADMIN_PASSWORD_HASH


def verify_admin_login(username: str, password: str) -> bool:
    """
    True only if both the username and password match the configured
    admin account. Both checks always run (rather than short-circuiting
    on a bad username) so a failed login doesn't leak, via response
    timing, whether the username or the password was the wrong part.
    """

    if not ADMIN_USERNAME or not ADMIN_PASSWORD_HASH:
        # Misconfigured deployment (env vars not set) - fail closed
        # rather than letting every login through.
        return False

    username_ok = secrets.compare_digest(
        username.encode("utf-8"),
        ADMIN_USERNAME.encode("utf-8")
    )

    try:
        password_ok = bcrypt.checkpw(
            password.encode("utf-8"),
            ADMIN_PASSWORD_HASH.encode("utf-8")
        )
    except ValueError:
        # Malformed hash in config - fail closed instead of raising.
        password_ok = False

    return username_ok and password_ok
