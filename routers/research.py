"""POST /api/research — Outlier / opportunity analysis (packaging science)."""
from fastapi import APIRouter

import ai_client
from brain_loader import system_for
from models import ApiResult, ResearchRequest
from routers import finish

router = APIRouter(prefix="/api/research", tags=["research"])

SYSTEM = system_for(
    "viral-video-brain.md",
    extra="Tum packaging/outlier science ke specialist ho (brain sections 14: PACKAGING SCIENCE). "
    "Outlier videos me woh pattern dhoondo jo channel average se zyada perform kare. "
    "OUTPUT: sirf valid JSON: "
    '{"patterns":[{"pattern":"...","evidence":"..."}],"angles":[{"angle":"...","title_draft":"..."}],'
    '"verdict":"2-3 lines me niche opportunity ka khulasa"}',
)


@router.post("", response_model=ApiResult)
def analyze(req: ResearchRequest):
    user = (
        f"Niche: {req.niche}\n"
        f"Sample videos/data (agar di gayi ho):\n{req.examples or '(koi sample nahi — general niche analysis karo)'}\n\n"
        "Outlier patterns nikalo, 5 transferable angles do, aur verdict likho: is niche me opportunity hai ya nahi, kyun."
    )
    result = ai_client.generate(SYSTEM, user, max_tokens=2500)
    return finish("research", req, result)
