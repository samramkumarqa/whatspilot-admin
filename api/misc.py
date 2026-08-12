from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

router = APIRouter()

templates = Jinja2Templates(directory="templates")


@router.get("/")
async def root(request: Request):
    # No dashboard here - admin only owns the business registry. Land
    # straight on it after login instead of a dead landing page.
    return await businesses_page(request)


@router.get("/businesses")
async def businesses_page(request: Request):

    return templates.TemplateResponse(
        request=request,
        name="businesses.html"
    )


@router.get("/health")
async def health_check():

    return {
        "status": "alive"
    }
