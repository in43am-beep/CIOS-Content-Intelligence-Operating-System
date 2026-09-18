"""POST /api/scripts — Script writer (brain system 03 + scripting science)."""
from fastapi import APIRouter, Depends

import ai_client
from auth import get_current_user
from brain_loader import system_for
from models import ApiResult, ScriptRequest
from routers import finish, wrap_user

router = APIRouter(prefix="/api/v1/scripts", tags=["scripts"])

SYSTEM = system_for(
    "03-script-writing.md",
    "viral-video-brain.md",
    extra="Tum scripting science ke specialist ho (hooks, pacing, WPM, retention). "
    "OUTPUT: sirf valid JSON: "
    '{"hook":"pehle 5 sec ki line","script":"poora script, paragraphs me",'
    '"beats":["beat 1 ..."],"cta":"call to action line","est_wpm":130}',
)


@router.post("", response_model=ApiResult)
def write_script(req: ScriptRequest, current: dict = Depends(get_current_user)):
    user = wrap_user(
        f"Topic: {req.topic}\nDuration: {req.duration_sec} seconds\nAudience: {req.audience}\n\n"
        "Poora script likho: strong hook (pehle 5 sec), But-Therefore story flow, "
        "retention beats, aur end me CTA. Audience ki raftaar ke mutabiq WPM rakho."
    )
    result = ai_client.generate(SYSTEM, user, max_tokens=3000, user_id=current["id"])
    return finish("scripts", req, result, user_id=current["id"])
