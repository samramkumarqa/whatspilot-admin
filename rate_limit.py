"""
Lightweight in-memory rate limiting for the login-adjacent endpoints in
api/auth.py: admin /login, and business-owner /business-login (OTP send)
+ /business-login/verify (OTP check).

Deliberately simple - a single-process in-memory sliding window, since
this app runs as one Render web service process with no Redis/shared
cache. Counts reset on process restart, which is an acceptable
trade-off for this threat model: the goal is raising the cost of naive
scripted brute-force / OTP-spam attempts against a small-business
admin/login panel, not defending against a determined, distributed
attacker (that would need real infrastructure - a WAF, Redis-backed
limiter, etc. - which is out of scope for what this app needs today).

Twilio Verify already rate-limits/expires OTP codes on its own side
(see verify.py) - the /business-login and /business-login/verify limits
here are defense in depth on top of that, primarily to stop a phone
number from being spammed with OTP SMS messages (cost, annoyance) more
than to stop code-guessing (Twilio already handles that).
"""

import threading
import time
from collections import defaultdict, deque

_lock = threading.Lock()
_attempts = defaultdict(deque)


def is_rate_limited(key: str, max_attempts: int, window_seconds: int) -> bool:
    """
    True if `key` has already recorded >= max_attempts within the last
    window_seconds. Callers should check this *before* doing the
    sensitive/expensive work (bcrypt check, Twilio Verify call) and
    reject the request without doing it if this returns True - then
    call record_attempt(key) after an attempt that was actually allowed
    through, whether it succeeds or fails.
    """

    now = time.monotonic()

    with _lock:
        dq = _attempts.get(key)

        if dq is None:
            return False

        while dq and now - dq[0] > window_seconds:
            dq.popleft()

        if not dq:
            del _attempts[key]
            return False

        return len(dq) >= max_attempts


def record_attempt(key: str) -> None:
    with _lock:
        _attempts[key].append(time.monotonic())


def reset(key: str) -> None:
    """Used by tests to reset a key's window between cases."""

    with _lock:
        _attempts.pop(key, None)


def clear_all() -> None:
    """
    Wipes every tracked key. `_attempts` is process-wide (module-level)
    state, so without this, tests in different files that hit the same
    rate-limited route (e.g. POST /login, which every admin-login test
    hits under the TestClient's fixed "testclient" host) would share
    counters across the whole pytest run and could trip the limit purely
    from test order/count, not from anything the test itself is doing.
    See tests/conftest.py's autouse fixture that calls this before every
    test.
    """

    with _lock:
        _attempts.clear()
