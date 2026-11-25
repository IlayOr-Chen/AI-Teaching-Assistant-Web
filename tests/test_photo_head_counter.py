"""
Purpose:
    Unit and HTTP integration tests for `PhotoHeadCounter` (Issue #4 / #15):
    YOLO person detection on uploaded classroom images (zero dependency on demo assets
    like `p3.jpeg`), annotated bounding-box persistence in Firebase RTDB (no `null`),
    `DELETE /api/attendance/headcount` (Reset YOLO), and GREEN / ORANGE / RED
    confidence & discrepancy logic.
Inputs:
    In-memory generated RGB JPEG bytes, deterministic YOLO model double,
    in-memory `FirebaseRepository`, and `TestClient`.
Outputs:
    Deterministic pytest assertions verifying YOLO headcount metrics, persistence,
    reset cleanup, and all color-coded confidence states.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image
import pytest
from fastapi.testclient import TestClient

from src.config import AppConfig
from src.main import create_app
from src.repository.firebase_repository import FirebaseRepository
from src.services.photo_head_counter import PhotoHeadCounter


def _build_synthetic_classroom_jpeg(width: int = 1200, height: int = 900) -> bytes:
    """
    Purpose:
        Generate valid in-memory RGB JPEG bytes simulating an uploaded classroom
        photo so tests never depend on external demo assets.
    Inputs:
        width (int): Image width in pixels.
        height (int): Image height in pixels.
    Outputs:
        bytes: Valid encoded JPEG binary payload.
    """
    img = Image.new("RGB", (width, height), color=(30, 41, 59))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


class _FakeYoloBox:
    """
    Purpose:
        Minimal Ultralytics YOLO detection box double for deterministic unit testing.
    Inputs:
        cls_id (int), conf (float), xyxy (list[float]).
    Outputs:
        `_FakeYoloBox` instance.
    """

    def __init__(self, cls_id: int, conf: float, xyxy: list[float]) -> None:
        self.cls = [cls_id]
        self.conf = [conf]
        self.xyxy = [xyxy]


class _FakeYoloResult:
    """
    Purpose:
        Minimal Ultralytics YOLO result container exposing `.boxes`.
    Inputs:
        boxes (list[_FakeYoloBox]).
    Outputs:
        `_FakeYoloResult` instance.
    """

    def __init__(self, boxes: list[_FakeYoloBox]) -> None:
        self.boxes = boxes


class _FakeYoloModel:
    """
    Purpose:
        Deterministic YOLO predictor returning configured person detections.
    Inputs:
        confidences (list[float]): Confidence scores for synthetic person detections.
    Outputs:
        `_FakeYoloModel` instance.
    """

    def __init__(self, confidences: list[float]) -> None:
        self._confidences = confidences

    def predict(self, **_kwargs: Any) -> list[_FakeYoloResult]:
        boxes = [
            _FakeYoloBox(
                0,
                conf,
                [40.0 + idx * 80.0, 60.0, 100.0 + idx * 80.0, 220.0],
            )
            for idx, conf in enumerate(self._confidences)
        ]
        return [_FakeYoloResult(boxes)]


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


def test_real_yolo_headcount_on_uploaded_photo_persistence_and_reset(
    tmp_path: Path,
) -> None:
    """
    Purpose:
        Verify `PhotoHeadCounter` analyzes uploaded JPEG bytes, persists BOTH detection
        metrics AND the compact `annotated_image_data_url` in Firebase RTDB (no `null`
        on refresh), and clears everything cleanly on `reset_headcount`.
    Inputs:
        tmp_path (Path): Pytest temporary directory for optional disk output.
    Outputs:
        None.
    """
    cfg = AppConfig(
        use_mock_firebase=True,
        yolo_model_path="yolov8n.pt",
        yolo_confidence_threshold=0.30,
    )
    repo = FirebaseRepository(use_mock=True)
    fake_model = _FakeYoloModel([0.84, 0.79, 0.75, 0.68, 0.64, 0.39])
    counter = PhotoHeadCounter(
        repository=repo, config=cfg, yolo_model=fake_model, imgsz=1280
    )

    jpeg_bytes = _build_synthetic_classroom_jpeg(1600, 1200)
    annotated_out = tmp_path / "annotated_classroom.jpg"
    report = counter.analyze_classroom_photo(
        image_source=jpeg_bytes,
        lesson_id="lesson_1",
        registered_count=6,
        output_image_path=annotated_out,
    )

    assert report["detected_count"] == 6
    assert report["registered_count"] == 6
    assert report["discrepancy"] == 0
    assert report["status_color"] == "GREEN"
    assert report["image_width"] == 1600
    assert report["image_height"] == 1200
    assert len(report["detections"]) == 6
    assert report["annotated_image_data_url"].startswith("data:image/jpeg;base64,")
    assert annotated_out.is_file() and annotated_out.stat().st_size > 1_000

    # Annotated image MUST be persisted in Firebase RTDB so page reloads never show null
    stored_yolo = repo.get("/attendance_sessions/lesson_1/yolo_headcount")
    assert stored_yolo is not None
    assert stored_yolo["detected_count"] == 6
    assert stored_yolo["status_color"] == "GREEN"
    assert str(stored_yolo["annotated_image_data_url"]).startswith("data:image/jpeg;base64,")

    # Reset clears persisted count & annotated image cleanly
    reset_res = counter.reset_headcount("lesson_1")
    assert reset_res["status"] == "reset"
    assert repo.get("/attendance_sessions/lesson_1/yolo_headcount") is None


def test_photo_head_counter_error_handling_and_http_endpoint() -> None:
    """
    Purpose:
        Verify `PhotoHeadCounter` input validation errors and test the Lecturer-protected
        `POST /api/attendance/headcount`, `GET /api/attendance/sessions/{lesson_id}`, and
        `DELETE /api/attendance/headcount` (Reset YOLO) HTTP endpoints without `p3.jpeg`.
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
    test_app.state.photo_head_counter._model = _FakeYoloModel(
        [0.88, 0.85, 0.81, 0.77, 0.72, 0.69, 0.65, 0.62]
    )
    client = TestClient(test_app)

    # Log in Lecturer (case-insensitive demo account)
    client.post(
        "/api/auth/lecturer/login",
        json={
            "email": "adminL@gmail.com",
            "password": "12345",
        },
    )

    # Missing file upload must return 400 (no default p3.jpeg fallback)
    missing_upload = client.post("/api/attendance/headcount?lesson_id=lesson_1")
    assert missing_upload.status_code == 400

    # Reject unsupported file extension (.txt)
    bad_upload = client.post(
        "/api/attendance/headcount",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert bad_upload.status_code == 400
    assert "Invalid photo file type" in bad_upload.json()["detail"]

    # Upload valid classroom JPEG -> returns & persists annotated image + RED discrepancy (8 vs 3)
    classroom_jpg = _build_synthetic_classroom_jpeg(1000, 750)
    ok_res = client.post(
        "/api/attendance/headcount",
        params={"lesson_id": "lesson_1", "registered_count": 3},
        files={"file": ("classroom.jpg", classroom_jpg, "image/jpeg")},
    )
    assert ok_res.status_code == 200
    payload = ok_res.json()
    assert payload["detected_count"] == 8
    assert payload["registered_count"] == 3
    assert payload["abs_discrepancy"] == 5
    assert payload["status_color"] == "RED"
    assert payload["annotated_image_data_url"].startswith("data:image/jpeg;base64,")

    # Fetching session report later must still include persisted annotated image
    session_report = client.get("/api/attendance/sessions/lesson_1").json()
    persisted = session_report["session"]["yolo_headcount"]
    assert persisted is not None
    assert persisted["detected_count"] == 8
    assert persisted["annotated_image_data_url"].startswith("data:image/jpeg;base64,")

    # Reset YOLO clears count, badge, and persisted image for lesson_1
    reset_res = client.delete("/api/attendance/headcount", params={"lesson_id": "lesson_1"})
    assert reset_res.status_code == 200
    assert reset_res.json()["status"] == "reset"

    after_reset = client.get("/api/attendance/sessions/lesson_1").json()
    assert (after_reset.get("session") or {}).get("yolo_headcount") is None
