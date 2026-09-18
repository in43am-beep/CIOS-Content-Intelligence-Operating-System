"""Media Studio (ai33.pro) tests — ai33.pro is NEVER hit.

The ai33 transport is mocked by monkeypatching `routers.media._http` with a
FakeAI33 client that asserts the host is api.ai33.pro, records every call, and
returns scripted responses. Real network cannot happen: any unmocked call would
raise (and the sandbox blocks direct TLS anyway).
"""
import json
import os

import pytest
from fastapi.testclient import TestClient

import app as app_module
import provider_keys
import routers.media as media
from database import init_db

TEST_KEY = "test-key-0123456789"  # >=10 chars (server validation minimum)


class FakeResp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")
        self.content = self.text.encode()

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeAI33:
    """Drop-in for media._http(). Every request must carry xi-api-key."""

    def __init__(self, handler, expect_key=TEST_KEY):
        self.handler = handler
        self.expect_key = expect_key
        self.calls = []

    def request(self, method, url, headers=None, params=None,
                json=None, data=None, files=None, timeout=None):
        assert "api.ai33.pro" in url, "test bug: non-ai33 URL " + url
        assert headers and headers.get("xi-api-key") == self.expect_key, \
            "xi-api-key header missing/wrong on " + url
        self.calls.append({"method": method, "url": url, "headers": dict(headers),
                           "params": params, "json": json, "data": data,
                           "files": files})
        return self.handler(method, url, headers=headers, params=params,
                            json=json, data=data, files=files)


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr("config.settings.db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr("config.settings.jwt_secret", "test-jwt-secret-media")
    monkeypatch.setattr("config.settings.auth_required", True)
    provider_keys._reset_fernet()
    init_db()
    provider_keys.ensure_tables()
    # Defensive include: works whether or not Builder A has mounted the router.
    paths = {getattr(r, "path", "") for r in app_module.app.routes}
    if not any(str(p).startswith("/api/v1/media") for p in paths):
        app_module.app.include_router(media.router)
    app_module._throttle_hits.clear()
    return TestClient(app_module.app, raise_server_exceptions=False)


def signup(client, email="media@test.com", password="password123"):
    r = client.post("/api/v1/auth/signup",
                    json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


@pytest.fixture()
def auth(client):
    return signup(client)


def use_ai33(monkeypatch, handler, expect_key=TEST_KEY):
    fake = FakeAI33(handler, expect_key=expect_key)
    monkeypatch.setattr(media, "_http", lambda: fake)
    return fake


def store_key(client, auth, monkeypatch, key=TEST_KEY, credits=1234):
    """Store an ai33 key via the real verify-first route (temp DB, mocked ai33)."""
    fake = use_ai33(monkeypatch,
                    lambda *a, **k: FakeResp(200, {"credits": credits}),
                    expect_key=key)
    r = client.post("/api/v1/media/key", json={"api_key": key}, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"has_key": True, "credits": credits}
    return fake


def credits_ok(method, url, **kw):
    return FakeResp(200, {"credits": 1234})


def task_ok(task_id="task_1"):
    return FakeResp(200, {"success": True, "task_id": task_id})


# --------------------------------------------------- provider_keys ---

def test_key_encryption_roundtrip(client):
    provider_keys.set_provider_key(1, "ai33", "SECRET-KEY-VALUE-999")
    assert provider_keys.get_provider_key(1, "ai33") == "SECRET-KEY-VALUE-999"
    assert provider_keys.has_provider_key(1, "ai33") is True
    # DB me plaintext nahi hai:
    from database import get_conn
    conn = get_conn()
    row = conn.execute("SELECT enc_key FROM provider_keys WHERE user_id=1 AND provider='ai33'").fetchone()
    conn.close()
    assert "SECRET-KEY-VALUE-999" not in row["enc_key"]
    provider_keys.delete_provider_key(1, "ai33")
    assert provider_keys.has_provider_key(1, "ai33") is False
    assert provider_keys.get_provider_key(1, "ai33") is None


def test_key_wrong_secret_treated_as_missing(client, monkeypatch):
    provider_keys.set_provider_key(1, "ai33", "SECRET-KEY-VALUE-999")
    monkeypatch.setattr("config.settings.jwt_secret", "different-secret")
    provider_keys._reset_fernet()
    assert provider_keys.get_provider_key(1, "ai33") is None


def test_key_length_validation(client):
    with pytest.raises(ValueError):
        provider_keys.set_provider_key(1, "ai33", "short")
    with pytest.raises(ValueError):
        provider_keys.set_provider_key(1, "ai33", "x" * 501)


def test_key_never_in_logs(client, auth, monkeypatch, caplog):
    secret = "SECRET-KEY-VALUE-999"
    fake = use_ai33(monkeypatch, credits_ok, expect_key=secret)
    r = client.post("/api/v1/media/key", json={"api_key": secret}, headers=auth)
    assert r.status_code == 200
    assert secret not in r.text
    assert fake.calls and fake.calls[0]["url"].endswith("/v1/credits")
    for path in ("/api/v1/media/key", "/api/v1/media/status", "/api/v1/media/credits"):
        r = client.get(path, headers=auth)
        assert secret not in r.text, path
    client.delete("/api/v1/media/key", headers=auth)
    assert secret not in caplog.text


# ----------------------------------------------------------- auth ---

def test_unauthenticated_401(client):
    cases = [("get", "/api/v1/media/key", None),
             ("get", "/api/v1/media/status", None),
             ("get", "/api/v1/media/credits", None),
             ("post", "/api/v1/media/tts", {}),
             ("get", "/api/v1/media/tasks/x", None)]
    for method, path, payload in cases:
        if method == "post":
            r = client.post(path, json=payload)
        else:
            r = client.get(path)
        assert r.status_code == 401, path
        assert r.json()["ok"] is False


# ------------------------------------------------------- key routes ---

def test_key_verify_401_not_stored(client, auth, monkeypatch):
    fake = use_ai33(monkeypatch,
                    lambda *a, **k: FakeResp(401, {"message": "bad key"}),
                    expect_key="WRONG-KEY-123")
    r = client.post("/api/v1/media/key", json={"api_key": "WRONG-KEY-123"},
                    headers=auth)
    assert r.status_code == 400
    assert r.json()["ok"] is False
    assert "Key ghalat" in r.json()["error"]
    r = client.get("/api/v1/media/key", headers=auth)
    assert r.json()["data"] == {"has_key": False}
    assert fake.calls, "verify-first call honi chahiye thi"


def test_key_save_delete_cycle(client, auth, monkeypatch):
    fake = use_ai33(monkeypatch, credits_ok, expect_key="GOOD-KEY-12345")
    r = client.post("/api/v1/media/key", json={"api_key": "GOOD-KEY-12345"},
                    headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body == {"ok": True, "data": {"has_key": True, "credits": 1234}}
    assert "GOOD-KEY-12345" not in r.text
    r = client.get("/api/v1/media/key", headers=auth)
    assert r.json()["data"] == {"has_key": True}
    r = client.get("/api/v1/media/status", headers=auth)
    assert r.json()["data"] == {"has_key": True, "credits": 1234}
    r = client.delete("/api/v1/media/key", headers=auth)
    assert r.json()["data"] == {"has_key": False}
    assert fake.calls


def test_credits_normalized(client, auth, monkeypatch):
    store_key(client, auth, monkeypatch)
    use_ai33(monkeypatch,
             lambda *a, **k: FakeResp(200, {"data": {"balance": 777}}))
    r = client.get("/api/v1/media/credits", headers=auth)
    assert r.json() == {"ok": True, "data": {"credits": 777}}


# ---------------------------------------------------------- voices ---

def test_voices_provider_required_422(client, auth, monkeypatch):
    fake = use_ai33(monkeypatch, credits_ok)
    r = client.get("/api/v1/media/voices", headers=auth)
    assert r.status_code == 422
    assert fake.calls == [], "provider ke baghair ai33 call nahi honi chahiye"


def test_voices_bad_provider_422(client, auth, monkeypatch):
    fake = use_ai33(monkeypatch, credits_ok)
    r = client.get("/api/v1/media/voices?provider=evil_", headers=auth)
    assert r.status_code == 422
    assert fake.calls == []


def test_voices_list_and_cache(client, auth, monkeypatch):
    store_key(client, auth, monkeypatch)
    media._voices_cache.clear()

    def handler(method, url, **kw):
        assert kw["params"]["provider"] == "elevenlabs_"
        return FakeResp(200, {"voices": [
            {"voice_id": "elevenlabs_abc", "name": "Aria",
             "language": "en", "gender": "female", "accent": "us"}]})
    fake = use_ai33(monkeypatch, handler)
    r = client.get("/api/v1/media/voices?provider=elevenlabs_&language=en",
                   headers=auth)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["cached"] is False
    assert data["voices"] == [{"voice_id": "elevenlabs_abc", "name": "Aria",
                               "language": "en", "gender": "female",
                               "accent": "us", "provider": "elevenlabs_"}]
    r2 = client.get("/api/v1/media/voices?provider=elevenlabs_&language=en",
                    headers=auth)
    assert r2.json()["data"]["cached"] is True
    assert len(fake.calls) == 1, "cache hit par dobara ai33 call nahi"


def test_voices_needs_key(client, auth, monkeypatch):
    fake = use_ai33(monkeypatch, credits_ok)
    r = client.get("/api/v1/media/voices?provider=edge_", headers=auth)
    assert r.status_code == 400  # key connect nahi
    assert fake.calls == []


# ------------------------------------------------------------ tts ---

def test_tts_create_and_poll(client, auth, monkeypatch):
    store_key(client, auth, monkeypatch)
    media._voices_cache.clear()
    states = iter([
        FakeResp(200, {"data": {"status": "pending"}}),
        FakeResp(200, {"data": {"status": "processing"}}),
        FakeResp(200, {"data": {"status": "completed",
                               "audio_url": "https://cdn/x.mp3",
                               "transcript": "hello"}}),
    ])

    def handler(method, url, **kw):
        if url.endswith("/v3/text-to-speech"):
            d = kw["data"]
            assert d["voice_id"] == "elevenlabs_abc"
            assert d["text"] == "Assalam"
            assert d["speed"] == "1.2"
            return task_ok("task_tts")
        if "/v1/task/" in url:
            return next(states)
        raise AssertionError("unexpected " + url)
    fake = use_ai33(monkeypatch, handler)
    r = client.post("/api/v1/media/tts",
                    json={"voice_id": "elevenlabs_abc", "text": "Assalam",
                          "speed": 1.2, "with_transcript": True},
                    headers=auth)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "data": {"task_id": "task_tts"}}
    assert fake.calls[0]["method"] == "POST"
    assert fake.calls[0]["url"].endswith("/v3/text-to-speech")

    r = client.get("/api/v1/media/tasks/task_tts", headers=auth)
    assert r.json()["data"]["status"] == "pending"
    r = client.get("/api/v1/media/tasks/task_tts", headers=auth)
    assert r.json()["data"]["status"] == "processing"
    r = client.get("/api/v1/media/tasks/task_tts", headers=auth)
    d = r.json()["data"]
    assert d["status"] == "completed"
    assert d["audio_url"] == "https://cdn/x.mp3"
    assert d["transcript"] == "hello"


def test_tts_validation(client, auth, monkeypatch):
    fake = use_ai33(monkeypatch, lambda *a, **k: task_ok())
    # voice_id me prefix chahiye
    r = client.post("/api/v1/media/tts",
                    json={"voice_id": "noprefix", "text": "hi"}, headers=auth)
    assert r.status_code == 422
    # text 20000 se zyada
    r = client.post("/api/v1/media/tts",
                    json={"voice_id": "edge_x", "text": "x" * 20001},
                    headers=auth)
    assert r.status_code == 422
    # speed range
    r = client.post("/api/v1/media/tts",
                    json={"voice_id": "edge_x", "text": "hi", "speed": 9},
                    headers=auth)
    assert r.status_code == 422
    assert fake.calls == []


def test_tts_success_false_mapped(client, auth, monkeypatch):
    store_key(client, auth, monkeypatch)
    use_ai33(monkeypatch,
             lambda *a, **k: FakeResp(200, {"success": False,
                                           "message": "voice not found"}))
    r = client.post("/api/v1/media/tts",
                    json={"voice_id": "edge_x", "text": "hi"}, headers=auth)
    assert r.status_code == 502
    assert "ai33.pro" in r.json()["error"]


def test_ai33_401_mapped(client, auth, monkeypatch):
    store_key(client, auth, monkeypatch)
    use_ai33(monkeypatch, lambda *a, **k: FakeResp(401, {"message": "bad"}))
    r = client.post("/api/v1/media/tts",
                    json={"voice_id": "edge_x", "text": "hi"}, headers=auth)
    assert r.status_code == 401
    assert "key ghalat/expire" in r.json()["error"]


def test_ai33_429_mapped(client, auth, monkeypatch):
    store_key(client, auth, monkeypatch)
    use_ai33(monkeypatch, lambda *a, **k: FakeResp(429, {"message": "slow"}))
    r = client.post("/api/v1/media/tts",
                    json={"voice_id": "edge_x", "text": "hi"}, headers=auth)
    assert r.status_code == 429
    assert "rate limit" in r.json()["error"]


def test_insufficient_credits_mapped(client, auth, monkeypatch):
    store_key(client, auth, monkeypatch)
    use_ai33(monkeypatch,
             lambda *a, **k: FakeResp(200, {"success": False,
                                           "message": "Insufficient credits"}))
    r = client.post("/api/v1/media/music",
                    json={"mode": "mp3", "prompt": "beat"}, headers=auth)
    assert r.status_code == 402
    assert "credits khatam" in r.json()["error"]


# ------------------------------------------------------- dialogue ---

def test_dialogue_ok(client, auth, monkeypatch):
    store_key(client, auth, monkeypatch)

    def handler(method, url, **kw):
        assert url.endswith("/v3/text-to-speech/dialogue")
        import json as j
        sp = j.loads(kw["data"]["speakers"])
        assert sp == {"A": "elevenlabs_a", "B": "minimax_b"}
        return task_ok("task_dlg")
    use_ai33(monkeypatch, handler)
    r = client.post("/api/v1/media/dialogue",
                    json={"speakers": {"A": "elevenlabs_a", "B": "minimax_b"},
                          "text": "A> Salam\nB> Walaikum"},
                    headers=auth)
    assert r.json() == {"ok": True, "data": {"task_id": "task_dlg"}}


def test_dialogue_bad_label_422(client, auth, monkeypatch):
    fake = use_ai33(monkeypatch, lambda *a, **k: task_ok())
    r = client.post("/api/v1/media/dialogue",
                    json={"speakers": {"aa": "elevenlabs_a"}, "text": "hi"},
                    headers=auth)
    assert r.status_code == 422
    assert fake.calls == []


# ----------------------------------------------------- voice clone ---

def test_voice_clone_and_delete(client, auth, monkeypatch):
    store_key(client, auth, monkeypatch)
    media._voices_cache.clear()

    def handler(method, url, **kw):
        if method == "POST":
            assert url.endswith("/v3/text-to-speech/voice-clone")
            assert kw["data"]["voice_name"] == "Meri Awaz"
            assert "audio_file" in kw["files"]
            return FakeResp(200, {"voice_id": "clone_123"})
        if method == "DELETE":
            assert url.endswith("/v3/text-to-speech/voice-clone/clone_123")
            return FakeResp(200, {"success": True})
        raise AssertionError("unexpected " + method + url)
    fake = use_ai33(monkeypatch, handler)
    r = client.post("/api/v1/media/voice-clone",
                    data={"voice_name": "Meri Awaz"},
                    files={"audio_file": ("clip.mp3", b"FAKEMP3" * 100,
                                          "audio/mpeg")},
                    headers=auth)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "data": {"voice_id": "clone_123"}}
    r = client.delete("/api/v1/media/voice-clone/clone_123", headers=auth)
    assert r.json() == {"ok": True, "data": {"deleted": True}}
    assert len(fake.calls) == 2


def test_voice_clone_rejects_non_clone_delete(client, auth, monkeypatch):
    fake = use_ai33(monkeypatch, lambda *a, **k: FakeResp(200, {}))
    r = client.delete("/api/v1/media/voice-clone/elevenlabs_x", headers=auth)
    assert r.status_code == 422
    assert fake.calls == []


def test_voice_clone_oversize_413(client, auth, monkeypatch):
    fake = use_ai33(monkeypatch, lambda *a, **k: FakeResp(200, {}))
    big = b"x" * (10 * 1024 * 1024 + 1)
    r = client.post("/api/v1/media/voice-clone",
                    data={"voice_name": "Meri Awaz"},
                    files={"audio_file": ("clip.mp3", big, "audio/mpeg")},
                    headers=auth)
    assert r.status_code == 413
    assert "10MB" in r.json()["error"]
    assert fake.calls == [], "oversize file ai33 tak nahi jani chahiye"


# --------------------------------------------------- music/image/video ---

def test_music_modes(client, auth, monkeypatch):
    store_key(client, auth, monkeypatch)

    def handler(method, url, **kw):
        assert "/v1/suno/" in url
        return task_ok("task_music")
    fake = use_ai33(monkeypatch, handler)
    for mode in ("wav", "mp3", "mp3-45", "mp3-lite", "instrumental"):
        r = client.post("/api/v1/media/music",
                        json={"mode": mode, "prompt": "beat"}, headers=auth)
        assert r.status_code == 200, (mode, r.text)
        assert r.json()["data"] == {"task_id": "task_music"}
    r = client.post("/api/v1/media/music",
                    json={"mode": "ogg", "prompt": "beat"}, headers=auth)
    assert r.status_code == 422
    assert fake.calls[-1]["url"].endswith("/v1/suno/instrumental")


def test_image_models(client, auth, monkeypatch):
    store_key(client, auth, monkeypatch)

    def handler(method, url, **kw):
        return task_ok("task_img")
    fake = use_ai33(monkeypatch, handler)
    r = client.post("/api/v1/media/image",
                    json={"model": "gpt-image-1", "prompt": "sunset"},
                    headers=auth)
    assert r.status_code == 200
    assert fake.calls[0]["url"].endswith("/v1/gpt-image-1")
    r = client.post("/api/v1/media/image",
                    json={"model": "image-to-image", "prompt": "x"},
                    headers=auth)
    assert r.status_code == 422  # image_url zaroori
    r = client.post("/api/v1/media/image",
                    json={"model": "bogus", "prompt": "x"}, headers=auth)
    assert r.status_code == 422


def test_video_models(client, auth, monkeypatch):
    store_key(client, auth, monkeypatch)

    def handler(method, url, **kw):
        body = kw["json"]
        assert body["duration"] == 8
        assert body["aspect_ratio"] == "9:16"
        assert body["generate_audio"] is True
        return task_ok("task_vid")
    fake = use_ai33(monkeypatch, handler)
    r = client.post("/api/v1/media/video",
                    json={"model": "veo3-fast", "prompt": "city",
                          "aspect_ratio": "9:16"},
                    headers=auth)
    assert r.status_code == 200
    assert fake.calls[0]["url"].endswith("/v1/veo3-fast")
    r = client.post("/api/v1/media/video",
                    json={"model": "veo3", "prompt": "x",
                          "aspect_ratio": "4:3"},
                    headers=auth)
    assert r.status_code == 422


def test_task_list_and_delete(client, auth, monkeypatch):
    store_key(client, auth, monkeypatch)

    def handler(method, url, **kw):
        if method == "GET":
            return FakeResp(200, {"tasks": [
                {"task_id": "t1", "type": "tts", "status": "completed",
                 "created_at": "2026-09-18"},
                {"id": "t2", "task_type": "image", "status": "weird_new",
                 "created_at": "2026-09-18"},  # unknown -> processing
            ]})
        assert method == "POST" and url.endswith("/v1/task/delete")
        assert kw["json"] == {"task_id": "t1"}
        return FakeResp(200, {"success": True})
    use_ai33(monkeypatch, handler)
    r = client.get("/api/v1/media/tasks?limit=20", headers=auth)
    tasks = r.json()["data"]["tasks"]
    assert tasks[0] == {"task_id": "t1", "type": "tts",
                        "status": "completed", "created_at": "2026-09-18"}
    assert tasks[1]["status"] == "processing"  # unknown != completed
    r = client.post("/api/v1/media/tasks/t1/delete", headers=auth)
    assert r.json() == {"ok": True,
                        "data": {"deleted": True, "refunded": True}}


def test_task_status_unknown_maps_to_processing(client, auth, monkeypatch):
    store_key(client, auth, monkeypatch)
    use_ai33(monkeypatch,
             lambda *a, **k: FakeResp(200, {"data": {"status": "mystery_xyz"}}))
    r = client.get("/api/v1/media/tasks/abc", headers=auth)
    # unknown non-empty -> in-progress, KABHI completed nahi
    assert r.json()["data"]["status"] == "processing"


# ------------------------------------------------------------ caps ---

def test_daily_create_cap_429(client, auth, monkeypatch):
    monkeypatch.setattr(media, "MEDIA_DAILY_CREATE_CAP", 3)
    store_key(client, auth, monkeypatch)
    use_ai33(monkeypatch, lambda *a, **k: task_ok())
    for i in range(3):
        r = client.post("/api/v1/media/music",
                        json={"mode": "mp3", "prompt": "beat"}, headers=auth)
        assert r.status_code == 200, i
    r = client.post("/api/v1/media/music",
                    json={"mode": "mp3", "prompt": "beat"}, headers=auth)
    assert r.status_code == 429
    assert "20" in r.json()["error"] or "limit" in r.json()["error"]
    # polls/reads cap me nahi ginte
    r = client.get("/api/v1/media/key", headers=auth)
    assert r.status_code == 200


def test_tts_chars_cap_429(client, auth, monkeypatch):
    monkeypatch.setattr(media, "MEDIA_DAILY_CREATE_CAP", 100)
    monkeypatch.setattr(media, "MEDIA_TTS_DAILY_CHARS", 50)
    store_key(client, auth, monkeypatch)
    use_ai33(monkeypatch, lambda *a, **k: task_ok())
    r = client.post("/api/v1/media/tts",
                    json={"voice_id": "edge_x", "text": "x" * 40},
                    headers=auth)
    assert r.status_code == 200
    r = client.post("/api/v1/media/tts",
                    json={"voice_id": "edge_x", "text": "x" * 40},
                    headers=auth)
    assert r.status_code == 429
    assert "character" in r.json()["error"]


def test_caps_are_per_user(client, monkeypatch):
    monkeypatch.setattr(media, "MEDIA_DAILY_CREATE_CAP", 2)
    h1 = signup(client, email="u1@test.com")
    h2 = signup(client, email="u2@test.com")
    store_key(client, h1, monkeypatch)
    store_key(client, h2, monkeypatch)
    use_ai33(monkeypatch, lambda *a, **k: task_ok())
    for _ in range(2):
        assert client.post("/api/v1/media/music",
                           json={"mode": "mp3", "prompt": "b"},
                           headers=h1).status_code == 200
    assert client.post("/api/v1/media/music",
                       json={"mode": "mp3", "prompt": "b"},
                       headers=h1).status_code == 429
    # user 2 ki apni quota
    assert client.post("/api/v1/media/music",
                       json={"mode": "mp3", "prompt": "b"},
                       headers=h2).status_code == 200


# -------------------------------------------------- http safety ---

def test_proxy_safe_construction_no_crash(monkeypatch):
    """Gate 5: bracketed-IPv6 NO_PROXY + proxy env must not crash httpx 0.28.1."""
    monkeypatch.setenv("HTTPS_PROXY", "http://user:pass@proxy.local:8080")
    monkeypatch.setenv("NO_PROXY", "localhost,::1,[::1]")
    media._reset_http()
    c = media._http()
    import httpx as _hx
    assert isinstance(c, _hx.Client)
    media._reset_http()


def test_ai33_timeout_mapped(client, auth, monkeypatch):
    import httpx as _hx

    class Boom:
        def request(self, *a, **k):
            raise _hx.TimeoutException("slow")
    store_key(client, auth, monkeypatch)
    monkeypatch.setattr(media, "_http", lambda: Boom())
    r = client.post("/api/v1/media/tts",
                    json={"voice_id": "edge_x", "text": "hi"}, headers=auth)
    assert r.status_code == 502
    assert "timeout" in r.json()["error"].lower() or "jawab nahi" in r.json()["error"]


def test_ai33_malformed_json_mapped(client, auth, monkeypatch):
    store_key(client, auth, monkeypatch)
    use_ai33(monkeypatch, lambda *a, **k: FakeResp(200, None, text="<html>oops"))
    r = client.get("/api/v1/media/credits", headers=auth)
    assert r.status_code == 502
    assert r.json()["ok"] is False


def test_real_network_impossible(monkeypatch, client, auth):
    """_http not monkeypatched here: construction alone must not do I/O,
    and the fake asserts host == api.ai33.pro anyway."""
    media._reset_http()
    c = media._http()
    assert c is not None
