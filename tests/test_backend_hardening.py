"""
Purpose:
    Tests for the backend hardening added while building the web UI (Checkpoint 5):
    QR grace window, lecturer-only roster/report endpoints, QR-gated student name list,
    same-network IP warning, and public-origin detection behind Cloudflare/Render proxies.
Inputs:
    In-memory `FirebaseRepository` and FastAPI `TestClient`.
Outputs:
    Deterministic pytest assertions.
"""

from __future__ import annotations

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from src.config import AppConfig
from src.main import create_app
from src.repository.firebase_repository import FirebaseRepository
from src.services.attendance_manager import AttendanceManager
from src.utils.ip_resolver import ClientIPResolver

SECRET = "hardening-test-secret-key-that-is-32-bytes-or-more"


def _seed_students(repo: FirebaseRepository) -> None:
    """
    Purpose:
        Seed two roster students (with emails) into the in-memory Firebase.
    Inputs:
        repo (FirebaseRepository): Mock repository to seed.
    Outputs:
        None.
    """
    repo.set(
        "/students",
        {
            "stu_a": {
                "student_id": "stu_a",
                "full_name": "Alice Cohen",
                "email": "alice@ariel.ac.il",
                "group": "1",
            },
            "stu_b": {
                "student_id": "stu_b",
                "full_name": "Bob Levi",
                "email": "bob@ariel.ac.il",
                "group": "",
            },
        },
    )


def _make_client(config: AppConfig | None = None) -> tuple[TestClient, FirebaseRepository]:
    """
    Purpose:
        Build a TestClient over a fresh mock-backed app with seeded students.
    Inputs:
        config (AppConfig | None): Optional config override.
    Outputs:
        tuple[TestClient, FirebaseRepository]: Client and its backing repository.
    """
    cfg = config or AppConfig(
        use_mock_firebase=True, jwt_secret=SECRET, lecturer_access_code="CODE-1"
    )
    repo = FirebaseRepository(use_mock=True)
    _seed_students(repo)
    return TestClient(create_app(config=cfg, repository=repo)), repo


def _register_lecturer(client: TestClient) -> None:
    """
    Purpose:
        Register a lecturer so the client carries a lecturer session cookie.
    Inputs:
        client (TestClient): Client to authenticate.
    Outputs:
        None.
    """
    res = client.post(
        "/api/auth/lecturer/register",
        json={
            "full_name": "Prof. Test",
            "email": "prof@ariel.ac.il",
            "password": "password123",
            "access_code": "CODE-1",
        },
    )
    assert res.status_code == 200


def test_qr_token_grace_window_keeps_rotated_token_usable_briefly() -> None:
    """
    Purpose:
        Verify the QR rotates every `rotation_seconds` but stays valid for
        `rotation_seconds + qr_grace_seconds`, then expires.
    Inputs:
        None.
    Outputs:
        None.
    """
    cfg = AppConfig(
        use_mock_firebase=True,
        jwt_secret=SECRET,
        qr_rotation_seconds=12,
        qr_grace_seconds=60,
    )
    repo = FirebaseRepository(use_mock=True)
    mgr = AttendanceManager(repository=repo, config=cfg)
    mgr.start_session("lesson_1", "Lesson 1", lecturer_ip="1.1.1.1")

    now = int(time.time())
    fresh = mgr.generate_rotating_qr_token("lesson_1")
    assert fresh["ttl_seconds"] == 12  # on-screen rotation period
    assert fresh["valid_for_seconds"] == 72  # acceptance window
    assert fresh["expires_at"] - fresh["issued_at"] == 72

    # Scanned 30s ago (two rotations ago) -> still accepted
    scanned_30s_ago = mgr.generate_rotating_qr_token("lesson_1", now_ts=now - 30)
    assert mgr.verify_qr_token(scanned_30s_ago["qr_token"], "lesson_1")["lesson_id"] == "lesson_1"

    # Issued 80s ago -> beyond rotation + grace -> rejected
    stale = mgr.generate_rotating_qr_token("lesson_1", now_ts=now - 80)
    with pytest.raises(ValueError, match="QR code has expired"):
        mgr.verify_qr_token(stale["qr_token"], "lesson_1")

    # Token claims really carry the extended exp
    claims = jwt.decode(fresh["qr_token"], SECRET, algorithms=["HS256"])
    assert claims["exp"] - claims["iat"] == 72


def test_roster_and_attendance_reports_are_lecturer_only() -> None:
    """
    Purpose:
        Verify roster list, session list and session report endpoints reject anonymous
        callers (401) and student sessions (403), but work for a lecturer.
    Inputs:
        None.
    Outputs:
        None.
    """
    client, _ = _make_client()
    protected = [
        "/api/roster/students",
        "/api/attendance/sessions",
        "/api/attendance/sessions/lesson_1",
    ]
    for url in protected:
        assert client.get(url).status_code == 401, url

    student = TestClient(client.app)
    assert (
        student.post("/api/auth/student/login", json={"email": "alice@ariel.ac.il"}).status_code
        == 200
    )
    for url in protected:
        assert student.get(url).status_code == 403, url

    _register_lecturer(client)
    for url in protected:
        assert client.get(url).status_code == 200, url


def test_checkin_context_is_qr_gated_and_hides_emails() -> None:
    """
    Purpose:
        Verify `/api/attendance/checkin-context` only returns names for a valid,
        unexpired token of an open session and never leaks emails or groups.
    Inputs:
        None.
    Outputs:
        None.
    """
    client, _ = _make_client()
    _register_lecturer(client)
    client.post(
        "/api/attendance/sessions",
        json={"lesson_id": "lesson_1", "lesson_title": "Lesson 1"},
        headers={"CF-Connecting-IP": "8.8.8.8"},
    )
    qr = client.get("/api/attendance/sessions/lesson_1/qr").json()

    phone = TestClient(client.app)  # anonymous phone: no lecturer cookie
    ok = phone.get(
        "/api/attendance/checkin-context",
        params={"lesson_id": "lesson_1", "qr_token": qr["qr_token"]},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["lesson_title"] == "Lesson 1"
    assert [s["full_name"] for s in body["students"]] == ["Alice Cohen", "Bob Levi"]
    for student in body["students"]:
        assert set(student) == {"student_id", "full_name"}

    # Missing/garbage token -> 400, and no names leak
    bad = phone.get(
        "/api/attendance/checkin-context",
        params={"lesson_id": "lesson_1", "qr_token": "garbage"},
    )
    assert bad.status_code == 400
    assert "students" not in bad.json()

    # Token for another lesson -> 400
    other = phone.get(
        "/api/attendance/checkin-context",
        params={"lesson_id": "lesson_2", "qr_token": qr["qr_token"]},
    )
    assert other.status_code == 400

    # Closed session -> 400 even with a still-valid token
    client.post("/api/attendance/sessions/lesson_1/close")
    closed = phone.get(
        "/api/attendance/checkin-context",
        params={"lesson_id": "lesson_1", "qr_token": qr["qr_token"]},
    )
    assert closed.status_code == 400
    assert "not open" in closed.json()["detail"]


def test_start_session_warns_when_lecturer_ip_is_local() -> None:
    """
    Purpose:
        Verify the lecturer gets an `ip_warning` when their captured IP is loopback/private
        and the same-network check is on, and no warning for a public IP or when the check is off.
    Inputs:
        None.
    Outputs:
        None.
    """
    client, _ = _make_client()
    _register_lecturer(client)

    local = client.post(
        "/api/attendance/sessions",
        json={"lesson_id": "l1", "lesson_title": "L1"},
        headers={"X-Forwarded-For": "192.168.1.20"},
    ).json()
    assert local["ip_warning"] and "192.168.1.20" in local["ip_warning"]

    public = client.post(
        "/api/attendance/sessions",
        json={"lesson_id": "l2", "lesson_title": "L2"},
        headers={"CF-Connecting-IP": "84.110.45.200"},
    ).json()
    assert public["ip_warning"] is None

    check_off = client.post(
        "/api/attendance/sessions",
        json={"lesson_id": "l3", "lesson_title": "L3", "enforce_same_network": False},
        headers={"X-Forwarded-For": "10.0.0.5"},
    ).json()
    assert check_off["ip_warning"] is None


def test_is_loopback_or_private_classification() -> None:
    """
    Purpose:
        Verify `ClientIPResolver.is_loopback_or_private` on typical addresses.
    Inputs:
        None.
    Outputs:
        None.
    """
    for local in ("127.0.0.1", "::1", "192.168.0.7", "10.1.2.3", "172.16.5.5", "testclient", ""):
        assert ClientIPResolver.is_loopback_or_private(local) is True, local
    for public in ("8.8.8.8", "84.110.45.200", "2001:4860:4860::8888"):
        assert ClientIPResolver.is_loopback_or_private(public) is False, public


def test_qr_link_uses_public_origin_from_proxy_headers_or_config() -> None:
    """
    Purpose:
        Verify QR check-in links point at the public tunnel/Render origin: derived from
        `X-Forwarded-Proto`/`X-Forwarded-Host`, or forced by `PUBLIC_BASE_URL`.
    Inputs:
        None.
    Outputs:
        None.
    """
    client, _ = _make_client()
    _register_lecturer(client)
    client.post(
        "/api/attendance/sessions",
        json={"lesson_id": "lesson_1", "lesson_title": "Lesson 1"},
    )

    proxied = client.get(
        "/api/attendance/sessions/lesson_1/qr",
        headers={
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "demo.trycloudflare.com",
        },
    ).json()
    assert proxied["checkin_url"].startswith("https://demo.trycloudflare.com/?lesson_id=lesson_1")
    assert isinstance(proxied["server_time"], int)

    forced_cfg = AppConfig(
        use_mock_firebase=True,
        jwt_secret=SECRET,
        lecturer_access_code="CODE-1",
        public_base_url="https://ta.onrender.com/",
    )
    forced_client, _ = _make_client(forced_cfg)
    _register_lecturer(forced_client)
    forced_client.post(
        "/api/attendance/sessions",
        json={"lesson_id": "lesson_1", "lesson_title": "Lesson 1"},
    )
    forced = forced_client.get("/api/attendance/sessions/lesson_1/qr").json()
    assert forced["checkin_url"].startswith("https://ta.onrender.com/?lesson_id=lesson_1")
