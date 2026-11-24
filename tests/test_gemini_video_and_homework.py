"""
Purpose:
    Comprehensive unit and HTTP integration tests for Checkpoint 8 (Issue #5):
    - `GeminiClient` (Firebase RTDB `/settings/gemini`, masked API key, configurable model ID
      defaulting to `gemini-2.5-flash-lite` and supporting custom IDs like `gemini-3.5-flash-lite`)
    - `VideoSummarizer` (strict `.mp4` extension & MIME validation, structured teaching and
      delivery insights summary)
    - `HomeworkReviewer` (strict `.pdf` validation & `pypdf` extraction, Gated Release Rule
      before deadline / "End Submission Now", Concise Review Card + Dedicated Peer Comparison
      Column, and First-Submission Edge Case notice when `total_submissions == 1`).
Inputs:
    In-memory `FirebaseRepository`, dynamically generated valid PDF streams, and `TestClient`.
Outputs:
    Deterministic pytest assertions verifying all happy paths and edge cases for Issue #5.
"""

from __future__ import annotations

import time
import pytest
from fastapi.testclient import TestClient

from src.config import AppConfig
from src.main import create_app
from src.repository.firebase_repository import FirebaseRepository
from src.services.gemini_client import GeminiClient
from src.services.homework_reviewer import (
    FIRST_SUBMISSION_NOTICE,
    HomeworkReviewer,
)
from src.services.video_summarizer import VideoSummarizer


def _build_minimal_pdf_bytes(text_content: str) -> bytes:
    """
    Purpose:
        Construct a standards-compliant, single-page PDF 1.4 binary document containing
        `text_content` so `pypdf.PdfReader` extracts the exact text deterministically.
    Inputs:
        text_content (str): ASCII text string to embed in the PDF page content stream.
    Outputs:
        bytes: Complete valid `%PDF-1.4` document bytes.
    """
    safe_text = (
        text_content.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    )
    stream_body = f"BT /F1 12 Tf 50 750 Td ({safe_text}) Tj ET".encode("latin-1")

    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n",
        f"4 0 obj\n<< /Length {len(stream_body)} >>\nstream\n".encode("latin-1")
        + stream_body
        + b"\nendstream\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf.extend(obj)

    xref_pos = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n".encode("latin-1"))
    pdf.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        pdf.extend(f"{off:010d} 00000 n \n".encode("latin-1"))

    pdf.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode(
            "latin-1"
        )
    )
    return bytes(pdf)


def test_gemini_client_settings_masking_and_custom_model_id() -> None:
    """
    Purpose:
        Verify that `GeminiClient` defaults to `gemini-2.5-flash-lite`, persists
        updated API keys and custom model IDs (e.g., `gemini-3.5-flash-lite`) in
        Firebase RTDB `/settings/gemini`, and masks the API key in public responses.
    Inputs:
        None.
    Outputs:
        None.
    """
    cfg = AppConfig(use_mock_firebase=True, gemini_api_key="", gemini_model="gemini-2.5-flash-lite")
    repo = FirebaseRepository(use_mock=True)
    client = GeminiClient(repository=repo, config=cfg)

    initial = client.get_settings()
    assert initial["model_id"] == "gemini-2.5-flash-lite"
    assert initial["has_api_key"] is False
    assert initial["masked_api_key"] == ""

    updated = client.update_settings(
        api_key="AIzaSyTestSecretKey987654321",
        model_id="gemini-3.5-flash-lite",
    )
    assert updated["model_id"] == "gemini-3.5-flash-lite"
    assert updated["has_api_key"] is True
    assert updated["masked_api_key"] == "AIza...4321"
    assert "AIzaSyTestSecretKey987654321" not in str(updated)

    with pytest.raises(ValueError, match="cannot be empty"):
        client.update_settings(model_id="   ")


def test_video_summarizer_strict_mp4_validation_and_structured_summary() -> None:
    """
    Purpose:
        Verify that `VideoSummarizer` strictly accepts `.mp4` files only, rejects
        non-MP4 extensions/MIMEs and empty files, and produces a concise structured
        teaching summary with Lecturer Delivery Insights (`pacing`, `clarity`, `audio_quality`).
    Inputs:
        None.
    Outputs:
        None.
    """
    cfg = AppConfig(use_mock_firebase=True)
    repo = FirebaseRepository(use_mock=True)
    gemini = GeminiClient(repository=repo, config=cfg)
    summarizer = VideoSummarizer(repository=repo, gemini_client=gemini)

    # Edge case 1: Non-MP4 file extensions rejected
    for bad_name in ("lecture.avi", "recording.mov", "zoom_summary.docx", "audio.mp3"):
        with pytest.raises(ValueError, match="Only MP4"):
            summarizer.summarize_video(bad_name, b"binary-content", "video/mp4")

    # Edge case 2: Non-MP4 MIME type rejected
    with pytest.raises(ValueError, match="Unsupported MIME type"):
        summarizer.summarize_video("lecture.mp4", b"binary-content", "video/quicktime")

    # Edge case 3: Empty MP4 file rejected
    with pytest.raises(ValueError, match="empty"):
        summarizer.summarize_video("lecture.mp4", b"", "video/mp4")

    # Happy case: Valid MP4 upload produces structured summary & delivery insights
    fake_mp4 = b"\x00\x00\x00\x18ftypmp42" + (b"0" * 4096)
    summary = summarizer.summarize_video(
        filename="Ex2_OOP_Lecture.mp4",
        video_bytes=fake_mp4,
        content_type="video/mp4",
        lesson_tag="Lesson 2",
        lecturer_notes="KISS Architecture & Edge Cases",
    )
    assert summary["filename"] == "Ex2_OOP_Lecture.mp4"
    assert summary["lesson_tag"] == "Lesson 2"
    assert len(summary["key_topics"]) >= 3
    assert len(summary["core_takeaways"]) >= 3
    assert len(summary["action_items"]) >= 3
    assert set(summary["delivery_insights"].keys()) == {
        "pacing",
        "clarity",
        "audio_quality",
        "overall_rating",
    }
    assert len(summarizer.list_summaries()) == 1


def test_homework_reviewer_gated_release_and_first_submission_edge_case() -> None:
    """
    Purpose:
        Verify `HomeworkReviewer` strict `.pdf` validation, `pypdf` text extraction,
        the Gated Release Rule (withholding review & peer comparison while submissions
        are open), the one-click "End Submission Now" unlock, and the exact
        First-Submission Edge Case notice when only 1 student has submitted.
    Inputs:
        None.
    Outputs:
        None.
    """
    cfg = AppConfig(use_mock_firebase=True)
    repo = FirebaseRepository(use_mock=True)
    gemini = GeminiClient(repository=repo, config=cfg)
    reviewer = HomeworkReviewer(repository=repo, gemini_client=gemini)

    now = int(time.time())
    assignment = reviewer.create_assignment(
        title="Ex2: OOP Class & Sequence Diagrams",
        description="Submit your PDF report covering Happy and Edge Cases.",
        deadline_ts=now + 3600,  # 1 hour in the future -> submissions OPEN
        lesson_tag="Lesson 2",
    )
    aid = assignment["assignment_id"]
    assert assignment["review_released"] is False

    # Edge case 1: Non-PDF extension or corrupted PDF header rejected
    with pytest.raises(ValueError, match="Only PDF"):
        reviewer.submit_homework_pdf(
            aid, "stu_alice", "Alice Cohen", "report.docx", b"not-a-pdf"
        )
    with pytest.raises(ValueError, match="Invalid or corrupted PDF"):
        reviewer.submit_homework_pdf(
            aid, "stu_alice", "Alice Cohen", "report.pdf", b"corrupted-bytes"
        )

    # Step 1: Alice uploads the FIRST valid PDF while submissions are open
    alice_pdf = _build_minimal_pdf_bytes(
        "Object-Oriented Class Diagram Sequence Diagram Firebase YOLO Geofencing Edge Cases Test Strategy"
    )
    receipt = reviewer.submit_homework_pdf(
        assignment_id=aid,
        student_id="stu_alice",
        student_name="Alice Cohen",
        filename="alice_ex2.pdf",
        pdf_bytes=alice_pdf,
        now_ts=now + 10,
    )
    assert receipt["page_count"] == 1
    assert receipt["word_count"] >= 10
    # Gated Release Rule: review and peer_comparison MUST be None while submissions are open
    assert receipt["review_released"] is False
    assert receipt["review"] is None
    assert receipt["peer_comparison"] is None

    # Querying review while still open must also withhold review and peer_comparison
    gated_view = reviewer.get_student_review_and_comparison(
        assignment_id=aid,
        student_id="stu_alice",
        viewer_role="student",
        now_ts=now + 20,
    )
    assert gated_view["review_released"] is False
    assert gated_view["review"] is None
    assert gated_view["peer_comparison"] is None

    # Step 2: Lecturer clicks "End Submission Now" -> unlocks review & peer comparison immediately
    ended = reviewer.end_submission_now(aid, now_ts=now + 30)
    assert ended["review_released"] is True
    assert ended["ended_manually"] is True

    # Step 3: Late upload after "End Submission Now" must be rejected with PermissionError
    with pytest.raises(PermissionError, match="Submissions are closed"):
        reviewer.submit_homework_pdf(
            aid, "stu_bob", "Bob Levi", "bob_late.pdf", alice_pdf, now_ts=now + 40
        )

    # Step 4: First-Submission Edge Case — only 1 student submitted, so peer_comparison
    # must display the exact FIRST_SUBMISSION_NOTICE
    unlocked_single = reviewer.get_student_review_and_comparison(
        assignment_id=aid,
        student_id="stu_alice",
        viewer_role="student",
        now_ts=now + 40,
    )
    assert unlocked_single["review_released"] is True
    assert unlocked_single["review"] is not None
    assert len(unlocked_single["review"]["strengths"]) >= 2
    assert unlocked_single["peer_comparison"]["is_first_submission_only"] is True
    assert unlocked_single["peer_comparison"]["total_submissions"] == 1
    assert unlocked_single["peer_comparison"]["notice"] == FIRST_SUBMISSION_NOTICE


def test_homework_reviewer_multi_student_peer_comparison_and_deadline_expiry() -> None:
    """
    Purpose:
        Verify that when 2+ students submit PDFs and the submission deadline passes,
        reviews unlock automatically and the Peer Comparison Column compares the
        student's PDF against all peer submissions (`is_first_submission_only == False`).
    Inputs:
        None.
    Outputs:
        None.
    """
    cfg = AppConfig(use_mock_firebase=True)
    repo = FirebaseRepository(use_mock=True)
    gemini = GeminiClient(repository=repo, config=cfg)
    reviewer = HomeworkReviewer(repository=repo, gemini_client=gemini)

    now = int(time.time())
    deadline = now + 600
    assignment = reviewer.create_assignment(
        title="Ex2: Deep Designing Peer Comparison",
        description="Compare architecture trade-offs.",
        deadline_ts=deadline,
    )
    aid = assignment["assignment_id"]

    pdf_alice = _build_minimal_pdf_bytes(
        "Alice architecture report covering Haversine geofencing, rotating QR tokens, and Firebase repository."
    )
    pdf_bob = _build_minimal_pdf_bytes(
        "Bob design submission discussing YOLO classroom headcount thresholds and pytest fixtures."
    )

    reviewer.submit_homework_pdf(
        aid, "stu_alice", "Alice Cohen", "alice.pdf", pdf_alice, now_ts=now + 10
    )
    reviewer.submit_homework_pdf(
        aid, "stu_bob", "Bob Levi", "bob.pdf", pdf_bob, now_ts=now + 20
    )

    # After deadline passes (now_ts = deadline + 5), reviews unlock automatically
    alice_unlocked = reviewer.get_student_review_and_comparison(
        assignment_id=aid,
        student_id="stu_alice",
        viewer_role="student",
        now_ts=deadline + 5,
    )
    assert alice_unlocked["review_released"] is True
    comp = alice_unlocked["peer_comparison"]
    assert comp["is_first_submission_only"] is False
    assert comp["total_submissions"] == 2
    assert comp["peer_count"] == 1
    assert len(comp["relative_strengths"]) >= 1
    assert len(comp["unique_ideas"]) >= 1
    assert len(comp["common_gaps"]) >= 1


def test_gemini_video_and_homework_http_endpoints_and_ui_assets() -> None:
    """
    Purpose:
        End-to-end HTTP test verifying `/api/settings/gemini`, `/api/video/summarize`,
        `/api/homework/assignments`, `/submit`, `/review`, `/end-now`, and static
        delivery of `/static/js/ai_tools.js`.
    Inputs:
        None.
    Outputs:
        None.
    """
    cfg = AppConfig(
        use_mock_firebase=True,
        jwt_secret="checkpoint-8-gemini-video-homework-secret-32-bytes",
        lecturer_access_code="TA-ADMIN-2026",
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
            }
        },
    )
    app = create_app(config=cfg, repository=repo)
    lec_client = TestClient(app)
    stu_client = TestClient(app)

    lec_client.post(
        "/api/auth/lecturer/login",
        json={
            "email": "adminL@gmail.com",
            "password": "12345",
        },
    )
    stu_client.post("/api/auth/student/login", json={"email": "alice@ariel.ac.il"})

    # 1. Lecturer updates Gemini settings
    put_res = lec_client.put(
        "/api/settings/gemini",
        json={"api_key": "AIzaSyDemoKey123456", "model_id": "gemini-2.5-flash-lite"},
    )
    assert put_res.status_code == 200
    assert put_res.json()["has_api_key"] is True
    assert put_res.json()["masked_api_key"] == "AIza...3456"

    # 2. Lecturer uploads non-MP4 -> 400; uploads valid .mp4 -> 200
    bad_vid = lec_client.post(
        "/api/video/summarize",
        files={"file": ("lecture.avi", b"1234", "video/x-msvideo")},
    )
    assert bad_vid.status_code == 400

    ok_vid = lec_client.post(
        "/api/video/summarize?lesson_tag=Lesson+1",
        files={"file": ("lecture1.mp4", b"\x00\x00\x00\x18ftypmp42data", "video/mp4")},
    )
    assert ok_vid.status_code == 200
    assert "delivery_insights" in ok_vid.json()

    # 3. Lecturer creates homework assignment
    hw_res = lec_client.post(
        "/api/homework/assignments",
        json={
            "title": "Ex2 Final Architecture PDF",
            "description": "Upload PDF only",
            "deadline_ts": int(time.time()) + 7200,
            "lesson_tag": "Lesson 2",
        },
    )
    assert hw_res.status_code == 200
    aid = hw_res.json()["assignment_id"]

    # 4. Student uploads non-PDF -> 400; uploads valid PDF -> 200 (gated)
    bad_hw = stu_client.post(
        f"/api/homework/assignments/{aid}/submit",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert bad_hw.status_code == 400

    valid_pdf = _build_minimal_pdf_bytes("Alice PDF submission content for Ex2.")
    ok_hw = stu_client.post(
        f"/api/homework/assignments/{aid}/submit",
        files={"file": ("alice_ex2.pdf", valid_pdf, "application/pdf")},
    )
    assert ok_hw.status_code == 200
    assert ok_hw.json()["review_released"] is False
    assert ok_hw.json()["review"] is None

    # 5. Student checks review before deadline -> still gated
    gated_res = stu_client.get(f"/api/homework/assignments/{aid}/review")
    assert gated_res.status_code == 200
    assert gated_res.json()["review_released"] is False

    # 6. Lecturer clicks "End Submission Now" -> unlocks review with First-Submission notice
    end_res = lec_client.post(f"/api/homework/assignments/{aid}/end-now")
    assert end_res.status_code == 200
    assert end_res.json()["review_released"] is True

    unlocked_res = stu_client.get(f"/api/homework/assignments/{aid}/review")
    assert unlocked_res.status_code == 200
    unlocked_body = unlocked_res.json()
    assert unlocked_body["review_released"] is True
    assert unlocked_body["review"] is not None
    assert (
        unlocked_body["peer_comparison"]["notice"] == FIRST_SUBMISSION_NOTICE
    )

    # 7. Verify frontend ai_tools.js asset loads cleanly
    ai_js = lec_client.get("/static/js/ai_tools.js")
    assert ai_js.status_code == 200
    assert "javascript" in ai_js.headers.get("content-type", "")
    assert "renderVideoSummaryTab" in ai_js.text
    assert "renderHomeworkTab" in ai_js.text
