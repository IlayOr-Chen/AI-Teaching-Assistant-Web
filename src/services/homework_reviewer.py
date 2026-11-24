"""
Purpose:
    Implements `HomeworkReviewer` for Feature 5 (Deep Homework Review & Peer Comparison
    — Multi-Format Files & Public Git Repository URLs + Gemini + Gated Release Rule)
    persisted in Firebase Realtime Database under `/homework_assignments`. Enforces:
    1) Multi-format file validation & text extraction for `.pdf`, `.docx`, `.txt`, `.md`,
       `.zip`, and common source-code files (`.py`, `.java`, `.c`, `.cpp`, `.h`, `.js`,
       `.ts`, `.html`, `.css`, `.json`),
    2) Public GitHub / Git repository URL ingestion over HTTPS zipball download without
       requiring a local `git` binary (extracting `README.md` and source files while
       skipping `node_modules/`, `venv/`, `.git/`, `__pycache__/`, and binaries),
    3) Gated Release Rule: students can submit/replace their work while submissions are open,
       but AI feedback and peer comparisons unlock ONLY AFTER the deadline passes OR the
       Lecturer clicks "End Submission Now",
    4) Concise Review & Summary Card + Dedicated Peer Comparison Column across all submissions
       via real Gemini API calls (zero fake/hardcoded production fallbacks),
    5) First-Submission Edge Case notice when only 1 student has submitted.
Inputs:
    `FirebaseRepository` persistence instance and `GeminiClient` AI wrapper.
Outputs:
    `HomeworkReviewer` class managing homework assignments, multi-format/Git submissions,
    and gated peer comparisons.
"""

from __future__ import annotations

import io
from pathlib import Path
import re
import secrets
import time
from typing import Any
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import zipfile

import httpx
from pypdf import PdfReader

from src.repository.firebase_repository import FirebaseRepository
from src.services.gemini_client import GeminiClient

SUPPORTED_TEXT_CODE_EXTENSIONS = {
    ".txt",
    ".md",
    ".py",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".js",
    ".ts",
    ".html",
    ".css",
    ".json",
}

SUPPORTED_FILE_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".zip",
    *SUPPORTED_TEXT_CODE_EXTENSIONS,
}

IGNORED_ARCHIVE_DIRS = {
    "node_modules",
    "venv",
    ".venv",
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "dist",
    "build",
    ".idea",
    ".vscode",
    "target",
    "bin",
    "obj",
}

MAX_EXTRACTED_CHARS = 60_000

FIRST_SUBMISSION_NOTICE = (
    "This is the first submission received for this assignment, so peer comparison "
    "is not yet available. Comparative insights will appear once additional students submit."
)

GATED_RELEASE_NOTICE = (
    "Your homework submission has been recorded. AI review and peer comparison are locked "
    "while submissions remain open and will unlock automatically once the deadline passes "
    "or the lecturer clicks 'End Submission Now'."
)


class HomeworkReviewer:
    """
    Purpose:
        Domain service managing homework assignments, multi-format file uploads
        (`.pdf`, `.docx`, `.txt`, `.md`, `.zip`, source code) and public GitHub repository
        URL submissions, deadline and manual "End Submission Now" release gating, individual
        Gemini AI reviews, and multi-submission peer comparisons in Firebase RTDB.
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
    def _extract_docx_text(docx_bytes: bytes) -> str:
        """
        Purpose:
            Extract readable paragraph text from a `.docx` Word document archive by
            parsing `word/document.xml` without requiring native C libraries.
        Inputs:
            docx_bytes (bytes): Raw `.docx` file bytes.
        Outputs:
            str: Extracted plain text from all paragraphs. Raises `ValueError` if invalid.
        """
        try:
            with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
                if "word/document.xml" not in zf.namelist():
                    raise ValueError("Missing word/document.xml inside .docx file.")
                xml_bytes = zf.read("word/document.xml")
            root = ET.fromstring(xml_bytes)
            paragraphs: list[str] = []
            for node in root.iter():
                if node.tag.endswith("}p") or node.tag == "p":
                    runs = [
                        t.text
                        for t in node.iter()
                        if (t.tag.endswith("}t") or t.tag == "t") and t.text
                    ]
                    line = "".join(runs).strip()
                    if line:
                        paragraphs.append(line)
            return "\n\n".join(paragraphs).strip()
        except Exception as exc:
            raise ValueError(f"Invalid or corrupted .docx file: {exc}") from exc

    @classmethod
    def _extract_zip_text(cls, zip_bytes: bytes, source_label: str = "archive.zip") -> tuple[str, int]:
        """
        Purpose:
            Extract readable text from a `.zip` archive (or GitHub repository zipball),
            prioritizing `README.md` and supported source/document files while skipping
            junk directories (`node_modules/`, `venv/`, `.git/`, `__pycache__/`, binaries)
            and capping total characters at `MAX_EXTRACTED_CHARS`.
        Inputs:
            zip_bytes (bytes): Raw ZIP archive bytes.
            source_label (str): Display label for error messages.
        Outputs:
            tuple[str, int]: `(combined_extracted_text, extracted_file_count)`.
            Raises `ValueError` if the archive is corrupted or contains no readable files.
        """
        try:
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        except Exception as exc:
            raise ValueError(f"Invalid or corrupted ZIP archive '{source_label}': {exc}") from exc

        with zf:
            entries = [info for info in zf.infolist() if not info.is_dir()]
            # Prioritize README files first, then sort alphabetically
            entries.sort(
                key=lambda info: (
                    0 if "readme" in Path(info.filename).name.lower() else 1,
                    info.filename.lower(),
                )
            )

            collected_sections: list[str] = []
            total_chars = 0
            file_count = 0

            for info in entries:
                norm_path = info.filename.replace("\\", "/").lstrip("/")
                parts = [p for p in norm_path.split("/") if p]
                if not parts:
                    continue
                if any(
                    part.lower() in IGNORED_ARCHIVE_DIRS or part.startswith(".")
                    for part in parts[:-1]
                ):
                    continue
                filename = parts[-1]
                if filename.startswith("."):
                    continue
                ext = Path(filename).suffix.lower()
                if ext not in SUPPORTED_TEXT_CODE_EXTENSIONS and ext not in {".pdf", ".docx"}:
                    continue
                if info.file_size <= 0 or info.file_size > 8 * 1024 * 1024:
                    continue

                raw_data = zf.read(info.filename)
                file_text = ""
                if ext in SUPPORTED_TEXT_CODE_EXTENSIONS:
                    if b"\x00" in raw_data[:1024]:
                        continue
                    file_text = raw_data.decode("utf-8", errors="replace").strip()
                elif ext == ".docx":
                    try:
                        file_text = cls._extract_docx_text(raw_data)
                    except ValueError:
                        continue
                elif ext == ".pdf":
                    try:
                        reader = PdfReader(io.BytesIO(raw_data))
                        file_text = "\n".join(
                            (p.extract_text() or "").strip() for p in reader.pages
                        ).strip()
                    except Exception:
                        continue

                if not file_text:
                    continue

                # Strip top-level GitHub zipball hash folder prefix if present (e.g. owner-repo-sha/...)
                display_path = (
                    "/".join(parts[1:])
                    if len(parts) > 1 and re.match(r"^[^/]+-[0-9a-f]{7,40}$", parts[0])
                    else norm_path
                )
                snippet = file_text[:12_000]
                section = f"--- File: {display_path} ---\n{snippet}"
                collected_sections.append(section)
                file_count += 1
                total_chars += len(section)
                if total_chars >= MAX_EXTRACTED_CHARS:
                    break

        combined = "\n\n".join(collected_sections)[:MAX_EXTRACTED_CHARS].strip()
        if not combined:
            raise ValueError(
                f"No readable source code or document files were found in '{source_label}'."
            )
        return combined, file_count

    @classmethod
    def validate_and_extract_pdf_text(
        cls,
        filename: str,
        pdf_bytes: bytes,
        content_type: str | None = "application/pdf",
    ) -> dict[str, Any]:
        """
        Purpose:
            Validate and extract readable text from an uploaded homework file across all
            supported formats: `.pdf`, `.docx`, `.zip`, `.txt`, `.md`, and common source-code
            files (`.py`, `.java`, `.c`, `.cpp`, `.h`, `.js`, `.ts`, `.html`, `.css`, `.json`).
            Retains the method name `validate_and_extract_pdf_text` for backward compatibility
            with the Low-Level Class Diagram and existing callers.
        Inputs:
            filename (str): Uploaded filename.
            pdf_bytes (bytes): Raw uploaded file bytes.
            content_type (str | None): Multipart HTTP MIME type.
        Outputs:
            dict[str, Any]: Dictionary containing `filename`, `submission_type`,
            `extracted_text`, `page_count`, and `word_count`. Raises `ValueError` for
            unsupported formats or corrupted files.
        """
        clean_name = (filename or "").strip()
        ext = Path(clean_name).suffix.lower()
        if not clean_name or ext not in SUPPORTED_FILE_EXTENSIONS:
            allowed_list = ", ".join(sorted(SUPPORTED_FILE_EXTENSIONS))
            raise ValueError(
                f"Unsupported homework file format '{ext or clean_name}'. "
                f"Allowed formats: Only PDF (.pdf), Word (.docx), ZIP (.zip), Markdown/Text (.md, .txt), "
                f"and source code files ({allowed_list})."
            )

        if not pdf_bytes or len(pdf_bytes) == 0:
            raise ValueError("Uploaded homework file is empty.")

        if ext == ".pdf":
            if not pdf_bytes.lstrip().startswith(b"%PDF"):
                raise ValueError(
                    "Invalid or corrupted PDF file. Please upload a valid PDF document."
                )
            try:
                reader = PdfReader(io.BytesIO(pdf_bytes))
                page_count = max(1, len(reader.pages))
                page_texts: list[str] = []
                for page in reader.pages:
                    txt = page.extract_text() or ""
                    if txt.strip():
                        page_texts.append(txt.strip())
                extracted_text = "\n\n".join(page_texts)[:MAX_EXTRACTED_CHARS].strip()
            except Exception as exc:
                raise ValueError(f"Failed to parse PDF document: {exc}") from exc
        elif ext == ".docx":
            extracted_text = cls._extract_docx_text(pdf_bytes)[:MAX_EXTRACTED_CHARS]
            page_count = max(1, round(len(extracted_text.splitlines()) / 40) or 1)
        elif ext == ".zip":
            extracted_text, file_count = cls._extract_zip_text(pdf_bytes, source_label=clean_name)
            page_count = max(1, file_count)
        else:
            if b"\x00" in pdf_bytes[:1024]:
                raise ValueError(
                    f"Binary content detected in text/code file '{clean_name}'. Please upload a valid UTF-8 text or source code file."
                )
            extracted_text = pdf_bytes.decode("utf-8", errors="replace")[:MAX_EXTRACTED_CHARS].strip()
            if not extracted_text:
                raise ValueError(f"Uploaded file '{clean_name}' contains no readable text.")
            page_count = max(1, round(len(extracted_text.splitlines()) / 45) or 1)

        words = re.findall(r"\S+", extracted_text)
        return {
            "filename": clean_name,
            "submission_type": f"file:{ext.lstrip('.')}",
            "extracted_text": extracted_text,
            "page_count": max(1, page_count),
            "word_count": len(words),
        }

    @classmethod
    def extract_public_git_repo_text(cls, repo_url: str) -> dict[str, Any]:
        """
        Purpose:
            Validate a public GitHub / Git repository URL, download its archive over HTTPS
            without requiring a local `git` binary, and extract `README.md` plus key source
            code files (skipping `node_modules/`, `venv/`, `.git/`, `__pycache__/`, binaries)
            capped safely at `MAX_EXTRACTED_CHARS` for Gemini analysis.
        Inputs:
            repo_url (str): Public HTTPS GitHub repository URL (e.g. `https://github.com/owner/repo`).
        Outputs:
            dict[str, Any]: Dictionary with `filename`, `repo_url`, `submission_type`,
            `extracted_text`, `page_count` (extracted file count), and `word_count`.
            Raises `ValueError` if the URL is invalid, private, or unreachable.
        """
        clean_url = (repo_url or "").strip()
        if not clean_url:
            raise ValueError("A public Git repository URL is required.")

        parsed = urlparse(clean_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(
                "Invalid repository URL. Please provide a full public HTTPS URL such as 'https://github.com/owner/repo'."
            )

        host = parsed.netloc.lower()
        path_parts = [p for p in parsed.path.strip("/").split("/") if p]
        if host not in {"github.com", "www.github.com"} or len(path_parts) < 2:
            raise ValueError(
                "Invalid GitHub repository URL. Expected format: 'https://github.com/<owner>/<repo>'."
            )

        owner = path_parts[0]
        repo = re.sub(r"\.git$", "", path_parts[1], flags=re.IGNORECASE)
        if not re.match(r"^[A-Za-z0-9_.-]+$", owner) or not re.match(r"^[A-Za-z0-9_.-]+$", repo):
            raise ValueError(
                f"Invalid GitHub owner or repository name in URL '{clean_url}'."
            )

        branch_hint: str | None = None
        if len(path_parts) >= 4 and path_parts[2] in {"tree", "blob"}:
            branch_hint = path_parts[3]

        candidate_urls: list[str] = []
        if branch_hint:
            candidate_urls.append(
                f"https://api.github.com/repos/{owner}/{repo}/zipball/{branch_hint}"
            )
            candidate_urls.append(
                f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{branch_hint}"
            )
        candidate_urls.extend(
            [
                f"https://api.github.com/repos/{owner}/{repo}/zipball",
                f"https://codeload.github.com/{owner}/{repo}/zip/HEAD",
                f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/main",
                f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/master",
            ]
        )

        headers = {
            "User-Agent": "AI-Teaching-Assistant-Web/1.0",
            "Accept": "application/vnd.github+json, application/zip, */*",
        }
        zip_bytes: bytes | None = None
        last_status: int | None = None

        try:
            with httpx.Client(follow_redirects=True, timeout=25.0, headers=headers) as client:
                for dl_url in candidate_urls:
                    resp = client.get(dl_url)
                    last_status = resp.status_code
                    if resp.status_code == 200 and resp.content:
                        zip_bytes = resp.content
                        break
                    if resp.status_code in {401, 403, 404}:
                        continue
        except Exception as exc:
            raise ValueError(
                f"Could not connect to GitHub to download '{owner}/{repo}': {exc}"
            ) from exc

        if not zip_bytes:
            raise ValueError(
                f"Public GitHub repository '{owner}/{repo}' could not be downloaded "
                f"(HTTP {last_status or 'unreachable'}). Please verify that the repository URL is correct and public."
            )

        canonical_url = f"https://github.com/{owner}/{repo}"
        extracted_text, file_count = cls._extract_zip_text(
            zip_bytes, source_label=canonical_url
        )
        header_banner = f"GitHub Repository: {canonical_url} ({file_count} files extracted)\n\n"
        full_text = (header_banner + extracted_text)[:MAX_EXTRACTED_CHARS]
        words = re.findall(r"\S+", full_text)

        return {
            "filename": f"github.com/{owner}/{repo}",
            "repo_url": canonical_url,
            "submission_type": "git_repo",
            "extracted_text": full_text,
            "page_count": max(1, file_count),
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

    def _generate_peer_comparison(
        self,
        assignment: dict[str, Any],
        target_submission: dict[str, Any],
        all_submissions: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Purpose:
            Compare a student's homework submission against all other students' submissions
            for the same assignment via Gemini, handling the First-Submission Edge Case
            (`len == 1`) with the exact English notice.
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

        peer_summaries = "\n".join(
            f"- Peer {idx + 1} ({p.get('filename', 'submission')}, {p.get('word_count', 0)} words):\n"
            f"{str(p.get('extracted_text', ''))[:1500]}"
            for idx, p in enumerate(peer_subs[:8])
        )
        prompt = (
            "You are a university Software Engineering AI Teaching Assistant comparing a student's "
            f"homework submission against {peer_count} peer submissions for '{assignment.get('title')}'. "
            "Analyze the actual code, architecture, and text content below and return ONLY a JSON object with keys:\n"
            '- "relative_strengths": list of 2-3 strings comparing this submission favorably against peers\n'
            '- "unique_ideas": list of 2 strings highlighting distinctive angles, design choices, or implementations in this submission\n'
            '- "common_gaps": list of 2 strings identifying gaps or improvement areas shared across the cohort.\n'
            f"Target student submission ({target_submission.get('filename')}, {target_words} words):\n"
            f"{str(target_submission.get('extracted_text', ''))[:4000]}\n\n"
            f"Peer excerpts:\n{peer_summaries}"
        )

        generated = self.gemini_client.generate_structured_json(prompt=prompt)

        return {
            "total_submissions": total_subs,
            "peer_count": peer_count,
            "is_first_submission_only": False,
            "notice": f"Compared against {peer_count} other student submission(s) in the class.",
            "relative_strengths": list(generated.get("relative_strengths", [])),
            "unique_ideas": list(generated.get("unique_ideas", [])),
            "common_gaps": list(generated.get("common_gaps", [])),
        }

    def submit_homework_pdf(
        self,
        assignment_id: str,
        student_id: str,
        student_name: str,
        filename: str = "",
        pdf_bytes: bytes = b"",
        content_type: str | None = "application/pdf",
        now_ts: int | None = None,
        repo_url: str | None = None,
    ) -> dict[str, Any]:
        """
        Purpose:
            Validate, analyze via Gemini, and store a student's homework submission
            (either a multi-format file `.pdf`/`.docx`/`.txt`/`.md`/`.zip`/code OR a
            public GitHub repository URL `repo_url`) under
            `/homework_assignments/{assignment_id}/submissions/{student_id}`.
            Strictly enforces the Gated Release Rule:
            - Rejects submissions after the deadline or after "End Submission Now" (`PermissionError`).
            - While submissions are open, stores the Gemini review internally but WITHHOLDS
              (`review=None`, `peer_comparison=None`) from the student response.
        Inputs:
            assignment_id (str): Target homework assignment ID.
            student_id (str): Submitting student ID.
            student_name (str): Submitting student full name.
            filename (str): Uploaded filename (when submitting a file).
            pdf_bytes (bytes): Uploaded binary file payload (when submitting a file).
            content_type (str | None): Multipart HTTP MIME type.
            now_ts (int | None): Optional UNIX timestamp override for deterministic tests.
            repo_url (str | None): Optional public GitHub repository URL (when submitting a Git repo).
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

        if repo_url and repo_url.strip():
            extracted_info = self.extract_public_git_repo_text(repo_url.strip())
        else:
            extracted_info = self.validate_and_extract_pdf_text(
                filename=filename,
                pdf_bytes=pdf_bytes,
                content_type=content_type,
            )

        title = str(raw_assignment.get("title", clean_aid))
        description = str(raw_assignment.get("description", ""))
        prompt = (
            "You are an expert university Software Engineering AI Teaching Assistant reviewing a student's "
            f"homework submission for '{title}' ({description}). Carefully analyze the actual submitted "
            "content below and return ONLY a valid JSON object with keys:\n"
            '- "brief_summary": string (2-3 sentences summarizing the submission and its quality)\n'
            '- "strengths": list of 3 specific strength strings observed in the submission\n'
            '- "areas_to_improve": list of 2-3 specific areas to improve\n'
            '- "actionable_recommendations": list of 3 concrete next steps for the student.\n'
            f"Student: {student_name}\n"
            f"Submission Source: {extracted_info['filename']} ({extracted_info['word_count']} words)\n"
            f"Extracted Submission Content:\n{extracted_info['extracted_text'][:18_000]}"
        )
        generated_review = self.gemini_client.generate_structured_json(prompt=prompt)

        submission_record: dict[str, Any] = {
            "assignment_id": clean_aid,
            "student_id": clean_sid,
            "student_name": (student_name or clean_sid).strip(),
            "filename": extracted_info["filename"],
            "submission_type": extracted_info.get("submission_type", "file:pdf"),
            "repo_url": extracted_info.get("repo_url"),
            "page_count": extracted_info["page_count"],
            "word_count": extracted_info["word_count"],
            "extracted_text": extracted_info["extracted_text"],
            "submitted_at": current_ts,
            "review": {
                "brief_summary": str(generated_review.get("brief_summary", "")),
                "strengths": list(generated_review.get("strengths", [])),
                "areas_to_improve": list(generated_review.get("areas_to_improve", [])),
                "actionable_recommendations": list(
                    generated_review.get("actionable_recommendations", [])
                ),
                "model_used": str(
                    generated_review.get("model_used", "gemini-2.5-flash")
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
            "submission_type": submission_record["submission_type"],
            "repo_url": submission_record["repo_url"],
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
                "submission_type": target_sub.get("submission_type", "file:pdf"),
                "repo_url": target_sub.get("repo_url"),
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
            "submission_type": target_sub.get("submission_type", "file:pdf"),
            "repo_url": target_sub.get("repo_url"),
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
                "submission_type": sub.get("submission_type", "file:pdf"),
                "repo_url": sub.get("repo_url"),
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
            list[str | dict[str, Any]]: Ordered list of formatted homework assignments.
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
