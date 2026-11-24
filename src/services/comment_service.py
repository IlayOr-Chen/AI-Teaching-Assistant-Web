"""
Purpose:
    Implements `CommentService` for Feature 2 (Comments & Suggestions Tab) persisted
    in Firebase Realtime Database under `/comments`. Supports:
    - Visibility: `public` (Everyone) vs. `private` (Lecturer Only)
    - Identity: `With Name` vs. `Anonymous`
    - Scope Tag: Specific Lesson (`Lesson 1`, `Lesson 2`, ...) vs. `General`
    - Threaded Replies and Scope Filtering.
Inputs:
    `FirebaseRepository` persistence instance.
Outputs:
    `CommentService` class managing `/comments/{comment_id}`.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from src.repository.firebase_repository import FirebaseRepository

VALID_VISIBILITIES = {"public", "private"}


class CommentService:
    """
    Purpose:
        Domain service managing public and private (lecturer-only) classroom
        comments, named vs. anonymous authorship, lesson/general scope tags,
        and threaded replies in Firebase RTDB (`/comments`).
    Inputs:
        repository (FirebaseRepository): Firebase RTDB persistence layer.
    Outputs:
        `CommentService` service instance.
    """

    def __init__(self, repository: FirebaseRepository) -> None:
        """
        Purpose:
            Initialize `CommentService` with a Firebase repository.
        Inputs:
            repository (FirebaseRepository): Target Firebase RTDB repository.
        Outputs:
            None.
        """
        self.repository = repository

    @staticmethod
    def _sanitize_for_viewer(
        raw_comment: dict[str, Any],
        viewer_role: str,
        viewer_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Purpose:
            Serialize a comment thread for a specific viewer, masking `author_id`
            and real `author_name` whenever `is_anonymous=True` so anonymous student
            identity is never leaked over the API.
        Inputs:
            raw_comment (dict[str, Any]): Raw comment dictionary from `/comments/{id}`.
            viewer_role (str): Role of the caller (`"lecturer"` or `"student"`).
            viewer_id (str | None): Optional caller ID (`student_id` or `lecturer_id`).
        Outputs:
            dict[str, Any]: Sanitized comment dictionary with chronologically sorted `replies`.
        """
        is_anon = bool(raw_comment.get("is_anonymous", False))
        display_name = (
            "Anonymous Student"
            if is_anon and raw_comment.get("author_role") != "lecturer"
            else str(raw_comment.get("author_name", "Student"))
        )

        raw_replies = raw_comment.get("replies")
        replies_map = raw_replies if isinstance(raw_replies, dict) else {}
        formatted_replies: list[dict[str, Any]] = []

        for rep in replies_map.values():
            if not isinstance(rep, dict):
                continue
            rep_anon = bool(rep.get("is_anonymous", False))
            rep_display = (
                "Anonymous Student"
                if rep_anon and rep.get("author_role") != "lecturer"
                else str(rep.get("author_name", "Participant"))
            )
            formatted_replies.append(
                {
                    "reply_id": rep.get("reply_id", ""),
                    "text": rep.get("text", ""),
                    "display_name": rep_display,
                    "author_role": rep.get("author_role", "student"),
                    "is_anonymous": rep_anon,
                    "created_at": int(rep.get("created_at", 0)),
                }
            )

        formatted_replies.sort(key=lambda r: int(r.get("created_at", 0)))

        return {
            "comment_id": raw_comment["comment_id"],
            "text": raw_comment.get("text", ""),
            "visibility": raw_comment.get("visibility", "public"),
            "is_anonymous": is_anon,
            "display_name": display_name,
            "author_role": raw_comment.get("author_role", "student"),
            "scope_tag": raw_comment.get("scope_tag", "General"),
            "created_at": int(raw_comment.get("created_at", 0)),
            "is_own": bool(viewer_id and raw_comment.get("author_id") == viewer_id),
            "reply_count": len(formatted_replies),
            "replies": formatted_replies,
        }

    def create_comment(
        self,
        text: str,
        author_id: str,
        author_name: str,
        author_role: str = "student",
        visibility: str = "public",
        is_anonymous: bool = False,
        scope_tag: str = "General",
    ) -> dict[str, Any]:
        """
        Purpose:
            Create a new comment or suggestion in Firebase RTDB under `/comments/{comment_id}`.
        Inputs:
            text (str): Comment body text (1..2000 characters).
            author_id (str): Internal user or session identifier.
            author_name (str): Author's full name (masked when `is_anonymous=True`).
            author_role (str): `"student"` or `"lecturer"`.
            visibility (str): `"public"` (Everyone) or `"private"` (Lecturer Only).
            is_anonymous (bool): True to post as `"Anonymous Student"`.
            scope_tag (str): Specific lesson tag (e.g., `"Lesson 1"`) or `"General"`.
        Outputs:
            dict[str, Any]: Sanitized created comment dictionary.
        """
        clean_text = (text or "").strip()
        if not clean_text:
            raise ValueError("Comment text cannot be empty.")
        if len(clean_text) > 2000:
            raise ValueError("Comment text cannot exceed 2000 characters.")

        clean_vis = (visibility or "public").strip().lower()
        if clean_vis not in VALID_VISIBILITIES:
            raise ValueError("Comment visibility must be 'public' or 'private'.")

        clean_scope = (scope_tag or "General").strip() or "General"
        clean_role = "lecturer" if author_role == "lecturer" else "student"
        now_ts = int(time.time())
        comment_id = f"cmt_{now_ts}_{secrets.token_hex(4)}"

        record: dict[str, Any] = {
            "comment_id": comment_id,
            "text": clean_text,
            "author_id": (author_id or "anonymous").strip(),
            "author_name": (author_name or "Student").strip(),
            "author_role": clean_role,
            "visibility": clean_vis,
            "is_anonymous": bool(is_anonymous) and clean_role != "lecturer",
            "scope_tag": clean_scope,
            "created_at": now_ts,
            "replies": {},
        }
        self.repository.set(f"/comments/{comment_id}", record)
        return self._sanitize_for_viewer(
            record, viewer_role=clean_role, viewer_id=author_id
        )

    def add_reply(
        self,
        comment_id: str,
        text: str,
        author_id: str,
        author_name: str,
        author_role: str = "student",
        is_anonymous: bool = False,
    ) -> dict[str, Any]:
        """
        Purpose:
            Append a threaded reply to an existing comment under `/comments/{comment_id}/replies`.
            Enforces privacy rules so other students cannot reply to a `private` (Lecturer-only) comment.
        Inputs:
            comment_id (str): Parent comment identifier.
            text (str): Reply message body.
            author_id (str): Replier identifier.
            author_name (str): Replier full name.
            author_role (str): `"student"` or `"lecturer"`.
            is_anonymous (bool): Whether the reply is posted anonymously.
        Outputs:
            dict[str, Any]: Updated parent comment thread dictionary.
        """
        clean_cid = (comment_id or "").strip()
        raw_comment = self.repository.get(f"/comments/{clean_cid}")
        if not isinstance(raw_comment, dict):
            raise ValueError(f"Comment '{clean_cid}' was not found.")

        clean_text = (text or "").strip()
        if not clean_text:
            raise ValueError("Reply text cannot be empty.")

        clean_role = "lecturer" if author_role == "lecturer" else "student"
        # Privacy guard: private comments are strictly visible/repliable by the Lecturer (or the author)
        if (
            raw_comment.get("visibility") == "private"
            and clean_role != "lecturer"
            and raw_comment.get("author_id") != (author_id or "").strip()
        ):
            raise PermissionError(
                "Only the lecturer can view or reply to private lecturer-only comments."
            )

        now_ts = int(time.time())
        reply_id = f"rep_{now_ts}_{secrets.token_hex(4)}"
        reply_record = {
            "reply_id": reply_id,
            "text": clean_text,
            "author_id": (author_id or "anonymous").strip(),
            "author_name": (author_name or "Student").strip(),
            "author_role": clean_role,
            "is_anonymous": bool(is_anonymous) and clean_role != "lecturer",
            "created_at": now_ts,
        }
        self.repository.set(
            f"/comments/{clean_cid}/replies/{reply_id}", reply_record
        )
        updated_raw = self.repository.get(f"/comments/{clean_cid}")
        return self._sanitize_for_viewer(
            updated_raw, viewer_role=clean_role, viewer_id=author_id
        )

    def list_comments(
        self,
        viewer_role: str = "student",
        viewer_id: str | None = None,
        scope_tag: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Purpose:
            Retrieve all comments visible to the caller, filtered by optional
            `scope_tag` (`"General"`, `"Lesson 1"`, etc.) and ordered newest-first.
            Students never receive other students' `private` (Lecturer-only) comments.
        Inputs:
            viewer_role (str): `"lecturer"` or `"student"`.
            viewer_id (str | None): Caller's user ID (`student_id` or `lecturer_id`).
            scope_tag (str | None): Optional scope filter (`"General"`, `"Lesson 1"`, etc.).
        Outputs:
            list[dict[str, Any]]: Ordered list of sanitized comment thread dictionaries.
        """
        raw_all = self.repository.get("/comments")
        if not isinstance(raw_all, dict):
            return []

        clean_scope = (
            scope_tag.strip()
            if scope_tag and scope_tag.strip().lower() not in {"all", ""}
            else None
        )
        is_lecturer = viewer_role == "lecturer"
        results: list[dict[str, Any]] = []

        for item in raw_all.values():
            if not isinstance(item, dict) or "comment_id" not in item:
                continue

            vis = item.get("visibility", "public")
            if vis == "private" and not is_lecturer:
                if not viewer_id or item.get("author_id") != viewer_id:
                    continue

            if clean_scope and item.get("scope_tag", "General") != clean_scope:
                continue

            results.append(
                self._sanitize_for_viewer(
                    item, viewer_role=viewer_role, viewer_id=viewer_id
                )
            )

        results.sort(key=lambda c: int(c.get("created_at", 0)), reverse=True)
        return results
