"""
Purpose:
    Unit and integration tests for `AuthService` (Lecturer & Student role-based
    authentication, PBKDF2 hashing, JWT cookies) and `RosterImporter` (parsing
    `/assets/courseid_130158_participants.xlsx` into Firebase RTDB, handling NaN/empty groups).
Inputs:
    In-memory `FirebaseRepository` instances and the real `/assets/courseid_130158_participants.xlsx` file.
Outputs:
    Deterministic pytest assertions verifying happy paths and edge cases for Issue #2.
"""

from __future__ import annotations

import io
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

from src.config import AppConfig
from src.main import create_app
from src.repository.firebase_repository import FirebaseRepository
from src.services.auth_service import AuthService
from src.services.roster_importer import RosterImporter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_ROSTER_XLSX = PROJECT_ROOT / "assets" / "courseid_130158_participants.xlsx"


@pytest.fixture()
def mock_env() -> tuple[AppConfig, FirebaseRepository, AuthService, RosterImporter]:
    """
    Purpose:
        Provide an isolated in-memory `FirebaseRepository`, `AppConfig`, `AuthService`,
        and `RosterImporter` tuple for each test function.
    Inputs:
        None.
    Outputs:
        tuple[AppConfig, FirebaseRepository, AuthService, RosterImporter]: Fresh test fixtures.
    """
    config = AppConfig(
        use_mock_firebase=True,
        jwt_secret="test-secret-key-for-checkpoint-2-unit-tests",
        lecturer_access_code="SECRET-LECTURER-2026",
    )
    repo = FirebaseRepository(use_mock=True)
    auth = AuthService(repository=repo, config=config)
    importer = RosterImporter(repository=repo)
    return config, repo, auth, importer


def test_password_hashing_and_constant_time_verification() -> None:
    """
    Purpose:
        Verify that `AuthService.hash_password` produces salted PBKDF2-HMAC-SHA256 digests
        and `verify_password` validates correct passwords while rejecting wrong or malformed hashes.
    Inputs:
        None.
    Outputs:
        None.
    """
    hashed1 = AuthService.hash_password("CorrectHorseBatteryStaple!")
    hashed2 = AuthService.hash_password("CorrectHorseBatteryStaple!")
    # Different random salts must produce distinct hash strings
    assert hashed1 != hashed2
    assert hashed1.startswith("pbkdf2_sha256$200000$")

    assert AuthService.verify_password("CorrectHorseBatteryStaple!", hashed1) is True
    assert AuthService.verify_password("WrongPassword", hashed1) is False
    assert AuthService.verify_password("CorrectHorseBatteryStaple!", "malformed$hash") is False


def test_lecturer_registration_and_login_happy_and_edge_cases(
    mock_env: tuple[AppConfig, FirebaseRepository, AuthService, RosterImporter]
) -> None:
    """
    Purpose:
        Verify Lecturer registration, access code enforcement, duplicate detection,
        password validation, login verification, and JWT claim integrity.
    Inputs:
        mock_env: Isolated test fixture tuple.
    Outputs:
        None.
    """
    _, repo, auth, _ = mock_env

    # Edge case 1: Invalid access code rejected with PermissionError
    with pytest.raises(PermissionError, match="Invalid lecturer access code"):
        auth.register_lecturer(
            full_name="Dr. Boaz",
            email="boaz@ariel.ac.il",
            password="securepassword",
            access_code="WRONG-CODE",
        )

    # Edge case 2: Short password rejected with ValueError
    with pytest.raises(ValueError, match="at least 5 characters"):
        auth.register_lecturer(
            full_name="Dr. Boaz",
            email="boaz@ariel.ac.il",
            password="123",
            access_code="SECRET-LECTURER-2026",
        )

    # Default configured lecturer (adminL@gmail.com / 12345) logs in case-insensitively
    demo_upper = auth.login_lecturer("adminL@gmail.com", "12345")
    assert demo_upper["user"]["email"] == "adminl@gmail.com"
    demo_lower = auth.login_lecturer("adminl@gmail.com", "12345")
    assert demo_lower["user"]["email"] == "adminl@gmail.com"

    # Happy case: Valid lecturer registration
    reg_result = auth.register_lecturer(
        full_name="Dr. Boaz Ben-Moshe",
        email="Boaz@Ariel.ac.il",
        password="StrongPassword2026",
        access_code="SECRET-LECTURER-2026",
    )
    assert reg_result["user"]["email"] == "boaz@ariel.ac.il"
    assert reg_result["user"]["role"] == "lecturer"
    assert "password_hash" not in reg_result["user"]

    # Verify stored record in Firebase RTDB contains the PBKDF2 password hash
    stored_lec = repo.get(f"/users/lecturers/{reg_result['user']['lecturer_id']}")
    assert stored_lec is not None
    assert stored_lec["password_hash"].startswith("pbkdf2_sha256$")

    # Edge case 3: Duplicate registration rejected
    with pytest.raises(ValueError, match="already registered"):
        auth.register_lecturer(
            full_name="Dr. Boaz Ben-Moshe",
            email="boaz@ariel.ac.il",
            password="StrongPassword2026",
            access_code="SECRET-LECTURER-2026",
        )

    # Happy case: Lecturer login with registered credentials
    login_result = auth.login_lecturer("boaz@ariel.ac.il", "StrongPassword2026")
    claims = auth.verify_jwt_token(login_result["token"], required_role="lecturer")
    assert claims["email"] == "boaz@ariel.ac.il"
    assert claims["role"] == "lecturer"

    # Edge case 4: Wrong password on login rejected
    with pytest.raises(PermissionError, match="Invalid lecturer email or password"):
        auth.login_lecturer("boaz@ariel.ac.il", "WrongPassword")


def test_jwt_expiration_and_role_enforcement(
    mock_env: tuple[AppConfig, FirebaseRepository, AuthService, RosterImporter]
) -> None:
    """
    Purpose:
        Verify that expired JWT tokens and role mismatches (`student` token presented
        for a `lecturer` requirement) are rejected deterministically.
    Inputs:
        mock_env: Isolated test fixture tuple.
    Outputs:
        None.
    """
    _, _, auth, _ = mock_env

    expired_token = auth.create_jwt_token(
        subject_id="lec_1",
        role="lecturer",
        email="lec@ariel.ac.il",
        full_name="Lecturer",
        expires_in_seconds=-5,
    )
    with pytest.raises(ValueError, match="expired"):
        auth.verify_jwt_token(expired_token)

    student_token = auth.create_jwt_token(
        subject_id="stu_1",
        role="student",
        email="stu@gmail.com",
        full_name="Student",
    )
    with pytest.raises(PermissionError, match="requires role 'lecturer'"):
        auth.verify_jwt_token(student_token, required_role="lecturer")


def test_real_excel_roster_import_104_students_and_nan_groups(
    mock_env: tuple[AppConfig, FirebaseRepository, AuthService, RosterImporter]
) -> None:
    """
    Purpose:
        Verify that `RosterImporter` parses the real `/assets/courseid_130158_participants.xlsx`
        file (104 students, Hebrew headers `שם פרטי`, `שם משפחה`, `דוא"ל`, `קבוצות`),
        gracefully handles the 2 empty/NaN `קבוצות` rows without crashing, normalizes
        `3.0` to `"3"`, and persists all 104 records in Firebase RTDB `/students`.
    Inputs:
        mock_env: Isolated test fixture tuple.
    Outputs:
        None.
    """
    _, repo, auth, importer = mock_env

    summary = importer.import_from_excel(REAL_ROSTER_XLSX)
    assert summary["total_imported"] == 104
    assert summary["unassigned_group_count"] == 2
    assert summary["skipped_rows"] == 0

    stored_students = repo.get("/students")
    assert isinstance(stored_students, dict)
    assert len(stored_students) == 104

    # Verify concatenation of 'שם פרטי' + 'שם משפחה' and float group normalization (3.0 -> "3")
    guy_id = RosterImporter.email_to_student_id("guygg197@gmail.com")
    guy_record = stored_students[guy_id]
    assert guy_record["full_name"] == "גיא אביב"
    assert guy_record["email"] == "guygg197@gmail.com"
    assert guy_record["group"] == "3"

    # Verify the 2 rows with empty 'קבוצות' (Boaz Ben-Moshe & Nadav Sudri) have group == ""
    boaz_id = RosterImporter.email_to_student_id("benmo@ariel.ac.il")
    nadav_id = RosterImporter.email_to_student_id("hpcity123@gmail.com")
    assert stored_students[boaz_id]["full_name"] == "בועז בן משה"
    assert stored_students[boaz_id]["group"] == ""
    assert stored_students[nadav_id]["full_name"] == "נדב סודרי"
    assert stored_students[nadav_id]["group"] == ""

    # Verify searchable roster filtering and group filtering
    search_hits = importer.list_students(search_query="guygg197")
    assert len(search_hits) == 1 and search_hits[0]["full_name"] == "גיא אביב"

    unassigned_hits = importer.list_students(group_filter="unassigned")
    assert len(unassigned_hits) == 2

    # Verify frictionless student authentication works by student_id, email, and full_name
    by_id = auth.authenticate_student(student_id=guy_id)
    assert by_id["user"]["email"] == "guygg197@gmail.com"

    by_email = auth.authenticate_student(email="GUYGG197@gmail.com")
    assert by_email["user"]["student_id"] == guy_id

    by_name = auth.authenticate_student(full_name="בועז בן משה")
    assert by_name["user"]["email"] == "benmo@ariel.ac.il"

    # Verify unknown student is rejected with PermissionError
    with pytest.raises(PermissionError, match="not found in the active class roster"):
        auth.authenticate_student(email="not_in_class@gmail.com")


def test_roster_importer_edge_cases_nan_and_invalid_workbooks(
    mock_env: tuple[AppConfig, FirebaseRepository, AuthService, RosterImporter]
) -> None:
    """
    Purpose:
        Verify `RosterImporter.normalize_group` on IEEE 754 `float('nan')`, `"NaN"`,
        `None`, and verify error handling on invalid Excel files or missing headers.
    Inputs:
        mock_env: Isolated test fixture tuple.
    Outputs:
        None.
    """
    _, _, _, importer = mock_env

    assert RosterImporter.normalize_group(None) == ""
    assert RosterImporter.normalize_group(float("nan")) == ""
    assert RosterImporter.normalize_group("NaN") == ""
    assert RosterImporter.normalize_group("  ") == ""
    assert RosterImporter.normalize_group(3.0) == "3"
    assert RosterImporter.normalize_group("4.0") == "4"
    assert RosterImporter.normalize_group("Group-B") == "Group-B"

    # Build an in-memory workbook missing the required email column
    bad_wb = openpyxl.Workbook()
    ws = bad_wb.active
    ws.append(["שם פרטי", "שם משפחה"])
    ws.append(["ישראל", "ישראלי"])
    buf = io.BytesIO()
    bad_wb.save(buf)

    with pytest.raises(ValueError, match="missing required student email column"):
        importer.import_from_excel(buf.getvalue())


def test_auth_and_roster_http_endpoints_integration() -> None:
    """
    Purpose:
        End-to-end HTTP integration test verifying disabled public lecturer registration
        (403 Forbidden), case-insensitive default lecturer login (`adminL@gmail.com` / `12345`),
        automatic startup seeding of the 104-student Hebrew course roster, protected Excel
        roster upload, student frictionless login, and role authorization guards.
    Inputs:
        None.
    Outputs:
        None.
    """
    cfg = AppConfig(
        use_mock_firebase=True,
        jwt_secret="integration-test-jwt-secret-key-at-least-32-bytes-long",
        lecturer_access_code="TA-ADMIN-2026",
    )
    repo = FirebaseRepository(use_mock=True)
    test_app = create_app(config=cfg, repository=repo)
    client = TestClient(test_app)

    # 1. Unauthenticated import attempt must return 401
    unauth_res = client.post("/api/roster/import?use_default_asset=true")
    assert unauth_res.status_code == 401

    # 2. Public lecturer self-registration endpoints must return 403 Forbidden
    for reg_path in ("/api/auth/lecturer/register", "/api/auth/register"):
        reg_res = client.post(
            reg_path,
            json={
                "full_name": "Prof. Boaz",
                "email": "boaz@ariel.ac.il",
                "password": "lecturerPass123",
                "access_code": "TA-ADMIN-2026",
            },
        )
        assert reg_res.status_code == 403
        assert "disabled" in reg_res.json()["detail"].lower()

    # 3. Default Lecturer logs in case-insensitively (adminL@gmail.com and adminl@gmail.com)
    login_upper = client.post(
        "/api/auth/lecturer/login",
        json={"email": "adminL@gmail.com", "password": "12345"},
    )
    assert login_upper.status_code == 200
    assert "ta_lecturer_session" in login_upper.cookies

    login_lower = client.post(
        "/api/auth/lecturer/login",
        json={"email": "adminl@gmail.com", "password": "12345"},
    )
    assert login_lower.status_code == 200
    assert login_lower.json()["user"]["role"] == "lecturer"

    # 4. Verify /api/auth/me recognizes the lecturer cookie
    me_res = client.get("/api/auth/me")
    assert me_res.status_code == 200
    assert me_res.json()["user"]["role"] == "lecturer"

    # 5. Verify the 104-student Hebrew roster was automatically seeded on startup
    auto_seeded_res = client.get("/api/roster/students")
    assert auto_seeded_res.status_code == 200
    assert auto_seeded_res.json()["count"] == 104

    # 6. Upload the real .xlsx file via multipart POST /api/roster/import
    xlsx_bytes = REAL_ROSTER_XLSX.read_bytes()
    import_res = client.post(
        "/api/roster/import",
        files={
            "file": (
                "courseid_130158_participants.xlsx",
                xlsx_bytes,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert import_res.status_code == 200
    import_payload = import_res.json()
    assert import_payload["total_imported"] == 104
    assert import_payload["unassigned_group_count"] == 2

    # 7. Query imported students via GET /api/roster/students
    list_res = client.get("/api/roster/students", params={"q": "avidorliel"})
    assert list_res.status_code == 200
    assert list_res.json()["count"] == 1
    assert list_res.json()["students"][0]["full_name"] == "ליאל אבידור"

    # 8. Student logs in via POST /api/auth/student/login using a fresh client
    stu_client = TestClient(test_app)
    stu_login = stu_client.post(
        "/api/auth/student/login",
        json={"email": "avidorliel@gmail.com"},
    )
    assert stu_login.status_code == 200
    assert stu_login.json()["user"]["full_name"] == "ליאל אבידור"

    # 9. Student attempting lecturer-only roster import must receive 403 Forbidden
    forbidden_res = stu_client.post("/api/roster/import?use_default_asset=true")
    assert forbidden_res.status_code == 403
