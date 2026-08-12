"""
Tests for the Phase 2 admin business registry - crm/customer_mapping.py's
register_business()/set_business_status()/delete_business()/list_businesses()
and the api/businesses.py routes built on top of them (see
templates/businesses.html for the admin UI).
"""

import asyncio

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from crm.customer_mapping import (
    register_business,
    set_business_status,
    delete_business,
    list_businesses,
    get_active_businesses,
    get_business_id,
    get_business_by_login_number,
    get_owning_business_user_id,
    save_mapping,
)
from api.businesses import (
    get_businesses,
    create_business,
    update_business_status,
    remove_business,
    RegisterBusinessRequest,
    UpdateBusinessStatusRequest,
)


# ---------------------------------------------------------------------
# crm/customer_mapping.py - registry functions
# ---------------------------------------------------------------------

def test_register_business_starts_inactive(isolated_db):
    result = register_business("u1", "+14155550000")

    assert result["status"] == "inactive"
    assert result["business_id"] == "business_001"


def test_register_business_auto_increments_business_id(isolated_db):
    register_business("u1", "+14155550000")
    result_2 = register_business("u2", "+14155550001")

    assert result_2["business_id"] == "business_002"


def test_register_business_rejects_duplicate_user_id(isolated_db):
    register_business("u1", "+14155550000")

    result = register_business("u1", "+14155559999")

    assert result is None


def test_newly_registered_business_excluded_from_active_businesses(isolated_db):
    register_business("u1", "+14155550000")

    assert get_active_businesses() == []


def test_activating_business_includes_it_in_active_businesses(isolated_db):
    register_business("u1", "+14155550000")

    assert set_business_status("u1", "active") is True

    active = get_active_businesses()

    assert len(active) == 1
    assert active[0]["user_id"] == "u1"
    assert active[0]["business_id"] == "business_001"


def test_deactivating_business_removes_it_from_active_businesses(isolated_db):
    register_business("u1", "+14155550000")
    set_business_status("u1", "active")

    set_business_status("u1", "inactive")

    assert get_active_businesses() == []


def test_set_business_status_returns_false_for_unknown_user_id(isolated_db):
    assert set_business_status("ghost", "active") is False


def test_delete_business_removes_it_from_registry(isolated_db):
    register_business("u1", "+14155550000")

    assert delete_business("u1") is True
    assert get_business_id("u1") is None
    assert list_businesses() == []


def test_delete_business_returns_false_for_unknown_user_id(isolated_db):
    assert delete_business("ghost") is False


def test_list_businesses_includes_inactive_and_active(isolated_db):
    register_business("u1", "+14155550000")
    register_business("u2", "+14155550001")
    set_business_status("u2", "active")

    businesses = list_businesses()

    assert len(businesses) == 2
    statuses = {b["user_id"]: b["status"] for b in businesses}
    assert statuses == {"u1": "inactive", "u2": "active"}


# ---------------------------------------------------------------------
# api/businesses.py - routes
# ---------------------------------------------------------------------

def test_create_business_route_success(isolated_db):
    result = asyncio.run(create_business(
        RegisterBusinessRequest(
            user_id="u1",
            whatsapp_number="+14155550000",
            owner_whatsapp_number="+919876543210"
        )
    ))

    assert result["status"] == "success"
    assert result["business"]["business_id"] == "business_001"
    assert result["business"]["owner_whatsapp_number"] == "+919876543210"


def test_create_business_route_rejects_duplicate_with_409(isolated_db):
    asyncio.run(create_business(
        RegisterBusinessRequest(user_id="u1", whatsapp_number="+14155550000")
    ))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(create_business(
            RegisterBusinessRequest(user_id="u1", whatsapp_number="+14155559999")
        ))

    assert exc_info.value.status_code == 409


def test_update_business_status_route_404s_for_unknown_user(isolated_db):
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(update_business_status(
            "ghost", UpdateBusinessStatusRequest(status="active")
        ))

    assert exc_info.value.status_code == 404


def test_update_business_status_route_activates(isolated_db):
    asyncio.run(create_business(
        RegisterBusinessRequest(user_id="u1", whatsapp_number="+14155550000")
    ))

    result = asyncio.run(update_business_status(
        "u1", UpdateBusinessStatusRequest(status="active")
    ))

    assert result["status"] == "success"
    assert get_active_businesses()[0]["user_id"] == "u1"


def test_remove_business_route_404s_for_unknown_user(isolated_db):
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(remove_business("ghost"))

    assert exc_info.value.status_code == 404


def test_remove_business_route_deletes(isolated_db):
    asyncio.run(create_business(
        RegisterBusinessRequest(user_id="u1", whatsapp_number="+14155550000")
    ))

    result = asyncio.run(remove_business("u1"))

    assert result["status"] == "success"
    assert list_businesses() == []


def test_get_businesses_route_lists_all(isolated_db):
    asyncio.run(create_business(
        RegisterBusinessRequest(user_id="u1", whatsapp_number="+14155550000")
    ))
    asyncio.run(create_business(
        RegisterBusinessRequest(user_id="u2", whatsapp_number="+14155550001")
    ))

    result = asyncio.run(get_businesses())

    assert result["status"] == "success"
    assert len(result["businesses"]) == 2


# ---------------------------------------------------------------------
# RegisterBusinessRequest / UpdateBusinessStatusRequest validation
# ---------------------------------------------------------------------

def test_register_request_rejects_invalid_whatsapp_number():
    with pytest.raises(ValidationError):
        RegisterBusinessRequest(user_id="u1", whatsapp_number="not-a-number")


def test_register_request_rejects_invalid_user_id_chars():
    with pytest.raises(ValidationError):
        RegisterBusinessRequest(user_id="u1<script>", whatsapp_number="+14155550000")


def test_register_request_rejects_empty_user_id():
    with pytest.raises(ValidationError):
        RegisterBusinessRequest(user_id="", whatsapp_number="+14155550000")


def test_register_request_allows_missing_owner_number():
    request = RegisterBusinessRequest(user_id="u1", whatsapp_number="+14155550000")
    assert request.owner_whatsapp_number is None


def test_register_request_rejects_invalid_owner_number():
    with pytest.raises(ValidationError):
        RegisterBusinessRequest(
            user_id="u1",
            whatsapp_number="+14155550000",
            owner_whatsapp_number="abc"
        )


def test_update_status_request_rejects_invalid_literal():
    with pytest.raises(ValidationError):
        UpdateBusinessStatusRequest(status="pending")


# ---------------------------------------------------------------------
# get_business_by_login_number() - business-owner login can use either
# the bot's own WhatsApp number or a separate personal number (see
# api/auth.py's /business-login routes). A business isn't required to
# set owner_whatsapp_number at all - it's only needed when the bot's
# number can't itself receive login codes (e.g. a WhatsApp Business API
# number once WhatsApp-channel OTPs are in use).
# ---------------------------------------------------------------------

def test_login_number_matches_business_whatsapp_number_with_no_owner_number_set(isolated_db):
    register_business("biz1", "+14155550001")
    set_business_status("biz1", "active")

    result = get_business_by_login_number("+14155550001")

    assert result is not None
    assert result["user_id"] == "biz1"


def test_login_number_matches_owner_number_when_set(isolated_db):
    register_business(
        "biz1", "+14155550001", owner_whatsapp_number="+919876500000"
    )
    set_business_status("biz1", "active")

    result = get_business_by_login_number("+919876500000")

    assert result is not None
    assert result["user_id"] == "biz1"


def test_login_number_matches_business_number_even_when_owner_number_also_set(isolated_db):
    # Both numbers should work - registering a personal number is an
    # addition, not a replacement for logging in with the bot's own
    # number.
    register_business(
        "biz1", "+14155550001", owner_whatsapp_number="+919876500000"
    )
    set_business_status("biz1", "active")

    result = get_business_by_login_number("+14155550001")

    assert result is not None
    assert result["user_id"] == "biz1"


def test_login_number_rejects_inactive_business(isolated_db):
    register_business("biz1", "+14155550001")
    # Left at the default 'inactive' status from registration.

    assert get_business_by_login_number("+14155550001") is None


def test_login_number_rejects_unknown_number(isolated_db):
    register_business("biz1", "+14155550001")
    set_business_status("biz1", "active")

    assert get_business_by_login_number("+10000000000") is None


# ---------------------------------------------------------------------
# get_owning_business_user_id() - the single-query replacement for the
# old get_business_phone_by_customer()+get_customer_by_number() pair,
# used by auth.py's enforce_tenant_access_for_customer() on every
# customer-detail route a business_owner session hits.
# ---------------------------------------------------------------------

def test_owning_business_user_id_resolves_correctly(isolated_db):
    register_business("biz1", "+14155550001")
    save_mapping("+919900000001", "+14155550001")

    assert get_owning_business_user_id("+919900000001") == "biz1"


def test_owning_business_user_id_none_for_unknown_customer(isolated_db):
    assert get_owning_business_user_id("+919900000099") is None


def test_owning_business_user_id_distinguishes_businesses(isolated_db):
    register_business("biz1", "+14155550001")
    register_business("biz2", "+14155550002")
    save_mapping("+919900000001", "+14155550001")
    save_mapping("+919900000002", "+14155550002")

    assert get_owning_business_user_id("+919900000001") == "biz1"
    assert get_owning_business_user_id("+919900000002") == "biz2"
