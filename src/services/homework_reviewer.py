"""
Purpose:
    Implements `HomeworkReviewer` for Feature 5 (Deep Homework Review & Peer Comparison
    — PDF Only + Gemini + Gated Release Rule) persisted in Firebase Realtime Database
    under `/homework_assignments`. Enforces:
    1) Strict `.pdf` file extension, MIME type, and `%PDF` header validation + `pypdf` text extraction,
    2) Gated Release Rule: students can upload/replace their PDF while submissions are open,
       but AI feedback and peer comparisons unlock ONLY AFTER the deadline passes OR the
       Lecturer clicks "End Submission Now",
    3) Concise Review & Summary Card + Dedicated Peer Comparison Column across all submitted PDFs,
    4) First-Submission Edge Case notice when only 1 student has submitted.
Inputs:
    `FirebaseRepository` persistence instance and `GeminiClient` AI wrapper.
Outputs:
    `HomeworkReviewer` class managing homework assignments, PDF submissions, and peer comparisons.
"""

from __future__ import annotations

import io
import re
import secrets
import time
from typing import Any

from pypdf import PdfReader

from src.repository.firebase_repository import FirebaseRepository
from src.services.gemini_client import GeminiClient

ALLOWED_PDF_MIMES = {"application/pdf", "application/x-pdf", "application/octet-stream", ""}

FIRST_SUBMISSION_NOTICE = (
    "This is the first submission received for this assignment, so peer comparison "
    "is not yet available. Comparative insights will appear once additional students submit."
)

GATED_RELEASE_NOTICE = (
    "Your PDF submission has been recorded. AI review and peer comparison are locked "
    "while submissions remain open and will unlock automatically once the deadline passes "
    "or the lecturer clicks 'End Submission Now'."
)


class HomeworkReviewer:
    """
    Purpose:
        Domain service managing homework assignments, strict PDF-only student submissions,
        deadline and manual "End Submission Now" release gating, individual Gemini AI reviews,
        and multi-document peer comparisons in Firebase RTDB (`/homework_assignments`).
    Inputs:
        repository (FirebaseRepository): Firebase RTDB persistence layer.
        gemini_client (GeminiClient): Configured Gemini AI client.
    Outputs:
        `HomeworkReviewer` service instance.
    """

    def __init__(
        self, repository: FirebaseRepository, gemini_client: GeminiClient
    ) -> None:
        """
        Purpose:
            Initialize `HomeworkReviewer` with a Firebase repository and `GeminiClient`.
        Inputs:
            repository (FirebaseRepository): Target Firebase RTDB repository.
            gemini_client (GeminiClient): Gemini client instance.
        Outputs:
            None.
        """
        self.repository = repository
        self.gemini_client = gemini_client

    @staticmethod
    def is_review_released(
        assignment: dict[str, Any], now_ts: int | None = None
    ) -> bool:
        """
        Purpose:
            Evaluate the Gated Release Rule: return True ONLY if the Lecturer clicked
            "End Submission Now" (`ended_manually=True`) OR the current timestamp has
            reached/passed `deadline_ts`.
        Inputs:
            assignment (dict[str, Any]): Homework assignment record from Firebase RTDB.
            now_ts (int | None): Optional current UNIX timestamp for deterministic testing.
        Outputs:
            bool: True when submissions are closed and reviews/comparisons are unlocked.
        """
        if bool(assignment.get("ended_manually", False)):
            return True
        current = int(now_ts) if now_ts is not None else int(time.time())
        deadline = int(assignment.get("deadline_ts", 0))
        return deadline > 0 and current >= deadline

    @staticmethod
    def validate_and_extract_pdf_text(
        filename: str,
        pdf_bytes: bytes,
        content_type: str | None = "application/pdf",
    ) -> dict[str, Any]:
        """
        Purpose:
            Strictly validate that an uploaded homework file is a genuine `.pdf`
            document and extract its text content and page/word statistics via `pypdf`.
        Inputs:
            filename (str): Uploaded filename.
            pdf_bytes (bytes): Raw uploaded file bytes.
            content_type (str | None): Multipart HTTP MIME type.
        Outputs:
            dict[str, Any]: Dictionary containing `filename`, `extracted_text`,
            `page_count`, and `word_count`. Raises `ValueError` for invalid formats.
        """
        clean_name = (filename or "").strip()
        if not clean_name.lower().endswith(".pdf"):
            raise ValueError(
                "Unsupported homework file format. Only PDF (.pdf) files are accepted."
            )

        clean_mime = (content_type or "").split(";")[0].strip().lower()
        if clean_mime not in ALLOWED_PDF_MIMES:
            raise ValueError(
                f"Unsupported MIME type '{clean_mime}'. Only PDF (application/pdf) files are accepted."
            )

        if not pdf_bytes or not pdf_bytes.lstrip().startswith(b"%PDF"):
            raise ValueError(
                "Invalid or corrupted PDF file. Please upload a valid PDF document."
            )

        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            page_count = len(reader.pages)
            page_texts: list[str] = []
            for page in reader.pages:
                txt = page.extract_text() or ""
                if txt.strip():
                    page_texts.append(txt.strip())
            extracted_text = "\n\n".join(page_texts).strip()
        except Exception as exc:
            raise ValueError(f"Failed to parse PDF document: {exc}") from exc

        words = re.findall(r"\S+", extracted_text)
        return {
            "filename": clean_name,
            "extracted_text": extracted_text,
            "page_count": max(1, page_count),
            "word_count": len(words),
        }

    def create_assignment(
        self,
        title: str,
        description: str,
        deadline_ts: int,
        lesson_tag: str = "Lesson 1",
    ) -> dict[str, Any]:
        """
        Purpose:
            Create a new homework assignment with a submission deadline in Firebase RTDB
            under `/homework_assignments/{assignment_id}`.
        Inputs:
            title (str): Assignment title (e.g., `"Ex2: OOP Architecture & Use Cases"`).
            description (str): Grading rubric or instructions.
            deadline_ts (int): Submission deadline as a UNIX timestamp in seconds.
            lesson_tag (str): Associated lesson tag.
        Outputs:
            dict[str, Any]: Persisted homework assignment metadata.
        """
        clean_title = (title or "").strip()
        if not clean_title:
            raise ValueError("Homework assignment title is required.")

        if int(deadline_ts) <= 0:
            raise ValueError("A valid submission deadline timestamp is required.")

        now_ts = int(time.time())
        assignment_id = f"hw_{now_ts}_{secrets.token_hex(4)}"
        record: dict[str, Any] = {
            "assignment_id": assignment_id,
            "title": clean_title,
            "description": (description or "").strip(),
            "lesson_tag": (lesson_tag or "Lesson 1").strip() or "Lesson 1",
            "deadline_ts": int(deadline_ts),
            "ended_manually": False,
            "ended_at": None,
            "created_at": now_ts,
            "submissions": {},
        }
        self.repository.set(f"/homework_assignments/{assignment_id}", record)
        return self._format_assignment_overview(record)

    def end_submission_now(
        self, assignment_id: str, now_ts: int | None = None
    ) -> dict[str, Any]:
        """
        Purpose:
            Lecturer one-click "End Submission Now" action: immediately closes
            submissions on `assignment_id` and unlocks AI reviews and peer comparisons.
        Inputs:
            assignment_id (str): Target homework assignment ID.
            now_ts (int | None): Optional UNIX timestamp override.
        Outputs:
            dict[str, Any]: Updated assignment overview dictionary (`review_released=True`).
        """
        clean_id = (assignment_id or "").strip()
        raw = self.repository.get(f"/homework_assignments/{clean_id}")
        if not isinstance(raw, dict):
            raise ValueError(f"Homework assignment '{clean_id}' was not found.")

        ended_ts = int(now_ts) if now_ts is not None else int(time.time())
        merged = self.repository.update(
            f"/homework_assignments/{clean_id}",
            {"ended_manually": True, "ended_at": ended_ts},
        )
        return self._format_assignment_overview(merged, now_ts=ended_ts)

    @staticmethod
    def _build_individual_review_fallback(
        title: str,
        student_name: str,
        extracted_text: str,
        page_count: int,
        word_count: int,
    ) -> dict[str, Any]:
        """
        Purpose:
            Generate a deterministic, content-aware structured review card based on
            the student's extracted PDF text when running offline or in unit tests.
        Inputs:
            title (str): Assignment title.
            student_name (str): Student full name.
            extracted_text (str): Text extracted from the PDF via `pypdf`.
            page_count (int): Number of pages in the PDF.
            word_count (int): Word count of extracted text.
        Outputs:
            dict[str, Any]: Structured review card (`brief_summary`, `strengths`,
            `areas_to_improve`, `actionable_recommendations`).
        """
        lower_txt = extracted_text.lower()
        detected_themes: list[str] = []
        for keyword, label in [
            ("class", "Object-Oriented Class Design"),
            ("sequence", "Sequence & Interaction Flow Modeling"),
            ("test", "Automated Verification & Testing Strategy"),
            ("edge", "Edge-Case & Fault-Tolerance Analysis"),
            ("firebase", "Realtime Cloud Persistence Architecture"),
            ("yolo", "Computer Vision Headcount Integration"),
            ("geofenc", "GPS Geofencing & Haversine Proximity Logic"),
        ]:
            if keyword in lower_txt:
                detected_themes.append(label)

        themes_str = (
            ", ".join(detected_themes[:3])
            if detected_themes
            else "core Software Engineering specifications and structured documentation"
        )
        return {
            "brief_summary": (
                f"{student_name}'s submission for '{title}' ({page_count} page(s), {word_count} words) "
                f"addresses {themes_str} with clear organization and practical implementation focus."
            ),
            "strengths": [
                f"Addresses key architectural themes ({themes_str}) in a structured PDF format.",
                f"Maintains clear technical terminology across {word_count} words and {page_count} page(s).",
                "Aligns design artifacts with practical classroom demonstration requirements.",
            ],
            "areas_to_improve": [
                "Expand explicit boundary-value and negative test cases for every public service method."
                if "test" not in lower_txt
                else "Include quantitative latency and confidence benchmarks alongside functional test descriptions.",
                "Detail explicit fallback transitions when external sensors or network permissions are denied."
                if "edge" not in lower_txt
                else "Add sequence diagram error/alt frames for timeout and permission-denial branches.",
            ],
            "actionable_recommendations": [
                "Map each Low-Level Class Diagram method 1-to-1 against an automated pytest assertion.",
                "Add a concise summary table contrasting Happy Case vs. Edge Case system responses.",
                "Verify that all external thresholds (QR grace window, geofence radius, YOLO confidence) are configurable via .env.",
            ],
        }

    def _generate_peer_comparison(
        self,
        assignment: dict[str, Any],
        target_submission: dict[str, Any],
        all_submissions: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Purpose:
            Compare a student's PDF submission against all other students' submitted PDFs
            for the same assignment, handling the First-Submission Edge Case (`len == 1`)
            with a clear English notice.
        Inputs:
            assignment (dict[str, Any]): Homework assignment dictionary.
            target_submission (dict[str, Any]): The target student's submission record.
            all_submissions (dict[str, dict[str, Any]]): Map of all submissions for the assignment.
        Outputs:
            dict[str, Any]: Dedicated Peer Comparison payload for the right-hand UI column.
        """
        total_subs = len(all_submissions)
        if total_subs <= 1:
            return {
                "total_submissions": total_subs,
                "peer_count": 0,
                "is_first_submission_only": True,
                "notice": FIRST_SUBMISSION_NOTICE,
                "relative_strengths": [],
                "unique_ideas": [],
                "common_gaps": [],
            }

        target_id = target_submission["student_id"]
        peer_subs = [
            sub
            for sid, sub in all_submissions.items()
            if sid != target_id and isinstance(sub, dict)
        ]
        peer_count = len(peer_subs)

        target_words = int(target_submission.get("word_count", 0))
        avg_peer_words = round(
            sum(int(p.get("word_count", 0)) for p in peer_subs) / max(1, peer_count)
        )

        # Tokenize target vs peer vocabulary to identify distinctive concepts
        target_tokens = set(
            re.findall(
                r"[a-zA-Z]{4,}",
                str(target_submission.get("extracted_text", "")).lower(),
            )
        )
        peer_tokens: set[str] = set()
        for p in peer_subs:
            peer_tokens.update(
                re.findall(
                    r"[a-zA-Z]{4,}",
                    str(p.get("extracted_text", "")).lower(),
                )
            )

        unique_vocab = sorted(target_tokens - peer_tokens)[:5]
        shared_vocab = sorted(target_tokens & peer_tokens)[:5]

        def _fallback_comparison() -> dict[str, Any]:
            depth_comparison = (
                f"More comprehensive than the peer average ({target_words} words vs. {avg_peer_words} peer avg words)."
                if target_words >= avg_peer_words
                else f"More concise than the peer average ({target_words} words vs. {avg_peer_words} peer avg words); consider expanding technical depth."
            )
            unique_idea_line = (
                f"Introduces distinctive terminology not emphasized in peer submissions: {', '.join(unique_vocab)}."
                if unique_vocab
                else "Presents a clean, self-contained walk-through of the core system workflow."
            )
            shared_line = (
                f"Like peers, covers foundational concepts ({', '.join(shared_vocab)}), while several submissions could further formalise failure-mode recovery."
                if shared_vocab
                else "Across the cohort, edge-case recovery and quantitative benchmarking remain common areas for deeper elaboration."
            )
            return {
                "relative_strengths": [
                    depth_comparison,
                    f"Compared against {peer_count} peer PDF submission(s), demonstrates clear structural separation of requirements and design.",
                ],
                "unique_ideas": [
                    unique_idea_line,
                    "Highlights practical classroom usability and human-in-the-loop controls.",
                ],
                "common_gaps": [
                    shared_line,
                    "Cohort-wide opportunity: explicitly trace every edge case from UI trigger through service validation to database state.",
                ],
            }

        peer_summaries = "\n".join(
            f"- Peer { idx + 1 } ({p.get('word_count', 0)} words): {str(p.get('extracted_text', ''))[:350]}"
            for idx, p in enumerate(peer_subs[:8])
        )
        prompt = (
            "You are a university Software Engineering AI Teaching Assistant comparing a student's "
            f"homework PDF against {peer_count} peer submissions for '{assignment.get('title')}'. "
            "Return ONLY a JSON object with keys:\n"
            '- "relative_strengths": list of 2-3 strings comparing this submission favorably against peers\n'
            '- "unique_ideas": list of 2 strings highlighting distinctive angles or terms in this submission\n'
            '- "common_gaps": list of 2 strings identifying gaps shared across the cohort.\n'
            f"Target student text ({target_words} words): {str(target_submission.get('extracted_text', ''))[:800]}\n"
            f"Peer excerpts:\n{peer_summaries}"
        )

        generated = self.gemini_client.generate_structured_json(
            prompt=prompt,
            fallback_builder=_fallback_comparison,
        )

        return {
            "total_submissions": total_subs,
            "peer_count": peer_count,
            "is_first_submission_only": False,
            "notice": f"Compared against {peer_count} other student PDF submission(s) in the class.",
            "relative_strengths": list(generated.get("relative_strengths", [])),
            "unique_ideas": list(generated.get("unique_ideas", [])),
            "common_gaps": list(generated.get("common_gaps", [])),
        }

    def submit_homework_pdf(
        self,
        assignment_id: str,
        student_id: str,
        student_name: str,
        filename: str,
        pdf_bytes: bytes,
        content_type: str | None = "application/pdf",
        now_ts: int | None = None,
    ) -> dict[str, Any]:
        """
        Purpose:
            Validate and store a student's `.pdf` homework submission under
            `/homework_assignments/{assignment_id}/submissions/{student_id}`.
            Strictly enforces the Gated Release Rule:
            - Rejects uploads after the deadline or after "End Submission Now" (`PermissionError`).
            - While submissions are open, generates and stores the internal review but
              WITHHOLDS (`review=None`, `peer_comparison=None`) from the student response!
        Inputs:
            assignment_id (str): Target homework assignment ID.
            student_id (str): Submitting student ID.
            student_name (str): Submitting student full name.
            filename (str): Uploaded `.pdf` filename.
            pdf_bytes (bytes): Binary PDF payload.
            content_type (str | None): Multipart HTTP MIME type.
            now_ts (int | None): Optional UNIX timestamp override for deterministic tests.
        Outputs:
            dict[str, Any]: Gated submission confirmation receipt (`review_released=False`).
        """
        clean_aid = (assignment_id or "").strip()
        clean_sid = (student_id or "").strip()
        if not clean_sid:
            raise ValueError("student_id is required to submit homework.")

        raw_assignment = self.repository.get(f"/homework_assignments/{clean_aid}")
        if not isinstance(raw_assignment, dict):
            raise ValueError(f"Homework assignment '{clean_aid}' was not found.")

        current_ts = int(now_ts) if now_ts is not None else int(time.time())
        if self.is_review_released(raw_assignment, now_ts=current_ts):
            raise PermissionError(
                "Submissions are closed for this assignment (deadline has passed or the lecturer ended submissions)."
            )

        pdf_info = self.validate_and_extract_pdf_text(
            filename=filename,
            pdf_bytes=pdf_bytes,
            content_type=content_type,
        )

        title = str(raw_assignment.get("title", clean_aid))
        prompt = (
            "You are a university Software Engineering AI Teaching Assistant reviewing a student's "
            f"PDF homework submission for '{title}'. Return ONLY a JSON object with keys:\n"
            '- "brief_summary": string (2-3 sentences)\n'
            '- "strengths": list of 3 concise strength strings\n'
            '- "areas_to_improve": list of 2-3 areas to improve\n'
            '- "actionable_recommendations": list of 3 concrete next steps.\n'
            f"Student: {student_name}\nExtracted PDF Text ({pdf_info['word_count']} words):\n"
            f"{pdf_info['extracted_text'][:2500]}"
        )
        generated_review = self.gemini_client.generate_structured_json(
            prompt=prompt,
            fallback_builder=lambda: self._build_individual_review_fallback(
                title=title,
                student_name=student_name,
                extracted_text=pdf_info["extracted_text"],
                page_count=pdf_info["page_count"],
                word_count=pdf_info["word_count"],
            ),
        )

        submission_record: dict[str, Any] = {
            "assignment_id": clean_aid,
            "student_id": clean_sid,
            "student_name": (student_name or clean_sid).strip(),
            "filename": pdf_info["filename"],
            "page_count": pdf_info["page_count"],
            "word_count": pdf_info["word_count"],
            "extracted_text": pdf_info["extracted_text"],
            "submitted_at": current_ts,
            "review": {
                "brief_summary": str(generated_review.get("brief_summary", "")),
                "strengths": list(generated_review.get("strengths", [])),
                "areas_to_improve": list(generated_review.get("areas_to_improve", [])),
                "actionable_recommendations": list(
                    generated_review.get("actionable_recommendations", [])
                ),
                "model_used": str(
                    generated_review.get("model_used", "gemini-2.5-flash-lite")
                ),
            },
        }
        self.repository.set(
            f"/homework_assignments/{clean_aid}/submissions/{clean_sid}",
            submission_record,
        )

        # Gated Release Rule: while submissions are open, withhold AI review and peer comparison
        return {
            "assignment_id": clean_aid,
            "student_id": clean_sid,
            "student_name": submission_record["student_name"],
            "filename": submission_record["filename"],
            "page_count": submission_record["page_count"],
            "word_count": submission_record["word_count"],
            "submitted_at": current_ts,
            "status": "submitted_pending_release",
            "review_released": False,
            "gated_message": GATED_RELEASE_NOTICE,
            "review": None,
            "peer_comparison": None,
        }

    def get_student_review_and_comparison(
        self,
        assignment_id: str,
        student_id: str,
        viewer_role: str = "student",
        now_ts: int | None = None,
    ) -> dict[str, Any]:
        """
        Purpose:
            Retrieve a student's homework submission review and peer comparison column.
            Enforces the Gated Release Rule: if `is_review_released` is False, withholds
            both `review` and `peer_comparison` from students until the deadline passes
            or the Lecturer clicks "End Submission Now".
        Inputs:
            assignment_id (str): Homework assignment ID.
            student_id (str): Target student ID.
            viewer_role (str): `"student"` or `"lecturer"`.
            now_ts (int | None): Optional UNIX timestamp override for deterministic tests.
        Outputs:
            dict[str, Any]: Review + Peer Comparison payload (or gated lock payload if still open).
        """
        clean_aid = (assignment_id or "").strip()
        clean_sid = (student_id or "").strip()
        raw_assignment = self.repository.get(f"/homework_assignments/{clean_aid}")
        if not isinstance(raw_assignment, dict):
            raise ValueError(f"Homework assignment '{clean_aid}' was not found.")

        raw_subs = raw_assignment.get("submissions")
        subs_map: dict[str, dict[str, Any]] = (
            raw_subs if isinstance(raw_subs, dict) else {}
        )
        target_sub = subs_map.get(clean_sid)
        if not isinstance(target_sub, dict):
            raise ValueError(
                f"No PDF submission found for student '{clean_sid}' in assignment '{clean_aid}'."
            )

        released = self.is_review_released(raw_assignment, now_ts=now_ts)
        if not released:
            return {
                "assignment_id": clean_aid,
                "assignment_title": raw_assignment.get("title", clean_aid),
                "student_id": clean_sid,
                "student_name": target_sub.get("student_name", clean_sid),
                "filename": target_sub.get("filename", ""),
                "page_count": target_sub.get("page_count", 1),
                "word_count": target_sub.get("word_count", 0),
                "submitted_at": target_sub.get("submitted_at"),
                "review_released": False,
                "gated_message": GATED_RELEASE_NOTICE,
                "review": None,
                "peer_comparison": None,
            }

        peer_comparison = self._generate_peer_comparison(
            assignment=raw_assignment,
            target_submission=target_sub,
            all_submissions=subs_map,
        )
        return {
            "assignment_id": clean_aid,
            "assignment_title": raw_assignment.get("title", clean_aid),
            "student_id": clean_sid,
            "student_name": target_sub.get("student_name", clean_sid),
            "filename": target_sub.get("filename", ""),
            "page_count": target_sub.get("page_count", 1),
            "word_count": target_sub.get("word_count", 0),
            "submitted_at": target_sub.get("submitted_at"),
            "review_released": True,
            "gated_message": None,
            "review": target_sub.get("review"),
            "peer_comparison": peer_comparison,
        }

    def _format_assignment_overview(
        self,
        raw_assignment: dict[str, Any],
        viewer_id: str | None = None,
        now_ts: int | None = None,
    ) -> dict[str, Any]:
        """
        Purpose:
            Format a homework assignment summary including submission count, release
            gate status (`review_released`), and whether `viewer_id` has submitted.
        Inputs:
            raw_assignment (dict[str, Any]): Raw assignment dictionary from Firebase RTDB.
            viewer_id (str | None): Optional student ID to check submission status.
            now_ts (int | None): Optional current UNIX timestamp override.
        Outputs:
            dict[str, Any]: Formatted assignment overview dictionary.
        """
        raw_subs = raw_assignment.get("submissions")
        subs_map = raw_subs if isinstance(raw_subs, dict) else {}
        released = self.is_review_released(raw_assignment, now_ts=now_ts)

        submitted_students = [
            {
                "student_id": sub.get("student_id", sid),
                "student_name": sub.get("student_name", sid),
                "filename": sub.get("filename", ""),
                "page_count": sub.get("page_count", 1),
                "word_count": sub.get("word_count", 0),
                "submitted_at": sub.get("submitted_at"),
            }
            for sid, sub in subs_map.items()
            if isinstance(sub, dict)
        ]
        submitted_students.sort(
            key=lambda s: int(s.get("submitted_at") or 0), reverse=True
        )

        return {
            "assignment_id": raw_assignment["assignment_id"],
            "title": raw_assignment.get("title", ""),
            "description": raw_assignment.get("description", ""),
            "lesson_tag": raw_assignment.get("lesson_tag", "Lesson 1"),
            "deadline_ts": int(raw_assignment.get("deadline_ts", 0)),
            "ended_manually": bool(raw_assignment.get("ended_manually", False)),
            "ended_at": raw_assignment.get("ended_at"),
            "created_at": int(raw_assignment.get("created_at", 0)),
            "review_released": released,
            "submission_count": len(submitted_students),
            "viewer_has_submitted": bool(viewer_id and viewer_id in subs_map),
            "submitted_students": submitted_students,
        }

    def list_assignments(
        self, viewer_id: str | None = None, now_ts: int | None = None
    ) -> list[dict[str, Any]]:
        """
        Purpose:
            List all homework assignments from `/homework_assignments` ordered newest-first.
        Inputs:
            viewer_id (str | None): Optional student ID to populate `viewer_has_submitted`.
            now_ts (int | None): Optional current UNIX timestamp override.
        Outputs:
            list[dict[str, Any]]: Ordered list of formatted homework assignments.
        """
        raw_all = self.repository.get("/homework_assignments")
        if not isinstance(raw_all, dict):
            return []

        results = [
            self._format_assignment_overview(item, viewer_id=viewer_id, now_ts=now_ts)
            for item in raw_all.values()
            if isinstance(item, dict) and "assignment_id" in item
        ]
        results.sort(key=lambda a: int(a.get("created_at", 0)), reverse=True)
        return results
