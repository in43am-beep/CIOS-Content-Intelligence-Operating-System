"""Avatar Studio tests — HeyGen transport is ALWAYS mocked.

NEVER hits the real https://api.heygen.com: every test installs an
httpx.MockTransport on routers.avatar._client whose handler ASSERTS the
request host is api.heygen.com (so a stray real-network call would fail
loudly, not silently pass).

provider_keys.py (Builder B) land ho chuka hai, is liye tests ASLI key-store
ke khilaf chalte hain — Fernet encrypt/decrypt roundtrip real hai (Gate 4).
"""
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

# Real provider_keys (Builder B — V2-DESIGN §0.2 signatures). B ne land kar
# diya hai, is liye mock ki zaroorat nahi; Fernet roundtrip asli hai.
# NOTE: ye file sys.modules me koi fake install NAHI karti — doosri test
# files (test_media.py) isi process me real module use karti hain.
import provider_keys as pk_module  # noqa: E402

import auth as auth_module  # noqa: E402
import routers.avatar as avatar_mod  # noqa: E402
from auth import AuthError  # noqa: E402
from database import get_conn, init_db  # noqa: E402

TEST_KEY = "sk_V2_testkey1234567890"


def build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(auth_module.router)  # signup/login -> real JWT
    app.include_router(avatar_mod.router)

    @app.exception_handler(AuthError)
    async def _auth_err(request, exc):
        return JSONResponse(status_code=401, content={"ok": False, "error": str(exc)})

    return app


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr("config.settings.db_path", str(tmp_path / "t.db"))
    monkeypatch.setattr("config.settings.jwt_secret", "test-jwt-secret-avatar-32bytes!!")
    monkeypatch.setattr("config.settings.auth_required", True)
    init_db()
    avatar_mod.init_avatar_tables()  # temp DB me tables
    pk_module._reset_fernet()  # test secret se Fernet dobara derive ho
    pk_module.ensure_tables()  # temp DB me provider_keys/media_usage
    avatar_mod._client = None  # har test fresh transport lagayega
    return TestClient(build_app(), raise_server_exceptions=False)


def signup(client, email="a@test.com", password="password123"):
    r = client.post("/api/v1/auth/signup", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["data"]["token"]
    return body["data"]  # {"token", "user"} (v2 envelope, §A5)


@pytest.fixture()
def auth_headers(client):
    data = signup(client)
    return {"Authorization": f"Bearer {data['token']}"}, data


# ------------------------------------------------- mock transport ---
def mock_heygen(monkeypatch, handler):
    """Install MockTransport; handler MUST return httpx.Response. Har request
    pe host + X-Api-Key header assert hota hai (real network impossible,
    key kabhi URL me nahi)."""
    def guard(request: httpx.Request):
        assert request.url.host == "api.heygen.com", \
            f"REAL NETWORK BLOCKED: {request.url}"
        key = request.headers.get("x-api-key")
        assert key and len(key) >= 10, "X-Api-Key header missing"
        assert "api_key" not in request.url.path and "api_key" not in str(request.url.query), \
            "key must travel in header, never in URL"
        return handler(request)

    avatar_mod._client = httpx.Client(
        transport=httpx.MockTransport(guard), trust_env=False, timeout=60)


def jresp(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(status, json=payload)


# ------------------------------------------------- 1-3: key routes ---
def test_key_save_verify_first_success(client, auth_headers, monkeypatch):
    headers, data = auth_headers
    uid = data["user"]["id"]
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.path == "/v3/avatars/looks"
        assert request.url.params.get("limit") == "1"
        return jresp(200, {"data": {"looks": []}, "error": None})

    mock_heygen(monkeypatch, handler)
    r = client.post("/api/v1/avatar/key", headers=headers,
                    json={"api_key": TEST_KEY})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"ok": True, "data": {"has_key": True}}
    assert TEST_KEY not in r.text  # key kabhi response me nahi
    assert pk_module.has_provider_key(uid, "heygen")  # store hui
    # Gate 4: DB me sirf Fernet token — plaintext kahin nahi.
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT enc_key FROM provider_keys WHERE user_id=? AND provider='heygen'",
            (uid,)).fetchone()
    finally:
        conn.close()
    assert row and TEST_KEY not in row["enc_key"]
    assert row["enc_key"] != TEST_KEY
    assert pk_module.get_provider_key(uid, "heygen") == TEST_KEY  # decrypt roundtrip
    assert len(seen) == 1


def test_key_save_bad_key_401_not_stored(client, auth_headers, monkeypatch):
    headers, data = auth_headers

    def handler(request):
        return jresp(401, {"data": None, "error": {"message": "invalid api key"}})

    mock_heygen(monkeypatch, handler)
    r = client.post("/api/v1/avatar/key", headers=headers,
                    json={"api_key": "sk_V2_boguskey999"})
    assert r.status_code == 400, r.text
    assert "ghalat" in r.json()["error"]
    assert not pk_module.has_provider_key(data["user"]["id"], "heygen")  # verify-first: store NAHI


def test_key_save_verify_403_not_stored(client, auth_headers, monkeypatch):
    headers, data = auth_headers
    mock_heygen(monkeypatch, lambda req: jresp(403, {"error": "forbidden"}))
    r = client.post("/api/v1/avatar/key", headers=headers,
                    json={"api_key": "sk_V2_boguskey999"})
    assert r.status_code == 400
    assert not pk_module.has_provider_key(data["user"]["id"], "heygen")


def test_key_status_and_delete(client, auth_headers, monkeypatch):
    headers, data = auth_headers
    uid = data["user"]["id"]
    r = client.get("/api/v1/avatar/key", headers=headers)
    assert r.json() == {"ok": True, "data": {"has_key": False}}
    assert TEST_KEY not in r.text

    mock_heygen(monkeypatch, lambda req: jresp(200, {"data": {"looks": []}}))
    client.post("/api/v1/avatar/key", headers=headers, json={"api_key": TEST_KEY})
    r = client.get("/api/v1/avatar/key", headers=headers)
    assert r.json()["data"]["has_key"] is True

    r = client.delete("/api/v1/avatar/key", headers=headers)
    assert r.json() == {"ok": True, "data": {"has_key": False}}
    assert not pk_module.has_provider_key(uid, "heygen")


def test_key_validation_too_short(client, auth_headers):
    headers, _ = auth_headers
    r = client.post("/api/v1/avatar/key", headers=headers, json={"api_key": "short"})
    assert r.status_code == 422


def test_keystore_missing_gives_503_not_crash(client, auth_headers, monkeypatch):
    """provider_keys.py pending (B) -> friendly 503, crash nahi."""
    headers, _ = auth_headers
    monkeypatch.setattr(avatar_mod, "_KEYSTORE_AVAILABLE", False)
    r = client.post("/api/v1/avatar/key", headers=headers,
                    json={"api_key": TEST_KEY})
    assert r.status_code == 503
    assert "tayyar nahi" in r.json()["error"]
    r = client.get("/api/v1/avatar/key", headers=headers)
    assert r.status_code == 503


# ------------------------------------------------- auth on all 11 ---
ALL_ROUTES = [
    ("post", "/api/v1/avatar/key", {"json": {"api_key": TEST_KEY}}),
    ("delete", "/api/v1/avatar/key", {}),
    ("get", "/api/v1/avatar/key", {}),
    ("get", "/api/v1/avatar/avatars", {}),
    ("get", "/api/v1/avatar/voices", {}),
    ("post", "/api/v1/avatar/videos", {"json": {"avatar_id": "a", "script": "s"}}),
    ("get", "/api/v1/avatar/videos/vid1", {}),
    ("get", "/api/v1/avatar/videos", {}),
    ("delete", "/api/v1/avatar/videos/vid1", {}),
    ("post", "/api/v1/avatar/agents", {"json": {"prompt": "p"}}),
    ("get", "/api/v1/avatar/agents/sess1", {}),
]


def test_all_11_routes_require_auth(client):
    assert len(ALL_ROUTES) == 11
    for method, path, kwargs in ALL_ROUTES:
        r = getattr(client, method)(path, **kwargs)
        assert r.status_code == 401, path
        assert r.json()["ok"] is False


# ------------------------------------------------- 4-5: avatars/voices ---
def _with_key(client, headers, monkeypatch):
    mock_heygen(monkeypatch, lambda req: jresp(200, {"data": {"looks": []}}))
    r = client.post("/api/v1/avatar/key", headers=headers,
                    json={"api_key": TEST_KEY})
    assert r.status_code == 200


def test_avatars_list_normalization(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    _with_key(client, headers, monkeypatch)

    def handler(request):
        assert request.url.path == "/v3/avatars/looks"
        return jresp(200, {"data": {
            "looks": [
                {"id": "look_1", "name": "Ayesha", "preview_image_url": "https://img/x.png",
                 "default_voice_id": "v1", "extra": "drop"},
                {"id": "look_2", "name": "Bilal"},
                {"name": "no-id-skip"},
            ],
            "token": "tok123",
        }, "error": None})

    mock_heygen(monkeypatch, handler)
    r = client.get("/api/v1/avatar/avatars?limit=20", headers=headers)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["next_token"] == "tok123"
    assert len(d["looks"]) == 2
    assert d["looks"][0] == {"id": "look_1", "name": "Ayesha",
                             "preview_image_url": "https://img/x.png",
                             "default_voice_id": "v1"}
    assert d["looks"][1]["preview_image_url"] is None


def test_voices_preview_audio_url(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    _with_key(client, headers, monkeypatch)

    def handler(request):
        assert request.url.path == "/v3/voices"
        assert request.url.params.get("type") == "public"
        return jresp(200, {"data": {
            "voices": [
                {"voice_id": "vv1", "name": "Sara", "language": "Urdu",
                 "gender": "female",
                 "preview_audio_url": "https://audio/p1.mp3"},
            ],
        }, "error": None})

    mock_heygen(monkeypatch, handler)
    r = client.get("/api/v1/avatar/voices", headers=headers)
    assert r.status_code == 200, r.text
    vs = r.json()["data"]["voices"]
    assert vs[0]["preview_audio_url"] == "https://audio/p1.mp3"
    assert vs[0]["voice_id"] == "vv1"


def test_requires_key_before_listing(client, auth_headers):
    headers, _ = auth_headers
    r = client.get("/api/v1/avatar/avatars", headers=headers)
    assert r.status_code == 400
    assert "key connect" in r.json()["error"]


# ------------------------------------------------- 6: create video ---
def test_create_video_body_and_cap(client, auth_headers, monkeypatch):
    headers, data = auth_headers
    uid = data["user"]["id"]
    _with_key(client, headers, monkeypatch)
    sent = []

    def handler(request):
        assert request.url.path == "/v3/videos"
        body = json.loads(request.content.decode())
        sent.append(body)
        assert body["type"] == "avatar"  # agents wala "mode" yahan NAHI
        assert "mode" not in body
        assert body["avatar_id"] == "look_1"
        assert body["script"] == "Assalam o alaikum"
        assert body["engine"] == {"type": "avatar_v"}
        assert body["aspect_ratio"] == "9:16"
        assert body["output_format"] == "mp4"
        assert body["caption"] == {"file_format": "srt"}
        assert body["voice_id"] == "vv1"
        assert body["title"] == "Test"
        return jresp(200, {"data": {"video_id": "vid_1", "status": "pending"},
                           "error": None})

    mock_heygen(monkeypatch, handler)
    r = client.post("/api/v1/avatar/videos", headers=headers, json={
        "avatar_id": "look_1", "voice_id": "vv1", "script": "Assalam o alaikum",
        "engine": "avatar_v", "aspect_ratio": "9:16", "title": "Test", "caption": True,
    })
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"video_id": "vid_1", "status": "pending"}
    assert len(sent) == 1


def test_create_video_defaults_omit_optionals(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    _with_key(client, headers, monkeypatch)
    sent = []

    def handler(request):
        body = json.loads(request.content.decode())
        sent.append(body)
        return jresp(200, {"data": {"video_id": "vid_9", "status": "pending"},
                           "error": None})

    mock_heygen(monkeypatch, handler)
    r = client.post("/api/v1/avatar/videos", headers=headers,
                    json={"avatar_id": "look_1", "script": "hello"})
    assert r.status_code == 200
    b = sent[0]
    assert b["engine"] == {"type": "avatar_iv"}  # default
    assert b["aspect_ratio"] == "16:9"  # default
    assert "voice_id" not in b and "title" not in b and "caption" not in b


def test_daily_create_cap_3(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    _with_key(client, headers, monkeypatch)

    def handler(request):
        return jresp(200, {"data": {"video_id": "v", "status": "pending"},
                           "error": None})

    mock_heygen(monkeypatch, handler)
    payload = {"avatar_id": "look_1", "script": "salam"}
    for i in range(3):
        r = client.post("/api/v1/avatar/videos", headers=headers, json=payload)
        assert r.status_code == 200, (i, r.text)
    r = client.post("/api/v1/avatar/videos", headers=headers, json=payload)
    assert r.status_code == 429
    assert "3" in r.json()["error"] and "kal try karo" in r.json()["error"]


def test_cap_counts_agents_too(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    _with_key(client, headers, monkeypatch)

    def handler(request):
        if request.url.path == "/v3/videos":
            return jresp(200, {"data": {"video_id": "v", "status": "pending"}, "error": None})
        return jresp(200, {"data": {"session_id": "s", "status": "pending",
                                    "video_id": None}, "error": None})

    mock_heygen(monkeypatch, handler)
    for _ in range(2):
        assert client.post("/api/v1/avatar/videos", headers=headers,
                           json={"avatar_id": "a", "script": "s"}).status_code == 200
    assert client.post("/api/v1/avatar/agents", headers=headers,
                       json={"prompt": "p"}).status_code == 200
    r = client.post("/api/v1/avatar/agents", headers=headers, json={"prompt": "p2"})
    assert r.status_code == 429  # 3 creates ho gaye (2 video + 1 agent)


def test_create_validation(client, auth_headers):
    headers, _ = auth_headers
    r = client.post("/api/v1/avatar/videos", headers=headers,
                    json={"avatar_id": "a", "script": "x" * 1501})
    assert r.status_code == 422  # script cap
    r = client.post("/api/v1/avatar/videos", headers=headers,
                    json={"script": "ok"})  # avatar_id missing
    assert r.status_code == 422
    r = client.post("/api/v1/avatar/videos", headers=headers,
                    json={"avatar_id": "a", "script": "ok", "engine": "nope"})
    assert r.status_code == 422
    r = client.post("/api/v1/avatar/agents", headers=headers,
                    json={"prompt": "x" * 10001})
    assert r.status_code == 422


# ------------------------------------------------- 7: poll proxy ---
def test_poll_pending_processing_completed(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    _with_key(client, headers, monkeypatch)
    seq = iter(["pending", "processing", "completed"])

    def handler(request):
        assert request.url.path == "/v3/videos/vid_1"
        s = next(seq)
        payload = {"data": {"video_id": "vid_1", "status": s}, "error": None}
        if s == "completed":
            payload["data"]["video_url"] = "https://files.heygen.ai/v.mp4"
            payload["data"]["thumbnail_url"] = "https://files.heygen.ai/t.png"
        return jresp(200, payload)

    mock_heygen(monkeypatch, handler)
    r = client.get("/api/v1/avatar/videos/vid_1", headers=headers)
    assert r.json()["data"]["status"] == "pending"
    r = client.get("/api/v1/avatar/videos/vid_1", headers=headers)
    assert r.json()["data"]["status"] == "processing"
    r = client.get("/api/v1/avatar/videos/vid_1", headers=headers)
    d = r.json()["data"]
    assert d["status"] == "completed"
    assert d["video_url"] == "https://files.heygen.ai/v.mp4"
    assert d["thumbnail_url"] == "https://files.heygen.ai/t.png"


def test_poll_unknown_status_never_completed(client, auth_headers, monkeypatch):
    """waiting / unknown strings -> in-progress bucket ("pending"), kabhi
    complete misreport nahi."""
    headers, _ = auth_headers
    _with_key(client, headers, monkeypatch)
    seq = iter(["waiting", "banana_jaari", "queued", ""])

    def handler(request):
        return jresp(200, {"data": {"video_id": "v", "status": next(seq)},
                           "error": None})

    mock_heygen(monkeypatch, handler)
    for _ in range(4):
        r = client.get("/api/v1/avatar/videos/v", headers=headers)
        assert r.json()["data"]["status"] == "pending"


def test_poll_failed_maps_error(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    _with_key(client, headers, monkeypatch)
    mock_heygen(monkeypatch, lambda req: jresp(
        200, {"data": {"video_id": "v", "status": "failed",
                       "error": "render broke"}, "error": None}))
    r = client.get("/api/v1/avatar/videos/v", headers=headers)
    d = r.json()["data"]
    assert d["status"] == "failed"
    assert d["error"] and "nakami" in d["error"]


def test_poll_upserts_avatar_jobs(client, auth_headers, monkeypatch):
    headers, data = auth_headers
    uid = data["user"]["id"]
    _with_key(client, headers, monkeypatch)
    mock_heygen(monkeypatch, lambda req: jresp(
        200, {"data": {"video_id": "vid_x", "status": "processing"},
              "error": None}))
    client.get("/api/v1/avatar/videos/vid_x", headers=headers)
    from database import get_conn
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM avatar_jobs WHERE video_id='vid_x'").fetchone()
    finally:
        conn.close()
    assert row and row["user_id"] == uid and row["status"] == "processing"


# ------------------------------------------------- 8-9: list/delete ---
def test_history_per_user_namespaced(client, monkeypatch):
    u1 = signup(client, email="u1@t.com")
    u2 = signup(client, email="u2@t.com")
    h1 = {"Authorization": f"Bearer {u1['token']}"}
    h2 = {"Authorization": f"Bearer {u2['token']}"}

    def handler(request):
        if request.url.path == "/v3/avatars/looks":
            return jresp(200, {"data": {"looks": []}})
        if request.url.path == "/v3/videos" and request.method == "POST":
            return jresp(200, {"data": {"video_id": "vid_u1", "status": "pending"},
                               "error": None})
        if request.url.path == "/v3/videos":  # enrichment list
            return jresp(200, {"data": {"videos": []}, "error": None})
        raise AssertionError(request.url.path)

    mock_heygen(monkeypatch, handler)
    client.post("/api/v1/avatar/key", headers=h1, json={"api_key": TEST_KEY})
    client.post("/api/v1/avatar/videos", headers=h1,
                json={"avatar_id": "a", "script": "s"})
    r1 = client.get("/api/v1/avatar/videos", headers=h1)
    assert len(r1.json()["data"]["videos"]) == 1
    assert r1.json()["data"]["videos"][0]["video_id"] == "vid_u1"
    r2 = client.get("/api/v1/avatar/videos", headers=h2)  # bina key bhi 400 nahi?
    # u2 ke paas key nahi -> enrichment skip, lekin apne (khaali) jobs wapas
    assert r2.status_code == 200
    assert r2.json()["data"]["videos"] == []  # B ko A ki rows NAHI


def test_history_live_enrichment_best_effort(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    _with_key(client, headers, monkeypatch)

    def handler(request):
        if request.url.path == "/v3/videos" and request.method == "POST":
            return jresp(200, {"data": {"video_id": "vid_e", "status": "pending"},
                               "error": None})
        if request.url.path == "/v3/videos":  # list enrichment
            return jresp(200, {"data": {"videos": [
                {"video_id": "vid_e", "status": "completed",
                 "video_url": "https://files.heygen.ai/e.mp4",
                 "thumbnail_url": "https://files.heygen.ai/e.png"}]}, "error": None})
        raise AssertionError(request.url.path)

    mock_heygen(monkeypatch, handler)
    client.post("/api/v1/avatar/videos", headers=headers,
                json={"avatar_id": "a", "script": "s"})
    r = client.get("/api/v1/avatar/videos", headers=headers)
    v = r.json()["data"]["videos"][0]
    assert v["status"] == "completed"  # live status overlay
    assert v["video_url"] == "https://files.heygen.ai/e.mp4"


def test_history_enrichment_failure_still_200(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    _with_key(client, headers, monkeypatch)

    def handler(request):
        if request.url.path == "/v3/videos" and request.method == "POST":
            return jresp(200, {"data": {"video_id": "vid_f", "status": "pending"},
                               "error": None})
        return jresp(500, {"error": "boom"})  # enrichment fail

    mock_heygen(monkeypatch, handler)
    client.post("/api/v1/avatar/videos", headers=headers,
                json={"avatar_id": "a", "script": "s"})
    r = client.get("/api/v1/avatar/videos", headers=headers)
    assert r.status_code == 200  # best-effort: DB rows wapas
    assert r.json()["data"]["videos"][0]["video_id"] == "vid_f"


def test_delete_video(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    _with_key(client, headers, monkeypatch)
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return jresp(200, {"data": {"video_id": "vid_d", "status": "pending"},
                               "error": None})
        if request.method == "DELETE":
            assert request.url.path == "/v3/videos/vid_d"
            return jresp(200, {"data": {"success": True}, "error": None})
        raise AssertionError(request.url.path)

    mock_heygen(monkeypatch, handler)
    client.post("/api/v1/avatar/videos", headers=headers,
                json={"avatar_id": "a", "script": "s"})
    r = client.delete("/api/v1/avatar/videos/vid_d", headers=headers)
    assert r.json() == {"ok": True, "data": {"deleted": True}}
    from database import get_conn
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT status FROM avatar_jobs WHERE video_id='vid_d'").fetchone()
    finally:
        conn.close()
    assert row["status"] == "deleted"


# ------------------------------------------------- 10-11: agents ---
def test_agent_create_sends_generate_mode(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    _with_key(client, headers, monkeypatch)
    sent = []

    def handler(request):
        assert request.url.path == "/v3/video-agents"
        body = json.loads(request.content.decode())
        sent.append(body)
        return jresp(200, {"data": {"session_id": "sess_1", "status": "pending",
                                    "video_id": None}, "error": None})

    mock_heygen(monkeypatch, handler)
    r = client.post("/api/v1/avatar/agents", headers=headers,
                    json={"prompt": "Ek host jo chai pe baat kare",
                          "voice_id": "vv1"})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["session_id"] == "sess_1" and d["video_id"] is None
    assert sent[0]["mode"] == "generate"  # fix: chat mode stalls, hamesha generate
    assert sent[0]["prompt"].startswith("Ek host")
    assert sent[0]["voice_id"] == "vv1"
    assert "type" not in sent[0]  # video wala "type:avatar" yahan NAHI


def test_agent_status_until_video(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    _with_key(client, headers, monkeypatch)
    seq = iter([{"status": "in_progress", "video_id": None},
                {"status": "completed", "video_id": "vid_ag"}])

    def handler(request):
        assert request.url.path == "/v3/video-agents/sess_1"
        nxt = next(seq)
        return jresp(200, {"data": {"session_id": "sess_1", **nxt}, "error": None})

    mock_heygen(monkeypatch, handler)
    r = client.get("/api/v1/avatar/agents/sess_1", headers=headers)
    assert r.json()["data"] == {"session_id": "sess_1", "status": "processing",
                                "video_id": None}
    r = client.get("/api/v1/avatar/agents/sess_1", headers=headers)
    d = r.json()["data"]
    assert d["status"] == "completed" and d["video_id"] == "vid_ag"


# ------------------------------------------------- error catalog ---
def test_error_401_bad_key_on_poll(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    _with_key(client, headers, monkeypatch)
    # Key baad me expire: HeyGen 401 -> hamari 401 + key copy
    mock_heygen(monkeypatch, lambda req: jresp(401, {"error": "unauthorized"}))
    r = client.get("/api/v1/avatar/videos/abc", headers=headers)
    assert r.status_code == 401
    assert "dobara key dalo" in r.json()["error"]


def test_error_402_insufficient_credits(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    _with_key(client, headers, monkeypatch)
    mock_heygen(monkeypatch, lambda req: jresp(
        402, {"error": {"message": "Insufficient credits"}}))
    r = client.post("/api/v1/avatar/videos", headers=headers,
                    json={"avatar_id": "a", "script": "s"})
    assert r.status_code == 402
    assert "credits khatam" in r.json()["error"]


def test_error_429_rate_limit(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    _with_key(client, headers, monkeypatch)
    mock_heygen(monkeypatch, lambda req: jresp(429, {"error": "slow down"}))
    r = client.get("/api/v1/avatar/avatars", headers=headers)
    assert r.status_code == 429
    assert "rate limit" in r.json()["error"]


def test_error_moderation_4xx(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    _with_key(client, headers, monkeypatch)
    mock_heygen(monkeypatch, lambda req: jresp(
        400, {"error": {"message": "Content moderated: violates policy"}}))
    r = client.post("/api/v1/avatar/videos", headers=headers,
                    json={"avatar_id": "a", "script": "bad stuff"})
    assert r.status_code == 400
    assert "moderation" in r.json()["error"]


def test_error_invalid_avatar_400(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    _with_key(client, headers, monkeypatch)
    mock_heygen(monkeypatch, lambda req: jresp(
        400, {"error": {"message": "avatar_id not found"}}))
    r = client.post("/api/v1/avatar/videos", headers=headers,
                    json={"avatar_id": "nope", "script": "s"})
    assert r.status_code == 400
    assert "Avatar ya voice ID ghalat" in r.json()["error"]


def test_error_heygen_500_maps_502(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    _with_key(client, headers, monkeypatch)
    mock_heygen(monkeypatch, lambda req: jresp(500, {"error": "internal"}))
    r = client.get("/api/v1/avatar/avatars", headers=headers)
    assert r.status_code == 502
    assert "masla" in r.json()["error"]


def test_error_network_timeout_maps_friendly(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    _with_key(client, headers, monkeypatch)

    def handler(request):
        raise httpx.ConnectError("dns boom")

    mock_heygen(monkeypatch, handler)
    r = client.get("/api/v1/avatar/avatars", headers=headers)
    assert r.status_code == 502
    assert "connect nahi ho raha" in r.json()["error"]


def test_error_malformed_json_maps_friendly(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    _with_key(client, headers, monkeypatch)
    mock_heygen(monkeypatch,
                lambda req: httpx.Response(200, content=b"not json {{{"))
    r = client.get("/api/v1/avatar/avatars", headers=headers)
    assert r.status_code == 502
    assert "samajh na aane wala" in r.json()["error"]


# ------------------------------------------------- Gate 5: HTTP safety ---
PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")


def test_proxy_url_picks_https_first(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://user:pass@proxy.example:3128")
    monkeypatch.setenv("https_proxy", "http://other:1@x.example:8080")
    assert avatar_mod._proxy_url() == "http://user:pass@proxy.example:3128"


def test_proxy_url_skips_garbage(monkeypatch):
    for v in PROXY_VARS:
        monkeypatch.delenv(v, raising=False)  # sandbox ke real proxy vars hatado
    monkeypatch.setenv("https_proxy", "not-a-url")
    monkeypatch.setenv("http_proxy", "ftp://x.example:21")
    assert avatar_mod._proxy_url() is None


def test_http_client_constructs_with_bracketed_no_proxy(monkeypatch):
    # Regression (AGENTS.md): httpx 0.28.1 NO_PROXY me '[::1]' pe crash karta
    # hai — trust_env=False + explicit proxy isko bypass karta hai.
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,::1,[::1],198.19.0.1")
    monkeypatch.setenv("no_proxy", "localhost,[::1]")
    monkeypatch.setenv("HTTPS_PROXY", "http://u:p@hatch-egress-proxy:3128")
    avatar_mod._client = None
    try:
        client = avatar_mod._http()  # must not raise
        assert isinstance(client, httpx.Client)
        assert client.timeout == httpx.Timeout(60)  # har call pe timeout
    finally:
        avatar_mod._client = None
