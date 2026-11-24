"""
Purpose:
    Comprehensive unit and HTTP integration tests for Features 4 & 5:
    - `GeminiClient` (Firebase RTDB `/settings/gemini`, masked API key, free-text model IDs
      defaulting to `gemini-3.5-flash-lite`, support for both AI Studio `AIza...` and `AQ....`
      keys via the standard Developer API, explicit `Clear Key` via `PUT`/`DELETE` without
      falling back to `.env`, strict error raising when API key is missing, YouTube
      `VideoMetadata(content_layers=...)` modes (`"transcript"`, `"audio"`, `"full_video"`)
      with automatic `TRANSCRIPT -> AUDIO` fallback, and automatic retry + model fallback on
      transient `503 UNAVAILABLE` / `429 RESOURCE_EXHAUSTED` spikes)
    - `VideoSummarizer` (YouTube-only public URL validation & canonicalization stripping
      `&list=...`/`&index=...` playlist params, rich academic summary schema including
      `detailed_overview`, `key_concepts`, `study_questions`, and backwards-compatible
      handling of older stored summaries)
    - `HomeworkReviewer` (multi-format `.pdf`, `.docx`, `.txt`, `.py`, `.zip` extraction,
      Hebrew/sparse PDF resilience without 500, public GitHub repository URL zipball
      ingestion, per-student BYOK `X-Gemini-Api-Key` / `400` guard, Gated Release Rule
      before deadline / "End Submission Now", regression test for `end_submission_now` on
      legacy Firebase records lacking an embedded `"assignment_id"` key, Concise Review Card +
      Dedicated Peer Comparison Column, and First-Submission Edge Case notice).
Inputs:
    In-memory `FirebaseRepository`, test-only mock Gemini transports/SDK stubs, and `TestClient`.
Outputs:
    Deterministic pytest assertions verifying all happy paths and edge cases.
"""

from __future__ import annotations

import io
import time
from typing import Any
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from src.config import AppConfig
from src.main import create_app
from src.repository.firebase_repository import FirebaseRepository
from src.services.gemini_client import GeminiClient, GeminiHighDemandError
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
        **kwargs (Any): `prompt`, `model_id`, `api_key`, `media_bytes`, `mime_type`, `file_uri`.
    Outputs:
        dict[str, Any]: Structured test JSON response matching the requested schema.
    """
    prompt = str(kwargs.get("prompt", ""))
    if "delivery_insights" in prompt:
        return {
            "executive_summary": "Analyzed lecture video covering graph algorithms and dynamic programming.",
            "detailed_overview": (
                "The lecture opened by motivating shortest-path problems on weighted directed graphs "
                "and contrasting unweighted Breadth-First Search (BFS) with Dijkstra's greedy relaxation "
                "strategy using a min-priority queue.\n\n"
                "In the second half, the lecturer walked through a concrete dry-run counterexample with "
                "negative edge weights and transitioned into Bellman-Ford and Dynamic Programming optimal "
                "substructure principles."
            ),
            "key_concepts": [
                {
                    "term": "Edge Relaxation",
                    "explanation": "Testing whether going through vertex u improves the shortest known distance to v.",
                },
                {
                    "term": "Optimal Substructure",
                    "explanation": "Subpaths of shortest paths are themselves shortest paths, enabling DP recurrences.",
                },
            ],
            "key_topics": ["Breadth-First Search", "Dijkstra Shortest Path", "Dynamic Programming"],
            "core_takeaways": [
                "BFS finds shortest paths in unweighted graphs in O(V + E).",
                "Dijkstra requires non-negative edge weights.",
                "Memoization avoids exponential recomputation.",
            ],
            "study_questions": [
                "Why does Dijkstra's algorithm fail when a graph contains negative-weight edges?",
                "How does memoization reduce time complexity in overlapping subproblems?",
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


def test_gemini_client_settings_masking_clear_key_and_custom_model_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Purpose:
        Verify that `GeminiClient` defaults to `gemini-3.5-flash-lite`, persists custom
        free-text model IDs, masks both `AIza...` and `AQ....` keys, always uses the
        standard Developer API client (never `vertexai=True` by default), genuinely clears
        the stored key (setting `has_key=False` / `has_api_key=False` and `masked_key=None`
        even if `.env` `gemini_api_key` was set), and raises `RuntimeError` when the API
        key is missing.
    Inputs:
        monkeypatch (pytest.MonkeyPatch): Pytest monkeypatch fixture.
    Outputs:
        None.
    """
    from google import genai

    # Even if an env fallback key exists on AppConfig, clearing the key in UI must NOT fall back to it!
    cfg = AppConfig(use_mock_firebase=True, gemini_api_key="AIzaEnvFallbackShouldNotStayActive")
    repo = FirebaseRepository(use_mock=True)
    client = GeminiClient(repository=repo, config=cfg)

    # Clear key explicitly -> must set has_key=False & masked_key=None (no .env fallback)
    cleared_initial = client.update_settings(api_key="")
    assert cleared_initial["has_api_key"] is False
    assert cleared_initial["has_key"] is False
    assert cleared_initial["masked_api_key"] == ""
    assert cleared_initial["masked_key"] is None

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
    assert updated["has_key"] is True
    assert updated["masked_api_key"] == "AIza...4321"
    assert updated["masked_key"] == "AIza...4321"
    assert "AIzaSyTestSecretKey987654321" not in str(updated)

    # Empty model_id must be rejected
    with pytest.raises(ValueError, match="cannot be empty"):
        client.update_settings(model_id="   ")

    # Supports AQ.... keys & clearing stored key
    aq_updated = client.update_settings(
        api_key="AQ.VertexExpressDemo987654321",
        model_id="gemini-3.5-flash-lite",
    )
    assert aq_updated["has_api_key"] is True
    assert aq_updated["has_key"] is True
    assert aq_updated["masked_api_key"] == "AQ.V...4321"

    cleared = client.update_settings(api_key="")
    assert cleared["has_api_key"] is False
    assert cleared["has_key"] is False
    assert cleared["masked_api_key"] == ""
    assert cleared["masked_key"] is None

    # Subsequent model_id-only update after clearing key must keep key cleared
    still_cleared = client.update_settings(model_id="gemini-2.5-flash")
    assert still_cleared["has_key"] is False
    assert still_cleared["model_id"] == "gemini-2.5-flash"

    # Verify _build_genai_client always uses standard Developer API (no vertexai=True)
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
    assert client_calls[1]["api_key"] == "AQ.AbCdEf123456"
    assert "vertexai" not in client_calls[1]
    assert client_calls[1]["http_options"].timeout == 75_000


def test_youtube_content_layers_modes_transcript_to_audio_fallback_and_503_model_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Purpose:
        Verify that `GeminiClient.generate_structured_json`:
        1) Uses `VideoMetadata(content_layers=["TRANSCRIPT"])` in `"transcript"` mode,
           `VideoMetadata(content_layers=["AUDIO"])` in `"audio"` mode, and
           `VideoMetadata(fps=0.2)` in `"full_video"` mode.
        2) Automatically falls back from `"transcript"` to `"audio"` when the video has no
           transcript or the `TRANSCRIPT` layer raises an error.
        3) Automatically retries once and falls back across `[selected_model, "gemini-3.5-flash",
           "gemini-2.5-flash", "gemini-3.5-flash-lite"]` on transient `503 UNAVAILABLE` / `429`
           errors, and raises `GeminiHighDemandError` when all models are overloaded.
    Inputs:
        monkeypatch (pytest.MonkeyPatch): Pytest monkeypatch fixture.
    Outputs:
        None.
    """
    from google import genai

    calls: list[dict[str, Any]] = []
    scenario = {"mode": "normal"}

    class FakeResp:
        text = (
            '{"executive_summary": "Recitation 5 on Algorithms 1 (BFS & DFS).", '
            '"detailed_overview": "Paragraph 1 explaining BFS queue traversal.\\n\\nParagraph 2 explaining DFS recursion.", '
            '"key_concepts": [{"term": "BFS", "explanation": "Level-order graph traversal using a FIFO queue."}], '
            '"key_topics": ["BFS", "DFS", "Topological Sort"], '
            '"core_takeaways": ["BFS uses a queue", "DFS uses a stack/recursion"], '
            '"study_questions": ["What is the time complexity of BFS?"], '
            '"action_items": ["Complete HW 5"], '
            '"delivery_insights": {"pacing": "Good", "clarity": "Clear", "audio_quality": "Good", "overall_rating": "Excellent"}}'
        )

    class FakeModelsAPI:
        def generate_content(self, *, model: str, contents: list[Any], config: Any) -> Any:
            first_part = contents[0]
            vm = getattr(first_part, "video_metadata", None)
            layers = getattr(vm, "content_layers", None) if vm else None
            fps_val = getattr(vm, "fps", None) if vm else None
            layer_names = [getattr(x, "value", str(x)) for x in layers] if layers else None
            calls.append(
                {
                    "model": model,
                    "layers": layer_names,
                    "fps": fps_val,
                    "file_uri": getattr(getattr(first_part, "file_data", None), "file_uri", None),
                }
            )

            if scenario["mode"] == "no_transcript" and layer_names == ["TRANSCRIPT"]:
                raise ValueError("400 INVALID_ARGUMENT: No transcript available for this video.")

            if scenario["mode"] == "primary_503_then_fallback_ok":
                if model == "gemini-3.5-flash-lite":
                    raise RuntimeError("503 UNAVAILABLE: The model is overloaded due to high demand.")
                return FakeResp()

            if scenario["mode"] == "all_503":
                raise RuntimeError("503 UNAVAILABLE: High demand across all models.")

            return FakeResp()

    class FakeGenAIClient:
        def __init__(self, *, api_key: str, http_options: Any = None) -> None:
            self.api_key = api_key
            self.models = FakeModelsAPI()

    monkeypatch.setattr(genai, "Client", FakeGenAIClient)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    cfg = AppConfig(use_mock_firebase=True, gemini_api_key="AIzaSyLiveTestKey123456")
    repo = FirebaseRepository(use_mock=True)
    gemini = GeminiClient(repository=repo, config=cfg)
    summarizer = VideoSummarizer(repository=repo, gemini_client=gemini)

    # 1. Default mode ("transcript") strips playlist params & passes content_layers=["TRANSCRIPT"]
    res_tx = summarizer.summarize_video(
        youtube_url="https://www.youtube.com/watch?v=8UY3LQI529o&list=PLW3u28&index=1",
        lesson_tag="Lesson 5",
    )
    assert res_tx["youtube_url"] == "https://www.youtube.com/watch?v=8UY3LQI529o"
    assert res_tx["video_mode"] == "transcript"
    assert calls[-1]["file_uri"] == "https://www.youtube.com/watch?v=8UY3LQI529o"
    assert calls[-1]["layers"] == ["TRANSCRIPT"]

    # 2. Explicit "audio" and "full_video" modes
    res_aud = summarizer.summarize_video(
        youtube_url="https://youtu.be/8UY3LQI529o",
        video_mode="audio",
    )
    assert res_aud["video_mode"] == "audio"
    assert calls[-1]["layers"] == ["AUDIO"]

    res_full = summarizer.summarize_video(
        youtube_url="https://youtu.be/8UY3LQI529o",
        video_mode="full_video",
    )
    assert res_full["video_mode"] == "full_video"
    assert calls[-1]["fps"] == 0.2

    # 3. Automatic fallback from TRANSCRIPT -> AUDIO when video has no transcript
    calls.clear()
    scenario["mode"] = "no_transcript"
    res_fallback_audio = summarizer.summarize_video(
        youtube_url="https://www.youtube.com/watch?v=8UY3LQI529o",
        video_mode="transcript",
    )
    assert [c["layers"] for c in calls] == [["TRANSCRIPT"], ["AUDIO"]]
    assert res_fallback_audio["video_mode"] == "audio"

    # 4. Automatic 503 retry + model fallback (gemini-3.5-flash-lite fails 2x -> gemini-3.5-flash succeeds)
    calls.clear()
    scenario["mode"] = "primary_503_then_fallback_ok"
    res_503_recovered = summarizer.summarize_video(
        youtube_url="https://www.youtube.com/watch?v=8UY3LQI529o",
        video_mode="audio",
    )
    assert [c["model"] for c in calls] == [
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash",
    ]
    assert res_503_recovered["model_used"] == "gemini-3.5-flash"

    # 5. When all candidate models return 503, raises GeminiHighDemandError with clear 503 detail
    calls.clear()
    scenario["mode"] = "all_503"
    with pytest.raises(GeminiHighDemandError, match="temporarily experiencing high demand"):
        summarizer.summarize_video(
            youtube_url="https://www.youtube.com/watch?v=8UY3LQI529o",
            video_mode="audio",
        )


def test_video_summarizer_youtube_canonicalization_rich_schema_and_backwards_compat() -> None:
    """
    Purpose:
        Verify that `VideoSummarizer`:
        1) Strictly validates YouTube URLs and strips playlist (`&list=...`, `&index=...`, `&t=...`)
           query parameters down to `https://www.youtube.com/watch?v=<video_id>`.
        2) Persists rich academic summary fields (`detailed_overview`, `key_concepts`, `study_questions`).
        3) Backwards-compatibly normalizes older saved summaries in Firebase RTDB that lack
           the new fields.
    Inputs:
        None.
    Outputs:
        None.
    """
    cfg = AppConfig(use_mock_firebase=True)
    repo = FirebaseRepository(use_mock=True)
    gemini = GeminiClient(repository=repo, config=cfg, transport=_test_gemini_transport)
    summarizer = VideoSummarizer(repository=repo, gemini_client=gemini)

    # Edge case 1: Empty or non-YouTube URLs rejected
    with pytest.raises(ValueError, match="YouTube URL is required"):
        summarizer.summarize_video(youtube_url="   ")
    with pytest.raises(ValueError, match="Invalid YouTube URL"):
        summarizer.summarize_video(youtube_url="https://vimeo.com/123456")

    # Canonicalization strips playlist & index query params
    assert (
        VideoSummarizer.validate_youtube_url(
            "https://www.youtube.com/watch?v=8UY3LQI529o&list=PLW3u28VuDAHJym6112ef3201&index=1"
        )
        == "https://www.youtube.com/watch?v=8UY3LQI529o"
    )
    assert (
        VideoSummarizer.validate_youtube_url("https://youtu.be/abc123XYZ?si=share99&t=45")
        == "https://www.youtube.com/watch?v=abc123XYZ"
    )
    assert (
        VideoSummarizer.validate_youtube_url("https://www.youtube.com/shorts/short99?feature=share")
        == "https://www.youtube.com/watch?v=short99"
    )

    yt_res = summarizer.summarize_video(
        youtube_url="https://www.youtube.com/watch?v=8UY3LQI529o&list=PL123&index=3",
        lesson_tag="Lesson 3",
        lecturer_notes="Shortest Paths",
    )
    assert yt_res["source_type"] == "youtube"
    assert yt_res["youtube_url"] == "https://www.youtube.com/watch?v=8UY3LQI529o"
    assert yt_res["model_used"] == "gemini-3.5-flash-lite"
    assert "Dijkstra" in yt_res["detailed_overview"]
    assert len(yt_res["key_concepts"]) == 2
    assert yt_res["key_concepts"][0]["term"] == "Edge Relaxation"
    assert len(yt_res["study_questions"]) == 2
    assert len(yt_res["key_topics"]) >= 3
    assert len(yt_res["core_takeaways"]) >= 3
    assert len(yt_res["action_items"]) >= 3

    # Backwards compatibility: insert an older summary record without new fields
    repo.set(
        "/video_summaries/vid_legacy_001",
        {
            "summary_id": "vid_legacy_001",
            "filename": "https://www.youtube.com/watch?v=legacy12345",
            "source_type": "youtube",
            "lesson_tag": "Lesson 1",
            "executive_summary": "Legacy summary record.",
            "key_topics": ["OOP"],
            "core_takeaways": ["Encapsulation"],
            "action_items": ["Read chapter 1"],
            "delivery_insights": {"overall_rating": "Good"},
            "created_at": 100,
        },
    )
    all_summaries = summarizer.list_summaries()
    assert len(all_summaries) == 2
    legacy = next(s for s in all_summaries if s["summary_id"] == "vid_legacy_001")
    assert legacy["detailed_overview"] == ""
    assert legacy["key_concepts"] == []
    assert legacy["study_questions"] == []
    assert legacy["video_mode"] == "transcript"


def test_homework_reviewer_gated_release_first_submission_and_legacy_end_now_regression() -> None:
    """
    Purpose:
        Verify `HomeworkReviewer` validation, `pypdf` text extraction,
        the Gated Release Rule (withholding review & peer comparison while submissions
        are open), the one-click "End Submission Now" unlock, the exact
        First-Submission Edge Case notice when only 1 student has submitted, and the
        regression fix ensuring `end_submission_now` and `list_assignments` never raise
        `KeyError: 'assignment_id'` on older Firebase assignment records lacking an
        embedded `"assignment_id"` field.
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

    # Step 4: First-Submission Edge Case — only 1 student submitted
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

    # Step 5: REGRESSION TEST — Legacy Firebase assignment dict WITHOUT embedded "assignment_id"
    legacy_aid = "hw_legacy_no_embedded_id"
    repo.set(
        f"/homework_assignments/{legacy_aid}",
        {
            "title": "Legacy Assignment Without Embedded ID",
            "description": "Created before assignment_id was embedded",
            "lesson_tag": "Lesson 1",
            "deadline_ts": now + 7200,
            "ended_manually": False,
            "created_at": now,
            "submissions": {
                "stu_alice": {
                    "filename": "alice.pdf",
                    "word_count": 42,
                    "submitted_at": now + 5,
                }
            },
        },
    )
    listed = reviewer.list_assignments(viewer_id="stu_alice", now_ts=now + 10)
    legacy_listed = next(a for a in listed if a["assignment_id"] == legacy_aid)
    assert legacy_listed["title"] == "Legacy Assignment Without Embedded ID"
    assert legacy_listed["viewer_has_submitted"] is True

    ended_legacy = reviewer.end_submission_now(legacy_aid, now_ts=now + 50)
    assert ended_legacy["assignment_id"] == legacy_aid
    assert ended_legacy["review_released"] is True
    assert ended_legacy["ended_manually"] is True
    assert repo.get(f"/homework_assignments/{legacy_aid}")["assignment_id"] == legacy_aid


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
        End-to-end HTTP test verifying `/api/settings/gemini` (`PUT`, `POST`, `DELETE`
        Clear Key), `/api/video/summarize-youtube` (YouTube-only, playlist URL stripping,
        rich summary fields, and verification that the old MP4 `/api/video/summarize`
        route is removed), `/api/homework/assignments`, per-student BYOK
        `X-Gemini-Api-Key` / `400` enforcement on `/submit`, `/submit-repo`, `/review`,
        `/end-now` (including legacy records without embedded `assignment_id`), and
        static delivery of `/static/js/ai_tools.js`.
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
        gemini_api_key="AIzaEnvKeyThatShouldClearCleanly",
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

    # 1. Lecturer updates Gemini settings & tests Clear Key (DELETE & PUT empty)
    put_res = lec_client.put(
        "/api/settings/gemini",
        json={"api_key": "AQ.LecturerVertexDemo1234", "model_id": "gemini-3.5-flash-lite"},
    )
    assert put_res.status_code == 200
    assert put_res.json()["has_api_key"] is True
    assert put_res.json()["has_key"] is True
    assert put_res.json()["masked_api_key"] == "AQ.L...1234"

    del_key_res = lec_client.delete("/api/settings/gemini")
    assert del_key_res.status_code == 200
    assert del_key_res.json()["has_api_key"] is False
    assert del_key_res.json()["has_key"] is False
    assert del_key_res.json()["masked_key"] is None

    get_after_clear = lec_client.get("/api/settings/gemini")
    assert get_after_clear.status_code == 200
    assert get_after_clear.json()["has_api_key"] is False
    assert get_after_clear.json()["has_key"] is False

    # Re-save key for subsequentLecturer tests
    lec_client.post(
        "/api/settings/gemini",
        json={"api_key": "AIzaLecturerActiveKey9999", "model_id": "gemini-3.5-flash-lite"},
    )

    # Blank model ID is rejected with HTTP 400
    bad_model = lec_client.put(
        "/api/settings/gemini",
        json={"model_id": "   "},
    )
    assert bad_model.status_code == 400

    # 2. Old MP4 endpoint /api/video/summarize is removed (404); YouTube endpoint works & canonicalizes URLs
    removed_mp4 = lec_client.post(
        "/api/video/summarize",
        files={"file": ("lecture1.mp4", b"\x00\x00\x00\x18ftypmp42data", "video/mp4")},
    )
    assert removed_mp4.status_code == 404

    bad_yt = lec_client.post(
        "/api/video/summarize-youtube",
        json={"youtube_url": "https://example.com/not-youtube", "lesson_tag": "Lesson 1"},
    )
    assert bad_yt.status_code == 400

    ok_yt = lec_client.post(
        "/api/video/summarize-youtube",
        json={
            "youtube_url": "https://www.youtube.com/watch?v=8UY3LQI529o&list=PLW3u28&index=1",
            "lesson_tag": "Lesson 1",
            "lecturer_notes": "Dynamic Programming",
            "video_mode": "transcript",
        },
    )
    assert ok_yt.status_code == 200
    yt_body = ok_yt.json()
    assert yt_body["source_type"] == "youtube"
    assert yt_body["youtube_url"] == "https://www.youtube.com/watch?v=8UY3LQI529o"
    assert yt_body["video_mode"] == "transcript"
    assert yt_body["model_used"] == "gemini-3.5-flash-lite"
    assert "detailed_overview" in yt_body and len(yt_body["detailed_overview"]) > 40
    assert len(yt_body["key_concepts"]) >= 1
    assert len(yt_body["study_questions"]) >= 1
    assert "delivery_insights" in yt_body
    vid_summary_id = yt_body["summary_id"]

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

    # 7. Verify frontend ai_tools.js asset loads cleanly with YouTube-only UI, rich sections, & Copy button
    ai_js = lec_client.get("/static/js/ai_tools.js")
    assert ai_js.status_code == 200
    assert "javascript" in ai_js.headers.get("content-type", "")
    assert "renderVideoSummaryTab" in ai_js.text
    assert "renderHomeworkTab" in ai_js.text
    assert "AI Lecture Video Summarizer" in ai_js.text
    assert "Fast (Transcript)" in ai_js.text
    assert "Audio Only" in ai_js.text
    assert "Full Video (Low FPS)" in ai_js.text
    assert "Lecture Overview & Walkthrough" in ai_js.text
    assert "Key Concepts & Definitions" in ai_js.text
    assert "Quick Review Questions" in ai_js.text
    assert "Summary copied to clipboard" in ai_js.text
    assert "Upload MP4 File" not in ai_js.text
    assert "__custom__" not in ai_js.text
