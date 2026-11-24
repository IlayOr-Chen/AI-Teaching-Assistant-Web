"""
Purpose:
    Automated tests verifying the Checkpoint 5 Frontend UI (Issue #8):
    HTML shell, CSS design system, ES module static assets (`app.js`, `lecturer.js`,
    `student.js`, `ui.js`, `api.js`, `icons.js`), MIME types, and end-to-end UI
    API flows for Lecturer Dashboard, animated rotating QR countdown ring,
    Student mobile check-in, navigation tabs, and toast notifications.
Inputs:
    In-memory `FirebaseRepository` and FastAPI `TestClient`.
Outputs:
    Deterministic pytest assertions verifying frontend static delivery and structure.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.config import AppConfig
from src.main import create_app
from src.repository.firebase_repository import FirebaseRepository


@pytest.fixture()
def ui_client() -> TestClient:
    """
    Purpose:
        Construct a `TestClient` instance backed by an isolated in-memory
        `FirebaseRepository` for testing frontend static assets and UI flows.
    Inputs:
        None.
    Outputs:
        TestClient: Configured FastAPI test client.
    """
    cfg = AppConfig(
        use_mock_firebase=True,
        jwt_secret="checkpoint-5-frontend-ui-test-secret-32-bytes",
        lecturer_access_code="TA-ADMIN-2026",
    )
    repo = FirebaseRepository(use_mock=True)
    return TestClient(create_app(config=cfg, repository=repo))


def test_frontend_static_assets_and_mime_types(ui_client: TestClient) -> None:
    """
    Purpose:
        Verify that `/`, `/static/index.html`, `/static/css/styles.css`, and all
        ES module JavaScript files load with HTTP 200 and valid MIME types.
    Inputs:
        ui_client (TestClient): Configured FastAPI test client.
    Outputs:
        None.
    """
    # 1. Root HTML shell + Leaflet.js CDN
    root_res = ui_client.get("/")
    assert root_res.status_code == 200
    assert "text/html" in root_res.headers.get("content-type", "")
    assert '/static/css/styles.css' in root_res.text
    assert '/static/js/app.js' in root_res.text
    assert 'id="app"' in root_res.text
    assert 'leaflet@1.9.4/dist/leaflet.css' in root_res.text
    assert 'leaflet@1.9.4/dist/leaflet.js' in root_res.text

    # 2. Design system stylesheet (animations, QR ring, tabs, toast notifications, mobile-first layout, Leaflet map)
    css_res = ui_client.get("/static/css/styles.css")
    assert css_res.status_code == 200
    assert "text/css" in css_res.headers.get("content-type", "")
    for expected_selector in (
        ".qr-ring",
        ".ring-fg",
        ".tabs",
        ".tab-indicator",
        ".toast-root",
        ".toast",
        ".checkin-shell",
        ".projector-overlay",
        ".geofence-map-container",
        ".radius-pills",
        ".radius-pill",
        ".combo-email",
        'input[type="url"]',
    ):
        assert expected_selector in css_res.text, f"Missing CSS rule: {expected_selector}"

    # 3. ES module JavaScript files
    js_files = {
        "/static/js/app.js": ["renderLecturerDashboard", "renderStudentPortal", "renderStudentCheckin"],
        "/static/js/lecturer.js": [
            "renderLecturerAuth",
            "renderLecturerDashboard",
            "buildQrDisplay",
            "Enter your email",
            "label: 'Overview'",
            "label: 'Attendance'",
            "label: 'Roster'",
            "label: 'Polls'",
            "label: 'Comments'",
            "label: 'Video AI'",
            "label: 'Homework'",
            "Select Existing Lesson or Open a New Lesson",
            "Switch / Open Another Lesson",
            "Lesson Scope",
            "Upload classroom photo",
            "Reset YOLO",
            "Sync from Course Excel",
            "Reset 15-Min Device Cooldowns",
            "Use My Current Location",
            "RADIUS_OPTIONS = [30, 50, 100]",
            "invalidateSize()",
            "Copy Link",
            "Scan to check in — protected by GPS Geofencing & 15-min device cooldown.",
        ],
        "/static/js/student.js": [
            "renderStudentCheckin",
            "renderStudentLogin",
            "renderStudentPortal",
            "combo-email",
            "My Attendance History",
            "/api/attendance/my-history",
            "inside the classroom GPS geofence",
        ],
        "/static/js/polls_comments.js": ["Selected — click again to unvote"],
        "/static/js/ai_tools.js": [
            "gemini-3.5-flash-lite",
            "gemini-3.5-flash",
            "gemini-2.5-flash",
            "Clear Key",
            "No API key configured",
            "No API key saved",
            "AI Lecture Video Summarizer",
            "Analyze YouTube Video",
            "Lecture Overview & Walkthrough",
            "Key Concepts & Definitions",
            "Quick Review Questions",
            "Summary copied to clipboard",
            "ta_student_gemini_api_key",
            "ta_student_gemini_model",
            "X-Gemini-Api-Key",
            "X-Gemini-Model",
            "Your Gemini API Key & Model (BYOK)",
            "/api/video/summarize-youtube",
            "/api/video/summaries/",
        ],
        "/static/js/ui.js": ["toast", "createTabs", "pageShell", "withLoading"],
        "/static/js/api.js": ["ApiError", "export const api", "extraHeaders", "Gateway/Server error", "text/html", "delete:"],
        "/static/js/icons.js": ["export function icon", "trash:", "refresh:"],
    }
    for path, expected_tokens in js_files.items():
        res = ui_client.get(path)
        assert res.status_code == 200, path
        content_type = res.headers.get("content-type", "")
        assert "javascript" in content_type, f"Unexpected content-type for {path}: {content_type}"
        for token in expected_tokens:
            assert token in res.text, f"Expected '{token}' in {path}"
    # Confirm free-text Gemini model ID replaced rigid __custom__ dropdown, MP4 upload UI & mode pills were removed
    ai_js = ui_client.get("/static/js/ai_tools.js").text
    assert "__custom__" not in ai_js
    assert "modelSelect" not in ai_js
    assert "Upload MP4 File" not in ai_js
    assert "/api/video/summarize?" not in ai_js
    assert "Fast (Transcript)" not in ai_js
    assert "Audio Only" not in ai_js
    assert "Full Video (Low FPS)" not in ai_js
    # Confirm public lecturer self-registration UI, email placeholder leak, and demo media assets were removed
    lec_js = ui_client.get("/static/js/lecturer.js").text
    assert "Create lecturer account" not in lec_js
    assert "/api/auth/lecturer/register" not in lec_js
    assert "adminL@gmail.com" not in lec_js
    assert "QR rotates every" not in lec_js
    assert "p3.jpeg" not in lec_js
    stu_js = ui_client.get("/static/js/student.js").text
    assert "on the classroom network" not in stu_js
