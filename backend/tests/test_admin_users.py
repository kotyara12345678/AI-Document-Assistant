"""Regression tests for admin user management + moderation (reports).

Security assertions enforced server-side:
  1. regular users get 403 on every /api/admin/user* endpoint;
  2. a moderator can moderate (block regular users, view reports) but can
     NEVER assign roles and cannot moderate staff accounts;
  3. only an admin can grant/revoke the moderator and admin roles;
  4. blocking (is_active=False) and soft-deleting (is_deleted=True) a user
     really keeps them out of login and authenticated endpoints;
  5. the user list paginates, searches and computes active report counts.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.database.session import SessionLocal
from app.main import app
from app.models.report import (
    REPORT_STATUS_ACTION_TAKEN,
    REPORT_STATUS_PENDING,
    REPORT_STATUS_REJECTED,
    Report,
)
from app.models.user import User

PWD = "test-pass-123"
USERS_API = "/api/admin/users"
REPORTS_API = "/api/reports"


def _set_role(user_id: int, role: str) -> None:
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        assert user is not None
        user.role = role
        db.commit()
    finally:
        db.close()


def _read_user(user_id: int) -> User:
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        assert user is not None
        db.refresh(user)
        return user
    finally:
        db.close()


def _add_report(reporter_id: int, reported_id: int, status: str = REPORT_STATUS_PENDING) -> int:
    db = SessionLocal()
    try:
        report = Report(reporter_id=reporter_id, reported_user_id=reported_id, reason="spam", status=status)
        db.add(report)
        db.commit()
        db.refresh(report)
        return report.id
    finally:
        db.close()


def _fresh_client(prefix: str) -> TestClient:
    c = TestClient(app)
    email = f"{prefix}{uuid.uuid4().hex[:8]}@example.com"
    resp = c.post(
        "/api/auth/register",
        json={"email": email, "password": PWD, "password_confirm": PWD},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    c.headers.update({"Authorization": f"Bearer {data['access_token']}"})
    return c


@pytest.fixture()
def admin_client(register_user) -> TestClient:
    with TestClient(app) as c:
        info = register_user(c, email=f"admin{uuid.uuid4().hex[:6]}@example.com")
        _set_role(info["user_id"], "admin")
        c.headers.update({"Authorization": f"Bearer {info['token']}"})
        yield c


@pytest.fixture()
def moderator_client(register_user) -> TestClient:
    with TestClient(app) as c:
        info = register_user(c, email=f"mod{uuid.uuid4().hex[:6]}@example.com")
        _set_role(info["user_id"], "moderator")
        c.headers.update({"Authorization": f"Bearer {info['token']}"})
        yield c


def test_regular_user_cannot_open_admin_endpoints(client, register_user):
    """A plain 'user' role gets 403 on the whole admin users surface."""
    with TestClient(app) as c:
        target = register_user(c)
    assert client.get(USERS_API).status_code == 403
    assert client.get(f"{USERS_API}/{target['user_id']}").status_code == 403
    assert (
        client.patch(f"{USERS_API}/{target['user_id']}/role", json={"role": "admin"}).status_code
        == 403
    )
    assert (
        client.patch(f"{USERS_API}/{target['user_id']}/status", json={"is_active": False}).status_code
        == 403
    )
    assert client.delete(f"{USERS_API}/{target['user_id']}").status_code == 403
    assert client.get(f"{USERS_API}/{target['user_id']}/reports").status_code == 403


def test_unauthenticated_gets_401():
    with TestClient(app) as c:
        assert c.get(USERS_API).status_code == 401
        assert c.get(f"{USERS_API}/1/reports").status_code == 401


def test_moderator_cannot_assign_roles(moderator_client, register_user):
    """Moderators must never be able to grant admin (or any role)."""
    with TestClient(app) as c:
        target = register_user(c)
    for role in ("admin", "moderator"):
        resp = moderator_client.patch(f"{USERS_API}/{target['user_id']}/role", json={"role": role})
        assert resp.status_code == 403, resp.text
    assert _read_user(target["user_id"]).role == "user"


def test_admin_can_assign_moderator(admin_client, register_user):
    with TestClient(app) as c:
        target = register_user(c)
    resp = admin_client.patch(f"{USERS_API}/{target['user_id']}/role", json={"role": "moderator"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "moderator"
    assert _read_user(target["user_id"]).role == "moderator"


def test_admin_can_assign_admin(admin_client, register_user):
    with TestClient(app) as c:
        target = register_user(c)
    resp = admin_client.patch(f"{USERS_API}/{target['user_id']}/role", json={"role": "admin"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "admin"
    assert _read_user(target["user_id"]).role == "admin"


def test_admin_can_revoke_role(admin_client, register_user):
    with TestClient(app) as c:
        target = register_user(c)
    _set_role(target["user_id"], "moderator")
    assert (
        admin_client.patch(f"{USERS_API}/{target['user_id']}/role", json={"role": "user"}).status_code
        == 200
    )
    assert _read_user(target["user_id"]).role == "user"


def test_admin_cannot_change_own_role_or_block_or_delete_self():
    """Prevent an admin from locking themselves out (sliding privilege)."""
    c = _fresh_client("self")
    me = c.get("/api/auth/me").json()["id"]
    _set_role(me, "admin")
    assert c.patch(f"{USERS_API}/{me}/role", json={"role": "user"}).status_code == 400
    assert c.patch(f"{USERS_API}/{me}/status", json={"is_active": False}).status_code == 400
    assert c.delete(f"{USERS_API}/{me}").status_code == 400


def test_moderator_cannot_moderate_staff(moderator_client, admin_client, register_user):
    with TestClient(app) as c:
        staff = register_user(c)
    admin_client.patch(f"{USERS_API}/{staff['user_id']}/role", json={"role": "moderator"})
    resp = moderator_client.patch(
        f"{USERS_API}/{staff['user_id']}/status", json={"is_active": False}
    )
    assert resp.status_code == 403, resp.text


def test_user_can_be_blocked_unblocked(admin_client, register_user):
    with TestClient(app) as c:
        target = register_user(c)
    tid = target["user_id"]

    resp = admin_client.patch(f"{USERS_API}/{tid}/status", json={"is_active": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False
    assert _read_user(tid).is_active is False

    # Blocked account can no longer log in or authenticate with its old token.
    with TestClient(app) as c:
        login = c.post("/api/auth/login", json={"email": target["email"], "password": PWD})
        assert login.status_code == 403, login.text
        me = c.get("/api/auth/me", headers={"Authorization": f"Bearer {target['token']}"})
        assert me.status_code == 403, me.text

    resp = admin_client.patch(f"{USERS_API}/{tid}/status", json={"is_active": True})
    assert resp.status_code == 200, resp.text
    assert _read_user(tid).is_active is True
    with TestClient(app) as c:
        login = c.post("/api/auth/login", json={"email": target["email"], "password": PWD})
        assert login.status_code == 200, login.text


def test_user_can_be_soft_deleted(admin_client, register_user):
    with TestClient(app) as c:
        target = register_user(c)
    tid = target["user_id"]

    resp = admin_client.delete(f"{USERS_API}/{tid}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": True, "user_id": tid}

    user = _read_user(tid)
    assert user.is_deleted is True
    assert user.is_active is False
    assert user.deleted_at is not None

    # Hidden from the normal list, gone from search, and cannot authenticate.
    body = admin_client.get(USERS_API).json()
    assert all(u["id"] != tid for u in body["items"])
    body = admin_client.get(USERS_API, params={"search": target["email"]}).json()
    assert body["items"] == []
    with TestClient(app) as c:
        login = c.post("/api/auth/login", json={"email": target["email"], "password": PWD})
        assert login.status_code == 403, login.text
        me = c.get("/api/auth/me", headers={"Authorization": f"Bearer {target['token']}"})
        assert me.status_code == 403, me.text

    # Deleted users behave as gone for admin actions too.
    assert admin_client.patch(f"{USERS_API}/{tid}/role", json={"role": "moderator"}).status_code == 404
    assert admin_client.get(f"{USERS_API}/{tid}").status_code == 404


def test_reports_count_is_correct(admin_client, register_user):
    with TestClient(app) as c:
        target = register_user(c)
        reporter = register_user(c)
    tid, rid = target["user_id"], reporter["user_id"]

    _add_report(rid, tid, REPORT_STATUS_PENDING)
    _add_report(rid, tid, REPORT_STATUS_PENDING)
    _add_report(rid, tid, REPORT_STATUS_PENDING)
    _add_report(rid, tid, REPORT_STATUS_ACTION_TAKEN)
    _add_report(rid, tid, REPORT_STATUS_REJECTED)

    body = admin_client.get(USERS_API).json()
    row = next(u for u in body["items"] if u["id"] == tid)
    assert row["reports_active"] == 3  # only pending/reviewed count as active

    one = admin_client.get(f"{USERS_API}/{tid}").json()
    assert one["reports_active"] == 3


def test_reports_listed_for_user_paginated(admin_client, register_user):
    with TestClient(app) as c:
        target = register_user(c)
        reporter = register_user(c)
    tid, rid = target["user_id"], reporter["user_id"]

    ids = [_add_report(rid, tid, REPORT_STATUS_PENDING) for _ in range(5)]

    body = admin_client.get(f"{USERS_API}/{tid}/reports").json()
    assert body["total"] == 5
    assert len(body["items"]) == 5
    for item in body["items"]:
        assert item["reported_user_id"] == tid
        assert item["reporter_email"] == reporter["email"]
        assert item["reason"] == "spam"
        assert item["status"] == "pending"
        assert item["created_at"]

    page = admin_client.get(f"{USERS_API}/{tid}/reports", params={"page": 1, "limit": 2}).json()
    assert page["total"] == 5
    assert len(page["items"]) == 2
    assert [i["id"] for i in page["items"]] == sorted(ids, reverse=True)[:2]


def test_users_list_paginates(admin_client, register_user):
    with TestClient(app) as c:
        for i in range(25):
            register_user(c, email=f"page{i}@{uuid.uuid4().hex[:6]}example.com")

    page1 = admin_client.get(USERS_API, params={"page": 1, "limit": 10}).json()
    assert page1["total"] == 26  # 25 created + the admin itself
    assert len(page1["items"]) == 10

    page2 = admin_client.get(USERS_API, params={"page": 2, "limit": 10}).json()
    page3 = admin_client.get(USERS_API, params={"page": 3, "limit": 10}).json()
    assert len(page2["items"]) == 10
    assert len(page3["items"]) == 6

    ids = [u["id"] for u in page1["items"] + page2["items"] + page3["items"]]
    assert len(set(ids)) == 26  # no overlap across pages


def test_users_search(admin_client, register_user):
    with TestClient(app) as c:
        maria = register_user(c, email=f"maria{uuid.uuid4().hex[:6]}@example.com")
        register_user(c, email=f"alice{uuid.uuid4().hex[:6]}@example.com")
        register_user(c, email=f"bob{uuid.uuid4().hex[:6]}@example.com")

    body = admin_client.get(USERS_API, params={"search": "MARIA"}).json()
    assert body["total"] == 1
    assert body["items"][0]["email"] == maria["email"]

    body = admin_client.get(USERS_API, params={"search": "zzz-not-there"}).json()
    assert body["total"] == 0
    assert body["items"] == []


def test_moderator_can_moderate_but_not_see_sensitive_stats(moderator_client, register_user):
    with TestClient(app) as c:
        target = register_user(c)
        reporter = register_user(c)
    tid = target["user_id"]
    _add_report(reporter["user_id"], tid)

    # moderator sees the list (moderation context) and can block a regular user
    assert moderator_client.get(USERS_API).status_code == 200
    resp = moderator_client.patch(f"{USERS_API}/{tid}/status", json={"is_active": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False
    body = moderator_client.get(f"{USERS_API}/{tid}/reports").json()
    assert body["total"] == 1

    # ...but the aggregate platform stats remain admin-only.
    assert moderator_client.get("/api/admin/stats").status_code == 403


def test_report_submission_flow(client, register_user):
    with TestClient(app) as c:
        target = register_user(c)
    tid = target["user_id"]

    resp = client.post(
        REPORTS_API,
        json={"reported_user_id": tid, "reason": "spam", "description": "Спам в чате"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["reported_user_id"] == tid
    assert body["reason"] == "spam"
    assert body["description"] == "Спам в чате"
    assert body["status"] == "pending"

    # self-report is rejected
    me = client.get("/api/auth/me").json()["id"]
    resp = client.post(REPORTS_API, json={"reported_user_id": me, "reason": "spam"})
    assert resp.status_code == 400, resp.text


def test_report_submission_requires_auth_and_valid_target():
    with TestClient(app) as c:
        anon = c.post(REPORTS_API, json={"reported_user_id": 1, "reason": "spam"})
        assert anon.status_code == 401, anon.text

        auth = _fresh_client("anon2")
        missing = auth.post(REPORTS_API, json={"reported_user_id": 999999, "reason": "spam"})
        assert missing.status_code == 404, missing.text