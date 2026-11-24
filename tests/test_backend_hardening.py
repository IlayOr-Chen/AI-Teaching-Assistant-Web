"""
Purpose:
    Tests for the backend hardening and GPS Geofencing refactor (Checkpoints 3.5 & 5):
    QR grace window (~8s UI rotation + ~7s grace = ~15s total validity), lecturer-only
    roster/report endpoints, QR-gated student name list, missing classroom coordinate
    geofence warning, and public-origin detection behind Cloudflare/Render proxies.
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
        Verify the QR rotates every `rotation_seconds` (~8s) on the UI but stays valid
        for `rotation_seconds + qr_grace_seconds` (~15s total), then expires.
    Inputs:
        None.
    Outputs:
        None.
    """
    cfg = AppConfig(
        use_mock_firebase=True,
        jwt_secret=SECRET,
        qr_rotation_seconds=8,
        qr_grace_seconds=7,
    )
    repo = FirebaseRepository(use_mock=True)
    mgr = AttendanceManager(repository=repo, config=cfg)
    mgr.start_session(
        "lesson_1",
        "Lesson 1",
        classroom_lat=32.1044,
        classroom_lng=35.2078,
    )

    now = int(time.time())
    fresh = mgr.generate_rotating_qr_token("lesson_1")
    assert fresh["ttl_seconds"] == 8  # on-screen UI rotation period
    assert fresh["valid_for_seconds"] == 15  # total server-side acceptance window
    assert fresh["expires_at"] - fresh["issued_at"] == 15

    # Scanned 10s ago (just after the 8s UI rotation) -> still accepted within 15s window
    scanned_10s_ago = mgr.generate_rotating_qr_token("lesson_1", now_ts=now - 10)
    assert (
        mgr.verify_qr_token(scanned_10s_ago["qr_token"], "lesson_1")["lesson_id"]
        == "lesson_1"
    )

    # Issued 20s ago -> beyond 15s (8s rotation + 7s grace) -> rejected
    stale = mgr.generate_rotating_qr_token("lesson_1", now_ts=now - 20)
    with pytest.raises(ValueError, match="QR code has expired"):
        mgr.verify_qr_token(stale["qr_token"], "lesson_1")

    # Token claims really carry the 15s extended exp
    claims = jwt.decode(fresh["qr_token"], SECRET, algorithms=["HS256"])
    assert claims["exp"] - claims["iat"] == 15


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
        Verify `/api/attendance/checkin-context` only returns names and geofence context
        for a valid, unexpired token of an open session and never leaks emails or groups.
    Inputs:
        None.
    Outputs:
        None.
    """
    client, _ = _make_client()
    _register_lecturer(client)
    client.post(
        "/api/attendance/sessions",
        json={
            "lesson_id": "lesson_1",
            "lesson_title": "Lesson 1",
            "classroom_lat": 32.1044,
            "classroom_lng": 35.2078,
            "geofence_radius_meters": 100,
        },
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
    assert body["enforce_geofence"] is True
    assert body["geofence_radius_meters"] == 100
    assert body["has_classroom_coords"] is True
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


def test_start_session_warns_when_geofence_enabled_without_classroom_coords() -> None:
    """
    Purpose:
        Verify the lecturer receives a `geofence_warning` when `enforce_geofence=True`
        but no `classroom_lat`/`classroom_lng` were provided, and no warning when
        coordinates are supplied or when `enforce_geofence=False`.
    Inputs:
        None.
    Outputs:
        None.
    """
    client, _ = _make_client()
    _register_lecturer(client)

    missing_coords = client.post(
        "/api/attendance/sessions",
        json={"lesson_id": "l1", "lesson_title": "L1"},
    ).json()
    assert (
        missing_coords["geofence_warning"]
        and "no classroom coordinates" in missing_coords["geofence_warning"]
    )

    with_coords = client.post(
        "/api/attendance/sessions",
        json={
            "lesson_id": "l2",
            "lesson_title": "L2",
            "classroom_lat": 32.1044,
            "classroom_lng": 35.2078,
        },
    ).json()
    assert with_coords["geofence_warning"] is None

    check_off = client.post(
        "/api/attendance/sessions",
        json={"lesson_id": "l3", "lesson_title": "L3", "enforce_geofence": False},
    ).json()
    assert check_off["geofence_warning"] is None


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
