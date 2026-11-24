"""
Purpose:
    Unit and real-world integration tests for `PhotoHeadCounter` (Issue #4):
    local Ultralytics YOLO person detection on `/assets/p3.jpeg`, annotated
    bounding-box image generation, and GREEN / ORANGE / RED confidence and
    discrepancy logic.
Inputs:
    Real classroom image `/assets/p3.jpeg`, in-memory `FirebaseRepository`, and `TestClient`.
Outputs:
    Deterministic pytest assertions verifying real YOLO detection metrics and all
    color-coded confidence states.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.config import AppConfig
from src.main import create_app
from src.repository.firebase_repository import FirebaseRepository
from src.services.photo_head_counter import PhotoHeadCounter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_CLASSROOM_PHOTO = PROJECT_ROOT / "assets" / "p3.jpeg"


def test_confidence_indicator_green_orange_red_thresholds() -> None:
    """
    Purpose:
        Verify that `PhotoHeadCounter.evaluate_confidence_indicator` classifies
        all GREEN (reliable), ORANGE (moderate uncertainty / back-row occlusion /
        2-3 student discrepancy), and RED (low confidence / large >=4 student
        discrepancy / 0 detections) cases accurately.
    Inputs:
        None.
    Outputs:
        None.
    """
    high_confs = [0.84, 0.81, 0.79, 0.76, 0.74, 0.71, 0.68, 0.65]

    # 1. GREEN: High confidence (avg >= 0.60, low_conf_ratio <= 0.25) and matching count (diff <= 1)
    green_res = PhotoHeadCounter.evaluate_confidence_indicator(
        detected_count=8,
        confidences=high_confs,
        registered_count=8,
    )
    assert green_res["status_color"] == "GREEN"
    assert green_res["discrepancy"] == 0

    # 2. ORANGE: Small count discrepancy (2-3 students) even when box confidence is high
    orange_diff = PhotoHeadCounter.evaluate_confidence_indicator(
        detected_count=8,
        confidences=high_confs,
        registered_count=6,
    )
    assert orange_diff["status_color"] == "ORANGE"
    assert orange_diff["abs_discrepancy"] == 2

    # 3. ORANGE: Back-row occlusion (>25% of boxes have confidence < 0.45)
    occluded_confs = [0.82, 0.78, 0.75, 0.70, 0.41, 0.38, 0.35]
    orange_occ = PhotoHeadCounter.evaluate_confidence_indicator(
        detected_count=7,
        confidences=occluded_confs,
        registered_count=7,
    )
    assert orange_occ["status_color"] == "ORANGE"
    assert orange_occ["low_confidence_count"] == 3

    # 4. RED: Large count discrepancy (>= 4 students difference)
    red_diff = PhotoHeadCounter.evaluate_confidence_indicator(
        detected_count=8,
        confidences=high_confs,
        registered_count=3,
    )
    assert red_diff["status_color"] == "RED"
    assert red_diff["abs_discrepancy"] == 5

    # 5. RED: Very low average detection confidence (< 0.45)
    red_low_conf = PhotoHeadCounter.evaluate_confidence_indicator(
        detected_count=4,
        confidences=[0.39, 0.36, 0.33, 0.31],
        registered_count=4,
    )
    assert red_low_conf["status_color"] == "RED"

    # 6. RED: Zero persons detected when students are registered
    red_zero = PhotoHeadCounter.evaluate_confidence_indicator(
        detected_count=0,
        confidences=[],
        registered_count=5,
    )
    assert red_zero["status_color"] == "RED"


def test_real_yolo_headcount_on_p3_jpeg_and_annotated_output(
    tmp_path: Path,
) -> None:
    """
    Purpose:
        Run a REAL local Ultralytics YOLO (`yolov8n.pt`) headcount test on
        `/assets/p3.jpeg`, verify real detection counts, confidence metrics,
        annotated JPEG output, Firebase RTDB persistence, and GREEN/ORANGE/RED comparisons.
    Inputs:
        tmp_path (Path): Pytest temporary directory for saving the annotated JPEG.
    Outputs:
        None.
    """
    cfg = AppConfig(
        use_mock_firebase=True,
        yolo_model_path="yolov8n.pt",
        yolo_confidence_threshold=0.30,
    )
    repo = FirebaseRepository(use_mock=True)
    counter = PhotoHeadCounter(repository=repo, config=cfg, imgsz=1280)

    annotated_out = tmp_path / "annotated_p3.jpg"
    report = counter.analyze_classroom_photo(
        image_source=REAL_CLASSROOM_PHOTO,
        lesson_id="lesson_1",
        registered_count=22,
        output_image_path=annotated_out,
    )

    # Real-life YOLOv8n (imgsz=1280, conf=0.30) detections on /assets/p3.jpeg:
    # 22 physical person detections across the lecture hall rows (avg_conf ~ 0.634)
    assert report["detected_count"] == 22
    assert report["registered_count"] == 22
    assert report["discrepancy"] == 0
    assert report["status_color"] == "GREEN"
    assert report["average_confidence"] >= 0.60
    assert report["high_confidence_count"] == 15
    assert report["low_confidence_count"] == 4
    assert report["image_width"] == 2000
    assert report["image_height"] == 1500
    assert len(report["detections"]) == 22
    assert report["annotated_image_data_url"].startswith("data:image/jpeg;base64,")
    assert annotated_out.is_file() and annotated_out.stat().st_size > 50_000

    # Verify Firebase RTDB persistence under /attendance_sessions/lesson_1/yolo_headcount
    stored_yolo = repo.get("/attendance_sessions/lesson_1/yolo_headcount")
    assert stored_yolo is not None
    assert stored_yolo["detected_count"] == 22
    assert stored_yolo["status_color"] == "GREEN"


def test_photo_head_counter_error_handling_and_http_endpoint() -> None:
    """
    Purpose:
        Verify `PhotoHeadCounter` input validation errors and test the
        Lecturer-protected `POST /api/attendance/headcount` HTTP endpoint.
    Inputs:
        None.
    Outputs:
        None.
    """
    cfg = AppConfig(
        use_mock_firebase=True,
        jwt_secret="photo-headcount-test-jwt-secret-key-32-bytes",
        lecturer_access_code="TA-ADMIN-2026",
    )
    repo = FirebaseRepository(use_mock=True)
    counter = PhotoHeadCounter(repository=repo, config=cfg)

    with pytest.raises(FileNotFoundError):
        counter.analyze_classroom_photo("assets/non_existent_classroom.jpg")

    with pytest.raises(ValueError, match="Invalid classroom image file"):
        counter.analyze_classroom_photo(b"not-a-valid-jpeg-binary")

    test_app = create_app(config=cfg, repository=repo)
    client = TestClient(test_app)

    # Register Lecturer
    client.post(
        "/api/auth/lecturer/register",
        json={
            "full_name": "Prof. Boaz",
            "email": "boaz@ariel.ac.il",
            "password": "StrongPassword2026",
            "access_code": "TA-ADMIN-2026",
        },
    )

    # Reject unsupported file extension (.txt)
    bad_upload = client.post(
        "/api/attendance/headcount",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert bad_upload.status_code == 400
    assert "Invalid photo file type" in bad_upload.json()["detail"]

    # Run real headcount via HTTP endpoint on /assets/p3.jpeg with registered_count=15 (RED discrepancy)
    ok_res = client.post(
        "/api/attendance/headcount",
        params={"use_default_asset": "true", "lesson_id": "lesson_1", "registered_count": 15},
    )
    assert ok_res.status_code == 200
    payload = ok_res.json()
    assert payload["detected_count"] == 22
    assert payload["registered_count"] == 15
    assert payload["abs_discrepancy"] == 7
    assert payload["status_color"] == "RED"
