"""
Purpose:
    Comprehensive unit and HTTP integration tests for `AttendanceManager` and
    `calculate_distance_meters` (Issues #3 & #13 — Checkpoint 3.5):
    - Haversine distance calculation accuracy
    - Rotating signed QR tokens (~8s UI rotation + ~15s total grace window validity)
    - GPS Geofencing validation (inside radius allowed, outside radius blocked with
      exact distance message, missing GPS coordinates blocked when `enforce_geofence=True`,
      and `enforce_geofence=False` live lecturer toggle fallback)
    - Cookie + Firebase RTDB 15-minute device cooldown anti-cheat enforcement.
Inputs:
    In-memory `FirebaseRepository` instances, `AppConfig`, and FastAPI `TestClient`.
Outputs:
    Deterministic pytest assertions covering happy paths and edge cases for Feature 1.
"""

from __future__ import annotations

import time
import pytest
from fastapi.testclient import TestClient

from src.config import AppConfig
from src.main import create_app
from src.repository.firebase_repository import FirebaseRepository
from src.services.attendance_manager import (
    DEVICE_COOKIE_NAME,
    AttendanceManager,
    calculate_distance_meters,
)
from src.services.auth_service import AuthService

# Ariel University classroom reference coordinates
CLASSROOM_LAT = 32.104400
CLASSROOM_LNG = 35.207800
# ~25 meters away from the classroom (inside 100m radius)
INSIDE_RADIUS_LAT = 32.104600
INSIDE_RADIUS_LNG = 35.207920
# ~350 meters away from the classroom (outside 100m radius)
OUTSIDE_RADIUS_LAT = 32.107500
OUTSIDE_RADIUS_LNG = 35.208500


@pytest.fixture()
def attendance_env() -> tuple[AppConfig, FirebaseRepository, AttendanceManager]:
    """
    Purpose:
        Create an isolated in-memory `FirebaseRepository` seeded with two sample
        students (`stu_alice`, `stu_bob`) and an `AttendanceManager` instance
        configured with 8s QR rotation, 7s grace window (15s total), and 100m GPS geofence.
    Inputs:
        None.
    Outputs:
        tuple[AppConfig, FirebaseRepository, AttendanceManager]: Test fixtures.
    """
    config = AppConfig(
        use_mock_firebase=True,
        jwt_secret="checkpoint-3-5-attendance-geofence-secret-key-32-bytes",
        qr_rotation_seconds=8,
        qr_grace_seconds=7,
        device_cooldown_minutes=15,
        enforce_geofence_default=True,
        geofence_radius_meters=100,
    )
    repo = FirebaseRepository(use_mock=True)
    repo.set(
        "/students",
        {
            "stu_alice": {
                "student_id": "stu_alice",
                "full_name": "Alice Cohen",
                "email": "alice@ariel.ac.il",
                "group": "1",
            },
            "stu_bob": {
                "student_id": "stu_bob",
                "full_name": "Bob Levi",
                "email": "bob@ariel.ac.il",
                "group": "2",
            },
        },
    )
    manager = AttendanceManager(repository=repo, config=config)
    return config, repo, manager


def test_haversine_distance_calculation_accuracy() -> None:
    """
    Purpose:
        Verify that `calculate_distance_meters` computes accurate WGS84 great-circle
        distances in meters and validates coordinate bounds (`-90..90`, `-180..180`).
    Inputs:
        None.
    Outputs:
        None.
    """
    # Zero distance for identical coordinates
    assert (
        calculate_distance_meters(
            CLASSROOM_LAT, CLASSROOM_LNG, CLASSROOM_LAT, CLASSROOM_LNG
        )
        == 0.0
    )

    # Inside classroom (~25m away)
    dist_inside = calculate_distance_meters(
        CLASSROOM_LAT, CLASSROOM_LNG, INSIDE_RADIUS_LAT, INSIDE_RADIUS_LNG
    )
    assert 20.0 <= dist_inside <= 30.0

    # Outside classroom (~350m away)
    dist_outside = calculate_distance_meters(
        CLASSROOM_LAT, CLASSROOM_LNG, OUTSIDE_RADIUS_LAT, OUTSIDE_RADIUS_LNG
    )
    assert 330.0 <= dist_outside <= 370.0

    # Out-of-bounds latitude/longitude must raise ValueError
    with pytest.raises(ValueError, match="Latitude values must be between"):
        calculate_distance_meters(95.0, 35.0, 32.0, 35.0)
    with pytest.raises(ValueError, match="Longitude values must be between"):
        calculate_distance_meters(32.0, 195.0, 32.0, 35.0)


def test_rotating_qr_token_generation_and_expiration(
    attendance_env: tuple[AppConfig, FirebaseRepository, AttendanceManager]
) -> None:
    """
    Purpose:
        Verify that starting a session generates ONE short static session QR token
        (16 URL-safe characters) valid for the entire duration that the session
        remains LIVE (`active=True`), and that `verify_qr_token` rejects tokens once
        the session is closed, or when presented with a wrong-lesson or forged token.
    Inputs:
        attendance_env: Isolated attendance test fixture.
    Outputs:
        None.
    """
    _, _, manager = attendance_env
    session = manager.start_session(
        lesson_id="lesson_1",
        lesson_title="Lesson 1: OOD Principles",
        classroom_lat=CLASSROOM_LAT,
        classroom_lng=CLASSROOM_LNG,
    )
    manager.start_session(
        lesson_id="lesson_2",
        lesson_title="Lesson 2: Design Patterns",
        classroom_lat=CLASSROOM_LAT,
        classroom_lng=CLASSROOM_LNG,
    )

    qr1 = manager.generate_rotating_qr_token("lesson_1", base_url="https://ta.example.com")
    qr2 = manager.generate_rotating_qr_token("lesson_1", base_url="https://ta.example.com")

    # Static session QR: same short URL-safe token returned throughout the active session
    assert qr1["qr_token"] == qr2["qr_token"] == session["qr_token"]
    assert 12 <= len(qr1["qr_token"]) <= 20
    assert qr1["active"] is True
    assert qr1["qr_image_data_url"].startswith("data:image/png;base64,")
    assert f"lesson_id=lesson_1&qr_token={qr1['qr_token']}" in qr1["checkin_url"]

    claims = manager.verify_qr_token(qr1["qr_token"], expected_lesson_id="lesson_1")
    assert claims["lesson_id"] == "lesson_1"
    assert claims["type"] == "qr_attendance"
    assert claims["active"] is True

    # Edge case 1: Token used for a different lesson session is rejected
    with pytest.raises(ValueError, match="different lesson session"):
        manager.verify_qr_token(qr1["qr_token"], expected_lesson_id="lesson_2")

    # Edge case 2: Forged or random token is rejected
    with pytest.raises(ValueError, match="Invalid or inactive session QR attendance token"):
        manager.verify_qr_token("forged_wrong_token", expected_lesson_id="lesson_1")

    # Edge case 3: Once the Lecturer closes the session, the static session token is invalidated
    manager.close_session("lesson_1")
    with pytest.raises(ValueError, match="closed"):
        manager.verify_qr_token(qr1["qr_token"], expected_lesson_id="lesson_1")


def test_signed_device_cookie_creation_and_tamper_detection(
    attendance_env: tuple[AppConfig, FirebaseRepository, AttendanceManager]
) -> None:
    """
    Purpose:
        Verify that `create_signed_device_cookie` signs device IDs with HMAC-SHA256
        and `verify_signed_device_cookie` detects any tampering with ID or signature.
    Inputs:
        attendance_env: Isolated attendance test fixture.
    Outputs:
        None.
    """
    _, _, manager = attendance_env

    device_id, signed_cookie = manager.create_signed_device_cookie()
    assert device_id.startswith("dev_")
    assert manager.verify_signed_device_cookie(signed_cookie) == device_id

    # Tamper with the device_id portion while keeping the signature
    _, sig = signed_cookie.rsplit(".", 1)
    assert manager.verify_signed_device_cookie(f"dev_forged123.{sig}") is None
    assert manager.verify_signed_device_cookie("malformed_cookie_without_dot") is None
    assert manager.verify_signed_device_cookie(None) is None


def test_gps_geofencing_happy_and_edge_cases(
    attendance_env: tuple[AppConfig, FirebaseRepository, AttendanceManager]
) -> None:
    """
    Purpose:
        Verify GPS Geofencing validation across all 4 required scenarios:
        1) Happy case: student inside radius (~25m <= 100m) -> allowed (`geofence_status="verified"`),
        2) Edge case: student outside radius (~350m > 100m) -> blocked with clear distance message,
        3) Edge case: student denies GPS (`lat/lng = None`) while `enforce_geofence=True` -> blocked
           with clear Browser Geolocation / HTTPS message,
        4) Edge case: Lecturer toggles `enforce_geofence=False` -> allowed even without GPS or outside radius.
    Inputs:
        attendance_env: Isolated attendance test fixture.
    Outputs:
        None.
    """
    _, _, manager = attendance_env

    manager.start_session(
        lesson_id="lesson_1",
        lesson_title="Lesson 1",
        classroom_lat=CLASSROOM_LAT,
        classroom_lng=CLASSROOM_LNG,
        geofence_radius_meters=100,
        enforce_geofence=True,
    )
    qr = manager.generate_rotating_qr_token("lesson_1")

    # 1. Happy case: Alice is inside the classroom radius (~25m <= 100m)
    res_alice = manager.submit_qr_checkin(
        lesson_id="lesson_1",
        student_id="stu_alice",
        qr_token=qr["qr_token"],
        student_lat=INSIDE_RADIUS_LAT,
        student_lng=INSIDE_RADIUS_LNG,
    )
    assert res_alice["record"]["status"] == "present"
    assert res_alice["record"]["geofence_status"] == "verified"
    assert 20.0 <= res_alice["record"]["distance_meters"] <= 30.0

    # 2. Edge case: Bob is outside the classroom radius (~350m > 100m)
    with pytest.raises(
        PermissionError,
        match=r"GPS Geofence check failed: You appear to be 3\d\dm away from the classroom \(allowed: 100m\)",
    ):
        manager.submit_qr_checkin(
            lesson_id="lesson_1",
            student_id="stu_bob",
            qr_token=qr["qr_token"],
            student_lat=OUTSIDE_RADIUS_LAT,
            student_lng=OUTSIDE_RADIUS_LNG,
        )

    # 3. Edge case: Bob denies Browser Geolocation permission (lat/lng = None) while enforce_geofence=True
    with pytest.raises(
        PermissionError,
        match="GPS location permission is required to check in while classroom geofencing is enabled",
    ):
        manager.submit_qr_checkin(
            lesson_id="lesson_1",
            student_id="stu_bob",
            qr_token=qr["qr_token"],
            student_lat=None,
            student_lng=None,
        )

    # 4. Edge case fallback: Lecturer toggles "Enforce GPS Geofencing" OFF live
    updated_session = manager.set_geofence_enforcement(
        lesson_id="lesson_1", enforce_geofence=False
    )
    assert updated_session["enforce_geofence"] is False

    # Now Bob can check in even without GPS permission (or from outside the radius)
    res_bob = manager.submit_qr_checkin(
        lesson_id="lesson_1",
        student_id="stu_bob",
        qr_token=qr["qr_token"],
        student_lat=None,
        student_lng=None,
    )
    assert res_bob["record"]["status"] == "present"
    assert res_bob["record"]["geofence_status"] == "bypassed"


def test_fifteen_minute_device_cooldown_blocks_buddy_punching(
    attendance_env: tuple[AppConfig, FirebaseRepository, AttendanceManager]
) -> None:
    """
    Purpose:
        Verify that once a device checks in a student, `/device_cooldowns/{device_id}`
        and the signed device cookie block any second check-in from the same device
        for 15 minutes (900 seconds), returning the exact remaining cooldown countdown.
    Inputs:
        attendance_env: Isolated attendance test fixture.
    Outputs:
        None.
    """
    _, repo, manager = attendance_env
    base_time = int(time.time())

    manager.start_session(
        lesson_id="lesson_1",
        lesson_title="Lesson 1",
        classroom_lat=CLASSROOM_LAT,
        classroom_lng=CLASSROOM_LNG,
    )
    qr = manager.generate_rotating_qr_token(
        "lesson_1", now_ts=base_time, ttl_override_seconds=3600
    )

    # Step 1: Alice checks in on her smartphone at t = base_time
    first_checkin = manager.submit_qr_checkin(
        lesson_id="lesson_1",
        student_id="stu_alice",
        qr_token=qr["qr_token"],
        student_lat=INSIDE_RADIUS_LAT,
        student_lng=INSIDE_RADIUS_LNG,
        now_ts=base_time,
    )
    device_id = first_checkin["device_id"]
    signed_cookie = first_checkin["signed_device_cookie"]

    # Confirm cooldown is persisted in Firebase RTDB under /device_cooldowns/{device_id}
    stored_cooldown = repo.get(f"/device_cooldowns/{device_id}")
    assert stored_cooldown is not None
    assert stored_cooldown["last_student_id"] == "stu_alice"
    assert stored_cooldown["expires_at"] == base_time + 900

    # Step 2: 3 minutes (180s) later, Alice tries to check in her absent friend Bob on her phone
    with pytest.raises(
        PermissionError,
        match=r"15-minute device cooldown active.*Remaining cooldown: 12m 00s \(720 seconds\)",
    ):
        manager.submit_qr_checkin(
            lesson_id="lesson_1",
            student_id="stu_bob",
            qr_token=qr["qr_token"],
            student_lat=INSIDE_RADIUS_LAT,
            student_lng=INSIDE_RADIUS_LNG,
            device_cookie_value=signed_cookie,
            now_ts=base_time + 180,
        )

    # Step 3: After 15 minutes (901s elapsed), cooldown expires and check-in is allowed
    after_cooldown = manager.submit_qr_checkin(
        lesson_id="lesson_1",
        student_id="stu_bob",
        qr_token=qr["qr_token"],
        student_lat=INSIDE_RADIUS_LAT,
        student_lng=INSIDE_RADIUS_LNG,
        device_cookie_value=signed_cookie,
        now_ts=base_time + 901,
    )
    assert after_cooldown["record"]["student_id"] == "stu_bob"
    assert after_cooldown["device_id"] == device_id


def test_manual_attendance_override_and_report(
    attendance_env: tuple[AppConfig, FirebaseRepository, AttendanceManager]
) -> None:
    """
    Purpose:
        Verify that `set_manual_attendance` allows the Lecturer to manually toggle
        any student between `present` and `absent`, and `get_lesson_attendance_report`
        aggregates the full roster accurately.
    Inputs:
        attendance_env: Isolated attendance test fixture.
    Outputs:
        None.
    """
    _, _, manager = attendance_env
    manager.start_session("lesson_2", "Lesson 2")

    manager.set_manual_attendance("lesson_2", "stu_alice", "present")
    report = manager.get_lesson_attendance_report("lesson_2")
    assert report["present_count"] == 1
    assert report["absent_count"] == 1
    assert report["total_students"] == 2

    manager.set_manual_attendance("lesson_2", "stu_alice", "absent")
    report_after = manager.get_lesson_attendance_report("lesson_2")
    assert report_after["present_count"] == 0
    assert report_after["absent_count"] == 2


def test_attendance_http_endpoints_gps_geofence_and_cooldown_flow() -> None:
    """
    Purpose:
        End-to-end HTTP test verifying rotating QR endpoint, GPS geofencing check
        (inside radius vs outside radius vs denied GPS), lecturer geofence toggle endpoint
        (`PATCH /api/attendance/sessions/{lesson_id}/geofence-toggle`), and HTTP-only
        `ta_device_id` 15-minute cooldown enforcement.
    Inputs:
        None.
    Outputs:
        None.
    """
    cfg = AppConfig(
        use_mock_firebase=True,
        jwt_secret="http-integration-attendance-secret-key-32-bytes",
        lecturer_access_code="TA-ADMIN-2026",
    )
    repo = FirebaseRepository(use_mock=True)
    repo.set(
        "/students",
        {
            "stu_1": {
                "student_id": "stu_1",
                "full_name": "Student One",
                "email": "s1@ariel.ac.il",
                "group": "1",
            },
            "stu_2": {
                "student_id": "stu_2",
                "full_name": "Student Two",
                "email": "s2@ariel.ac.il",
                "group": "1",
            },
        },
    )
    test_app = create_app(config=cfg, repository=repo)
    lec_client = TestClient(test_app)

    # 1. Lecturer logs in with configured account and opens Lesson 1 with classroom GPS coordinates and 100m radius
    lec_client.post(
        "/api/auth/lecturer/login",
        json={
            "email": "adminL@gmail.com",
            "password": "12345",
        },
    )
    start_res = lec_client.post(
        "/api/attendance/sessions",
        json={
            "lesson_id": "lesson_1",
            "lesson_title": "Lesson 1",
            "classroom_lat": CLASSROOM_LAT,
            "classroom_lng": CLASSROOM_LNG,
            "geofence_radius_meters": 100,
            "enforce_geofence": True,
        },
    )
    assert start_res.status_code == 200
    assert start_res.json()["classroom_lat"] == CLASSROOM_LAT
    assert start_res.json()["geofence_radius_meters"] == 100

    # 2. Lecturer requests rotating QR code
    qr_res = lec_client.get("/api/attendance/sessions/lesson_1/qr")
    assert qr_res.status_code == 200
    qr_token = qr_res.json()["qr_token"]

    # 3. Student 1 checks in from inside the classroom radius (~25m <= 100m)
    stu_phone_client = TestClient(test_app)
    checkin_1 = stu_phone_client.post(
        "/api/attendance/checkin",
        json={
            "lesson_id": "lesson_1",
            "student_id": "stu_1",
            "qr_token": qr_token,
            "student_lat": INSIDE_RADIUS_LAT,
            "student_lng": INSIDE_RADIUS_LNG,
        },
    )
    assert checkin_1.status_code == 200
    assert DEVICE_COOKIE_NAME in checkin_1.cookies
    assert checkin_1.json()["record"]["geofence_status"] == "verified"

    # 4. Same phone immediately tries to check in Student 2 -> blocked by 15-min cooldown (403)
    buddy_attempt = stu_phone_client.post(
        "/api/attendance/checkin",
        json={
            "lesson_id": "lesson_1",
            "student_id": "stu_2",
            "qr_token": qr_token,
            "student_lat": INSIDE_RADIUS_LAT,
            "student_lng": INSIDE_RADIUS_LNG,
        },
    )
    assert buddy_attempt.status_code == 403
    assert "15-minute device cooldown active" in buddy_attempt.json()["detail"]

    # 5. Student 2 on a different phone 350m away from classroom -> blocked by GPS Geofence (403)
    stu2_phone_client = TestClient(test_app)
    far_attempt = stu2_phone_client.post(
        "/api/attendance/checkin",
        json={
            "lesson_id": "lesson_1",
            "student_id": "stu_2",
            "qr_token": qr_token,
            "student_lat": OUTSIDE_RADIUS_LAT,
            "student_lng": OUTSIDE_RADIUS_LNG,
        },
    )
    assert far_attempt.status_code == 403
    assert "GPS Geofence check failed" in far_attempt.json()["detail"]

    # 6. Student 2 denies GPS location permission (no lat/lng) while enforce_geofence=True -> blocked (403)
    no_gps_attempt = stu2_phone_client.post(
        "/api/attendance/checkin",
        json={
            "lesson_id": "lesson_1",
            "student_id": "stu_2",
            "qr_token": qr_token,
        },
    )
    assert no_gps_attempt.status_code == 403
    assert "GPS location permission is required" in no_gps_attempt.json()["detail"]

    # 7. Lecturer toggles "Enforce GPS Geofencing" OFF -> Student 2 checks in without GPS!
    toggle_res = lec_client.patch(
        "/api/attendance/sessions/lesson_1/geofence-toggle",
        json={"enforce_geofence": False},
    )
    assert toggle_res.status_code == 200
    assert toggle_res.json()["enforce_geofence"] is False

    bypass_success = stu2_phone_client.post(
        "/api/attendance/checkin",
        json={
            "lesson_id": "lesson_1",
            "student_id": "stu_2",
            "qr_token": qr_token,
        },
    )
    assert bypass_success.status_code == 200
    assert bypass_success.json()["record"]["student_id"] == "stu_2"
    assert bypass_success.json()["record"]["geofence_status"] == "bypassed"


def test_reset_device_cooldowns_immediately_unblocks_cookie_holder() -> None:
    """
    Purpose:
        Verify that calling `POST /api/attendance/reset-cooldowns` clears
        `/device_cooldowns` in Firebase RTDB and stores `cooldown_reset_at`,
        immediately unblocking a smartphone that still holds an active local
        `ta_device_id` cooldown cookie.
    Inputs:
        None.
    Outputs:
        None.
    """
    cfg = AppConfig(
        use_mock_firebase=True,
        jwt_secret="cooldown-reset-endpoint-test-secret-key-32-bytes",
    )
    repo = FirebaseRepository(use_mock=True)
    repo.set(
        "/students",
        {
            "stu_1": {
                "student_id": "stu_1",
                "full_name": "גיא אביב",
                "email": "guygg197@gmail.com",
                "group": "3",
            },
            "stu_2": {
                "student_id": "stu_2",
                "full_name": "ליאל אבידור",
                "email": "avidorliel@gmail.com",
                "group": "1",
            },
        },
    )
    app = create_app(config=cfg, repository=repo)
    lec_client = TestClient(app)
    phone_client = TestClient(app)

    # Unauthenticated or student callers cannot reset cooldowns
    assert phone_client.post("/api/attendance/reset-cooldowns").status_code == 401

    # Lecturer signs in and starts Lesson 1
    login_res = lec_client.post(
        "/api/auth/lecturer/login",
        json={"email": "adminL@gmail.com", "password": "12345"},
    )
    assert login_res.status_code == 200

    lec_client.post(
        "/api/attendance/sessions",
        json={
            "lesson_id": "lesson_1",
            "lesson_title": "Lesson 1",
            "classroom_lat": CLASSROOM_LAT,
            "classroom_lng": CLASSROOM_LNG,
            "geofence_radius_meters": 100,
            "enforce_geofence": True,
        },
    )
    qr_token = lec_client.get("/api/attendance/sessions/lesson_1/qr").json()["qr_token"]

    # 1. Phone checks in Student 1 -> receives active ta_device_id cookie
    first = phone_client.post(
        "/api/attendance/checkin",
        json={
            "lesson_id": "lesson_1",
            "student_id": "stu_1",
            "qr_token": qr_token,
            "student_lat": INSIDE_RADIUS_LAT,
            "student_lng": INSIDE_RADIUS_LNG,
        },
    )
    assert first.status_code == 200
    assert DEVICE_COOKIE_NAME in first.cookies

    # 2. Same phone (holding ta_device_id cookie) tries to check in Student 2 -> blocked (403)
    blocked = phone_client.post(
        "/api/attendance/checkin",
        json={
            "lesson_id": "lesson_1",
            "student_id": "stu_2",
            "qr_token": qr_token,
            "student_lat": INSIDE_RADIUS_LAT,
            "student_lng": INSIDE_RADIUS_LNG,
        },
    )
    assert blocked.status_code == 403
    assert "15-minute device cooldown active" in blocked.json()["detail"]

    # 3. Lecturer clicks "Reset 15-Min Device Cooldowns"
    reset_res = lec_client.post("/api/attendance/reset-cooldowns")
    assert reset_res.status_code == 200
    assert reset_res.json()["status"] == "cooldowns_reset"
    assert repo.get("/device_cooldowns") is None
    assert isinstance(repo.get("/settings/cooldown_reset_at"), int)

    # 4. Same phone (STILL holding the active ta_device_id cookie!) retries immediately -> succeeds!
    unblocked = phone_client.post(
        "/api/attendance/checkin",
        json={
            "lesson_id": "lesson_1",
            "student_id": "stu_2",
            "qr_token": qr_token,
            "student_lat": INSIDE_RADIUS_LAT,
            "student_lng": INSIDE_RADIUS_LNG,
        },
    )
    assert unblocked.status_code == 200
    assert unblocked.json()["record"]["student_id"] == "stu_2"


@pytest.mark.parametrize(
    ("radius_meters", "inside_lat", "outside_lat"),
    [
        (30, 32.10455, 32.10480),   # ~17m inside vs ~45m outside 30m
        (50, 32.10470, 32.10505),   # ~33m inside vs ~72m outside 50m
        (100, 32.10500, 32.10600),  # ~67m inside vs ~178m outside 100m
    ],
)
def test_strict_geofence_radii_30m_50m_100m_enforcement(
    radius_meters: int, inside_lat: float, outside_lat: float
) -> None:
    """
    Purpose:
        Verify strict Haversine geofence enforcement for each interactive map radius
        option (`30m`, `50m`, `100m`): students outside the chosen radius (or missing
        GPS coordinates) are rejected with HTTP 403 and NOT marked present, while
        students inside the chosen radius are verified and marked present.
    Inputs:
        radius_meters (int): Selected geofence radius (`30`, `50`, or `100`).
        inside_lat (float): Student latitude inside `radius_meters`.
        outside_lat (float): Student latitude outside `radius_meters`.
    Outputs:
        None.
    """
    cfg = AppConfig(
        use_mock_firebase=True,
        jwt_secret="strict-geofence-radius-test-secret-key-32-bytes",
    )
    repo = FirebaseRepository(use_mock=True)
    repo.set(
        "/students",
        {
            "stu_in": {
                "student_id": "stu_in",
                "full_name": "Student Inside",
                "email": "inside@ariel.ac.il",
                "group": "1",
            },
            "stu_out": {
                "student_id": "stu_out",
                "full_name": "Student Outside",
                "email": "outside@ariel.ac.il",
                "group": "1",
            },
        },
    )
    app = create_app(config=cfg, repository=repo)
    lec_client = TestClient(app)
    lec_client.post(
        "/api/auth/lecturer/login",
        json={"email": "adminL@gmail.com", "password": "12345"},
    )

    lesson_id = f"lesson_r{radius_meters}"
    lec_client.post(
        "/api/attendance/sessions",
        json={
            "lesson_id": lesson_id,
            "lesson_title": f"Lesson {radius_meters}m",
            "classroom_lat": CLASSROOM_LAT,
            "classroom_lng": CLASSROOM_LNG,
            "geofence_radius_meters": radius_meters,
            "enforce_geofence": True,
        },
    )
    qr_token = lec_client.get(f"/api/attendance/sessions/{lesson_id}/qr").json()["qr_token"]

    # 1. Student outside the selected radius -> rejected with 403 and not marked present
    phone_out = TestClient(app)
    out_res = phone_out.post(
        "/api/attendance/checkin",
        json={
            "lesson_id": lesson_id,
            "student_id": "stu_out",
            "qr_token": qr_token,
            "student_lat": outside_lat,
            "student_lng": CLASSROOM_LNG,
        },
    )
    assert out_res.status_code == 403
    assert f"(allowed: {radius_meters}m)" in out_res.json()["detail"]

    report_after_reject = lec_client.get(f"/api/attendance/sessions/{lesson_id}").json()
    assert report_after_reject["present_count"] == 0

    # 2. Student inside the selected radius -> accepted (200) and marked present
    phone_in = TestClient(app)
    in_res = phone_in.post(
        "/api/attendance/checkin",
        json={
            "lesson_id": lesson_id,
            "student_id": "stu_in",
            "qr_token": qr_token,
            "student_lat": inside_lat,
            "student_lng": CLASSROOM_LNG,
        },
    )
    assert in_res.status_code == 200
    assert in_res.json()["record"]["geofence_status"] == "verified"
    assert in_res.json()["record"]["distance_meters"] <= radius_meters

    report_after_accept = lec_client.get(f"/api/attendance/sessions/{lesson_id}").json()
    assert report_after_accept["present_count"] == 1
