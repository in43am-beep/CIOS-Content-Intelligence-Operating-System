"""JWT auth: signup / login / me + FastAPI dependencies.

Bearer-header auth (no cookies) -> CSRF n/a by design (audit L2).
Passwords hashed with pwdlib[argon2].
"""
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pwdlib import PasswordHash

from config import settings
from database import count_users, create_user, get_user_by_email, get_user_by_id
from models import LoginRequest, SignupRequest

log = logging.getLogger("cios.auth")

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_hasher = PasswordHash.recommended()  # argon2 when argon2-cffi is installed


class AuthError(Exception):
    """-> 401 {ok:false, error} via app handler."""


class ForbiddenError(Exception):
    """-> 403 {ok:false, error} via app handler."""


def _public_user(user: dict) -> dict:
    return {"id": user["id"], "email": user["email"], "is_admin": bool(user["is_admin"])}


def make_token(user: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user["id"]),
        "email": user["email"],
        "is_admin": bool(user["is_admin"]),
        "iat": now,
        "exp": now + timedelta(days=settings.jwt_expire_days),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def _valid_email(email: str) -> bool:
    email = email.strip()
    if "@" not in email or " " in email:
        return False
    local, _, domain = email.partition("@")
    return bool(local) and "." in domain and len(domain) >= 3


@router.post("/signup")
def signup(body: SignupRequest):
    email = body.email.strip().lower()
    if not _valid_email(email):
        return JSONResponse(status_code=400, content={
            "ok": False, "error": "Sahi email likho (misal: naam@example.com)."})
    if len(body.password) < 8:
        return JSONResponse(status_code=400, content={
            "ok": False, "error": "Password kam az kam 8 characters ka ho."})
    if get_user_by_email(email):
        return JSONResponse(status_code=400, content={
            "ok": False, "error": "Ye email pehle se registered hai — login karo."})

    is_admin = count_users() == 0  # first-ever user becomes admin
    try:
        user = create_user(email, _hasher.hash(body.password), is_admin,
                           settings.daily_request_cap)
    except sqlite3.IntegrityError:
        return JSONResponse(status_code=400, content={
            "ok": False, "error": "Ye email pehle se registered hai — login karo."})
    log.info("signup ok email=%s admin=%s", email, is_admin)
    # v2 envelope: {ok, data:{token, user}} (V2-DESIGN.md §A5)
    return {"ok": True, "data": {"token": make_token(user), "user": _public_user(user)}}


@router.post("/login")
def login(body: LoginRequest):
    email = body.email.strip().lower()
    user = get_user_by_email(email)
    if not user or not _hasher.verify(body.password, user["pw_hash"]):
        log.info("login failed email=%s", email)
        return JSONResponse(status_code=401, content={
            "ok": False, "error": "Email ya password ghalat hai."})
    log.info("login ok email=%s", email)
    return {"ok": True, "data": {"token": make_token(user), "user": _public_user(user)}}


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise AuthError("Token expire ho gaya — dobara login karo.")
    except jwt.InvalidTokenError:
        raise AuthError("Ghalat ya kharab token — dobara login karo.")


async def get_current_user(request: Request) -> dict:
    """Auth dependency. Skipped when AUTH_REQUIRED=false (explicit dev opt-out)."""
    if not settings.auth_required:
        return {"id": 0, "email": "dev@local", "is_admin": True}
    authz = request.headers.get("Authorization", "")
    if not authz.lower().startswith("bearer "):
        raise AuthError("Login required — pehle /api/v1/auth/login se token lo.")
    token = authz[7:].strip()
    if not token:
        raise AuthError("Login required — pehle /api/v1/auth/login se token lo.")
    payload = _decode_token(token)
    user = get_user_by_id(int(payload["sub"]))
    if not user:
        raise AuthError("User nahi mila — dobara signup/login karo.")
    return _public_user(user)


async def get_admin_user(current: dict = Depends(get_current_user)) -> dict:
    if not current.get("is_admin"):
        raise ForbiddenError("Sirf admin ye action kar sakta hai.")
    return current


@router.get("/me")
def me(current: dict = Depends(get_current_user)):
    return {"ok": True, "data": {"user": current}}
