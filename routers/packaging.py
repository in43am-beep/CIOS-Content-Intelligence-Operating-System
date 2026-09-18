"""POST /api/packaging — Titles + thumbnail concepts (brain system 10)."""
from fastapi import APIRouter, Depends

import ai_client
from auth import get_current_user
from brain_loader import system_for
from models import ApiResult, PackagingRequest
from routers import finish, wrap_user

router = APIRouter(prefix="/api/v1/packaging", tags=["packaging"])

SYSTEM = system_for(
    "10-thumbnail-making.md",
    "viral-video-brain.md",
    extra="Tum packaging specialist ho: title + thumbnail ek sath sochte ho (curiosity gap, "
    "ek hi idea, 3-second rule). OUTPUT: sirf valid JSON: "
    '{"titles":["...5 titles"],"thumbnails":[{"concept":"...","text_on_thumb":"2-4 alfaz",'
    '"why":"ek line"}],"best_combo":{"title":"...","thumb":"..."}}',
)


@router.post("", response_model=ApiResult)
def pack(req: PackagingRequest, current: dict = Depends(get_current_user)):
    user = wrap_user(
        f"Topic: {req.topic}\nAudience: {req.audience}\n\n"
        "5 click-worthy titles aur 3 thumbnail concepts do (har ek me thumbnail text + wajah). "
        "Aakhir me best title+thumbnail combo chuno."
    )
    result = ai_client.generate(SYSTEM, user, max_tokens=2500, user_id=current["id"])
    return finish("packaging", req, result, user_id=current["id"])
