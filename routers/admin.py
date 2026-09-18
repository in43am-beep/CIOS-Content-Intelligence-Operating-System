"""Admin endpoints: user list, per-user usage, quota overrides (admin only)."""
from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from auth import get_admin_user
from database import get_users, set_user_cap, usage_by_day
from models import QuotaOverrideRequest

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/users")
def list_users(admin: dict = Depends(get_admin_user)):
    return {"ok": True, "data": {"users": get_users()}}


@router.get("/usage")
def usage_report(day: str = Query(default_factory=lambda: date.today().isoformat()),
                 admin: dict = Depends(get_admin_user)):
    return {"ok": True, "data": {"day": day, "rows": usage_by_day(day)}}


@router.post("/quota")
def override_quota(body: QuotaOverrideRequest, admin: dict = Depends(get_admin_user)):
    if not set_user_cap(body.user_id, body.cap):
        return JSONResponse(status_code=404, content={"ok": False, "error": "User nahi mila."})
    return {"ok": True, "data": {"user_id": body.user_id, "cap": body.cap}}
