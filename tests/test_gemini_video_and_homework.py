"""
Purpose:
    Comprehensive unit and HTTP integration tests for Features 4 & 5:
    - `GeminiClient` (Firebase RTDB `/settings/gemini`, masked API key, free-text model IDs
      defaulting to `gemini-3.5-flash-lite`, support for both AI Studio `AIza...` and Vertex
      AI Express `AQ....` keys, strict error raising when API key is missing, Gemini Files
      API upload + status polling + cleanup for `AIza...` MP4s, inline `Part.from_bytes`
      for `AQ....` MP4s <= 20 MB, and direct `Part.from_uri` for YouTube URLs)
    - `VideoSummarizer` (public YouTube URL validation & normalization, strict `.mp4`
      extension/MIME/20 MB validation, structured teaching and delivery insights summary)
    - `HomeworkReviewer` (multi-format `.pdf`, `.docx`, `.txt`, `.py`, `.zip` extraction,
      Hebrew/sparse PDF resilience without 500, public GitHub repository URL zipball
      ingestion, per-student BYOK `X-Gemini-Api-Key` / `400` guard, Gated Release Rule
      before deadline / "End Submission Now", Concise Review Card + Dedicated Peer
      Comparison Column, and First-Submission Edge Case notice when `total_submissions == 1`).
Inputs:
    In-memory `FirebaseRepository`, test-only mock Gemini transports/SDK stubs, and `TestClient`.
Outputs:
    Deterministic pytest assertions verifying all happy paths and edge cases.
"""

from __future__ import annotations

import io
from pathlib import Path
import tempfile
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


def test_gemini_client_settings_masking_and_custom_model_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Purpose:
        Verify that `GeminiClient` defaults to `gemini-3.5-flash-lite`, persists custom
        free-text model IDs (e.g. `gemini-3.5-flash-lite`, `gemini-3.5-flash`, `gemini-2.5-flash`),
        masks both AI Studio (`AIza...`) and Vertex AI Express (`AQ....`) keys, selects
        `vertexai=True` strictly for `AQ.` keys, clears keys cleanly, and raises `RuntimeError`
        (never a fake fallback) when the API key is missing.
    Inputs:
        monkeypatch (pytest.MonkeyPatch): Pytest monkeypatch fixture.
    Outputs:
        None.
    """
    from google import genai

    cfg = AppConfig(use_mock_firebase=True, gemini_api_key="")
    repo = FirebaseRepository(use_mock=True)
    client = GeminiClient(repository=repo, config=cfg)

    initial = client.get_settings()
    assert initial["model_id"] == "gemini-3.5-flash-lite"
    assert initial["has_api_key"] is False
    assert initial["masked_api_key"] == ""

    # Missing API key without test transport MUST raise RuntimeError, never fake fallback content
    with pytest.raises(RuntimeError, match="Gemini API Key is missing"):
        client.generate_structured_json(prompt="Summarize lecture")

    # Supports AI Studio key + custom model IDs including gemini-3.5-flash
    updated = client.update_settings(
        api_key="AIzaSyTestSecretKey987654321",
        model_id="gemini-3.5-flash",
    )
    assert updated["model_id"] == "gemini-3.5-flash"
    assert updated["has_api_key"] is True
    assert updated["masked_api_key"] == "AIza...4321"
    assert "AIzaSyTestSecretKey987654321" not in str(updated)

    # Empty model_id must be rejected
    with pytest.raises(ValueError, match="cannot be empty"):
        client.update_settings(model_id="   ")

    # Supports Vertex AI Express (AQ....) keys & clearing stored key
    vertex_updated = client.update_settings(
        api_key="AQ.VertexExpressDemo987654321",
        model_id="gemini-3.5-flash-lite",
    )
    assert vertex_updated["has_api_key"] is True
    assert vertex_updated["masked_api_key"] == "AQ.V...4321"

    cleared = client.update_settings(api_key="")
    assert cleared["has_api_key"] is False
    assert cleared["masked_api_key"] == ""

    # Verify _build_genai_client routes AQ. to vertexai=True and AIza to Developer client
    client_calls: list[dict[str, Any]] = []

    class RecordingClient:
        def __init__(self, **kwargs: Any) -> None:
            client_calls.append(kwargs)

    monkeypatch.setattr(genai, "Client", RecordingClient)
    client._build_genai_client("AIzaSyTestKey123")
    client._build_genai_client("AQ.AbCdEf123456")
    assert client_calls[0]["api_key"] == "AIzaSyTestKey123"
    assert "vertexai" not in client_calls[0]
    assert client_calls[0]["http_options"].timeout == 75_000
    assert client_calls[1]["vertexai"] is True
    assert client_calls[1]["api_key"] == "AQ.AbCdEf123456"
    assert client_calls[1]["http_options"].timeout == 75_000


def test_gemini_files_api_large_mp4_upload_polling_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Purpose:
        Verify that `GeminiClient.generate_structured_json`:
        1) For AI Studio (`AIza...`), uploads MP4 payloads via `client.files.upload` using
           an ASCII-safe temp path (handling Hebrew filenames), polls `PROCESSING -> ACTIVE`,
           generates JSON, and cleans up remote + local temp files in `finally:`.
        2) For Vertex AI Express (`AQ....`), uses inline `Part.from_bytes` without calling `files.upload`.
        3) For YouTube URLs, passes `types.Part.from_uri(file_uri=..., mime_type="video/mp4")`.
    Inputs:
        monkeypatch (pytest.MonkeyPatch): Pytest monkeypatch fixture.
    Outputs:
        None.
    """
    from google import genai
    from google.genai import types

    events: dict[str, Any] = {
        "uploaded_temp_path": None,
        "poll_calls": 0,
        "deleted_remote_name": None,
        "model_called": None,
        "vertex_calls": [],
        "contents_seen": [],
    }

    class FakeState:
        def __init__(self, name: str) -> None:
            self.name = name

    class FakeUploadedFile:
        def __init__(self, name: str, state_name: str) -> None:
            self.name = name
            self.state = FakeState(state_name)

    class FakeFilesAPI:
        def __init__(self, is_vertex: bool) -> None:
            self._is_vertex = is_vertex

        def upload(self, *, file: str) -> FakeUploadedFile:
            if self._is_vertex:
                raise ValueError("This method is only supported in the Gemini Developer client.")
            events["uploaded_temp_path"] = file
            assert file.isascii()
            assert Path(file).is_file()
            # Must write inside OS temp directory (never inside watched project source dirs)
            assert Path(file).resolve().parent == Path(tempfile.gettempdir()).resolve()
            return FakeUploadedFile("files/lec_hebrew_123", "PROCESSING")

        def get(self, *, name: str) -> FakeUploadedFile:
            events["poll_calls"] += 1
            return FakeUploadedFile(name, "ACTIVE")

        def delete(self, *, name: str) -> None:
            events["deleted_remote_name"] = name

    class FakeModelsAPI:
        def generate_content(self, *, model: str, contents: list[Any], config: Any) -> Any:
            events["model_called"] = model
            events["contents_seen"].append(contents)

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
        def __init__(self, *, api_key: str, vertexai: bool = False) -> None:
            self.api_key = api_key
            self.vertexai = vertexai
            events["vertex_calls"].append(vertexai)
            self.files = FakeFilesAPI(vertexai)
            self.models = FakeModelsAPI()

    monkeypatch.setattr(genai, "Client", FakeGenAIClient)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    cfg = AppConfig(use_mock_firebase=True, gemini_api_key="AIzaSyLiveTestKey123456")
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
    assert summary["model_used"] == "gemini-3.5-flash-lite"
    assert summary["provider"] == "gemini_live"
    assert events["poll_calls"] == 1
    assert events["deleted_remote_name"] == "files/lec_hebrew_123"
    assert events["uploaded_temp_path"] is not None
    assert not Path(events["uploaded_temp_path"]).exists()

    # Vertex AI Express (AQ.) uses inline Part.from_bytes for MP4 <= 20 MB instead of files.upload()
    vertex_summary = summarizer.summarize_video(
        filename="vertex_clip.mp4",
        video_bytes=b"\x00\x00\x00\x18ftypmp42" + (b"x" * 1024),
        content_type="video/mp4",
        gemini_api_key="AQ.VertexExpressKey999",
    )
    assert vertex_summary["provider"] == "gemini_live"
    assert events["vertex_calls"][-1] is True

    # YouTube URL uses types.Part.from_uri with video/mp4 MIME type
    yt_summary = summarizer.summarize_video(
        youtube_url="https://youtu.be/dQw4w9WgXcQ",
        gemini_api_key="AQ.VertexExpressKey999",
    )
    assert yt_summary["source_type"] == "youtube"
    yt_first_part = events["contents_seen"][-1][0]
    assert isinstance(yt_first_part, types.Part)
    assert yt_first_part.file_data is not None
    assert "youtube.com/watch?v=dQw4w9WgXcQ" in (yt_first_part.file_data.file_uri or "")


def test_video_summarizer_strict_mp4_validation_and_structured_summary() -> None:
    """
    Purpose:
        Verify that `VideoSummarizer` strictly accepts `.mp4` files (<= 20 MB) or valid
        public YouTube URLs, rejects non-MP4 extensions/MIMEs, empty or > 20 MB files,
        and non-YouTube URLs, and produces structured delivery insights.
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

    # Edge case 3: Empty MP4 or > 20 MB MP4 rejected
    with pytest.raises(ValueError, match="empty"):
        summarizer.summarize_video("lecture.mp4", b"", "video/mp4")

    with pytest.raises(ValueError, match="20 MB"):
        summarizer.summarize_video(
            "huge_lecture.mp4", b"x" * (20 * 1024 * 1024 + 1), "video/mp4"
        )

    # Edge case 4: Invalid YouTube URL rejected & valid YouTube URLs normalized
    with pytest.raises(ValueError, match="Invalid YouTube URL"):
        summarizer.summarize_video(youtube_url="https://vimeo.com/123456")

    assert (
        VideoSummarizer.validate_youtube_url("https://youtu.be/abc123XYZ")
        == "https://www.youtube.com/watch?v=abc123XYZ"
    )
    assert (
        VideoSummarizer.validate_youtube_url("https://www.youtube.com/shorts/short99")
        == "https://www.youtube.com/watch?v=short99"
    )

    yt_res = summarizer.summarize_video(
        youtube_url="https://www.youtube.com/watch?v=abc123XYZ",
        lesson_tag="Lesson 3",
        lecturer_notes="Shortest Paths",
    )
    assert yt_res["source_type"] == "youtube"
    assert yt_res["youtube_url"] == "https://www.youtube.com/watch?v=abc123XYZ"
    assert yt_res["model_used"] == "gemini-3.5-flash-lite"

    # Happy case: Valid MP4 upload (<= 20 MB) produces structured summary & delivery insights
    fake_mp4 = b"\x00\x00\x00\x18ftypmp42" + (b"0" * 4096)
    summary = summarizer.summarize_video(
        filename="Ex2_OOP_Lecture.mp4",
        video_bytes=fake_mp4,
        content_type="video/mp4",
        lesson_tag="Lesson 2",
        lecturer_notes="Graph Algorithms",
    )
    assert summary["filename"] == "Ex2_OOP_Lecture.mp4"
    assert summary["source_type"] == "mp4"
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
    assert len(summarizer.list_summaries()) == 2


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

    # 5. Hebrew filename + sparse/scanned empty-text PDF & dotted student_id must never crash
    sparse_pdf = _build_minimal_pdf_bytes("")
    hebrew_res = reviewer.submit_homework_pdf(
        assignment_id=aid,
        student_id="stu.dotted.user@ariel.ac.il",
        student_name="עוז לוי",
        filename="תרגיל_2_אופטימיזציה.pdf",
        pdf_bytes=sparse_pdf,
        content_type="application/pdf",
        now_ts=now + 30,
    )
    assert hebrew_res["submission_type"] == "file:pdf"
    assert hebrew_res["student_id"] == "stu_dotted_user@ariel_ac_il"
    assert hebrew_res["page_count"] == 1
    assert hebrew_res["word_count"] >= 1
    stored_hebrew = repo.get(
        f"/homework_assignments/{aid}/submissions/stu_dotted_user@ariel_ac_il"
    )
    assert stored_hebrew["filename"] == "תרגיל_2_אופטימיזציה.pdf"
    assert "visual/scanned" in stored_hebrew["extracted_text"]


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
        End-to-end HTTP test verifying `/api/settings/gemini` (`gemini-3.5-flash-lite`
        & `AQ.` Vertex AI Express keys), `/api/video/summarize`,
        `/api/video/summarize-youtube`, `/api/homework/assignments`, per-student BYOK
        `X-Gemini-Api-Key` / `400` enforcement on `/submit`, `/submit-repo`, `/review`,
        `/end-now`, and static delivery of `/static/js/ai_tools.js`.
    Inputs:
        None.
    Outputs:
        None.
    """
    captured_transport_calls: list[dict[str, Any]] = []

    def tracking_transport(**kwargs: Any) -> dict[str, Any]:
        captured_transport_calls.append(kwargs)
        return _test_gemini_transport(**kwargs)

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
    app.state.gemini_client._transport = tracking_transport

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

    # 1. Lecturer updates Gemini settings with Vertex AI Express key & default model ID
    initial_settings = lec_client.get("/api/settings/gemini")
    assert initial_settings.status_code == 200
    assert initial_settings.json()["model_id"] == "gemini-3.5-flash-lite"

    put_res = lec_client.put(
        "/api/settings/gemini",
        json={"api_key": "AQ.LecturerVertexDemo1234", "model_id": "gemini-3.5-flash-lite"},
    )
    assert put_res.status_code == 200
    assert put_res.json()["has_api_key"] is True
    assert put_res.json()["masked_api_key"] == "AQ.L...1234"
    assert put_res.json()["model_id"] == "gemini-3.5-flash-lite"

    # Blank model ID is rejected with HTTP 400
    bad_model = lec_client.put(
        "/api/settings/gemini",
        json={"model_id": "   "},
    )
    assert bad_model.status_code == 400

    # 2. Lecturer uploads non-MP4 / >20MB -> 400; valid .mp4 & YouTube URL -> 200
    bad_vid = lec_client.post(
        "/api/video/summarize",
        files={"file": ("lecture.avi", b"1234", "video/x-msvideo")},
    )
    assert bad_vid.status_code == 400

    bad_yt = lec_client.post(
        "/api/video/summarize-youtube",
        json={"youtube_url": "https://example.com/not-youtube", "lesson_tag": "Lesson 1"},
    )
    assert bad_yt.status_code == 400

    ok_yt = lec_client.post(
        "/api/video/summarize-youtube",
        json={
            "youtube_url": "https://youtu.be/dQw4w9WgXcQ",
            "lesson_tag": "Lesson 1",
            "lecturer_notes": "Dynamic Programming",
        },
    )
    assert ok_yt.status_code == 200
    assert ok_yt.json()["source_type"] == "youtube"
    assert ok_yt.json()["model_used"] == "gemini-3.5-flash-lite"

    ok_vid = lec_client.post(
        "/api/video/summarize?lesson_tag=Lesson+1",
        files={"file": ("lecture1.mp4", b"\x00\x00\x00\x18ftypmp42data", "video/mp4")},
    )
    assert ok_vid.status_code == 200
    assert "delivery_insights" in ok_vid.json()
    vid_summary_id = ok_vid.json()["summary_id"]

    # 2b. Delete Video AI summary (lecturer-protected, 404 on unknown ID, 200 removes record)
    assert stu_client.delete(f"/api/video/summaries/{vid_summary_id}").status_code == 403
    assert lec_client.delete("/api/video/summaries/nonexistent_vid").status_code == 404
    del_vid = lec_client.delete(f"/api/video/summaries/{vid_summary_id}")
    assert del_vid.status_code == 200
    assert del_vid.json()["status"] == "deleted"
    remaining_ids = {s["summary_id"] for s in lec_client.get("/api/video/summaries").json()["summaries"]}
    assert vid_summary_id not in remaining_ids

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

    # 4a. Per-student BYOK enforcement: submitting WITHOUT X-Gemini-Api-Key must return 400
    # even though Lecturer has a key saved in /settings/gemini!
    valid_pdf = _build_minimal_pdf_bytes("Alice PDF submission content for Ex2.")
    missing_key_hw = stu_client.post(
        f"/api/homework/assignments/{aid}/submit",
        files={"file": ("alice_ex2.pdf", valid_pdf, "application/pdf")},
    )
    assert missing_key_hw.status_code == 400
    assert "Please enter your Gemini API Key" in missing_key_hw.json()["detail"]

    missing_key_repo = stu_client.post(
        f"/api/homework/assignments/{aid}/submit-repo",
        json={"repo_url": "https://github.com/owner/repo"},
    )
    assert missing_key_repo.status_code == 400
    assert "Please enter your Gemini API Key" in missing_key_repo.json()["detail"]

    # 4b. Student sends BYOK headers + unsupported .exe -> 400; valid .pdf -> 200 (gated)
    stu_byok_headers = {
        "X-Gemini-Api-Key": "AIzaSyStudentPersonalKey777",
        "X-Gemini-Model": "gemini-3.5-flash",
    }
    bad_hw = stu_client.post(
        f"/api/homework/assignments/{aid}/submit",
        headers=stu_byok_headers,
        files={"file": ("virus.exe", b"hello", "application/octet-stream")},
    )
    assert bad_hw.status_code == 400

    ok_hw = stu_client.post(
        f"/api/homework/assignments/{aid}/submit",
        headers=stu_byok_headers,
        files={"file": ("alice_ex2.pdf", valid_pdf, "application/pdf")},
    )
    assert ok_hw.status_code == 200
    assert ok_hw.json()["review_released"] is False
    assert ok_hw.json()["review"] is None
    assert captured_transport_calls[-1]["api_key"] == "AIzaSyStudentPersonalKey777"
    assert captured_transport_calls[-1]["model_id"] == "gemini-3.5-flash"

    # 5. Student checks review before deadline -> still gated
    gated_res = stu_client.get(
        f"/api/homework/assignments/{aid}/review",
        headers=stu_byok_headers,
    )
    assert gated_res.status_code == 200
    assert gated_res.json()["review_released"] is False

    # 6. Lecturer clicks "End Submission Now" -> unlocks review with First-Submission notice
    end_res = lec_client.post(f"/api/homework/assignments/{aid}/end-now")
    assert end_res.status_code == 200
    assert end_res.json()["review_released"] is True

    unlocked_res = stu_client.get(
        f"/api/homework/assignments/{aid}/review",
        headers=stu_byok_headers,
    )
    assert unlocked_res.status_code == 200
    unlocked_body = unlocked_res.json()
    assert unlocked_body["review_released"] is True
    assert unlocked_body["review"] is not None
    assert (
        unlocked_body["peer_comparison"]["notice"] == FIRST_SUBMISSION_NOTICE
    )

    # 7. Verify frontend ai_tools.js asset loads cleanly with free-text models, BYOK, & YouTube UI
    ai_js = lec_client.get("/static/js/ai_tools.js")
    assert ai_js.status_code == 200
    assert "javascript" in ai_js.headers.get("content-type", "")
    assert "renderVideoSummaryTab" in ai_js.text
    assert "renderHomeworkTab" in ai_js.text
    assert "gemini-3.5-flash-lite" in ai_js.text
    assert "Clear Key" in ai_js.text
    assert "ta_student_gemini_api_key" in ai_js.text
    assert "X-Gemini-Api-Key" in ai_js.text
    assert "X-Gemini-Model" in ai_js.text
    assert "/api/video/summarize-youtube" in ai_js.text
    assert "/api/video/summaries/" in ai_js.text
    assert "Submit Public Git Repo URL" in ai_js.text
    assert "__custom__" not in ai_js.text
