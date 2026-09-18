"""POST /api/ideas — Idea Generation (brain systems 01 + 02)."""
from fastapi import APIRouter, Depends

import ai_client
from auth import get_current_user
from brain_loader import system_for
from models import ApiResult, IdeasRequest
from routers import finish, wrap_user

router = APIRouter(prefix="/api/v1/ideas", tags=["ideas"])

SYSTEM = system_for(
    "01-idea-generation.md",
    "02-high-demand-low-competition.md",
    extra="OUTPUT: sirf valid JSON: "
    '{"ideas":[{"title":"...","angle":"...","score":0-30,"why":"ek line"}]}. '
    "Score = idea scoring system (30 me se). Behtareen se kamzor tak sort karo.",
)


@router.post("", response_model=ApiResult)
def create_ideas(req: IdeasRequest, current: dict = Depends(get_current_user)):
    user = wrap_user(
        f"Niche: {req.niche}\nAudience: {req.audience}\n"
        f"{req.count} video ideas do — outlier method, angle transfer, comment mining ka istemal karo. "
        "Har idea ke sath score (30 me se) aur ek line ki wajah."
    )
    result = ai_client.generate(SYSTEM, user, max_tokens=2500, user_id=current["id"])
    return finish("ideas", req, result, user_id=current["id"])
