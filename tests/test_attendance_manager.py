"""
Purpose:
    Comprehensive unit and HTTP integration tests for `AttendanceManager` (Issue #3):
    rotating signed QR tokens, Cloudflare/Render external IP same-network validation
    with Lecturer toggle, and Cookie + Firebase RTDB 15-minute device cooldown.
Inputs:
    In-memory `FirebaseRepository` instances, `AppConfig`, and FastAPI `TestClient`.
Outputs:
    Deterministic pytest assertions covering happy paths and edge cases for Feature 1.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.config import AppConfig
from src.main import create_app
from src.repository.firebase_repository import FirebaseRepository
from src.services.attendance_manager import (
    DEVICE_COOKIE_NAME,
    AttendanceManager,
)
from src.services.auth_service import AuthService


@pytest.fixture()
def attendance_env() -> tuple[AppConfig, FirebaseRepository, AttendanceManager]:
    """
    Purpose:
        Create an isolated in-memory `FirebaseRepository` seeded with two sample
        students (`stu_alice`, `stu_bob`) and an `AttendanceManager` instance.
    Inputs:
        None.
    Outputs:
        tuple[AppConfig, FirebaseRepository, AttendanceManager]: Test fixtures.
    """
    config = AppConfig(
        use_mock_firebase=True,
        jwt_secret="checkpoint-3-attendance-test-secret-key-32-bytes",
        qr_rotation_seconds=12,
        device_cooldown_minutes=15,
        enforce_same_network_default=True,
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


def test_rotating_qr_token_generation_and_expiration(
    attendance_env: tuple[AppConfig, FirebaseRepository, AttendanceManager]
) -> None:
    """
    Purpose:
        Verify that `generate_rotating_qr_token` produces unique, short-lived,
        cryptographically signed tokens and Base64 PNG QR images, and that
        `verify_qr_token` rejects expired, tampered, or wrong-lesson tokens.
    Inputs:
        attendance_env: Isolated attendance test fixture.
    Outputs:
        None.
    """
    config, _, manager = attendance_env
    manager.start_session(
        lesson_id="lesson_1",
        lesson_title="Lesson 1: OOD Principles",
        lecturer_ip="147.235.10.20",
    )

    qr1 = manager.generate_rotating_qr_token("lesson_1", base_url="https://ta.example.com")
    qr2 = manager.generate_rotating_qr_token("lesson_1", base_url="https://ta.example.com")

    assert qr1["qr_token"] != qr2["qr_token"]
    assert qr1["ttl_seconds"] == 12
    assert qr1["qr_image_data_url"].startswith("data:image/png;base64,")
    assert "lesson_id=lesson_1" in qr1["checkin_url"]

    claims = manager.verify_qr_token(qr1["qr_token"], expected_lesson_id="lesson_1")
    assert claims["lesson_id"] == "lesson_1"
    assert claims["type"] == "qr_attendance"

    # Edge case 1: Token used for a different lesson session is rejected
    with pytest.raises(ValueError, match="different lesson session"):
        manager.verify_qr_token(qr1["qr_token"], expected_lesson_id="lesson_2")

    # Edge case 2: Expired rotating QR token is rejected
    expired_qr = manager.generate_rotating_qr_token(
        "lesson_1", ttl_override_seconds=-5
    )
    with pytest.raises(ValueError, match="QR code has expired"):
        manager.verify_qr_token(expired_qr["qr_token"], expected_lesson_id="lesson_1")

    # Edge case 3: Non-QR JWT token is rejected
    auth_svc = AuthService(repository=manager.repository, config=config)
    regular_user_jwt = auth_svc.create_jwt_token(
        subject_id="stu_alice",
        role="student",
        email="alice@ariel.ac.il",
        full_name="Alice Cohen",
    )
    with pytest.raises(ValueError, match="Invalid token type"):
        manager.verify_qr_token(regular_user_jwt, expected_lesson_id="lesson_1")


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


def test_same_network_external_ip_validation_and_lecturer_toggle(
    attendance_env: tuple[AppConfig, FirebaseRepository, AttendanceManager]
) -> None:
    """
    Purpose:
        Verify External IP Same-Network Validation blocks students on different
        external IPs when `enforce_same_network=True`, and allows check-in when
        the Lecturer toggles `enforce_same_network=False` (cellular 4G/5G fallback).
    Inputs:
        attendance_env: Isolated attendance test fixture.
    Outputs:
        None.
    """
    _, _, manager = attendance_env
    classroom_ip = "84.110.45.200"
    cellular_ip = "31.168.99.14"

    manager.start_session(
        lesson_id="lesson_1",
        lesson_title="Lesson 1",
        lecturer_ip=classroom_ip,
        enforce_same_network=True,
    )
    qr = manager.generate_rotating_qr_token("lesson_1")

    # Happy case: Alice is on classroom Wi-Fi (matching external IP)
    res_alice = manager.submit_qr_checkin(
        lesson_id="lesson_1",
        student_id="stu_alice",
        qr_token=qr["qr_token"],
        student_ip=classroom_ip,
    )
    assert res_alice["record"]["status"] == "present"
    assert res_alice["record"]["student_ip"] == classroom_ip

    # Edge case: Bob is on cellular 4G/5G (different external IP) while enforcement is ON
    with pytest.raises(PermissionError, match="Same-Network IP check failed"):
        manager.submit_qr_checkin(
            lesson_id="lesson_1",
            student_id="stu_bob",
            qr_token=qr["qr_token"],
            student_ip=cellular_ip,
        )

    # Lecturer disables "Enforce Same-Network IP Check" via toggle
    updated_session = manager.set_same_network_enforcement(
        lesson_id="lesson_1", enforce_same_network=False
    )
    assert updated_session["enforce_same_network"] is False

    # Now Bob can check in from his cellular IP on his own phone
    res_bob = manager.submit_qr_checkin(
        lesson_id="lesson_1",
        student_id="stu_bob",
        qr_token=qr["qr_token"],
        student_ip=cellular_ip,
    )
    assert res_bob["record"]["status"] == "present"
    assert res_bob["record"]["student_ip"] == cellular_ip


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
    import time

    _, repo, manager = attendance_env
    classroom_ip = "147.235.1.1"
    base_time = int(time.time())

    manager.start_session(
        lesson_id="lesson_1",
        lesson_title="Lesson 1",
        lecturer_ip=classroom_ip,
    )
    qr = manager.generate_rotating_qr_token(
        "lesson_1", now_ts=base_time, ttl_override_seconds=3600
    )

    # Step 1: Alice checks in on her smartphone at t = base_time
    first_checkin = manager.submit_qr_checkin(
        lesson_id="lesson_1",
        student_id="stu_alice",
        qr_token=qr["qr_token"],
        student_ip=classroom_ip,
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
            student_ip=classroom_ip,
            device_cookie_value=signed_cookie,
            now_ts=base_time + 180,
        )

    # Step 3: After 15 minutes (901s elapsed), cooldown expires and check-in is allowed
    after_cooldown = manager.submit_qr_checkin(
        lesson_id="lesson_1",
        student_id="stu_bob",
        qr_token=qr["qr_token"],
        student_ip=classroom_ip,
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
    manager.start_session("lesson_2", "Lesson 2", lecturer_ip="127.0.0.1")

    manager.set_manual_attendance("lesson_2", "stu_alice", "present")
    report = manager.get_lesson_attendance_report("lesson_2")
    assert report["present_count"] == 1
    assert report["absent_count"] == 1
    assert report["total_students"] == 2

    manager.set_manual_attendance("lesson_2", "stu_alice", "absent")
    report_after = manager.get_lesson_attendance_report("lesson_2")
    assert report_after["present_count"] == 0
    assert report_after["absent_count"] == 2


def test_attendance_http_endpoints_cloudflare_and_cooldown_flow() -> None:
    """
    Purpose:
        End-to-end HTTP test verifying rotating QR endpoint, `CF-Connecting-IP`
        and `X-Forwarded-For` IP matching, lecturer network toggle endpoint,
        and HTTP-only `ta_device_id` 15-minute cooldown enforcement.
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

    # 1. Lecturer registers behind Cloudflare Tunnel IP 212.179.55.10
    lec_client.post(
        "/api/auth/lecturer/register",
        json={
            "full_name": "Prof. Boaz",
            "email": "boaz@ariel.ac.il",
            "password": "StrongPassword123",
            "access_code": "TA-ADMIN-2026",
        },
    )
    start_res = lec_client.post(
        "/api/attendance/sessions",
        json={"lesson_id": "lesson_1", "lesson_title": "Lesson 1"},
        headers={"CF-Connecting-IP": "212.179.55.10"},
    )
    assert start_res.status_code == 200
    assert start_res.json()["lecturer_ip"] == "212.179.55.10"

    # 2. Lecturer requests rotating QR code
    qr_res = lec_client.get("/api/attendance/sessions/lesson_1/qr")
    assert qr_res.status_code == 200
    qr_token = qr_res.json()["qr_token"]

    # 3. Student 1 checks in from the same classroom network IP
    stu_phone_client = TestClient(test_app)
    checkin_1 = stu_phone_client.post(
        "/api/attendance/checkin",
        json={"lesson_id": "lesson_1", "student_id": "stu_1", "qr_token": qr_token},
        headers={"CF-Connecting-IP": "212.179.55.10"},
    )
    assert checkin_1.status_code == 200
    assert DEVICE_COOKIE_NAME in checkin_1.cookies

    # 4. Same phone immediately tries to check in Student 2 -> blocked by 15-min cooldown (403)
    buddy_attempt = stu_phone_client.post(
        "/api/attendance/checkin",
        json={"lesson_id": "lesson_1", "student_id": "stu_2", "qr_token": qr_token},
        headers={"CF-Connecting-IP": "212.179.55.10"},
    )
    assert buddy_attempt.status_code == 403
    assert "15-minute device cooldown active" in buddy_attempt.json()["detail"]

    # 5. Student 2 on a different phone over cellular network (X-Forwarded-For) -> blocked by IP check (403)
    stu2_phone_client = TestClient(test_app)
    cell_attempt = stu2_phone_client.post(
        "/api/attendance/checkin",
        json={"lesson_id": "lesson_1", "student_id": "stu_2", "qr_token": qr_token},
        headers={"X-Forwarded-For": "185.120.10.99, 10.0.0.1"},
    )
    assert cell_attempt.status_code == 403
    assert "Same-Network IP check failed" in cell_attempt.json()["detail"]

    # 6. Lecturer toggles Same-Network IP Check OFF -> Student 2 checks in over cellular!
    toggle_res = lec_client.patch(
        "/api/attendance/sessions/lesson_1/network-toggle",
        json={"enforce_same_network": False},
        headers={"CF-Connecting-IP": "212.179.55.10"},
    )
    assert toggle_res.status_code == 200
    assert toggle_res.json()["enforce_same_network"] is False

    cell_success = stu2_phone_client.post(
        "/api/attendance/checkin",
        json={"lesson_id": "lesson_1", "student_id": "stu_2", "qr_token": qr_token},
        headers={"X-Forwarded-For": "185.120.10.99, 10.0.0.1"},
    )
    assert cell_success.status_code == 200
    assert cell_success.json()["record"]["student_id"] == "stu_2"
