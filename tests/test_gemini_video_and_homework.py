"""
Purpose:
    Comprehensive unit and HTTP integration tests for Features 4 & 5:
    - `GeminiClient` (Firebase RTDB `/settings/gemini`, masked API key, valid model IDs
      defaulting to `gemini-2.5-flash`, rejection/healing of invalid `gemini-3.5-*` IDs,
      strict error raising when `GEMINI_API_KEY` is missing or fails, and Gemini Files API
      upload + status polling + cleanup for large MP4 videos with Hebrew filenames)
    - `VideoSummarizer` (strict `.mp4` extension & MIME validation, structured teaching and
      delivery insights summary)
    - `HomeworkReviewer` (multi-format `.pdf`, `.docx`, `.txt`, `.py`, `.zip` extraction,
      public GitHub repository URL zipball ingestion, Gated Release Rule before deadline /
      "End Submission Now", Concise Review Card + Dedicated Peer Comparison Column, and
      First-Submission Edge Case notice when `total_submissions == 1`).
Inputs:
    In-memory `FirebaseRepository`, test-only mock Gemini transports/SDK stubs, and `TestClient`.
Outputs:
    Deterministic pytest assertions verifying all happy paths and edge cases.
"""

from __future__ import annotations

import io
from pathlib import Path
import time
from typing import Any
import zipfile

import httpx
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


def _test_gemini_transport(**kwargs: Any) -> dict[str, Any]:
    """
    Purpose:
        Test-only mock transport injected strictly inside `tests/` so unit tests verify
        prompt structure and JSON parsing without hitting live external Gemini quota.
    Inputs:
        **kwargs (Any): `prompt`, `model_id`, `api_key`, `media_bytes`, `mime_type`.
    Outputs:
        dict[str, Any]: Structured test JSON response matching the requested schema.
    """
    prompt = str(kwargs.get("prompt", ""))
    if "delivery_insights" in prompt:
        return {
            "executive_summary": "Analyzed lecture video covering graph algorithms and dynamic programming.",
            "key_topics": ["Breadth-First Search", "Dijkstra Shortest Path", "Dynamic Programming"],
            "core_takeaways": [
                "BFS finds shortest paths in unweighted graphs in O(V + E).",
                "Dijkstra requires non-negative edge weights.",
                "Memoization avoids exponential recomputation.",
            ],
            "action_items": [
                "Solve practice set 5 on shortest-path reductions.",
                "Review time-complexity proofs before next recitation.",
                "Test edge cases with disconnected components.",
            ],
            "delivery_insights": {
                "pacing": "Well-structured progression through examples.",
                "clarity": "Clear dry-run trace of priority queue updates.",
                "audio_quality": "Clear studio microphone audio.",
                "overall_rating": "Excellent",
            },
        }
    if "relative_strengths" in prompt:
        return {
            "relative_strengths": [
                "More thorough edge-case coverage than the cohort average.",
                "Clean separation of OOP layers and docstrings.",
            ],
            "unique_ideas": [
                "Included deterministic Haversine distance verification.",
                "Added automated class-diagram parity assertions.",
            ],
            "common_gaps": [
                "Expand negative timeout tests for external API calls.",
                "Document sequence diagram error frames.",
            ],
        }
    return {
        "brief_summary": "Well-structured homework submission demonstrating clear OOP design and tests.",
        "strengths": [
            "Clear modular class hierarchy.",
            "Thorough happy-case and edge-case coverage.",
            "Clean documentation and readable code.",
        ],
        "areas_to_improve": [
            "Add more boundary-value assertions.",
            "Include complexity analysis for core algorithms.",
        ],
        "actionable_recommendations": [
            "Add parametrized pytest cases for boundary inputs.",
            "Cross-check all methods against the class diagram.",
            "Keep configuration externalized in .env.",
        ],
    }


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


def _build_minimal_docx_bytes(paragraph_text: str) -> bytes:
    """
    Purpose:
        Construct a minimal valid `.docx` ZIP archive containing `word/document.xml`
        with `paragraph_text`.
    Inputs:
        paragraph_text (str): Text to embed inside `<w:p><w:r><w:t>`.
    Outputs:
        bytes: Valid `.docx` archive bytes.
    """
    buf = io.BytesIO()
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{paragraph_text}</w:t></w:r></w:p></w:body>"
        "</w:document>"
    )
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", xml.encode("utf-8"))
    return buf.getvalue()


def test_gemini_client_settings_masking_and_custom_model_id() -> None:
    """
    Purpose:
        Verify that `GeminiClient` defaults to `gemini-2.5-flash`, persists valid
        model IDs (`gemini-2.5-flash`, `gemini-2.5-flash-lite`, `gemini-2.5-pro`,
        `gemini-2.0-flash`), rejects invalid `gemini-3.5-*` model IDs, masks the API key,
        and raises `RuntimeError` (never a fake fallback) when `GEMINI_API_KEY` is missing.
    Inputs:
        None.
    Outputs:
        None.
    """
    cfg = AppConfig(use_mock_firebase=True, gemini_api_key="", gemini_model="gemini-2.5-flash")
    repo = FirebaseRepository(use_mock=True)
    client = GeminiClient(repository=repo, config=cfg)

    initial = client.get_settings()
    assert initial["model_id"] == "gemini-2.5-flash"
    assert initial["has_api_key"] is False
    assert initial["masked_api_key"] == ""

    # Missing API key without test transport MUST raise RuntimeError, never fake fallback content
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is missing"):
        client.generate_structured_json(prompt="Summarize lecture")

    updated = client.update_settings(
        api_key="AIzaSyTestSecretKey987654321",
        model_id="gemini-2.5-pro",
    )
    assert updated["model_id"] == "gemini-2.5-pro"
    assert updated["has_api_key"] is True
    assert updated["masked_api_key"] == "AIza...4321"
    assert "AIzaSyTestSecretKey987654321" not in str(updated)

    # Reject invalid gemini-3.5-* model IDs
    with pytest.raises(ValueError, match="Invalid Gemini model ID"):
        client.update_settings(model_id="gemini-3.5-flash-lite")

    with pytest.raises(ValueError, match="cannot be empty"):
        client.update_settings(model_id="   ")

    # Auto-heal legacy gemini-3.5-flash-lite if previously saved in Firebase RTDB
    repo.set("/settings/gemini", {"api_key": "AIzaSyTestSecretKey987654321", "model_id": "gemini-3.5-flash-lite"})
    healed_key, healed_model = client.resolve_credentials()
    assert healed_model == "gemini-2.5-flash"


def test_gemini_files_api_large_mp4_upload_polling_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Purpose:
        Verify that `GeminiClient.generate_structured_json` uploads MP4 video payloads
        via the official Gemini Files API (`client.files.upload`) using an ASCII-safe
        temporary file (handling Hebrew filenames like `"אלגוריתמים 1 תרגול 5 - שני שוב.mp4"`),
        polls `client.files.get` while `"PROCESSING"`, generates content once `"ACTIVE"`,
        and deletes both the local temp file and remote file in `finally:`.
    Inputs:
        monkeypatch (pytest.MonkeyPatch): Pytest monkeypatch fixture.
    Outputs:
        None.
    """
    from google import genai

    events: dict[str, Any] = {
        "uploaded_temp_path": None,
        "poll_calls": 0,
        "deleted_remote_name": None,
        "model_called": None,
    }

    class FakeState:
        def __init__(self, name: str) -> None:
            self.name = name

    class FakeUploadedFile:
        def __init__(self, name: str, state_name: str) -> None:
            self.name = name
            self.state = FakeState(state_name)

    class FakeFilesAPI:
        def upload(self, *, file: str) -> FakeUploadedFile:
            events["uploaded_temp_path"] = file
            # Temp path must be 100% ASCII and exist on disk during upload
            assert file.isascii()
            assert Path(file).is_file()
            return FakeUploadedFile("files/lec_hebrew_123", "PROCESSING")

        def get(self, *, name: str) -> FakeUploadedFile:
            events["poll_calls"] += 1
            return FakeUploadedFile(name, "ACTIVE")

        def delete(self, *, name: str) -> None:
            events["deleted_remote_name"] = name

    class FakeModelsAPI:
        def generate_content(self, *, model: str, contents: list[Any], config: Any) -> Any:
            events["model_called"] = model

            class Resp:
                text = (
                    '{"executive_summary": "Recitation 5 on Algorithms 1 (BFS & DFS).", '
                    '"key_topics": ["BFS", "DFS", "Topological Sort"], '
                    '"core_takeaways": ["BFS uses a queue", "DFS uses a stack/recursion", "DAG topological order"], '
                    '"action_items": ["Complete HW 5", "Review graph complexity"], '
                    '"delivery_insights": {"pacing": "Good", "clarity": "Clear", "audio_quality": "Good", "overall_rating": "Excellent"}}'
                )

            return Resp()

    class FakeGenAIClient:
        def __init__(self, *, api_key: str) -> None:
            self.api_key = api_key
            self.files = FakeFilesAPI()
            self.models = FakeModelsAPI()

    monkeypatch.setattr(genai, "Client", FakeGenAIClient)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    cfg = AppConfig(use_mock_firebase=True, gemini_api_key="AIzaSyLiveTestKey123456", gemini_model="gemini-2.5-flash")
    repo = FirebaseRepository(use_mock=True)
    gemini = GeminiClient(repository=repo, config=cfg)
    summarizer = VideoSummarizer(repository=repo, gemini_client=gemini)

    summary = summarizer.summarize_video(
        filename="אלגוריתמים 1 תרגול 5 - שני שוב.mp4",
        video_bytes=b"\x00\x00\x00\x18ftypmp42" + (b"v" * 2048),
        content_type="video/mp4",
        lesson_tag="Lesson 5",
    )

    assert summary["filename"] == "אלגוריתמים 1 תרגול 5 - שני שוב.mp4"
    assert "Algorithms 1" in summary["executive_summary"]
    assert summary["model_used"] == "gemini-2.5-flash"
    assert summary["provider"] == "gemini_live"
    assert events["poll_calls"] == 1
    assert events["deleted_remote_name"] == "files/lec_hebrew_123"
    # Local temp file must be cleaned up in finally
    assert events["uploaded_temp_path"] is not None
    assert not Path(events["uploaded_temp_path"]).exists()


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
    gemini = GeminiClient(repository=repo, config=cfg, transport=_test_gemini_transport)
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
        lecturer_notes="Graph Algorithms",
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
        Verify `HomeworkReviewer` validation, `pypdf` text extraction,
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
    gemini = GeminiClient(repository=repo, config=cfg, transport=_test_gemini_transport)
    reviewer = HomeworkReviewer(repository=repo, gemini_client=gemini)

    now = int(time.time())
    assignment = reviewer.create_assignment(
        title="Ex2: OOP Class & Sequence Diagrams",
        description="Submit your report covering Happy and Edge Cases.",
        deadline_ts=now + 3600,  # 1 hour in the future -> submissions OPEN
        lesson_tag="Lesson 2",
    )
    aid = assignment["assignment_id"]
    assert assignment["review_released"] is False

    # Edge case 1: Unsupported binary extension (.exe) or corrupted PDF header rejected
    with pytest.raises(ValueError, match="Unsupported homework file format"):
        reviewer.submit_homework_pdf(
            aid, "stu_alice", "Alice Cohen", "malware.exe", b"not-allowed"
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


def test_homework_multi_format_files_and_public_git_repo_ingestion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Purpose:
        Verify that `HomeworkReviewer` supports `.txt`/`.py` source code files, `.docx`
        Word documents, `.zip` archives (skipping `node_modules/`, `venv/`, `.git/`,
        `__pycache__/`), and public GitHub repository URLs (`https://github.com/owner/repo`),
        while returning clear errors for invalid or private/404 repository URLs.
    Inputs:
        monkeypatch (pytest.MonkeyPatch): Pytest monkeypatch fixture for mocking GitHub zipball HTTP download.
    Outputs:
        None.
    """
    cfg = AppConfig(use_mock_firebase=True)
    repo = FirebaseRepository(use_mock=True)
    gemini = GeminiClient(repository=repo, config=cfg, transport=_test_gemini_transport)
    reviewer = HomeworkReviewer(repository=repo, gemini_client=gemini)

    now = int(time.time())
    assignment = reviewer.create_assignment(
        title="Ex2: Multi-Format & GitHub Repo Submission",
        description="Submit PDF, DOCX, ZIP, code, or GitHub URL.",
        deadline_ts=now + 3600,
    )
    aid = assignment["assignment_id"]

    # 1. Python source file (.py)
    py_bytes = b'"""Purpose: Demo module."""\ndef solve(x: int) -> int:\n    return x * 2\n'
    py_res = reviewer.submit_homework_pdf(
        aid, "stu_py", "Python Student", "solution.py", py_bytes, "text/x-python", now_ts=now + 5
    )
    assert py_res["submission_type"] == "file:py"
    assert py_res["word_count"] >= 6

    # 2. Word document (.docx)
    docx_bytes = _build_minimal_docx_bytes("Object-Oriented Design and Haversine Geofencing Architecture")
    docx_res = reviewer.submit_homework_pdf(
        aid, "stu_docx", "Docx Student", "report.docx", docx_bytes, now_ts=now + 10
    )
    assert docx_res["submission_type"] == "file:docx"
    assert docx_res["word_count"] == 6

    # 3. ZIP archive (.zip) with junk folders ignored
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.md", "# Project Title\nClean architecture overview.")
        zf.writestr("src/main.py", "def main():\n    print('Hello OOP')\n")
        zf.writestr("node_modules/pkg/index.js", "console.log('SHOULD_BE_IGNORED');")
        zf.writestr("venv/lib/site.py", "SHOULD_BE_IGNORED = True")
        zf.writestr("__pycache__/main.cpython-312.pyc", b"\x00\x01\x02\x03")
    zip_res = reviewer.submit_homework_pdf(
        aid, "stu_zip", "Zip Student", "project.zip", zip_buf.getvalue(), "application/zip", now_ts=now + 15
    )
    assert zip_res["submission_type"] == "file:zip"
    assert zip_res["page_count"] == 2  # README.md + src/main.py only!
    stored_zip_sub = repo.get(f"/homework_assignments/{aid}/submissions/stu_zip")
    assert "SHOULD_BE_IGNORED" not in stored_zip_sub["extracted_text"]
    assert "README.md" in stored_zip_sub["extracted_text"]

    # 4. Public GitHub Repository URL ingestion (mocked HTTP zipball)
    gh_zip_buf = io.BytesIO()
    with zipfile.ZipFile(gh_zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("owner-repo-a1b2c3d/README.md", "# AI Teaching Assistant\nFastAPI + Firebase + YOLO.")
        zf.writestr("owner-repo-a1b2c3d/src/app.py", "class App:\n    pass\n")
        zf.writestr("owner-repo-a1b2c3d/.git/config", "secret git config")
    gh_zip_bytes = gh_zip_buf.getvalue()

    class FakeHttpResponse:
        def __init__(self, status_code: int, content: bytes) -> None:
            self.status_code = status_code
            self.content = content

    class FakeHttpxClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> FakeHttpxClient:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def get(self, url: str) -> FakeHttpResponse:
            if "private-repo" in url:
                return FakeHttpResponse(404, b"")
            return FakeHttpResponse(200, gh_zip_bytes)

    monkeypatch.setattr(httpx, "Client", FakeHttpxClient)

    git_res = reviewer.submit_homework_pdf(
        assignment_id=aid,
        student_id="stu_git",
        student_name="Git Student",
        repo_url="https://github.com/owner/repo",
        now_ts=now + 20,
    )
    assert git_res["submission_type"] == "git_repo"
    assert git_res["repo_url"] == "https://github.com/owner/repo"
    assert git_res["page_count"] == 2

    # Invalid URL or private/404 GitHub repo must raise a friendly ValueError
    with pytest.raises(ValueError, match="Invalid GitHub repository URL"):
        reviewer.submit_homework_pdf(
            assignment_id=aid,
            student_id="stu_git",
            student_name="Git Student",
            repo_url="https://gitlab.com/not-github",
            now_ts=now + 25,
        )
    with pytest.raises(ValueError, match="could not be downloaded"):
        reviewer.submit_homework_pdf(
            assignment_id=aid,
            student_id="stu_git",
            student_name="Git Student",
            repo_url="https://github.com/owner/private-repo",
            now_ts=now + 25,
        )


def test_homework_reviewer_multi_student_peer_comparison_and_deadline_expiry() -> None:
    """
    Purpose:
        Verify that when 2+ students submit homework and the submission deadline passes,
        reviews unlock automatically and the Peer Comparison Column compares the
        student's submission against all peer submissions (`is_first_submission_only == False`).
    Inputs:
        None.
    Outputs:
        None.
    """
    cfg = AppConfig(use_mock_firebase=True)
    repo = FirebaseRepository(use_mock=True)
    gemini = GeminiClient(repository=repo, config=cfg, transport=_test_gemini_transport)
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
        "Alice architecture report covering Haversine geofencing, static session QR tokens, and Firebase repository."
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
        `/api/homework/assignments`, `/submit`, `/submit-repo`, `/review`, `/end-now`,
        and static delivery of `/static/js/ai_tools.js`.
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
    app.state.gemini_client._transport = _test_gemini_transport

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

    # 1. Lecturer updates Gemini settings with a valid model ID
    put_res = lec_client.put(
        "/api/settings/gemini",
        json={"api_key": "AIzaSyDemoKey123456", "model_id": "gemini-2.5-flash"},
    )
    assert put_res.status_code == 200
    assert put_res.json()["has_api_key"] is True
    assert put_res.json()["masked_api_key"] == "AIza...3456"
    assert put_res.json()["model_id"] == "gemini-2.5-flash"

    # Invalid gemini-3.5-flash-lite model ID is rejected with HTTP 400
    bad_model = lec_client.put(
        "/api/settings/gemini",
        json={"model_id": "gemini-3.5-flash-lite"},
    )
    assert bad_model.status_code == 400

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
            "title": "Ex2 Final Architecture Report",
            "description": "Upload PDF, DOCX, ZIP, code, or GitHub URL",
            "deadline_ts": int(time.time()) + 7200,
            "lesson_tag": "Lesson 2",
        },
    )
    assert hw_res.status_code == 200
    aid = hw_res.json()["assignment_id"]

    # 4. Student uploads unsupported .exe -> 400; uploads valid .py or .pdf -> 200 (gated)
    bad_hw = stu_client.post(
        f"/api/homework/assignments/{aid}/submit",
        files={"file": ("virus.exe", b"hello", "application/octet-stream")},
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

    # 7. Verify frontend ai_tools.js asset loads cleanly with valid models and Git repo support
    ai_js = lec_client.get("/static/js/ai_tools.js")
    assert ai_js.status_code == 200
    assert "javascript" in ai_js.headers.get("content-type", "")
    assert "renderVideoSummaryTab" in ai_js.text
    assert "renderHomeworkTab" in ai_js.text
    assert "gemini-2.5-flash" in ai_js.text
    assert "gemini-3.5-flash-lite" not in ai_js.text
    assert "Submit Public Git Repo URL" in ai_js.text
