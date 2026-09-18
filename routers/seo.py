"""POST /api/seo — Video SEO pack (brain system 09)."""
from fastapi import APIRouter

import ai_client
from brain_loader import system_for
from models import ApiResult, SeoRequest
from routers import finish

router = APIRouter(prefix="/api/seo", tags=["seo"])

SYSTEM = system_for(
    "09-video-seo.md",
    extra="Tum YouTube SEO specialist ho. OUTPUT: sirf valid JSON: "
    '{"tags":["...12 tags"],"description":"SEO optimized description, 2-3 paragraphs",'
    '"hashtags":["...5"],"chapters":[{"time":"0:00","label":"..."}]}',
)


@router.post("", response_model=ApiResult)
def seo_pack(req: SeoRequest):
    user = (
        f"Title: {req.title}\nTopic: {req.topic}\n\n"
        "Poora SEO pack do: 12 tags, keyword-rich description, 5 hashtags, aur video chapters."
    )
    result = ai_client.generate(SYSTEM, user, max_tokens=2500)
    return finish("seo", req, result)
