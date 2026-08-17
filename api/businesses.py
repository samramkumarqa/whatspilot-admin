from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from crm.customer_mapping import (
    list_businesses,
    register_business,
    set_business_status,
    delete_business,
    get_business,
)
from provisioning.orchestrator import provision_business

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

    # The business's own number, already registered with Twilio as a
    # WhatsApp sender (with its webhook pointed at this business's Render
    # URL) - neither of which this app automates, see PROVISIONING.md.
    # Optional and almost always blank at registration time: leaving it
    # unset means provisioning falls back to the shared Twilio Sandbox
    # number every business has used until now (see
    # provisioning/orchestrator.py's _env_vars_for_business()). Only fill
    # this in for a business that already has its own live number set up
    # end-to-end on the Twilio/Meta side.
    twilio_whatsapp_number: str | None = Field(
        default=None, pattern=PHONE_PATTERN
    )

    # A business's own individually-allocated Groq API key (console.groq.com
    # -> API Keys), used instead of this admin app's shared GROQ_API_KEY once
    # set (see provisioning/orchestrator.py's _env_vars_for_business()).
    # Optional - leaving it blank means the business's AI replies keep
    # running on the shared account, same as every business before this
    # field existed. Length/charset matches Groq's own key format
    # (e.g. "gsk_" followed by alphanumerics); not validated further since
    # a wrong key just fails at Groq's API, not this app's.
    groq_api_key: str | None = Field(
        default=None, min_length=20, max_length=200,
        pattern=r"^[A-Za-z0-9_\-]+$"
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
        request.owner_whatsapp_number,
        request.twilio_whatsapp_number,
        request.groq_api_key
    )

    if result is None:

        raise HTTPException(
            status_code=409,
            detail=f"user_id {request.user_id!r} is already registered."
        )

    # Automatically spins up this business's own GitHub repo + Render
    # deployment (see provisioning/orchestrator.py) - the registration
    # above has already succeeded and committed at this point, so a
    # provisioning failure here is reported back in the response (via
    # the merged-in provisioning_status/provisioning_error fields) but
    # does NOT roll back or fail this request; the business still
    # exists in the registry and can be retried from the Businesses
    # page (see provision_business_route() below).
    provisioning = await run_in_threadpool(
        provision_business, request.user_id, result["business_id"]
    )
    result.update(provisioning)

    return {

        "status": "success",

        "business": result

    }


# --------------------------------------------------------
# Retry Provisioning
# --------------------------------------------------------

@router.post("/business-registry/{user_id}/provision")
async def provision_business_route(user_id: str):

    business = await run_in_threadpool(get_business, user_id)

    if not business:

        raise HTTPException(
            status_code=404,
            detail="Business not found"
        )

    # Retry Setup is meant for a business stuck at 'pending'/'failed' -
    # templates/businesses.html already hides the button once a business
    # is 'live', but that's only a UI convenience, not enforcement. A
    # direct POST here (a stale tab, a second admin, or a double-click
    # racing the first request) on an already-live business would call
    # create_web_service() again and spin up a *second* Render service
    # under the same deterministic name, which Render then rejects -
    # see provisioning/orchestrator.py and PROVISIONING.md's own
    # warning about this. Reject it server-side instead of relying on
    # the button being hidden.
    if business["provisioning_status"] == "live":

        raise HTTPException(
            status_code=409,
            detail=(
                "This business is already live - re-running setup would "
                "risk creating a duplicate Render deployment. See "
                "PROVISIONING.md's \"Giving a business its own WhatsApp "
                "number\" section for how to update an already-live "
                "business safely."
            )
        )

    provisioning = await run_in_threadpool(
        provision_business, user_id, business["business_id"]
    )

    return {

        "status": "success",

        "provisioning": provisioning

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
