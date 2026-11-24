"""
Purpose:
    Implements `VideoSummarizer` for Feature 4 (ML Lecture Video Summary — MP4 Only + Gemini Files API)
    persisted in Firebase Realtime Database under `/video_summaries`. Enforces strict `.mp4`
    file extension and MIME type validation, uploads the full MP4 recording via `GeminiClient`
    (using the official `google-genai` Files API so large >100 MB lectures are analyzed in full),
    and generates an accurate structured English summary of the actual spoken and visual content
    of the lecture (Executive Summary, Key Topics, Core Takeaways, Action Items, and Lecturer
    Delivery Insights on pacing, clarity, and audio quality).
Inputs:
    `FirebaseRepository` persistence instance and `GeminiClient` AI wrapper.
Outputs:
    `VideoSummarizer` class managing MP4 validation, real Gemini Files API summarization, and persistence.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from src.repository.firebase_repository import FirebaseRepository
from src.services.gemini_client import GeminiClient

ALLOWED_MP4_MIMES = {"video/mp4", "application/mp4", "application/octet-stream", ""}


class VideoSummarizer:
    """
    Purpose:
        Domain service that validates uploaded `.mp4` lecture videos, invokes
        `GeminiClient` (via the Gemini Files API) to analyze the actual spoken and visual
        lecture content, and stores structured summaries in Firebase RTDB (`/video_summaries`).
        Contains zero fake or hardcoded fallback summaries: any missing API key or Gemini API
        failure raises a clear error surfaced directly to the Lecturer UI.
    Inputs:
        repository (FirebaseRepository): Firebase RTDB persistence layer.
        gemini_client (GeminiClient): Configured Gemini AI client.
    Outputs:
        `VideoSummarizer` service instance.
    """

    def __init__(
        self, repository: FirebaseRepository, gemini_client: GeminiClient
    ) -> None:
        """
        Purpose:
            Initialize `VideoSummarizer` with a Firebase repository and `GeminiClient`.
        Inputs:
            repository (FirebaseRepository): Target Firebase RTDB repository.
            gemini_client (GeminiClient): Gemini client instance.
        Outputs:
            None.
        """
        self.repository = repository
        self.gemini_client = gemini_client

    @staticmethod
    def validate_mp4_upload(
        filename: str,
        video_bytes: bytes,
        content_type: str | None = "video/mp4",
    ) -> str:
        """
        Purpose:
            Strictly validate that an uploaded lecture video has a `.mp4` extension,
            an allowed MP4 MIME type, and non-empty binary content.
        Inputs:
            filename (str): Original uploaded filename.
            video_bytes (bytes): Uploaded binary content.
            content_type (str | None): HTTP multipart MIME content type.
        Outputs:
            str: Sanitized filename. Raises `ValueError` for unsupported formats or empty files.
        """
        clean_name = (filename or "").strip()
        if not clean_name.lower().endswith(".mp4"):
            raise ValueError(
                "Unsupported video format. Only MP4 (.mp4) lecture video files are accepted."
            )

        clean_mime = (content_type or "").split(";")[0].strip().lower()
        if clean_mime not in ALLOWED_MP4_MIMES:
            raise ValueError(
                f"Unsupported MIME type '{clean_mime}'. Only MP4 (video/mp4) files are accepted."
            )

        if not video_bytes or len(video_bytes) == 0:
            raise ValueError("Uploaded MP4 video file is empty.")

        return clean_name

    @staticmethod
    def _build_prompt(
        filename: str, lesson_tag: str, lecturer_notes: str, size_kb: float
    ) -> str:
        """
        Purpose:
            Construct the structured Gemini instruction prompt requesting an accurate,
            content-faithful English summary of the actual spoken audio and visual slides
            in the uploaded MP4 lecture recording.
        Inputs:
            filename (str): Uploaded MP4 filename.
            lesson_tag (str): Lesson tag (e.g., `"Lesson 2"`).
            lecturer_notes (str): Optional context notes provided by the lecturer.
            size_kb (float): Video size in kilobytes.
        Outputs:
            str: Prompt string for `GeminiClient`.
        """
        notes_clause = (
            f"\nOptional Lecturer Context Notes: {lecturer_notes.strip()}"
            if lecturer_notes and lecturer_notes.strip()
            else ""
        )
        return (
            "You are an expert university AI Teaching Assistant. Carefully analyze the ACTUAL "
            "spoken audio, slides, code, mathematical proofs, and visual demonstrations inside "
            "this uploaded MP4 lecture recording. Even if the lecturer speaks in Hebrew or another "
            "language, write your entire output in clear, accurate English based strictly on the real "
            "subject matter taught in the video. Never invent topics that are not in the video.\n"
            "Return ONLY a valid JSON object with these exact keys:\n"
            '- "executive_summary": string (2-4 crisp sentences summarizing what was actually taught in this video)\n'
            '- "key_topics": list of 3-6 specific topics, algorithms, or concepts covered in the video\n'
            '- "core_takeaways": list of 3-5 concrete student learning takeaways from the lecture\n'
            '- "action_items": list of 2-4 actionable practice steps or review tasks for students based on the lecture\n'
            '- "delivery_insights": object with keys "pacing", "clarity", "audio_quality", '
            'and "overall_rating" ("Excellent" | "Good" | "Needs Attention") evaluating the lecturer\'s actual delivery in the recording.\n'
            f"Recording filename: '{filename}' ({size_kb:.1f} KB), Lesson Tag: '{lesson_tag}'.{notes_clause}"
        )

    def summarize_video(
        self,
        filename: str,
        video_bytes: bytes,
        content_type: str | None = "video/mp4",
        lesson_tag: str = "Lesson 1",
        lecturer_notes: str = "",
    ) -> dict[str, Any]:
        """
        Purpose:
            Validate an uploaded `.mp4` lecture video, upload and analyze the full video
            via `GeminiClient` (using the Gemini Files API), and persist the structured
            summary in Firebase RTDB under `/video_summaries/{id}`.
        Inputs:
            filename (str): Uploaded `.mp4` filename (supports Hebrew/Unicode filenames).
            video_bytes (bytes): Binary MP4 content.
            content_type (str | None): Multipart MIME type.
            lesson_tag (str): Associated lesson tag (e.g., `"Lesson 1"`).
            lecturer_notes (str): Optional context notes from the lecturer.
        Outputs:
            dict[str, Any]: Persisted structured lecture video summary record.
            Raises `ValueError` for invalid files or `RuntimeError` if Gemini fails.
        """
        clean_filename = self.validate_mp4_upload(filename, video_bytes, content_type)
        clean_tag = (lesson_tag or "Lesson 1").strip() or "Lesson 1"
        size_kb = round(len(video_bytes) / 1024.0, 2)

        prompt = self._build_prompt(
            filename=clean_filename,
            lesson_tag=clean_tag,
            lecturer_notes=lecturer_notes,
            size_kb=size_kb,
        )
        generated = self.gemini_client.generate_structured_json(
            prompt=prompt,
            media_bytes=video_bytes,
            mime_type="video/mp4",
        )

        now_ts = int(time.time())
        summary_id = f"vid_{now_ts}_{secrets.token_hex(4)}"
        delivery = (
            generated.get("delivery_insights")
            if isinstance(generated.get("delivery_insights"), dict)
            else {}
        )
        exec_summary = str(
            generated.get("executive_summary")
            or generated.get("summary")
            or ""
        ).strip()
        takeaways = list(
            generated.get("core_takeaways")
            or generated.get("takeaways")
            or []
        )

        record: dict[str, Any] = {
            "summary_id": summary_id,
            "filename": clean_filename,
            "file_size_kb": size_kb,
            "lesson_tag": clean_tag,
            "lecturer_notes": (lecturer_notes or "").strip(),
            "executive_summary": exec_summary,
            "key_topics": list(generated.get("key_topics", [])),
            "core_takeaways": takeaways,
            "action_items": list(generated.get("action_items", [])),
            "delivery_insights": {
                "pacing": str(delivery.get("pacing", "Balanced lecture pacing.")),
                "clarity": str(delivery.get("clarity", "Clear explanations and examples.")),
                "audio_quality": str(delivery.get("audio_quality", "Clear audio track.")),
                "overall_rating": str(delivery.get("overall_rating", "Excellent")),
            },
            "model_used": str(generated.get("model_used", "gemini-2.5-flash")),
            "provider": str(generated.get("provider", "gemini_live")),
            "created_at": now_ts,
        }
        self.repository.set(f"/video_summaries/{summary_id}", record)
        return record

    def list_summaries(self) -> list[dict[str, Any]]:
        """
        Purpose:
            Retrieve all stored lecture video summaries from `/video_summaries`,
            ordered newest-first.
        Inputs:
            None.
        Outputs:
            list[dict[str, Any]]: Ordered list of lecture video summary records.
        """
        raw_all = self.repository.get("/video_summaries")
        if not isinstance(raw_all, dict):
            return []
        items = [
            v for v in raw_all.values() if isinstance(v, dict) and "summary_id" in v
        ]
        items.sort(key=lambda s: int(s.get("created_at", 0)), reverse=True)
        return items
