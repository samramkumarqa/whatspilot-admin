"""
Unit tests for rate_limit.py in isolation - see tests/test_auth.py for
the end-to-end behavior of the routes that actually use this (admin
/login, business-owner /business-login and /business-login/verify).
"""

import time

from rate_limit import is_rate_limited, record_attempt, reset, clear_all


def test_not_rate_limited_before_any_attempts():
    assert is_rate_limited("k1", max_attempts=3, window_seconds=60) is False


def test_rate_limited_once_max_attempts_reached():
    for _ in range(3):
        record_attempt("k2")

    assert is_rate_limited("k2", max_attempts=3, window_seconds=60) is True


def test_not_rate_limited_below_max_attempts():
    for _ in range(2):
        record_attempt("k3")

    assert is_rate_limited("k3", max_attempts=3, window_seconds=60) is False


def test_keys_are_independent():
    for _ in range(5):
        record_attempt("k4a")

    assert is_rate_limited("k4a", max_attempts=3, window_seconds=60) is True
    assert is_rate_limited("k4b", max_attempts=3, window_seconds=60) is False


def test_old_attempts_outside_the_window_dont_count():
    # Simulate attempts that happened "long ago" by recording them, then
    # asking with a window shorter than the real elapsed time - the
    # window itself is what's under test, not real wall-clock waiting.
    record_attempt("k5")
    record_attempt("k5")
    record_attempt("k5")

    # A 0-second window means every prior attempt is immediately
    # "outside" the window by the time this check runs.
    assert is_rate_limited("k5", max_attempts=3, window_seconds=0) is False


def test_reset_clears_a_single_key():
    for _ in range(3):
        record_attempt("k6")
    assert is_rate_limited("k6", max_attempts=3, window_seconds=60) is True

    reset("k6")

    assert is_rate_limited("k6", max_attempts=3, window_seconds=60) is False


def test_clear_all_clears_every_key():
    record_attempt("k7a")
    record_attempt("k7b")

    clear_all()

    assert is_rate_limited("k7a", max_attempts=1, window_seconds=60) is False
    assert is_rate_limited("k7b", max_attempts=1, window_seconds=60) is False
