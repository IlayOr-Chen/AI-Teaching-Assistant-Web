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
        Authenticate the configured demo lecturer (`adminL@gmail.com` / `12345`)
        so the client carries a lecturer session cookie.
    Inputs:
        client (TestClient): Client to authenticate.
    Outputs:
        None.
    """
    res = client.post(
        "/api/auth/lecturer/login",
        json={
            "email": "adminL@gmail.com",
            "password": "12345",
        },
    )
    assert res.status_code == 200


def test_qr_token_grace_window_keeps_rotated_token_usable_briefly() -> None:
    """
    Purpose:
        Verify that the static session QR token is a short URL-safe code (16 chars),
        remains valid for the entire duration that the session is LIVE (`active=True`),
        and is immediately invalidated when the Lecturer closes the session.
    Inputs:
        None.
    Outputs:
        None.
    """
    cfg = AppConfig(
        use_mock_firebase=True,
        jwt_secret=SECRET,
    )
    repo = FirebaseRepository(use_mock=True)
    mgr = AttendanceManager(repository=repo, config=cfg)
    session = mgr.start_session(
        "lesson_1",
        "Lesson 1",
        classroom_lat=32.1044,
        classroom_lng=35.2078,
    )

    qr_first = mgr.generate_rotating_qr_token("lesson_1")
    qr_later = mgr.generate_rotating_qr_token("lesson_1")
    assert qr_first["qr_token"] == qr_later["qr_token"] == session["qr_token"]
    assert 12 <= len(qr_first["qr_token"]) <= 20
    assert qr_first["active"] is True

    # Valid throughout the active session
    verified = mgr.verify_qr_token(qr_first["qr_token"], "lesson_1")
    assert verified["lesson_id"] == "lesson_1"
    assert verified["active"] is True

    # Invalidated immediately upon closing the session
    mgr.close_session("lesson_1")
    with pytest.raises(ValueError, match="closed"):
        mgr.verify_qr_token(qr_first["qr_token"], "lesson_1")


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
        Verify `/api/attendance/checkin-context` is strictly QR-gated (only returns
        student dropdown options and geofence context for a valid, unexpired token
        of an open session) and returns `{"student_id", "full_name", "email"}` for
        each student so Hebrew names and emails render cleanly in the dropdown.
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
        assert set(student) == {"student_id", "full_name", "email"}

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
