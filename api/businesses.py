from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from crm.customer_mapping import (
    list_businesses,
    register_business,
    set_business_status,
    delete_business,
)

router = APIRouter(tags=["Businesses"])


# --------------------------------------------------------
# Request Models
# --------------------------------------------------------

PHONE_PATTERN = r"^\+?[0-9]{7,15}$"

# user_id doubles as the app-wide account identifier used throughout every
# other route (see /dashboard/{user_id}, /automation/rules/{user_id}, ...).
# It's often just the WhatsApp number itself (real production data has
# user_id == whatsapp_number), but the registry doesn't require that - the
# one existing test tenant in production ("testB") uses a plain slug
# instead. Kept intentionally permissive (letters, numbers, +, -, _, .)
# rather than phone-shaped, since Phase 3's login mechanism hasn't been
# decided yet and may end up wanting an email address here instead.
class RegisterBusinessRequest(BaseModel):

    user_id: str = Field(
        min_length=1, max_length=50,
        pattern=r"^[a-zA-Z0-9+\-_.]+$"
    )

    whatsapp_number: str = Field(pattern=PHONE_PATTERN)

    # The business owner/admin's personal WhatsApp number, for OTP
    # delivery once Phase 3 (login) exists - deliberately separate from
    # whatsapp_number, which is the Twilio-connected, customer-facing
    # WABA number and generally can't receive messages in a normal
    # WhatsApp client. Optional at registration time since a business can
    # be added and activated before this is known.
    owner_whatsapp_number: str | None = Field(
        default=None, pattern=PHONE_PATTERN
    )


class UpdateBusinessStatusRequest(BaseModel):

    status: Literal["active", "inactive"]


# --------------------------------------------------------
# List Businesses
# --------------------------------------------------------

@router.get("/business-registry")
async def get_businesses():

    return {

        "status": "success",

        "businesses": await run_in_threadpool(list_businesses)

    }


# --------------------------------------------------------
# Register Business
# --------------------------------------------------------

@router.post("/business-registry")
async def create_business(request: RegisterBusinessRequest):

    result = await run_in_threadpool(
        register_business,
        request.user_id,
        request.whatsapp_number,
        request.owner_whatsapp_number
    )

    if result is None:

        raise HTTPException(
            status_code=409,
            detail=f"user_id {request.user_id!r} is already registered."
        )

    return {

        "status": "success",

        "business": result

    }


# --------------------------------------------------------
# Activate / Deactivate Business
# --------------------------------------------------------

@router.put("/business-registry/{user_id}/status")
async def update_business_status(
    user_id: str,
    request: UpdateBusinessStatusRequest
):

    updated = await run_in_threadpool(
        set_business_status, user_id, request.status
    )

    if not updated:

        raise HTTPException(
            status_code=404,
            detail="Business not found"
        )

    return {

        "status": "success"

    }


# --------------------------------------------------------
# Delete Business
# --------------------------------------------------------

@router.delete("/business-registry/{user_id}")
async def remove_business(user_id: str):

    deleted = await run_in_threadpool(delete_business, user_id)

    if not deleted:

        raise HTTPException(
            status_code=404,
            detail="Business not found"
        )

    return {

        "status": "success"

    }
