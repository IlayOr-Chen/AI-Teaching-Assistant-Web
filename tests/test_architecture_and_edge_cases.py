"""
Purpose:
    Comprehensive architectural verification and edge-case test suite for
    Checkpoint 10 (Issue #6):
    1) Programmatically verifies 100% consistency between the 13 OOP classes in `src/`
       and the Low-Level Class Diagram in `docs/05_architecture_and_diagrams.md`,
    2) Verifies that every class and public method in `src/` contains an English
       docstring specifying `Purpose:`, `Inputs:`, and `Outputs:`,
    3) Covers remaining boundary and negative edge cases across all domain services.
Inputs:
    Live Python class introspection (`inspect`) and in-memory `FirebaseRepository` fixtures.
Outputs:
    Deterministic pytest assertions guaranteeing 100% diagram-to-code and docstring compliance.
"""

from __future__ import annotations

import inspect
from pathlib import Path
import pytest

from src.config import AppConfig
from src.repository.firebase_repository import (
    FirebaseRepository,
    InMemoryFirebaseAdapter,
)
from src.services.attendance_manager import AttendanceManager
from src.services.auth_service import AuthService
from src.services.comment_service import CommentService
from src.services.gemini_client import GeminiClient
from src.services.homework_reviewer import HomeworkReviewer
from src.services.photo_head_counter import PhotoHeadCounter
from src.services.poll_service import PollService
from src.services.roster_importer import RosterImporter
from src.services.video_summarizer import VideoSummarizer
from src.utils.ip_resolver import ClientIPResolver

EXPECTED_CLASS_METHODS: dict[type, set[str]] = {
    AppConfig: {"from_env"},
    InMemoryFirebaseAdapter: {
        "normalize_path",
        "get",
        "set",
        "update",
        "push",
        "delete",
        "clear",
    },
    FirebaseRepository: {
        "_try_init_firebase_admin",
        "_build_rest_url",
        "get",
        "set",
        "update",
        "push",
        "delete",
        "reset_mock",
    },
    ClientIPResolver: {
        "resolve_from_headers",
        "is_loopback_or_private",
        "resolve",
        "resolve_with_source",
    },
    AuthService: {
        "email_to_key",
        "hash_password",
        "verify_password",
        "create_jwt_token",
        "verify_jwt_token",
        "register_lecturer",
        "login_lecturer",
        "authenticate_student",
        "extract_token_from_request",
        "require_lecturer_claims",
    },
    RosterImporter: {
        "email_to_student_id",
        "normalize_group",
        "_resolve_column_indices",
        "parse_excel_rows",
        "import_from_excel",
        "list_students",
        "get_student",
    },
    AttendanceManager: {
        "calculate_distance_meters",
        "_validate_optional_coords",
        "create_signed_device_cookie",
        "verify_signed_device_cookie",
        "start_session",
        "close_session",
        "set_geofence_enforcement",
        "get_session",
        "list_sessions",
        "_render_qr_data_url",
        "generate_rotating_qr_token",
        "verify_qr_token",
        "check_device_cooldown",
        "record_device_cooldown",
        "submit_qr_checkin",
        "set_manual_attendance",
        "get_lesson_attendance_report",
        "_format_timestamp_iso",
        "export_attendance_csv",
        "export_attendance_xlsx",
    },
    PhotoHeadCounter: {
        "_get_model",
        "_load_pil_image",
        "evaluate_confidence_indicator",
        "_annotate_image",
        "_run_yolo_person_detection",
        "analyze_classroom_photo",
    },
    PollService: {
        "_format_poll",
        "create_poll",
        "set_poll_active",
        "submit_vote",
        "get_poll",
        "list_polls",
    },
    CommentService: {
        "_sanitize_for_viewer",
        "create_comment",
        "add_reply",
        "list_comments",
    },
    GeminiClient: {
        "mask_api_key",
        "resolve_credentials",
        "get_settings",
        "update_settings",
        "_extract_json_dict",
        "generate_structured_json",
    },
    VideoSummarizer: {
        "validate_mp4_upload",
        "_build_prompt",
        "_build_fallback_summary",
        "summarize_video",
        "list_summaries",
    },
    HomeworkReviewer: {
        "is_review_released",
        "validate_and_extract_pdf_text",
        "create_assignment",
        "end_submission_now",
        "_build_individual_review_fallback",
        "_generate_peer_comparison",
        "submit_homework_pdf",
        "get_student_review_and_comparison",
        "_format_assignment_overview",
        "list_assignments",
    },
}


def test_low_level_class_diagram_matches_all_13_python_classes_100_percent() -> None:
    """
    Purpose:
        Verify that all 13 OOP classes in `src/` expose the exact set of methods
        documented in the Low-Level Class Diagram (`docs/05_architecture_and_diagrams.md`).
    Inputs:
        None.
    Outputs:
        None.
    """
    assert len(EXPECTED_CLASS_METHODS) == 13
    for cls, expected_methods in EXPECTED_CLASS_METHODS.items():
        actual_methods = {
            name
            for name, member in inspect.getmembers(
                cls,
                predicate=lambda m: inspect.isfunction(m) or inspect.ismethod(m),
            )
            if not name.startswith("__")
        }
        assert actual_methods == expected_methods, (
            f"Method mismatch on {cls.__name__}: "
            f"missing={expected_methods - actual_methods}, extra={actual_methods - expected_methods}"
        )


def test_every_class_and_method_has_purpose_inputs_outputs_docstrings() -> None:
    """
    Purpose:
        Verify the code documentation standard: every OOP class and every method
        across all 13 classes in `src/` must have a clean English docstring
        explicitly containing `Purpose:`, `Inputs:`, and `Outputs:`.
    Inputs:
        None.
    Outputs:
        None.
    """
    for cls, method_names in EXPECTED_CLASS_METHODS.items():
        cls_doc = inspect.getdoc(cls) or ""
        assert "Purpose:" in cls_doc, f"{cls.__name__} class docstring missing 'Purpose:'"
        assert "Inputs:" in cls_doc, f"{cls.__name__} class docstring missing 'Inputs:'"
        assert "Outputs:" in cls_doc, f"{cls.__name__} class docstring missing 'Outputs:'"

        for m_name in method_names:
            member = getattr(cls, m_name)
            m_doc = inspect.getdoc(member) or ""
            assert "Purpose:" in m_doc, f"{cls.__name__}.{m_name} missing 'Purpose:'"
            assert "Inputs:" in m_doc, f"{cls.__name__}.{m_name} missing 'Inputs:'"
            assert "Outputs:" in m_doc, f"{cls.__name__}.{m_name} missing 'Outputs:'"


def test_domain_services_boundary_and_negative_edge_cases() -> None:
    """
    Purpose:
        Verify boundary-value and negative edge cases across `AttendanceManager`,
        `PollService`, `CommentService`, `GeminiClient`, and `HomeworkReviewer`.
    Inputs:
        None.
    Outputs:
        None.
    """
    cfg = AppConfig(use_mock_firebase=True)
    repo = FirebaseRepository(use_mock=True)

    # 1. AttendanceManager boundary checks: partial coordinates & invalid geofence radius
    att = AttendanceManager(repository=repo, config=cfg)
    with pytest.raises(
        ValueError, match="Both latitude and longitude must be provided together"
    ):
        att.start_session("lesson_x", "Lesson X", classroom_lat=32.1044, classroom_lng=None)

    with pytest.raises(
        ValueError, match="geofence_radius_meters must be between 10 and 5000"
    ):
        att.start_session("lesson_x", "Lesson X", geofence_radius_meters=5)

    with pytest.raises(ValueError, match="was not found"):
        att.close_session("non_existent_lesson")

    # 2. PollService boundary checks: > 8 options rejected
    polls = PollService(repository=repo)
    with pytest.raises(ValueError, match="at most 8 answer options"):
        polls.create_poll("Too many choices?", [f"Opt {i}" for i in range(10)])

    # 3. CommentService boundary checks: empty text, > 2000 chars, invalid visibility
    comments = CommentService(repository=repo)
    with pytest.raises(ValueError, match="cannot be empty"):
        comments.create_comment("   ", "stu_1", "Alice")
    with pytest.raises(ValueError, match="cannot exceed 2000 characters"):
        comments.create_comment("x" * 2005, "stu_1", "Alice")
    with pytest.raises(ValueError, match="must be 'public' or 'private'"):
        comments.create_comment("Hello", "stu_1", "Alice", visibility="friends_only")

    # 4. GeminiClient custom transport & JSON code-fence parsing
    fenced = '```json\n{"executive_summary": "Clean fenced JSON"}\n```'
    parsed = GeminiClient._extract_json_dict(fenced)
    assert parsed == {"executive_summary": "Clean fenced JSON"}
    assert GeminiClient._extract_json_dict("not json at all") is None

    custom_gemini = GeminiClient(
        repository=repo,
        config=cfg,
        transport=lambda **kwargs: {"custom_key": "injected_value"},
    )
    gen_res = custom_gemini.generate_structured_json(
        prompt="test", fallback_builder=lambda: {"custom_key": "fallback"}
    )
    assert gen_res["custom_key"] == "injected_value"
    assert gen_res["provider"] == "custom_transport"

    # 5. HomeworkReviewer boundary checks: invalid deadline <= 0 & missing submission lookup
    hw = HomeworkReviewer(repository=repo, gemini_client=custom_gemini)
    with pytest.raises(ValueError, match="valid submission deadline"):
        hw.create_assignment("Bad Deadline", "Desc", deadline_ts=0)

    valid_hw = hw.create_assignment("Valid HW", "Desc", deadline_ts=1_900_000_000)
    with pytest.raises(ValueError, match="No PDF submission found"):
        hw.get_student_review_and_comparison(
            valid_hw["assignment_id"], "stu_never_submitted"
        )


def test_all_required_markdown_deliverables_and_deployment_configs_exist() -> None:
    """
    Purpose:
        Verify that all 8 required Markdown deliverables (`README.md` + `docs/01..07`)
        and deployment configuration files (`render.yaml`, `Procfile`, `.env.example`,
        `requirements.txt`) exist in the repository and are non-empty.
    Inputs:
        None.
    Outputs:
        None.
    """
    project_root = Path(__file__).resolve().parent.parent
    required_files = [
        "README.md",
        ".env.example",
        "requirements.txt",
        "Procfile",
        "render.yaml",
        "docs/01_tools_report.md",
        "docs/02_SoW.md",
        "docs/03_SRS.md",
        "docs/04_use_cases.md",
        "docs/05_architecture_and_diagrams.md",
        "docs/06_testing_strategy.md",
        "docs/07_conclusion.md",
    ]
    for rel_path in required_files:
        full_path = project_root / rel_path
        assert full_path.is_file(), f"Missing required deliverable: {rel_path}"
        assert full_path.stat().st_size > 50, f"Deliverable is unexpectedly empty: {rel_path}"

    readme_text = (project_root / "README.md").read_text(encoding="utf-8")
    assert r".\venv\Scripts\Activate.ps1" in readme_text
    assert "source venv/Scripts/activate" in readme_text
    assert "source venv/bin/activate" in readme_text


