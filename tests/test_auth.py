import sqlite3

from fastapi.testclient import TestClient

from concept_branch.app import configured_cors_origins, create_app
from concept_branch.config import ConfigStore
from concept_branch.db import Database


def make_app(tmp_path, db_path=None):
    store = ConfigStore(tmp_path / "config")
    database = Database(db_path or tmp_path / "db.sqlite3")
    return create_app(database, store), database


def make_client(tmp_path):
    app, _ = make_app(tmp_path)
    return TestClient(app)


def test_unauthenticated_api_returns_401(tmp_path):
    client = make_client(tmp_path)
    for method, url in [
        ("get", "/api/discussions"),
        ("get", "/api/providers"),
        ("get", "/api/settings"),
        ("get", "/api/frontends"),
        ("get", "/api/active-model"),
        ("get", "/api/search?q=test"),
        ("post", "/api/discussions"),
        ("put", "/api/settings"),
    ]:
        kwargs = {"json": {"title": "x"}} if method == "post" else {}
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code == 401, f"{method} {url} -> {response.status_code}"
    assert client.get("/api/health").status_code == 200


def test_register_first_user_is_admin_second_is_user(tmp_path):
    client = make_client(tmp_path)
    first = client.post("/api/auth/register", json={"username": "alice", "password": "alice-pass-123"})
    assert first.status_code == 201
    assert first.json()["user"]["role"] == "admin"

    other = TestClient(make_app(tmp_path)[0])
    second = other.post("/api/auth/register", json={"username": "bob", "password": "bob-pass-12345"})
    assert second.status_code == 201
    assert second.json()["user"]["role"] == "user"


def test_register_duplicate_username_conflict(tmp_path):
    client = make_client(tmp_path)
    client.post("/api/auth/register", json={"username": "alice", "password": "alice-pass-123"})
    response = client.post("/api/auth/register", json={"username": "ALICE", "password": "password123"})
    assert response.status_code == 409


def test_register_rejects_invalid_username_and_short_password(tmp_path):
    client = make_client(tmp_path)
    assert client.post("/api/auth/register", json={"username": "bad name!", "password": "password123"}).status_code == 422
    assert client.post("/api/auth/register", json={"username": "okname", "password": "short"}).status_code == 422


def test_login_logout_and_me_flow(tmp_path):
    client = make_client(tmp_path)
    client.post("/api/auth/register", json={"username": "alice", "password": "alice-pass-123"})

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "alice"
    assert "password" not in me.text and "hash" not in me.text

    fresh = TestClient(make_app(tmp_path)[0])
    wrong = fresh.post("/api/auth/login", json={"username": "alice", "password": "wrong-pass-999"})
    assert wrong.status_code == 401
    assert wrong.json()["detail"] == "用户名或密码错误"
    assert fresh.get("/api/auth/me").status_code == 401

    unknown = fresh.post("/api/auth/login", json={"username": "nobody", "password": "whatever-123"})
    assert unknown.status_code == 401
    assert unknown.json()["detail"] == "用户名或密码错误"

    ok = fresh.post("/api/auth/login", json={"username": "alice", "password": "alice-pass-123"})
    assert ok.status_code == 200
    assert fresh.get("/api/auth/me").status_code == 200
    assert fresh.get("/api/discussions").status_code == 200

    fresh.post("/api/auth/logout")
    assert fresh.get("/api/auth/me").status_code == 401


def test_password_stored_as_scrypt_hash_not_plaintext(tmp_path):
    client = make_client(tmp_path)
    client.post("/api/auth/register", json={"username": "alice", "password": "alice-pass-123"})

    with sqlite3.connect(str(tmp_path / "db.sqlite3")) as db:
        row = db.execute("SELECT username, password_hash, salt FROM users").fetchone()
    username, password_hash, salt = row
    assert username == "alice"
    assert "alice-pass-123" not in password_hash and "alice-pass-123" not in salt
    assert len(salt) == 32
    assert len(password_hash) == 64

    second = TestClient(make_app(tmp_path)[0])
    second.post("/api/auth/register", json={"username": "bob", "password": "alice-pass-123"})
    with sqlite3.connect(str(tmp_path / "db.sqlite3")) as db:
        hashes = db.execute("SELECT password_hash FROM users ORDER BY username").fetchall()
    assert hashes[0][0] != hashes[1][0]


def test_session_expiry_rejects_stale_token(tmp_path):
    app, database = make_app(tmp_path)
    client = TestClient(app)
    client.post("/api/auth/register", json={"username": "alice", "password": "alice-pass-123"})
    assert client.get("/api/auth/me").status_code == 200

    with sqlite3.connect(str(tmp_path / "db.sqlite3")) as db:
        db.execute("UPDATE sessions SET expires_at = '2000-01-01T00:00:00+00:00'")

    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/discussions").status_code == 401


def test_cookie_is_httponly_and_session_token_not_exposed(tmp_path):
    client = make_client(tmp_path)
    response = client.post("/api/auth/register", json={"username": "alice", "password": "alice-pass-123"})
    cookie = response.headers.get("set-cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert response.json()["user"].keys() == {"id", "username", "role"}
    body = response.text
    token_marker = cookie.split("concept_branch_session=")[1].split(";")[0]
    assert token_marker not in body


def test_secure_cookie_can_be_enabled_for_https(tmp_path, monkeypatch):
    monkeypatch.setenv("CONCEPT_BRANCH_SECURE_COOKIES", "1")
    client = make_client(tmp_path)
    response = client.post("/api/auth/register", json={"username": "alice", "password": "alice-pass-123"})
    assert "Secure" in response.headers.get("set-cookie", "")


def test_cors_origins_are_configurable(monkeypatch):
    monkeypatch.setenv("CONCEPT_BRANCH_CORS_ORIGINS", "https://one.example, https://two.example")
    assert configured_cors_origins() == ["https://one.example", "https://two.example"]
