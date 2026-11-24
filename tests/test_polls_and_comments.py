"""
Purpose:
    Comprehensive unit and HTTP integration tests for `PollService` (Feature 3 —
    On-the-Spot KISS Polls Tab) and `CommentService` (Feature 2 — Threaded Comments
    & Suggestions Tab with Public/Lecturer-Only visibility, Named/Anonymous identity,
    and Lesson/General scope tags) for Checkpoint 7 (Issue #10).
Inputs:
    In-memory `FirebaseRepository` instances and FastAPI `TestClient`.
Outputs:
    Deterministic pytest assertions verifying happy cases, privacy guards, and edge cases.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.config import AppConfig
from src.main import create_app
from src.repository.firebase_repository import FirebaseRepository
from src.services.comment_service import CommentService
from src.services.poll_service import PollService


def test_poll_service_lifecycle_voting_percentages_and_single_vote_enforcement() -> None:
    """
    Purpose:
        Verify `PollService` creation validation, live percentage calculations,
        single-vote-per-student enforcement, and open/close voting controls.
    Inputs:
        None.
    Outputs:
        None.
    """
    repo = FirebaseRepository(use_mock=True)
    polls = PollService(repository=repo)

    # Edge cases: empty question or fewer than 2 distinct options rejected
    with pytest.raises(ValueError, match="cannot be empty"):
        polls.create_poll("", ["Yes", "No"])
    with pytest.raises(ValueError, match="at least 2 distinct"):
        polls.create_poll("Only one option?", ["Yes", "Yes"])

    # Create active poll
    created = polls.create_poll(
        question="Which design pattern encapsulates interchangeable algorithms?",
        options=["Strategy", "Observer", "Singleton"],
        lesson_tag="Lesson 2",
        created_by="Prof. Boaz",
    )
    poll_id = created["poll_id"]
    assert created["active"] is True
    assert created["total_votes"] == 0
    assert len(created["options"]) == 3

    # Student 1 votes for option 0 ("Strategy")
    after_v1 = polls.submit_vote(poll_id, voter_id="stu_alice", option_index=0)
    assert after_v1["total_votes"] == 1
    assert after_v1["viewer_vote"] == 0
    assert after_v1["options"][0]["count"] == 1
    assert after_v1["options"][0]["percentage"] == 100.0

    # Edge case: Student 1 tries to vote a second time -> blocked with PermissionError
    with pytest.raises(PermissionError, match="already voted"):
        polls.submit_vote(poll_id, voter_id="stu_alice", option_index=1)

    # Edge case: Out-of-range option index -> ValueError
    with pytest.raises(ValueError, match="out of range"):
        polls.submit_vote(poll_id, voter_id="stu_bob", option_index=9)

    # Student 2 votes for option 1 ("Observer")
    after_v2 = polls.submit_vote(poll_id, voter_id="stu_bob", option_index=1)
    assert after_v2["total_votes"] == 2
    assert after_v2["options"][0]["percentage"] == 50.0
    assert after_v2["options"][1]["percentage"] == 50.0

    # Lecturer closes the poll -> Student 3 cannot vote
    closed = polls.set_poll_active(poll_id, active=False)
    assert closed["active"] is False
    with pytest.raises(PermissionError, match="Voting is closed"):
        polls.submit_vote(poll_id, voter_id="stu_charlie", option_index=0)

    # Lecturer reopens the poll -> Student 3 votes for option 0
    polls.set_poll_active(poll_id, active=True)
    after_v3 = polls.submit_vote(poll_id, voter_id="stu_charlie", option_index=0)
    assert after_v3["total_votes"] == 3
    assert after_v3["options"][0]["percentage"] == 66.7
    assert after_v3["options"][1]["percentage"] == 33.3


def test_comment_service_public_private_anonymous_scope_and_threaded_replies() -> None:
    """
    Purpose:
        Verify `CommentService` visibility isolation (`public` vs `private` Lecturer-only),
        identity masking (`With Name` vs `Anonymous`), scope tag filtering (`Lesson 1` vs `General`),
        and threaded reply permissions.
    Inputs:
        None.
    Outputs:
        None.
    """
    repo = FirebaseRepository(use_mock=True)
    comments = CommentService(repository=repo)

    # 1. Alice posts a Public, Named comment for Lesson 1
    c_pub_named = comments.create_comment(
        text="Can we review the Mermaid sequence diagram syntax?",
        author_id="stu_alice",
        author_name="Alice Cohen",
        author_role="student",
        visibility="public",
        is_anonymous=False,
        scope_tag="Lesson 1",
    )
    assert c_pub_named["display_name"] == "Alice Cohen"
    assert c_pub_named["visibility"] == "public"

    # 2. Alice posts a Public, Anonymous suggestion for General
    c_pub_anon = comments.create_comment(
        text="Please speak slightly louder for the back rows.",
        author_id="stu_alice",
        author_name="Alice Cohen",
        author_role="student",
        visibility="public",
        is_anonymous=True,
        scope_tag="General",
    )
    assert c_pub_anon["display_name"] == "Anonymous Student"
    assert "Alice Cohen" not in str(c_pub_anon)

    # 3. Alice posts a Private (Lecturer-only) comment for Lesson 1
    c_priv = comments.create_comment(
        text="I had a medical absence last week; attaching my note.",
        author_id="stu_alice",
        author_name="Alice Cohen",
        author_role="student",
        visibility="private",
        is_anonymous=False,
        scope_tag="Lesson 1",
    )
    assert c_priv["visibility"] == "private"

    # Visibility Check A: Bob (another student) sees ONLY the 2 public comments
    bob_view = comments.list_comments(viewer_role="student", viewer_id="stu_bob")
    assert len(bob_view) == 2
    assert all(c["visibility"] == "public" for c in bob_view)

    # Visibility Check B: Alice sees the 2 public comments + her own private comment (3 total)
    alice_view = comments.list_comments(viewer_role="student", viewer_id="stu_alice")
    assert len(alice_view) == 3

    # Visibility Check C: Lecturer sees all 3 comments (public + private)
    lec_view = comments.list_comments(viewer_role="lecturer", viewer_id="lec_boaz")
    assert len(lec_view) == 3

    # Scope Tag Filtering Check: Lecturer filtering by "General" sees only the 1 General comment
    general_only = comments.list_comments(
        viewer_role="lecturer", viewer_id="lec_boaz", scope_tag="General"
    )
    assert len(general_only) == 1
    assert general_only[0]["comment_id"] == c_pub_anon["comment_id"]

    # Threaded Replies Check:
    # Bob replies anonymously to the public comment
    with_reply = comments.add_reply(
        comment_id=c_pub_named["comment_id"],
        text="+1, I would also love a quick example!",
        author_id="stu_bob",
        author_name="Bob Levi",
        author_role="student",
        is_anonymous=True,
    )
    assert with_reply["reply_count"] == 1
    assert with_reply["replies"][0]["display_name"] == "Anonymous Student"

    # Bob tries to reply to Alice's private (Lecturer-only) comment -> blocked with PermissionError
    with pytest.raises(PermissionError, match="Only the lecturer"):
        comments.add_reply(
            comment_id=c_priv["comment_id"],
            text="I am trying to snoop on a private thread.",
            author_id="stu_bob",
            author_name="Bob Levi",
            author_role="student",
        )

    # Lecturer replies to Alice's private comment -> succeeds
    priv_thread = comments.add_reply(
        comment_id=c_priv["comment_id"],
        text="Received, thank you Alice — I updated your Lesson 1 attendance.",
        author_id="lec_boaz",
        author_name="Prof. Boaz",
        author_role="lecturer",
    )
    assert priv_thread["reply_count"] == 1
    assert priv_thread["replies"][0]["author_role"] == "lecturer"


def test_polls_and_comments_http_endpoints_and_ui_assets() -> None:
    """
    Purpose:
        End-to-end HTTP integration test verifying `/api/polls`, `/api/comments`,
        role-based permissions, single-vote enforcement, private comment isolation,
        and static delivery of `/static/js/polls_comments.js`.
    Inputs:
        None.
    Outputs:
        None.
    """
    cfg = AppConfig(
        use_mock_firebase=True,
        jwt_secret="checkpoint-7-polls-comments-http-secret-32-bytes",
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
            },
            "stu_bob": {
                "student_id": "stu_bob",
                "full_name": "Bob Levi",
                "email": "bob@ariel.ac.il",
                "group": "1",
            },
        },
    )
    app = create_app(config=cfg, repository=repo)

    lec_client = TestClient(app)
    alice_client = TestClient(app)
    bob_client = TestClient(app)

    # Authenticate Lecturer, Alice, and Bob
    lec_client.post(
        "/api/auth/lecturer/register",
        json={
            "full_name": "Prof. Boaz",
            "email": "boaz@ariel.ac.il",
            "password": "StrongPassword2026",
            "access_code": "TA-ADMIN-2026",
        },
    )
    alice_client.post("/api/auth/student/login", json={"email": "alice@ariel.ac.il"})
    bob_client.post("/api/auth/student/login", json={"email": "bob@ariel.ac.il"})

    # 1. Student cannot create a poll (403 Forbidden)
    assert (
        alice_client.post(
            "/api/polls",
            json={"question": "Unauthorized?", "options": ["A", "B"]},
        ).status_code
        == 403
    )

    # 2. Lecturer creates a poll
    poll_res = lec_client.post(
        "/api/polls",
        json={
            "question": "How is the lecture pacing so far?",
            "options": ["Too fast", "Just right", "Too slow"],
            "lesson_tag": "Lesson 1",
        },
    )
    assert poll_res.status_code == 200
    poll_id = poll_res.json()["poll_id"]

    # 3. Alice votes once -> 200 OK; second vote -> 403 Forbidden
    v1 = alice_client.post(f"/api/polls/{poll_id}/vote", json={"option_index": 1})
    assert v1.status_code == 200
    assert v1.json()["options"][1]["percentage"] == 100.0

    v1_dup = alice_client.post(f"/api/polls/{poll_id}/vote", json={"option_index": 0})
    assert v1_dup.status_code == 403

    # 4. Alice posts a private comment to the Lecturer
    priv_res = alice_client.post(
        "/api/comments",
        json={
            "text": "Private question for the lecturer.",
            "visibility": "private",
            "is_anonymous": False,
            "scope_tag": "Lesson 1",
        },
    )
    assert priv_res.status_code == 200
    cid = priv_res.json()["comment_id"]

    # 5. Bob lists comments -> cannot see Alice's private comment, and cannot reply to it (403)
    bob_list = bob_client.get("/api/comments").json()
    assert bob_list["count"] == 0
    bob_reply = bob_client.post(
        f"/api/comments/{cid}/replies", json={"text": "Snooping"}
    )
    assert bob_reply.status_code == 403

    # 6. Lecturer sees Alice's private comment and replies to it
    lec_list = lec_client.get("/api/comments", params={"scope_tag": "Lesson 1"}).json()
    assert lec_list["count"] == 1
    lec_reply = lec_client.post(
        f"/api/comments/{cid}/replies",
        json={"text": "Happy to help after class!"},
    )
    assert lec_reply.status_code == 200
    assert lec_reply.json()["reply_count"] == 1

    # 7. Verify frontend Polls & Comments JS asset loads cleanly
    ui_js = lec_client.get("/static/js/polls_comments.js")
    assert ui_js.status_code == 200
    assert "javascript" in ui_js.headers.get("content-type", "")
    assert "renderPollsTab" in ui_js.text
    assert "renderCommentsTab" in ui_js.text
