"""POST /api/niche — Niche validation scorecard (brain system 02)."""
from fastapi import APIRouter, Depends

import ai_client
from auth import get_current_user
from brain_loader import system_for
from models import ApiResult, NicheRequest
from routers import finish, wrap_user

router = APIRouter(prefix="/api/v1/niche", tags=["niche"])

SYSTEM = system_for(
    "02-high-demand-low-competition.md",
    extra="Tum niche validation specialist ho (scorecard system). Sakht lekin munsif raho. "
    "OUTPUT: sirf valid JSON: "
    '{"score":0-100,"verdict":"GREEN / YELLOW / RED","strengths":["..."],"risks":["..."],'
    '"first_10_videos":["video 1 ..."]} ',
)


@router.post("", response_model=ApiResult)
def validate(req: NicheRequest, current: dict = Depends(get_current_user)):
    user = wrap_user(
        f"Niche: {req.niche}\n\n"
        "Niche validation scorecard lagao (70+ = GREEN). Strengths, risks, aur pehli 10 videos ki list do."
    )
    result = ai_client.generate(SYSTEM, user, max_tokens=2500, user_id=current["id"])
    return finish("niche", req, result, user_id=current["id"])
