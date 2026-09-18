"""POST /api/niche — Niche validation scorecard (brain system 02)."""
from fastapi import APIRouter

import ai_client
from brain_loader import system_for
from models import ApiResult, NicheRequest
from routers import finish

router = APIRouter(prefix="/api/niche", tags=["niche"])

SYSTEM = system_for(
    "02-high-demand-low-competition.md",
    extra="Tum niche validation specialist ho (scorecard system). Sakht lekin munsif raho. "
    "OUTPUT: sirf valid JSON: "
    '{"score":0-100,"verdict":"GREEN / YELLOW / RED","strengths":["..."],"risks":["..."],'
    '"first_10_videos":["video 1 ..."]} ',
)


@router.post("", response_model=ApiResult)
def validate(req: NicheRequest):
    user = (
        f"Niche: {req.niche}\n\n"
        "Niche validation scorecard lagao (70+ = GREEN). Strengths, risks, aur pehli 10 videos ki list do."
    )
    result = ai_client.generate(SYSTEM, user, max_tokens=2500)
    return finish("niche", req, result)
