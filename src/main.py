"""
Purpose:
    FastAPI application entry point and application factory (`create_app`) for the
    Web-Based AI Teaching Assistant platform.
Inputs:
    Optional `AppConfig` and `FirebaseRepository` instances for dependency injection.
Outputs:
    Configured `FastAPI` application instance serving REST endpoints and static UI assets.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import (
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src import __version__
from src.config import AppConfig
from src.repository.firebase_repository import FirebaseRepository
from src.services.attendance_manager import (
    DEVICE_COOKIE_NAME,
    AttendanceManager,
)
from src.services.auth_service import (
    LECTURER_COOKIE_NAME,
    STUDENT_COOKIE_NAME,
    AuthService,
)
from src.services.photo_head_counter import PhotoHeadCounter
from src.services.roster_importer import RosterImporter
from src.utils.ip_resolver import ClientIPResolver

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_ROSTER_ASSET = (
    Path(__file__).resolve().parent.parent
    / "assets"
    / "courseid_130158_participants.xlsx"
)
DEFAULT_CLASSROOM_PHOTO_ASSET = (
    Path(__file__).resolve().parent.parent / "assets" / "p3.jpeg"
)


class LecturerRegisterRequest(BaseModel):
    """
    Purpose:
        Pydantic schema validating lecturer registration payloads.
    Inputs:
        full_name (str), email (str), password (str), access_code (str).
    Outputs:
        Validated `LecturerRegisterRequest` model instance.
    """

    full_name: str
    email: str
    password: str
    access_code: str


class LecturerLoginRequest(BaseModel):
    """
    Purpose:
        Pydantic schema validating lecturer login payloads.
    Inputs:
        email (str), password (str).
    Outputs:
        Validated `LecturerLoginRequest` model instance.
    """

    email: str
    password: str


class StudentLoginRequest(BaseModel):
    """
    Purpose:
        Pydantic schema validating frictionless student authentication payloads.
    Inputs:
        student_id (str | None), email (str | None), full_name (str | None).
    Outputs:
        Validated `StudentLoginRequest` model instance.
    """

    student_id: str | None = None
    email: str | None = None
    full_name: str | None = None


class StartAttendanceSessionRequest(BaseModel):
    """
    Purpose:
        Pydantic schema for starting or reopening a smart attendance session.
    Inputs:
        lesson_id (str), lesson_title (str), enforce_same_network (bool | None),
        rotation_seconds (int | None).
    Outputs:
        Validated `StartAttendanceSessionRequest` instance.
    """

    lesson_id: str
    lesson_title: str = ""
    enforce_same_network: bool | None = None
    rotation_seconds: int | None = None


class NetworkToggleRequest(BaseModel):
    """
    Purpose:
        Pydantic schema for toggling the Same-Network IP check on an attendance session.
    Inputs:
        enforce_same_network (bool): True to require matching external IP, False to disable.
    Outputs:
        Validated `NetworkToggleRequest` instance.
    """

    enforce_same_network: bool


class StudentQRCheckInRequest(BaseModel):
    """
    Purpose:
        Pydantic schema for a Student QR code mobile check-in submission.
    Inputs:
        lesson_id (str), student_id (str), qr_token (str).
    Outputs:
        Validated `StudentQRCheckInRequest` instance.
    """

    lesson_id: str
    student_id: str
    qr_token: str


class ManualAttendanceRequest(BaseModel):
    """
    Purpose:
        Pydantic schema for manual lecturer attendance override (`present` or `absent`).
    Inputs:
        student_id (str), status (str).
    Outputs:
        Validated `ManualAttendanceRequest` instance.
    """

    student_id: str
    status: str


def create_app(
    config: AppConfig | None = None,
    repository: FirebaseRepository | None = None,
) -> FastAPI:
    """
    Purpose:
        Construct and configure the FastAPI application instance with dependency-injected
        configuration, Firebase RTDB repository, `AuthService`, `RosterImporter`,
        and `AttendanceManager`.
    Inputs:
        config (AppConfig | None): Application configuration (loaded from environment if None).
        repository (FirebaseRepository | None): Pre-configured Firebase repository
            (created from `config` if None).
    Outputs:
        FastAPI: Initialized FastAPI application ready to serve requests.
    """
    resolved_config = config or AppConfig.from_env()
    resolved_repo = repository or FirebaseRepository(
        db_url=resolved_config.firebase_db_url,
        credentials_path=resolved_config.firebase_credentials_path,
        use_mock=resolved_config.use_mock_firebase,
    )
    auth_service = AuthService(repository=resolved_repo, config=resolved_config)
    roster_importer = RosterImporter(repository=resolved_repo)
    attendance_manager = AttendanceManager(
        repository=resolved_repo, config=resolved_config
    )
    photo_head_counter = PhotoHeadCounter(
        repository=resolved_repo, config=resolved_config
    )

    fastapi_app = FastAPI(
        title="Web-Based AI Teaching Assistant",
        description=(
            "OOP/OOD Classroom Platform: Smart QR + YOLO Attendance, Live Polls, "
            "Threaded Comments, Gemini MP4 Lecture Summarizer, and PDF Homework Reviewer."
        ),
        version=__version__,
    )

    # Store configuration, repository, and domain services on app.state
    fastapi_app.state.config = resolved_config
    fastapi_app.state.repository = resolved_repo
    fastapi_app.state.auth_service = auth_service
    fastapi_app.state.roster_importer = roster_importer
    fastapi_app.state.attendance_manager = attendance_manager
    fastapi_app.state.photo_head_counter = photo_head_counter

    if STATIC_DIR.is_dir():
        fastapi_app.mount(
            "/static", StaticFiles(directory=str(STATIC_DIR)), name="static"
        )

    @fastapi_app.get("/", response_class=HTMLResponse)
    async def serve_index() -> Any:
        """
        Purpose:
            Serve the main frontend HTML application shell at the root URL (`/`).
        Inputs:
            None.
        Outputs:
            FileResponse | HTMLResponse: The root HTML document.
        """
        index_file = STATIC_DIR / "index.html"
        if index_file.is_file():
            return FileResponse(str(index_file))
        return HTMLResponse(
            content="<h1>AI Teaching Assistant Web</h1><p>Backend running.</p>",
            status_code=200,
        )

    @fastapi_app.get("/api/health", response_class=JSONResponse)
    async def health_check(request: Request) -> dict[str, Any]:
        """
        Purpose:
            Provide a deterministic health and configuration readiness endpoint
            for automated testing, Render health checks, and Cloudflare Tunnel verification.
        Inputs:
            request (Request): Incoming FastAPI HTTP request.
        Outputs:
            dict[str, Any]: Health status payload including service version and Firebase mode.
        """
        app_repo: FirebaseRepository = request.app.state.repository
        app_cfg: AppConfig = request.app.state.config
        return {
            "status": "ok",
            "service": "AI Teaching Assistant Web",
            "version": __version__,
            "firebase_mode": "mock" if app_repo.use_mock else "live",
            "firebase_db_url": app_cfg.firebase_db_url,
        }

    @fastapi_app.get("/api/network-info", response_class=JSONResponse)
    async def network_info(request: Request) -> dict[str, str]:
        """
        Purpose:
            Return the caller's resolved external IP address and the HTTP header
            source (`CF-Connecting-IP`, `X-Forwarded-For`, or `client.host`).
        Inputs:
            request (Request): Incoming FastAPI HTTP request.
        Outputs:
            dict[str, str]: Dictionary containing `"ip"` and `"source"`.
        """
        return ClientIPResolver.resolve_with_source(request)

    @fastapi_app.post("/api/auth/lecturer/register", response_class=JSONResponse)
    async def register_lecturer_endpoint(
        payload: LecturerRegisterRequest, response: Response, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Register a new Lecturer account protected by `LECTURER_ACCESS_CODE`
            and set an HTTP-only signed JWT cookie (`ta_lecturer_session`).
        Inputs:
            payload (LecturerRegisterRequest): Registration form data.
            response (Response): FastAPI HTTP response object used to set cookies.
            request (Request): Incoming FastAPI HTTP request.
        Outputs:
            dict[str, Any]: Registered lecturer profile and JWT token.
        """
        svc: AuthService = request.app.state.auth_service
        cfg: AppConfig = request.app.state.config
        try:
            result = svc.register_lecturer(
                full_name=payload.full_name,
                email=payload.email,
                password=payload.password,
                access_code=payload.access_code,
            )
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

        response.set_cookie(
            key=LECTURER_COOKIE_NAME,
            value=result["token"],
            httponly=True,
            samesite="lax",
            max_age=cfg.jwt_expiration_hours * 3600,
        )
        return result

    @fastapi_app.post("/api/auth/lecturer/login", response_class=JSONResponse)
    async def login_lecturer_endpoint(
        payload: LecturerLoginRequest, response: Response, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Authenticate an existing Lecturer and issue an HTTP-only JWT cookie.
        Inputs:
            payload (LecturerLoginRequest): Lecturer email and password.
            response (Response): FastAPI HTTP response for setting cookie.
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Authenticated lecturer profile and JWT token.
        """
        svc: AuthService = request.app.state.auth_service
        cfg: AppConfig = request.app.state.config
        try:
            result = svc.login_lecturer(email=payload.email, password=payload.password)
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

        response.set_cookie(
            key=LECTURER_COOKIE_NAME,
            value=result["token"],
            httponly=True,
            samesite="lax",
            max_age=cfg.jwt_expiration_hours * 3600,
        )
        return result

    @fastapi_app.post("/api/auth/student/login", response_class=JSONResponse)
    async def login_student_endpoint(
        payload: StudentLoginRequest, response: Response, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Authenticate a Student against the imported Firebase class roster
            and issue an HTTP-only signed student session cookie (`ta_student_session`).
        Inputs:
            payload (StudentLoginRequest): Student ID, email, or full name.
            response (Response): FastAPI HTTP response for setting cookie.
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Matched student record and JWT token.
        """
        svc: AuthService = request.app.state.auth_service
        cfg: AppConfig = request.app.state.config
        try:
            result = svc.authenticate_student(
                student_id=payload.student_id,
                email=payload.email,
                full_name=payload.full_name,
            )
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

        response.set_cookie(
            key=STUDENT_COOKIE_NAME,
            value=result["token"],
            httponly=True,
            samesite="lax",
            max_age=cfg.jwt_expiration_hours * 3600,
        )
        return result

    @fastapi_app.post("/api/auth/logout", response_class=JSONResponse)
    async def logout_endpoint(response: Response) -> dict[str, str]:
        """
        Purpose:
            Clear both Lecturer and Student authentication cookies.
        Inputs:
            response (Response): FastAPI HTTP response.
        Outputs:
            dict[str, str]: Confirmation status.
        """
        response.delete_cookie(LECTURER_COOKIE_NAME)
        response.delete_cookie(STUDENT_COOKIE_NAME)
        return {"status": "logged_out"}

    @fastapi_app.get("/api/auth/me", response_class=JSONResponse)
    async def current_user_endpoint(request: Request) -> dict[str, Any]:
        """
        Purpose:
            Inspect the caller's session cookie or Bearer token and return their
            decoded user claims (`role`, `sub`, `email`, `full_name`).
        Inputs:
            request (Request): Incoming FastAPI HTTP request.
        Outputs:
            dict[str, Any]: Decoded user claims dictionary.
        """
        svc: AuthService = request.app.state.auth_service
        token = svc.extract_token_from_request(request)
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated.",
            )
        try:
            claims = svc.verify_jwt_token(token)
            return {"authenticated": True, "user": claims}
        except (ValueError, PermissionError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
            ) from exc

    @fastapi_app.post("/api/roster/import", response_class=JSONResponse)
    async def import_roster_endpoint(
        request: Request,
        file: UploadFile | None = File(default=None),
        use_default_asset: bool = Query(default=False),
    ) -> dict[str, Any]:
        """
        Purpose:
            Lecturer-protected endpoint to import the class roster from an uploaded
            `.xlsx` file (or from `/assets/courseid_130158_participants.xlsx` when
            `use_default_asset=true`) into Firebase RTDB `/students`.
        Inputs:
            request (Request): Incoming FastAPI request (must carry valid Lecturer token).
            file (UploadFile | None): Uploaded `.xlsx` spreadsheet.
            use_default_asset (bool): If True and no file is uploaded, imports the
                default course participants asset.
        Outputs:
            dict[str, Any]: Import summary (`total_imported`, `unassigned_group_count`, `students`).
        """
        svc: AuthService = request.app.state.auth_service
        svc.require_lecturer_claims(request)

        importer: RosterImporter = request.app.state.roster_importer
        try:
            if file is not None:
                filename = (file.filename or "").lower()
                if not filename.endswith(".xlsx"):
                    raise ValueError(
                        "Invalid roster file type. Please upload an Excel (.xlsx) file."
                    )
                content = await file.read()
                return importer.import_from_excel(content)

            if use_default_asset:
                return importer.import_from_excel(DEFAULT_ROSTER_ASSET)

            raise ValueError(
                "Please upload an Excel (.xlsx) file or set use_default_asset=true."
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    @fastapi_app.get("/api/roster/students", response_class=JSONResponse)
    async def list_roster_students_endpoint(
        request: Request,
        q: str | None = Query(default=None),
        group: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """
        Purpose:
            Return the imported student roster from `/students`, optionally filtered
            by search query `q` (name/email) or `group`.
        Inputs:
            request (Request): Incoming FastAPI request.
            q (str | None): Optional search query substring.
            group (str | None): Optional group filter.
        Outputs:
            dict[str, Any]: Dictionary with `count` and list of `students`.
        """
        importer: RosterImporter = request.app.state.roster_importer
        students = importer.list_students(search_query=q, group_filter=group)
        return {"count": len(students), "students": students}

    @fastapi_app.post("/api/attendance/sessions", response_class=JSONResponse)
    async def start_attendance_session_endpoint(
        payload: StartAttendanceSessionRequest, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Lecturer-protected endpoint to activate an attendance session and capture
            the Lecturer's external IP via `ClientIPResolver`.
        Inputs:
            payload (StartAttendanceSessionRequest): Lesson ID, title, and IP check toggle.
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Created or reopened attendance session state.
        """
        svc: AuthService = request.app.state.auth_service
        svc.require_lecturer_claims(request)

        mgr: AttendanceManager = request.app.state.attendance_manager
        lecturer_ip = ClientIPResolver.resolve(request)
        try:
            return mgr.start_session(
                lesson_id=payload.lesson_id,
                lesson_title=payload.lesson_title or payload.lesson_id,
                lecturer_ip=lecturer_ip,
                enforce_same_network=payload.enforce_same_network,
                rotation_seconds=payload.rotation_seconds,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    @fastapi_app.get("/api/attendance/sessions", response_class=JSONResponse)
    async def list_attendance_sessions_endpoint(request: Request) -> dict[str, Any]:
        """
        Purpose:
            List all attendance sessions stored in Firebase RTDB.
        Inputs:
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Dictionary with `count` and `sessions`.
        """
        mgr: AttendanceManager = request.app.state.attendance_manager
        sessions = mgr.list_sessions()
        return {"count": len(sessions), "sessions": sessions}

    @fastapi_app.get(
        "/api/attendance/sessions/{lesson_id}", response_class=JSONResponse
    )
    async def get_attendance_session_report_endpoint(
        lesson_id: str, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Retrieve the attendance report and roster check-in matrix for a lesson.
        Inputs:
            lesson_id (str): Target lesson identifier.
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Complete attendance report.
        """
        mgr: AttendanceManager = request.app.state.attendance_manager
        return mgr.get_lesson_attendance_report(lesson_id)

    @fastapi_app.post(
        "/api/attendance/sessions/{lesson_id}/close", response_class=JSONResponse
    )
    async def close_attendance_session_endpoint(
        lesson_id: str, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Lecturer-protected endpoint to deactivate an attendance session.
        Inputs:
            lesson_id (str): Target lesson identifier.
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Closed attendance session dictionary.
        """
        svc: AuthService = request.app.state.auth_service
        svc.require_lecturer_claims(request)

        mgr: AttendanceManager = request.app.state.attendance_manager
        try:
            return mgr.close_session(lesson_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc

    @fastapi_app.patch(
        "/api/attendance/sessions/{lesson_id}/network-toggle",
        response_class=JSONResponse,
    )
    async def toggle_same_network_ip_check_endpoint(
        lesson_id: str, payload: NetworkToggleRequest, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Lecturer-protected endpoint to toggle "Enforce Same-Network IP Check"
            on or off for an attendance session.
        Inputs:
            lesson_id (str): Target lesson session ID.
            payload (NetworkToggleRequest): Desired `enforce_same_network` boolean.
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Updated attendance session dictionary.
        """
        svc: AuthService = request.app.state.auth_service
        svc.require_lecturer_claims(request)

        mgr: AttendanceManager = request.app.state.attendance_manager
        lecturer_ip = ClientIPResolver.resolve(request)
        try:
            return mgr.set_same_network_enforcement(
                lesson_id=lesson_id,
                enforce_same_network=payload.enforce_same_network,
                lecturer_ip=lecturer_ip,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc

    @fastapi_app.get(
        "/api/attendance/sessions/{lesson_id}/qr", response_class=JSONResponse
    )
    async def generate_rotating_qr_endpoint(
        lesson_id: str, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Lecturer-protected endpoint generating a fresh short-lived signed QR token
            and Base64 PNG QR image for the live rotating classroom display.
        Inputs:
            lesson_id (str): Active lesson identifier.
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Rotating QR payload (`qr_token`, `qr_image_data_url`, `ttl_seconds`).
        """
        svc: AuthService = request.app.state.auth_service
        svc.require_lecturer_claims(request)

        mgr: AttendanceManager = request.app.state.attendance_manager
        base_url = str(request.base_url).rstrip("/")
        try:
            return mgr.generate_rotating_qr_token(
                lesson_id=lesson_id, base_url=base_url
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    @fastapi_app.post("/api/attendance/checkin", response_class=JSONResponse)
    async def student_qr_checkin_endpoint(
        payload: StudentQRCheckInRequest, response: Response, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Student mobile QR check-in endpoint enforcing:
            1) Cryptographically signed rotating QR token validity,
            2) External IP Same-Network validation (when enabled),
            3) 15-Minute Device Cooldown via signed HTTP-only cookie + Firebase RTDB.
        Inputs:
            payload (StudentQRCheckInRequest): Lesson ID, Student ID, and QR token.
            response (Response): FastAPI response used to set the signed device cookie.
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Confirmed attendance check-in record and cooldown timestamp.
        """
        mgr: AttendanceManager = request.app.state.attendance_manager
        cfg: AppConfig = request.app.state.config
        student_ip = ClientIPResolver.resolve(request)
        incoming_device_cookie = request.cookies.get(DEVICE_COOKIE_NAME)

        try:
            result = mgr.submit_qr_checkin(
                lesson_id=payload.lesson_id,
                student_id=payload.student_id,
                qr_token=payload.qr_token,
                student_ip=student_ip,
                device_cookie_value=incoming_device_cookie,
            )
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

        response.set_cookie(
            key=DEVICE_COOKIE_NAME,
            value=result["signed_device_cookie"],
            httponly=True,
            samesite="lax",
            max_age=cfg.device_cooldown_minutes * 60,
        )
        return result

    @fastapi_app.post(
        "/api/attendance/sessions/{lesson_id}/manual", response_class=JSONResponse
    )
    async def manual_attendance_override_endpoint(
        lesson_id: str, payload: ManualAttendanceRequest, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Lecturer-protected endpoint to manually mark a student `present` or `absent`.
        Inputs:
            lesson_id (str): Target lesson identifier.
            payload (ManualAttendanceRequest): Student ID and desired status.
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Updated attendance record.
        """
        svc: AuthService = request.app.state.auth_service
        svc.require_lecturer_claims(request)

        mgr: AttendanceManager = request.app.state.attendance_manager
        lecturer_ip = ClientIPResolver.resolve(request)
        try:
            record = mgr.set_manual_attendance(
                lesson_id=lesson_id,
                student_id=payload.student_id,
                status=payload.status,
                lecturer_ip=lecturer_ip,
            )
            return {"lesson_id": lesson_id, "record": record}
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    @fastapi_app.post("/api/attendance/headcount", response_class=JSONResponse)
    async def analyze_classroom_photo_endpoint(
        request: Request,
        file: UploadFile | None = File(default=None),
        lesson_id: str | None = Query(default=None),
        registered_count: int | None = Query(default=None),
        use_default_asset: bool = Query(default=False),
    ) -> dict[str, Any]:
        """
        Purpose:
            Lecturer-protected endpoint that runs local Ultralytics YOLO person detection
            on an uploaded classroom photo (or `/assets/p3.jpeg` when `use_default_asset=true`),
            returns the annotated image with bounding boxes and confidence scores, and
            computes the GREEN / ORANGE / RED status indicator against self-registered attendance.
        Inputs:
            request (Request): Incoming FastAPI request (requires Lecturer JWT).
            file (UploadFile | None): Uploaded JPEG/PNG classroom photo.
            lesson_id (str | None): Optional lesson session ID to link and compare with.
            registered_count (int | None): Optional override for self-registered student count.
            use_default_asset (bool): When True and no file is uploaded, analyzes `/assets/p3.jpeg`.
        Outputs:
            dict[str, Any]: Full YOLO headcount report and annotated image Data URL.
        """
        svc: AuthService = request.app.state.auth_service
        svc.require_lecturer_claims(request)

        counter: PhotoHeadCounter = request.app.state.photo_head_counter
        try:
            if file is not None:
                filename = (file.filename or "").lower()
                if not filename.endswith((".jpg", ".jpeg", ".png", ".webp")):
                    raise ValueError(
                        "Invalid photo file type. Please upload a JPEG, PNG, or WebP classroom photo."
                    )
                image_bytes = await file.read()
                return counter.analyze_classroom_photo(
                    image_source=image_bytes,
                    lesson_id=lesson_id,
                    registered_count=registered_count,
                )

            if use_default_asset:
                return counter.analyze_classroom_photo(
                    image_source=DEFAULT_CLASSROOM_PHOTO_ASSET,
                    lesson_id=lesson_id,
                    registered_count=registered_count,
                )

            raise ValueError(
                "Please upload a classroom photo or set use_default_asset=true."
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    return fastapi_app


app = create_app()
