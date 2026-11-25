"""
Purpose:
    Automated tests for Checkpoint 6 (Issue #9): Attendance Management UI &
    CSV/Excel (`.xlsx`) export endpoints, manual attendance toggle persistence,
    and YOLO GREEN/ORANGE/RED visual confidence display wiring.
Inputs:
    In-memory `FirebaseRepository`, real `/assets/courseid_130158_participants.xlsx`,
    and FastAPI `TestClient`.
Outputs:
    Deterministic pytest assertions verifying CSV/XLSX spreadsheet generation and UI wiring.
"""

from __future__ import annotations

import csv
import io
import openpyxl
from fastapi.testclient import TestClient

from src.config import AppConfig
from src.main import create_app
from src.repository.firebase_repository import FirebaseRepository
from src.services.roster_importer import RosterImporter


def _setup_authenticated_lecturer_client() -> TestClient:
    """
    Purpose:
        Create a `TestClient` with an authenticated Lecturer session and the
        104-student course roster imported into mock Firebase RTDB.
    Inputs:
        None.
    Outputs:
        TestClient: Authenticated lecturer test client.
    """
    cfg = AppConfig(
        use_mock_firebase=True,
        jwt_secret="checkpoint-6-export-and-ui-test-secret-32-bytes",
        lecturer_access_code="TA-ADMIN-2026",
    )
    repo = FirebaseRepository(use_mock=True)
    client = TestClient(create_app(config=cfg, repository=repo))

    reg = client.post(
        "/api/auth/lecturer/login",
        json={
            "email": "adminL@gmail.com",
            "password": "12345",
        },
    )
    assert reg.status_code == 200

    imp = client.post("/api/roster/import?use_default_asset=true")
    assert imp.status_code == 200
    assert imp.json()["total_imported"] == 104
    return client


def test_attendance_csv_export_utf8_bom_and_hebrew_roster() -> None:
    """
    Purpose:
        Verify that `GET /api/attendance/sessions/{lesson_id}/export?format=csv`
        returns a valid UTF-8-BOM CSV containing all 104 students, preserving
        Hebrew names and reflecting manual attendance toggles.
    Inputs:
        None.
    Outputs:
        None.
    """
    client = _setup_authenticated_lecturer_client()
    guy_id = RosterImporter.email_to_student_id("guygg197@gmail.com")

    # Toggle Guy Aviv to present manually
    manual_res = client.post(
        "/api/attendance/sessions/lesson_1/manual",
        json={"student_id": guy_id, "status": "present"},
    )
    assert manual_res.status_code == 200

    csv_res = client.get("/api/attendance/sessions/lesson_1/export?format=csv")
    assert csv_res.status_code == 200
    assert "text/csv" in csv_res.headers.get("content-type", "")
    assert 'filename="attendance_lesson_1.csv"' in csv_res.headers.get(
        "content-disposition", ""
    )

    # Verify UTF-8 BOM so Excel opens Hebrew characters properly
    assert csv_res.content.startswith(b"\xef\xbb\xbf")
    decoded = csv_res.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(decoded)))
    assert len(rows) == 105  # 1 header row + 104 student rows
    assert rows[0] == [
        "Student ID",
        "Full Name",
        "Email",
        "Group",
        "Lesson ID",
        "Status",
        "Method",
        "Checked In At",
        "Distance (m)",
        "Geofence Status",
    ]

    guy_row = next(r for r in rows[1:] if r[0] == guy_id)
    assert guy_row[1] == "גיא אביב"
    assert guy_row[5] == "present"
    assert guy_row[6] == "manual"
    assert "UTC" in guy_row[7]
    assert guy_row[9] == "manual"


def test_attendance_xlsx_export_valid_workbook_and_format_validation() -> None:
    """
    Purpose:
        Verify that `GET /api/attendance/sessions/{lesson_id}/export?format=xlsx`
        produces a valid `.xlsx` workbook with all 104 students and rejects
        unsupported export formats with HTTP 400.
    Inputs:
        None.
    Outputs:
        None.
    """
    client = _setup_authenticated_lecturer_client()
    boaz_id = RosterImporter.email_to_student_id("benmo@ariel.ac.il")

    client.post(
        "/api/attendance/sessions/lesson_2/manual",
        json={"student_id": boaz_id, "status": "present"},
    )

    xlsx_res = client.get("/api/attendance/sessions/lesson_2/export?format=xlsx")
    assert xlsx_res.status_code == 200
    assert (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        in xlsx_res.headers.get("content-type", "")
    )
    assert 'filename="attendance_lesson_2.xlsx"' in xlsx_res.headers.get(
        "content-disposition", ""
    )

    wb = openpyxl.load_workbook(io.BytesIO(xlsx_res.content), data_only=True)
    ws = wb.active
    sheet_rows = list(ws.iter_rows(values_only=True))
    assert len(sheet_rows) == 105
    boaz_row = next(r for r in sheet_rows[1:] if r[0] == boaz_id)
    assert boaz_row[1] == "בועז בן משה"
    assert boaz_row[3] == "Unassigned"
    assert boaz_row[5] == "present"

    # Unsupported format must return 400 Bad Request
    bad_fmt = client.get("/api/attendance/sessions/lesson_2/export?format=pdf")
    assert bad_fmt.status_code == 400


def test_export_endpoint_requires_lecturer_role() -> None:
    """
    Purpose:
        Verify that anonymous callers (401) and students (403) cannot download
        the class attendance export spreadsheet.
    Inputs:
        None.
    Outputs:
        None.
    """
    client = _setup_authenticated_lecturer_client()
    anon_client = TestClient(client.app)
    assert (
        anon_client.get("/api/attendance/sessions/lesson_1/export?format=csv").status_code
        == 401
    )

    stu_client = TestClient(client.app)
    stu_login = stu_client.post(
        "/api/auth/student/login", json={"email": "guygg197@gmail.com"}
    )
    assert stu_login.status_code == 200
    assert (
        stu_client.get("/api/attendance/sessions/lesson_1/export?format=csv").status_code
        == 403
    )


def test_attendance_management_and_yolo_ui_wiring() -> None:
    """
    Purpose:
        Verify that the Lecturer Dashboard frontend includes the Roster & YOLO
        Headcount tab, CSV/Excel export controls, manual attendance toggle, and
        GREEN / ORANGE / RED visual confidence card styles.
    Inputs:
        None.
    Outputs:
        None.
    """
    client = _setup_authenticated_lecturer_client()

    js_res = client.get("/static/js/lecturer.js")
    assert js_res.status_code == 200
    for expected in (
        "renderRosterAndHeadcountTab",
        "label: 'Roster'",
        "Export CSV (.csv)",
        "Export Excel (.xlsx)",
        "Upload classroom photo",
        "Reset YOLO",
        "Lesson Scope",
        "YOLO Detected Count",
        "Self-Registered Count",
        "Mark Present",
        "Mark Absent",
    ):
        assert expected in js_res.text, f"Missing '{expected}' in lecturer.js"
    assert "p3.jpeg" not in js_res.text
    assert "Test on Classroom Photo" not in js_res.text

    stu_res = client.get("/static/js/student.js")
    assert stu_res.status_code == 200
    assert "My Attendance History" in stu_res.text
    assert "inside the classroom GPS geofence" in stu_res.text
    assert "on the classroom network" not in stu_res.text

    css_res = client.get("/static/css/styles.css")
    assert css_res.status_code == 200
    for expected_css in (
        ".yolo-status-card.status-GREEN",
        ".yolo-status-card.status-ORANGE",
        ".yolo-status-card.status-RED",
        ".yolo-compare-grid",
        ".table-wrap",
    ):
        assert expected_css in css_res.text, f"Missing '{expected_css}' in styles.css"
