"""
Purpose:
    Implements `PollService` for Feature 3 (On-the-Spot KISS Polls Tab) persisted
    in Firebase Realtime Database under `/polls`. Supports instant multiple-choice
    poll creation, open/close voting controls, single-vote enforcement per student,
    and real-time vote count and percentage calculations.
Inputs:
    `FirebaseRepository` persistence instance.
Outputs:
    `PollService` class managing `/polls/{poll_id}`.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from src.repository.firebase_repository import FirebaseRepository


class PollService:
    """
    Purpose:
        Domain service managing multiple-choice classroom polls, live voting,
        and single-vote-per-student enforcement in Firebase RTDB (`/polls`).
    Inputs:
        repository (FirebaseRepository): Firebase RTDB persistence layer.
    Outputs:
        `PollService` service instance.
    """

    def __init__(self, repository: FirebaseRepository) -> None:
        """
        Purpose:
            Initialize `PollService` with a Firebase repository.
        Inputs:
            repository (FirebaseRepository): Target Firebase RTDB repository.
        Outputs:
            None.
        """
        self.repository = repository

    @staticmethod
    def _format_poll(
        raw_poll: dict[str, Any], viewer_id: str | None = None
    ) -> dict[str, Any]:
        """
        Purpose:
            Compute live vote totals, per-option counts and percentages (`0.0`..`100.0`),
            and the current viewer's selected option index from a raw RTDB poll node.
        Inputs:
            raw_poll (dict[str, Any]): Stored poll dictionary from `/polls/{poll_id}`.
            viewer_id (str | None): Optional student/user identifier to check existing vote.
        Outputs:
            dict[str, Any]: Enriched poll payload ready for Lecturer and Student UI rendering.
        """
        options: list[str] = [str(opt) for opt in raw_poll.get("options", [])]
        raw_votes = raw_poll.get("votes")
        votes_map: dict[str, Any] = raw_votes if isinstance(raw_votes, dict) else {}

        counts = [0 for _ in options]
        viewer_vote: int | None = None

        for voter_key, vote_entry in votes_map.items():
            idx = (
                vote_entry.get("option_index")
                if isinstance(vote_entry, dict)
                else vote_entry
            )
            if isinstance(idx, int) and 0 <= idx < len(options):
                counts[idx] += 1
                if viewer_id and voter_key == viewer_id:
                    viewer_vote = idx

        total_votes = sum(counts)
        option_stats: list[dict[str, Any]] = []
        for idx, label in enumerate(options):
            cnt = counts[idx]
            pct = round((cnt / total_votes) * 100.0, 1) if total_votes > 0 else 0.0
            option_stats.append(
                {
                    "index": idx,
                    "text": label,
                    "count": cnt,
                    "percentage": pct,
                }
            )

        return {
            "poll_id": raw_poll["poll_id"],
            "question": raw_poll.get("question", ""),
            "lesson_tag": raw_poll.get("lesson_tag", "General"),
            "active": bool(raw_poll.get("active", False)),
            "created_by": raw_poll.get("created_by", "Lecturer"),
            "created_at": int(raw_poll.get("created_at", 0)),
            "closed_at": raw_poll.get("closed_at"),
            "total_votes": total_votes,
            "options": option_stats,
            "viewer_vote": viewer_vote,
        }

    def create_poll(
        self,
        question: str,
        options: list[str],
        lesson_tag: str = "General",
        created_by: str = "Lecturer",
    ) -> dict[str, Any]:
        """
        Purpose:
            Create and open a new multiple-choice poll in Firebase RTDB under `/polls/{poll_id}`.
        Inputs:
            question (str): Poll question prompt.
            options (list[str]): List of 2 to 8 distinct answer choices.
            lesson_tag (str): Optional lesson or `"General"` scope tag.
            created_by (str): Creator display name.
        Outputs:
            dict[str, Any]: Newly created enriched poll dictionary.
        """
        clean_question = (question or "").strip()
        if not clean_question:
            raise ValueError("Poll question cannot be empty.")

        clean_options: list[str] = []
        for opt in options or []:
            text = str(opt).strip()
            if text and text not in clean_options:
                clean_options.append(text)

        if len(clean_options) < 2:
            raise ValueError("A poll must include at least 2 distinct answer options.")
        if len(clean_options) > 8:
            raise ValueError("A poll may contain at most 8 answer options.")

        now_ts = int(time.time())
        poll_id = f"poll_{now_ts}_{secrets.token_hex(4)}"
        clean_tag = (lesson_tag or "General").strip() or "General"

        record: dict[str, Any] = {
            "poll_id": poll_id,
            "question": clean_question,
            "options": clean_options,
            "lesson_tag": clean_tag,
            "active": True,
            "created_by": (created_by or "Lecturer").strip(),
            "created_at": now_ts,
            "closed_at": None,
            "votes": {},
        }
        self.repository.set(f"/polls/{poll_id}", record)
        return self._format_poll(record)

    def set_poll_active(self, poll_id: str, active: bool) -> dict[str, Any]:
        """
        Purpose:
            Open (`active=True`) or close (`active=False`) voting on an existing poll.
        Inputs:
            poll_id (str): Target poll identifier.
            active (bool): Desired active voting status.
        Outputs:
            dict[str, Any]: Updated enriched poll dictionary.
        """
        clean_id = (poll_id or "").strip()
        raw = self.repository.get(f"/polls/{clean_id}")
        if not isinstance(raw, dict):
            raise ValueError(f"Poll '{clean_id}' was not found.")

        updates = {
            "active": bool(active),
            "closed_at": None if active else int(time.time()),
        }
        merged = self.repository.update(f"/polls/{clean_id}", updates)
        return self._format_poll(merged)

    def submit_vote(
        self, poll_id: str, voter_id: str, option_index: int
    ) -> dict[str, Any]:
        """
        Purpose:
            Record a student's vote on an active poll, strictly enforcing one vote
            per student (`voter_id`) per poll.
        Inputs:
            poll_id (str): Target poll identifier.
            voter_id (str): Unique voter identifier (`student_id` or `device_id`).
            option_index (int): Zero-based index of the chosen option.
        Outputs:
            dict[str, Any]: Updated enriched poll dictionary including live percentages.
        """
        clean_poll_id = (poll_id or "").strip()
        clean_voter_id = (voter_id or "").strip()
        if not clean_voter_id:
            raise ValueError("A valid student/voter identifier is required to vote.")

        raw = self.repository.get(f"/polls/{clean_poll_id}")
        if not isinstance(raw, dict):
            raise ValueError(f"Poll '{clean_poll_id}' was not found.")

        if not raw.get("active", False):
            raise PermissionError("Voting is closed for this poll.")

        options = raw.get("options", [])
        if not isinstance(option_index, int) or not (0 <= option_index < len(options)):
            raise ValueError("Selected poll option index is out of range.")

        votes_map = raw.get("votes") if isinstance(raw.get("votes"), dict) else {}
        # Single-vote enforcement: block if voter_id already exists under /polls/{poll_id}/votes
        if clean_voter_id in votes_map:
            raise PermissionError("You have already voted in this poll.")

        vote_entry = {
            "option_index": int(option_index),
            "voted_at": int(time.time()),
        }
        self.repository.set(
            f"/polls/{clean_poll_id}/votes/{clean_voter_id}", vote_entry
        )
        updated_raw = self.repository.get(f"/polls/{clean_poll_id}")
        return self._format_poll(updated_raw, viewer_id=clean_voter_id)

    def get_poll(
        self, poll_id: str, viewer_id: str | None = None
    ) -> dict[str, Any] | None:
        """
        Purpose:
            Retrieve a single enriched poll by `poll_id`.
        Inputs:
            poll_id (str): Target poll identifier.
            viewer_id (str | None): Optional viewer ID to populate `viewer_vote`.
        Outputs:
            dict[str, Any] | None: Enriched poll dictionary if found, else `None`.
        """
        if not poll_id or not poll_id.strip():
            return None
        raw = self.repository.get(f"/polls/{poll_id.strip()}")
        if not isinstance(raw, dict):
            return None
        return self._format_poll(raw, viewer_id=viewer_id)

    def list_polls(
        self, active_only: bool = False, viewer_id: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Purpose:
            List all polls from `/polls` ordered newest-first, optionally filtering
            to only active polls.
        Inputs:
            active_only (bool): When True, returns only currently open polls.
            viewer_id (str | None): Optional viewer ID to populate `viewer_vote`.
        Outputs:
            list[dict[str, Any]]: Sorted list of enriched poll dictionaries.
        """
        raw_all = self.repository.get("/polls")
        if not isinstance(raw_all, dict):
            return []

        results: list[dict[str, Any]] = []
        for item in raw_all.values():
            if not isinstance(item, dict) or "poll_id" not in item:
                continue
            if active_only and not item.get("active", False):
                continue
            results.append(self._format_poll(item, viewer_id=viewer_id))

        results.sort(key=lambda p: int(p.get("created_at", 0)), reverse=True)
        return results
