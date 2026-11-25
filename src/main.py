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

import logging
import mimetypes
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

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
from fastapi.concurrency import run_in_threadpool
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
from src.services.comment_service import CommentService
from src.services.gemini_client import (
    GeminiClient,
    GeminiHighDemandError,
    GeminiRateLimitError,
)
from src.services.homework_reviewer import HomeworkReviewer
from src.services.photo_head_counter import PhotoHeadCounter
from src.services.poll_service import PollService
from src.services.roster_importer import RosterImporter
from src.services.video_summarizer import VideoSummarizer
from src.utils.ip_resolver import ClientIPResolver

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Windows registries sometimes map `.js` to `text/plain`, which makes browsers refuse
# ES modules; register the correct types explicitly so the UI loads on every OS.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")


def resolve_public_origin(request: Request, config: AppConfig) -> str:
    """
    Purpose:
        Work out the public origin (scheme + host) that students must open, so QR links
        point at the Cloudflare Tunnel / Render URL instead of the server's local address.
    Inputs:
        request (Request): Incoming FastAPI request (proxy headers are inspected).
        config (AppConfig): Configuration; `public_base_url` wins when set.
    Outputs:
        str: Origin without a trailing slash, e.g. `https://abc.trycloudflare.com`.
    """
    if config.public_base_url:
        return config.public_base_url.rstrip("/")

    # Proxies may send comma-separated chains; the first entry is the original client-facing value
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    forwarded_host = request.headers.get("x-forwarded-host", "").split(",")[0].strip()
    scheme = forwarded_proto or request.url.scheme
    host = forwarded_host or request.headers.get("host", "") or request.url.netloc
    return f"{scheme}://{host}".rstrip("/")
DEFAULT_ROSTER_ASSET = (
    Path(__file__).resolve().parent.parent
    / "assets"
    / "courseid_130158_participants.xlsx"
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
        Pydantic schema for starting or reopening a smart attendance session
        configured with GPS Geofencing and rotating QR parameters.
    Inputs:
        lesson_id (str), lesson_title (str), classroom_lat (float | None),
        classroom_lng (float | None), geofence_radius_meters (int | None),
        enforce_geofence (bool | None), rotation_seconds (int | None).
    Outputs:
        Validated `StartAttendanceSessionRequest` instance.
    """

    lesson_id: str
    lesson_title: str = ""
    classroom_lat: float | None = None
    classroom_lng: float | None = None
    geofence_radius_meters: int | None = None
    enforce_geofence: bool | None = None
    rotation_seconds: int | None = None


class GeofenceToggleRequest(BaseModel):
    """
    Purpose:
        Pydantic schema for toggling "Enforce GPS Geofencing" or updating the
        geofence radius / classroom GPS coordinates on an active attendance session.
    Inputs:
        enforce_geofence (bool), geofence_radius_meters (int | None),
        classroom_lat (float | None), classroom_lng (float | None).
    Outputs:
        Validated `GeofenceToggleRequest` instance.
    """

    enforce_geofence: bool
    geofence_radius_meters: int | None = None
    classroom_lat: float | None = None
    classroom_lng: float | None = None


class StudentQRCheckInRequest(BaseModel):
    """
    Purpose:
        Pydantic schema for a Student QR code mobile check-in submission,
        including optional Browser Geolocation coordinates (`student_lat`, `student_lng`).
    Inputs:
        lesson_id (str), student_id (str), qr_token (str),
        student_lat (float | None), student_lng (float | None).
    Outputs:
        Validated `StudentQRCheckInRequest` instance.
    """

    lesson_id: str
    student_id: str
    qr_token: str
    student_lat: float | None = None
    student_lng: float | None = None


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


class CreatePollRequest(BaseModel):
    """
    Purpose:
        Pydantic schema for creating an on-the-spot multiple-choice classroom poll.
    Inputs:
        question (str), options (list[str]), lesson_tag (str).
    Outputs:
        Validated `CreatePollRequest` instance.
    """

    question: str
    options: list[str]
    lesson_tag: str = "General"


class PollStatusToggleRequest(BaseModel):
    """
    Purpose:
        Pydantic schema for opening or closing voting on a poll.
    Inputs:
        active (bool): True to accept votes, False to close voting.
    Outputs:
        Validated `PollStatusToggleRequest` instance.
    """

    active: bool


class SubmitPollVoteRequest(BaseModel):
    """
    Purpose:
        Pydantic schema for submitting a student vote on an active poll.
    Inputs:
        option_index (int): Zero-based index of the selected option.
        voter_id (str | None): Optional fallback voter ID if unauthenticated.
    Outputs:
        Validated `SubmitPollVoteRequest` instance.
    """

    option_index: int
    voter_id: str | None = None


class CreateCommentRequest(BaseModel):
    """
    Purpose:
        Pydantic schema for posting a public or private (lecturer-only) comment.
    Inputs:
        text (str), visibility (str), is_anonymous (bool), scope_tag (str),
        author_name (str | None).
    Outputs:
        Validated `CreateCommentRequest` instance.
    """

    text: str
    visibility: str = "public"
    is_anonymous: bool = False
    scope_tag: str = "General"
    author_name: str | None = None


class CreateCommentReplyRequest(BaseModel):
    """
    Purpose:
        Pydantic schema for posting a threaded reply on an existing comment.
    Inputs:
        text (str), is_anonymous (bool), author_name (str | None).
    Outputs:
        Validated `CreateCommentReplyRequest` instance.
    """

    text: str
    is_anonymous: bool = False
    author_name: str | None = None


class UpdateGeminiSettingsRequest(BaseModel):
    """
    Purpose:
        Pydantic schema for updating the Lecturer's Gemini API key and model ID.
    Inputs:
        api_key (str | None), model_id (str | None).
    Outputs:
        Validated `UpdateGeminiSettingsRequest` instance.
    """

    api_key: str | None = None
    model_id: str | None = None


class CreateHomeworkAssignmentRequest(BaseModel):
    """
    Purpose:
        Pydantic schema for creating a homework assignment with a submission deadline.
    Inputs:
        title (str), description (str), deadline_ts (int), lesson_tag (str).
    Outputs:
        Validated `CreateHomeworkAssignmentRequest` instance.
    """

    title: str
    description: str = ""
    deadline_ts: int
    lesson_tag: str = "Lesson 1"


class SubmitHomeworkRepoRequest(BaseModel):
    """
    Purpose:
        Pydantic schema for submitting a public GitHub / Git repository URL for homework review.
    Inputs:
        repo_url (str), student_id (str | None), student_name (str | None).
    Outputs:
        Validated `SubmitHomeworkRepoRequest` instance.
    """

    repo_url: str
    student_id: str | None = None
    student_name: str | None = None


class SummarizeYoutubeVideoRequest(BaseModel):
    """
    Purpose:
        Pydantic schema for summarizing a public YouTube lecture video directly via Gemini.
    Inputs:
        youtube_url (str), lesson_tag (str), lecturer_notes (str).
    Outputs:
        Validated `SummarizeYoutubeVideoRequest` instance.
    """

    youtube_url: str
    lesson_tag: str = "Lesson 1"
    lecturer_notes: str = ""


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
    poll_service = PollService(repository=resolved_repo)
    comment_service = CommentService(repository=resolved_repo)
    gemini_client = GeminiClient(repository=resolved_repo, config=resolved_config)
    video_summarizer = VideoSummarizer(
        repository=resolved_repo, gemini_client=gemini_client
    )
    homework_reviewer = HomeworkReviewer(
        repository=resolved_repo, gemini_client=gemini_client
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
    fastapi_app.state.poll_service = poll_service
    fastapi_app.state.comment_service = comment_service
    fastapi_app.state.gemini_client = gemini_client
    fastapi_app.state.video_summarizer = video_summarizer
    fastapi_app.state.homework_reviewer = homework_reviewer

    # Seed the fixed lecturer account (adminL@gmail.com / 12345) and auto-load the
    # 104-student Hebrew course roster from assets/courseid_130158_participants.xlsx if empty
    try:
        auth_service.seed_default_lecturer()
        roster_importer.ensure_roster_seeded(DEFAULT_ROSTER_ASSET)
    except Exception:
        pass

    if STATIC_DIR.is_dir():
        fastapi_app.mount(
            "/static", StaticFiles(directory=str(STATIC_DIR)), name="static"
        )

    @fastapi_app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """
        Purpose:
            Ensure any unexpected unhandled server exception returns a clean JSON
            `{"detail": ...}` payload rather than a raw HTML/text 500 response.
        Inputs:
            request (Request): Incoming FastAPI request.
            exc (Exception): Unhandled exception instance.
        Outputs:
            JSONResponse: Structured HTTP 500 JSON error response.
        """
        logger.exception("Unhandled exception on %s: %s", request.url.path, exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": f"Server error: {exc}"},
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
    @fastapi_app.post("/api/auth/register", response_class=JSONResponse)
    async def register_lecturer_endpoint() -> dict[str, Any]:
        """
        Purpose:
            Block public lecturer self-registration. Allowed lecturer accounts are
            strictly configured via `.env` / `AppConfig` and seeded on startup.
        Inputs:
            None.
        Outputs:
            Raises HTTP 403 Forbidden.
        """
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Public lecturer self-registration is disabled. "
                "Please sign in using the configured lecturer account."
            ),
        )

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
                return await run_in_threadpool(importer.import_from_excel, content)

            if use_default_asset:
                return await run_in_threadpool(
                    importer.import_from_excel, DEFAULT_ROSTER_ASSET
                )

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
            Lecturer-protected endpoint returning the imported student roster from
            `/students`, optionally filtered by search query `q` (name/email) or `group`.
            Students never see this list (it contains emails); they get names only
            through the QR-gated `/api/attendance/checkin-context` endpoint.
        Inputs:
            request (Request): Incoming FastAPI request (requires Lecturer JWT).
            q (str | None): Optional search query substring.
            group (str | None): Optional group filter.
        Outputs:
            dict[str, Any]: Dictionary with `count` and list of `students`.
        """
        request.app.state.auth_service.require_lecturer_claims(request)
        importer: RosterImporter = request.app.state.roster_importer
        students = importer.list_students(search_query=q, group_filter=group)
        return {"count": len(students), "students": students}

    @fastapi_app.post("/api/attendance/sessions", response_class=JSONResponse)
    async def start_attendance_session_endpoint(
        payload: StartAttendanceSessionRequest, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Lecturer-protected endpoint to activate an attendance session with
            classroom GPS geofencing coordinates, radius, and rotating QR settings.
        Inputs:
            payload (StartAttendanceSessionRequest): Lesson ID, title, and GPS geofence settings.
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Created or reopened attendance session state.
        """
        svc: AuthService = request.app.state.auth_service
        svc.require_lecturer_claims(request)

        mgr: AttendanceManager = request.app.state.attendance_manager
        try:
            session = mgr.start_session(
                lesson_id=payload.lesson_id,
                lesson_title=payload.lesson_title or payload.lesson_id,
                classroom_lat=payload.classroom_lat,
                classroom_lng=payload.classroom_lng,
                geofence_radius_meters=payload.geofence_radius_meters,
                enforce_geofence=payload.enforce_geofence,
                rotation_seconds=payload.rotation_seconds,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

        geofence_warning = None
        if session.get("enforce_geofence") and (
            session.get("classroom_lat") is None
            or session.get("classroom_lng") is None
        ):
            geofence_warning = (
                "GPS Geofencing is enabled, but no classroom coordinates were set. "
                "Provide classroom latitude/longitude (via Browser GPS or manual entry) "
                "so student check-in distance can be verified."
            )
        return {**session, "geofence_warning": geofence_warning}

    @fastapi_app.get("/api/attendance/sessions", response_class=JSONResponse)
    async def list_attendance_sessions_endpoint(request: Request) -> dict[str, Any]:
        """
        Purpose:
            Lecturer-protected listing of all attendance sessions stored in Firebase RTDB.
        Inputs:
            request (Request): Incoming FastAPI request (requires Lecturer JWT).
        Outputs:
            dict[str, Any]: Dictionary with `count` and `sessions`.
        """
        request.app.state.auth_service.require_lecturer_claims(request)
        mgr: AttendanceManager = request.app.state.attendance_manager
        sessions = mgr.list_sessions()
        return {"count": len(sessions), "sessions": sessions}

    @fastapi_app.get(
        "/api/attendance/checkin-context", response_class=JSONResponse
    )
    async def checkin_context_endpoint(
        request: Request,
        lesson_id: str = Query(...),
        qr_token: str = Query(...),
    ) -> dict[str, Any]:
        """
        Purpose:
            Public, QR-gated endpoint for the student mobile check-in page. It verifies the
            scanned rotating token and, only then, returns the lesson title, GPS geofencing
            requirements, and the list of roster names (`student_id` + `full_name`, no emails/groups)
            for the searchable dropdown.
        Inputs:
            request (Request): Incoming FastAPI request.
            lesson_id (str): Lesson identifier from the QR link.
            qr_token (str): Signed rotating QR token from the QR link.
        Outputs:
            dict[str, Any]: `lesson_id`, `lesson_title`, `enforce_geofence`,
            `geofence_radius_meters`, `has_classroom_coords`, and `students`.
        """
        mgr: AttendanceManager = request.app.state.attendance_manager
        importer: RosterImporter = request.app.state.roster_importer
        session = mgr.get_session(lesson_id)
        if session is None or not session.get("active", False):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This attendance session is not open.",
            )
        try:
            mgr.verify_qr_token(qr_token, expected_lesson_id=lesson_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

        names = [
            {
                "student_id": stu["student_id"],
                "full_name": stu.get("full_name", ""),
                "email": stu.get("email", ""),
            }
            for stu in importer.list_students()
        ]
        has_coords = (
            session.get("classroom_lat") is not None
            and session.get("classroom_lng") is not None
        )
        return {
            "lesson_id": session["lesson_id"],
            "lesson_title": session.get("lesson_title", session["lesson_id"]),
            "enforce_geofence": bool(session.get("enforce_geofence", True)),
            "geofence_radius_meters": int(session.get("geofence_radius_meters", 100)),
            "has_classroom_coords": has_coords,
            "students": names,
        }

    @fastapi_app.get("/api/attendance/my-history", response_class=JSONResponse)
    async def student_attendance_history_endpoint(
        request: Request,
        student_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """
        Purpose:
            Student-accessible endpoint returning chronological per-lesson attendance
            history (`present` or `absent`) and overall attendance percentage for the
            authenticated student (or a target `student_id` when called by Lecturer).
        Inputs:
            request (Request): Incoming FastAPI request with student or lecturer JWT session.
            student_id (str | None): Optional student ID override when requested by Lecturer.
        Outputs:
            dict[str, Any]: Attendance history summary (`attended_count`, `total_lessons`,
            `attendance_rate`, `lessons`).
        """
        svc: AuthService = request.app.state.auth_service
        auth_header = request.headers.get("Authorization", "").strip()
        bearer = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else None
        student_cookie = request.cookies.get(STUDENT_COOKIE_NAME)
        lecturer_cookie = request.cookies.get(LECTURER_COOKIE_NAME)

        claims: dict[str, Any] | None = None
        for tok in (student_cookie, bearer, lecturer_cookie):
            if not tok:
                continue
            try:
                claims = svc.verify_jwt_token(tok)
                break
            except Exception:
                continue

        if claims is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Please sign in as a student to view your attendance history.",
            )

        role = str(claims.get("role", "student"))
        if role == "lecturer" and student_id and student_id.strip():
            target_sid = student_id.strip()
        else:
            target_sid = str(claims.get("sub", "")).strip()

        mgr: AttendanceManager = request.app.state.attendance_manager
        try:
            return await run_in_threadpool(mgr.get_student_attendance_history, target_sid)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    @fastapi_app.get(
        "/api/attendance/sessions/{lesson_id}", response_class=JSONResponse
    )
    async def get_attendance_session_report_endpoint(
        lesson_id: str, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Lecturer-protected attendance report and roster check-in matrix for a lesson.
        Inputs:
            lesson_id (str): Target lesson identifier.
            request (Request): Incoming FastAPI request (requires Lecturer JWT).
        Outputs:
            dict[str, Any]: Complete attendance report.
        """
        request.app.state.auth_service.require_lecturer_claims(request)
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
        "/api/attendance/sessions/{lesson_id}/geofence-toggle",
        response_class=JSONResponse,
    )
    async def toggle_geofence_check_endpoint(
        lesson_id: str, payload: GeofenceToggleRequest, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Lecturer-protected endpoint to toggle "Enforce GPS Geofencing" or update
            the allowed radius / classroom coordinates on an attendance session.
        Inputs:
            lesson_id (str): Target lesson session ID.
            payload (GeofenceToggleRequest): Desired geofence enforcement state and optional radius/coords.
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Updated attendance session dictionary.
        """
        svc: AuthService = request.app.state.auth_service
        svc.require_lecturer_claims(request)

        mgr: AttendanceManager = request.app.state.attendance_manager
        try:
            return mgr.set_geofence_enforcement(
                lesson_id=lesson_id,
                enforce_geofence=payload.enforce_geofence,
                geofence_radius_meters=payload.geofence_radius_meters,
                classroom_lat=payload.classroom_lat,
                classroom_lng=payload.classroom_lng,
            )
        except ValueError as exc:
            status_code = (
                status.HTTP_404_NOT_FOUND
                if "not found" in str(exc).lower()
                else status.HTTP_400_BAD_REQUEST
            )
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    @fastapi_app.get(
        "/api/attendance/sessions/{lesson_id}/qr", response_class=JSONResponse
    )
    async def generate_rotating_qr_endpoint(
        lesson_id: str, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Lecturer-protected endpoint returning the static session QR token
            and Base64 PNG QR image for the active classroom display.
        Inputs:
            lesson_id (str): Active lesson identifier.
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Static session QR payload (`qr_token`, `checkin_url`, `qr_image_data_url`).
        """
        svc: AuthService = request.app.state.auth_service
        svc.require_lecturer_claims(request)

        mgr: AttendanceManager = request.app.state.attendance_manager
        base_url = resolve_public_origin(request, request.app.state.config)
        try:
            payload = mgr.generate_rotating_qr_token(
                lesson_id=lesson_id, base_url=base_url
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        return {**payload, "server_time": int(time.time())}

    @fastapi_app.post("/api/attendance/checkin", response_class=JSONResponse)
    async def student_qr_checkin_endpoint(
        payload: StudentQRCheckInRequest, response: Response, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Student mobile QR check-in endpoint enforcing:
            1) Active lesson session & static session QR token validity,
            2) GPS Geofencing validation via Haversine distance (when enabled),
            3) 15-Minute Device Cooldown via signed HTTP-only cookie + Firebase RTDB.
        Inputs:
            payload (StudentQRCheckInRequest): Lesson ID, Student ID, QR token, and GPS coordinates.
            response (Response): FastAPI response used to set the signed device cookie.
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Confirmed attendance check-in record and cooldown timestamp.
        """
        mgr: AttendanceManager = request.app.state.attendance_manager
        cfg: AppConfig = request.app.state.config
        incoming_device_cookie = request.cookies.get(DEVICE_COOKIE_NAME)

        try:
            result = mgr.submit_qr_checkin(
                lesson_id=payload.lesson_id,
                student_id=payload.student_id,
                qr_token=payload.qr_token,
                student_lat=payload.student_lat,
                student_lng=payload.student_lng,
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

    @fastapi_app.post("/api/attendance/reset-cooldowns", response_class=JSONResponse)
    async def reset_device_cooldowns_endpoint(request: Request) -> dict[str, Any]:
        """
        Purpose:
            Lecturer-protected endpoint that clears all `/device_cooldowns` records
            in Firebase RTDB and updates `/settings/cooldown_reset_at` so any device
            (even one still holding an active cooldown cookie) is immediately unblocked.
        Inputs:
            request (Request): Incoming FastAPI request (requires Lecturer JWT).
        Outputs:
            dict[str, Any]: Confirmation dictionary with `status` and `cooldown_reset_at`.
        """
        svc: AuthService = request.app.state.auth_service
        svc.require_lecturer_claims(request)

        mgr: AttendanceManager = request.app.state.attendance_manager
        return mgr.reset_all_device_cooldowns()

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
        try:
            record = mgr.set_manual_attendance(
                lesson_id=lesson_id,
                student_id=payload.student_id,
                status=payload.status,
            )
            return {"lesson_id": lesson_id, "record": record}
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    @fastapi_app.get("/api/attendance/sessions/{lesson_id}/export")
    async def export_attendance_spreadsheet_endpoint(
        lesson_id: str,
        request: Request,
        format: str = Query(default="csv"),
    ) -> Response:
        """
        Purpose:
            Lecturer-protected endpoint to export a lesson's complete student
            attendance spreadsheet as either a UTF-8-BOM CSV (`.csv`) or an
            Excel workbook (`.xlsx`).
        Inputs:
            lesson_id (str): Target lesson session identifier.
            request (Request): Incoming FastAPI request (requires Lecturer JWT).
            format (str): Export format (`"csv"` or `"xlsx"`).
        Outputs:
            Response: Binary file download attachment response.
        """
        svc: AuthService = request.app.state.auth_service
        svc.require_lecturer_claims(request)

        mgr: AttendanceManager = request.app.state.attendance_manager
        fmt = format.strip().lower()
        clean_lesson = lesson_id.strip() or "lesson"

        if fmt == "csv":
            csv_bytes = mgr.export_attendance_csv(clean_lesson)
            return Response(
                content=csv_bytes,
                media_type="text/csv; charset=utf-8",
                headers={
                    "Content-Disposition": f'attachment; filename="attendance_{clean_lesson}.csv"'
                },
            )
        if fmt in {"xlsx", "excel"}:
            xlsx_bytes = mgr.export_attendance_xlsx(clean_lesson)
            return Response(
                content=xlsx_bytes,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={
                    "Content-Disposition": f'attachment; filename="attendance_{clean_lesson}.xlsx"'
                },
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported export format. Use format=csv or format=xlsx.",
        )

    @fastapi_app.post("/api/attendance/headcount", response_class=JSONResponse)
    async def analyze_classroom_photo_endpoint(
        request: Request,
        file: UploadFile | None = File(default=None),
        lesson_id: str | None = Query(default=None),
        registered_count: int | None = Query(default=None),
    ) -> dict[str, Any]:
        """
        Purpose:
            Lecturer-protected endpoint that runs local Ultralytics YOLO person detection
            on an uploaded classroom photo in a worker thread, persists and returns the
            annotated image Data URL with bounding boxes and confidence scores, and computes
            the GREEN / ORANGE / RED status indicator against self-registered attendance.
        Inputs:
            request (Request): Incoming FastAPI request (requires Lecturer JWT).
            file (UploadFile | None): Uploaded JPEG/PNG/WebP classroom photo.
            lesson_id (str | None): Optional lesson session ID to link and compare with.
            registered_count (int | None): Optional override for self-registered student count.
        Outputs:
            dict[str, Any]: Full YOLO headcount report and persisted annotated image Data URL.
        """
        svc: AuthService = request.app.state.auth_service
        svc.require_lecturer_claims(request)

        counter: PhotoHeadCounter = request.app.state.photo_head_counter
        try:
            if file is None:
                raise ValueError(
                    "Please upload a classroom photo (JPEG, PNG, or WebP)."
                )
            filename = (file.filename or "").lower()
            if not filename.endswith((".jpg", ".jpeg", ".png", ".webp")):
                raise ValueError(
                    "Invalid photo file type. Please upload a JPEG, PNG, or WebP classroom photo."
                )
            image_bytes = await file.read()
            return await run_in_threadpool(
                counter.analyze_classroom_photo,
                image_source=image_bytes,
                lesson_id=lesson_id,
                registered_count=registered_count,
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    @fastapi_app.delete("/api/attendance/headcount", response_class=JSONResponse)
    @fastapi_app.post("/api/attendance/headcount/reset", response_class=JSONResponse)
    async def reset_classroom_headcount_endpoint(
        request: Request,
        lesson_id: str = Query(...),
    ) -> dict[str, Any]:
        """
        Purpose:
            Lecturer-protected endpoint to clear the saved YOLO headcount, discrepancy status,
            and annotated classroom photo for `lesson_id`.
        Inputs:
            request (Request): Incoming FastAPI request (requires Lecturer JWT).
            lesson_id (str): Target lesson identifier.
        Outputs:
            dict[str, Any]: Reset confirmation dictionary.
        """
        svc: AuthService = request.app.state.auth_service
        svc.require_lecturer_claims(request)

        counter: PhotoHeadCounter = request.app.state.photo_head_counter
        try:
            return await run_in_threadpool(counter.reset_headcount, lesson_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    def _resolve_caller_identity(
        request: Request, prefer_role: str | None = None
    ) -> dict[str, str]:
        """
        Purpose:
            Inspect the caller's JWT cookies/header and return their Firebase-safe `id`,
            `name`, and `role` (`"lecturer"` or `"student"`), falling back to a signed
            device cookie or sanitized IP identity when unauthenticated.
        Inputs:
            request (Request): Incoming FastAPI request.
            prefer_role (str | None): Optional preferred role (`"student"` or `"lecturer"`).
        Outputs:
            dict[str, str]: Dictionary with keys `"id"`, `"name"`, and `"role"`.
        """
        svc: AuthService = request.app.state.auth_service
        candidate_tokens: list[str] = []
        auth_header = request.headers.get("Authorization", "").strip()
        if auth_header.lower().startswith("bearer "):
            candidate_tokens.append(auth_header[7:].strip())

        if prefer_role == "student":
            cookie_order = (STUDENT_COOKIE_NAME, LECTURER_COOKIE_NAME)
        else:
            cookie_order = (LECTURER_COOKIE_NAME, STUDENT_COOKIE_NAME)

        for cookie_name in cookie_order:
            cookie_val = request.cookies.get(cookie_name)
            if cookie_val and cookie_val not in candidate_tokens:
                candidate_tokens.append(cookie_val)

        for tok in candidate_tokens:
            try:
                claims = svc.verify_jwt_token(tok)
                raw_sub = str(claims.get("sub", "guest")).strip()
                safe_sub = re.sub(r"[.$#\[\]/]", "_", raw_sub) or "guest"
                return {
                    "id": safe_sub,
                    "name": str(claims.get("full_name", "Student")),
                    "role": str(claims.get("role", "student")),
                }
            except Exception:
                continue

        mgr: AttendanceManager = request.app.state.attendance_manager
        dev_id = mgr.verify_signed_device_cookie(
            request.cookies.get(DEVICE_COOKIE_NAME)
        )
        raw_fallback = dev_id or f"ip_{ClientIPResolver.resolve(request)}"
        safe_fallback = re.sub(r"[^a-zA-Z0-9_-]", "_", raw_fallback) or "guest_student"
        return {
            "id": safe_fallback,
            "name": "Student",
            "role": "student",
        }

    @fastapi_app.post("/api/polls", response_class=JSONResponse)
    async def create_poll_endpoint(
        payload: CreatePollRequest, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Lecturer-protected endpoint to create and launch an on-the-spot
            multiple-choice poll in Firebase RTDB (`/polls`).
        Inputs:
            payload (CreatePollRequest): Poll question, answer options, and lesson tag.
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Created enriched poll dictionary.
        """
        svc: AuthService = request.app.state.auth_service
        claims = svc.require_lecturer_claims(request)

        polls: PollService = request.app.state.poll_service
        try:
            return polls.create_poll(
                question=payload.question,
                options=payload.options,
                lesson_tag=payload.lesson_tag,
                created_by=str(claims.get("full_name", "Lecturer")),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    @fastapi_app.get("/api/polls", response_class=JSONResponse)
    async def list_polls_endpoint(
        request: Request,
        active_only: bool = Query(default=False),
    ) -> dict[str, Any]:
        """
        Purpose:
            List classroom polls from `/polls` with live vote counts, percentages,
            and the caller's selected `viewer_vote`.
        Inputs:
            request (Request): Incoming FastAPI request.
            active_only (bool): When True, returns only active open polls.
        Outputs:
            dict[str, Any]: Dictionary containing `count` and `polls`.
        """
        caller = _resolve_caller_identity(request)
        polls: PollService = request.app.state.poll_service
        items = polls.list_polls(active_only=active_only, viewer_id=caller["id"])
        return {"count": len(items), "polls": items}

    @fastapi_app.patch("/api/polls/{poll_id}/status", response_class=JSONResponse)
    async def toggle_poll_status_endpoint(
        poll_id: str, payload: PollStatusToggleRequest, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Lecturer-protected endpoint to open (`active=True`) or close (`active=False`)
            voting on a poll.
        Inputs:
            poll_id (str): Target poll ID.
            payload (PollStatusToggleRequest): Desired `active` boolean.
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Updated enriched poll dictionary.
        """
        svc: AuthService = request.app.state.auth_service
        svc.require_lecturer_claims(request)

        polls: PollService = request.app.state.poll_service
        try:
            return polls.set_poll_active(poll_id=poll_id, active=payload.active)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc

    @fastapi_app.post("/api/polls/{poll_id}/vote", response_class=JSONResponse)
    async def submit_poll_vote_endpoint(
        poll_id: str, payload: SubmitPollVoteRequest, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Submit a student vote on an active poll, enforcing single-vote-per-poll.
        Inputs:
            poll_id (str): Target poll ID.
            payload (SubmitPollVoteRequest): Chosen `option_index` and optional fallback `voter_id`.
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Updated enriched poll dictionary with live percentage breakdown.
        """
        caller = _resolve_caller_identity(request)
        voter_id = (
            caller["id"]
            if caller["id"] and not caller["id"].startswith("ip_")
            else (payload.voter_id or caller["id"])
        )
        polls: PollService = request.app.state.poll_service
        try:
            return polls.submit_vote(
                poll_id=poll_id,
                voter_id=voter_id,
                option_index=payload.option_index,
            )
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
            ) from exc
        except ValueError as exc:
            status_code = (
                status.HTTP_404_NOT_FOUND
                if "not found" in str(exc).lower()
                else status.HTTP_400_BAD_REQUEST
            )
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    @fastapi_app.post("/api/comments", response_class=JSONResponse)
    async def create_comment_endpoint(
        payload: CreateCommentRequest, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Create a classroom comment or suggestion with configurable visibility
            (`public` vs. `private` Lecturer-only), identity (`With Name` vs. `Anonymous`),
            and scope tag (`Lesson 1..N` vs. `General`).
        Inputs:
            payload (CreateCommentRequest): Comment body and metadata.
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Created sanitized comment dictionary.
        """
        caller = _resolve_caller_identity(request)
        author_name = (
            (payload.author_name or "").strip()
            if caller["name"] == "Student" and payload.author_name
            else caller["name"]
        )
        comments: CommentService = request.app.state.comment_service
        try:
            return comments.create_comment(
                text=payload.text,
                author_id=caller["id"],
                author_name=author_name or "Student",
                author_role=caller["role"],
                visibility=payload.visibility,
                is_anonymous=payload.is_anonymous,
                scope_tag=payload.scope_tag,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    @fastapi_app.get("/api/comments", response_class=JSONResponse)
    async def list_comments_endpoint(
        request: Request,
        scope_tag: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """
        Purpose:
            List comments visible to the caller (Lecturers see all public & private
            comments; Students see public comments + their own private comments),
            optionally filtered by `scope_tag`.
        Inputs:
            request (Request): Incoming FastAPI request.
            scope_tag (str | None): Optional scope filter (`"General"`, `"Lesson 1"`, etc.).
        Outputs:
            dict[str, Any]: Dictionary containing `count` and `comments`.
        """
        caller = _resolve_caller_identity(request)
        comments: CommentService = request.app.state.comment_service
        items = comments.list_comments(
            viewer_role=caller["role"],
            viewer_id=caller["id"],
            scope_tag=scope_tag,
        )
        return {"count": len(items), "comments": items}

    @fastapi_app.post(
        "/api/comments/{comment_id}/replies", response_class=JSONResponse
    )
    async def create_comment_reply_endpoint(
        comment_id: str, payload: CreateCommentReplyRequest, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Append a threaded reply to an existing comment, enforcing privacy rules
            so other students cannot reply to a `private` (Lecturer-only) comment.
        Inputs:
            comment_id (str): Parent comment ID.
            payload (CreateCommentReplyRequest): Reply text and anonymity flag.
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Updated parent comment thread dictionary.
        """
        caller = _resolve_caller_identity(request)
        author_name = (
            (payload.author_name or "").strip()
            if caller["name"] == "Student" and payload.author_name
            else caller["name"]
        )
        comments: CommentService = request.app.state.comment_service
        try:
            return comments.add_reply(
                comment_id=comment_id,
                text=payload.text,
                author_id=caller["id"],
                author_name=author_name or "Student",
                author_role=caller["role"],
                is_anonymous=payload.is_anonymous,
            )
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
            ) from exc
        except ValueError as exc:
            status_code = (
                status.HTTP_404_NOT_FOUND
                if "not found" in str(exc).lower()
                else status.HTTP_400_BAD_REQUEST
            )
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    @fastapi_app.get("/api/settings/gemini", response_class=JSONResponse)
    async def get_gemini_settings_endpoint(request: Request) -> dict[str, Any]:
        """
        Purpose:
            Lecturer-protected endpoint returning the active Gemini model ID and
            masked API key status from Firebase RTDB (`/settings/gemini`).
        Inputs:
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Dictionary with `model_id`, `has_api_key`, and `masked_api_key`.
        """
        svc: AuthService = request.app.state.auth_service
        svc.require_lecturer_claims(request)

        gemini: GeminiClient = request.app.state.gemini_client
        return gemini.get_settings()

    @fastapi_app.put("/api/settings/gemini", response_class=JSONResponse)
    @fastapi_app.post("/api/settings/gemini", response_class=JSONResponse)
    async def update_gemini_settings_endpoint(
        payload: UpdateGeminiSettingsRequest, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Lecturer-protected endpoint allowing the Lecturer to enter/update their
            Gemini API Key and configure the Gemini Model ID (defaulting to `gemini-3.5-flash-lite`
            with support for presets like `gemini-3.5-flash`, `gemini-2.5-flash`, or custom IDs).
        Inputs:
            payload (UpdateGeminiSettingsRequest): New API key and/or model ID.
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Updated public Gemini settings with masked key.
        """
        svc: AuthService = request.app.state.auth_service
        svc.require_lecturer_claims(request)

        gemini: GeminiClient = request.app.state.gemini_client
        try:
            return gemini.update_settings(
                api_key=payload.api_key, model_id=payload.model_id
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    @fastapi_app.delete("/api/settings/gemini", response_class=JSONResponse)
    async def clear_gemini_settings_key_endpoint(
        request: Request,
    ) -> dict[str, Any]:
        """
        Purpose:
            Lecturer-protected endpoint that explicitly clears/deletes the stored
            Gemini API key in Firebase RTDB (`/settings/gemini`) while keeping the
            configured model ID, returning `has_api_key=False` and `has_key=False`.
        Inputs:
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Updated Gemini settings dictionary with `has_api_key=False`.
        """
        svc: AuthService = request.app.state.auth_service
        svc.require_lecturer_claims(request)

        gemini: GeminiClient = request.app.state.gemini_client
        return gemini.update_settings(api_key="")

    @fastapi_app.post("/api/video/summarize-youtube", response_class=JSONResponse)
    async def summarize_youtube_video_endpoint(
        payload: SummarizeYoutubeVideoRequest,
        request: Request,
    ) -> dict[str, Any]:
        """
        Purpose:
            Lecturer-protected endpoint for Feature 4 (AI Lecture Video Summary — YouTube Only).
            Canonicalizes the public YouTube video URL, fetches the real title via oEmbed,
            runs the automatic 2-step fast strategy (`youtube-transcript-api` -> Gemini text
            analysis, or fallback to direct YouTube URI with `VideoMetadata(fps=0.1)`), and
            persists the rich structured academic summary under `/video_summaries`.
        Inputs:
            payload (SummarizeYoutubeVideoRequest): Public YouTube URL, lesson tag, and notes.
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Structured lecture video summary record.
        """
        svc: AuthService = request.app.state.auth_service
        svc.require_lecturer_claims(request)

        summarizer: VideoSummarizer = request.app.state.video_summarizer
        header_api_key = request.headers.get("X-Gemini-Api-Key")
        header_model = request.headers.get("X-Gemini-Model")
        try:
            return await run_in_threadpool(
                summarizer.summarize_video,
                youtube_url=payload.youtube_url,
                lesson_tag=payload.lesson_tag,
                lecturer_notes=payload.lecturer_notes,
                gemini_api_key=header_api_key,
                gemini_model_id=header_model,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        except GeminiRateLimitError as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
            ) from exc
        except GeminiHighDemandError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
            ) from exc
        except Exception as exc:
            logger.exception("Unexpected error during YouTube video summarization: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"YouTube video summarization failed: {exc}",
            ) from exc

    @fastapi_app.get("/api/video/summaries", response_class=JSONResponse)
    async def list_video_summaries_endpoint(request: Request) -> dict[str, Any]:
        """
        Purpose:
            List all generated lecture video summaries from `/video_summaries`.
        Inputs:
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Dictionary with `count` and `summaries`.
        """
        summarizer: VideoSummarizer = request.app.state.video_summarizer
        items = summarizer.list_summaries()
        return {"count": len(items), "summaries": items}

    @fastapi_app.delete(
        "/api/video/summaries/{summary_id}", response_class=JSONResponse
    )
    async def delete_video_summary_endpoint(
        summary_id: str, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Lecturer-protected endpoint removing a saved video summary from Firebase
            RTDB under `/video_summaries/{summary_id}`.
        Inputs:
            summary_id (str): Target summary identifier.
            request (Request): Incoming FastAPI request (requires Lecturer JWT).
        Outputs:
            dict[str, Any]: Deletion confirmation payload.
        """
        svc: AuthService = request.app.state.auth_service
        svc.require_lecturer_claims(request)

        summarizer: VideoSummarizer = request.app.state.video_summarizer
        try:
            return await run_in_threadpool(summarizer.delete_summary, summary_id)
        except ValueError as exc:
            status_code = (
                status.HTTP_404_NOT_FOUND
                if "not found" in str(exc).lower()
                else status.HTTP_400_BAD_REQUEST
            )
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    @fastapi_app.post("/api/homework/assignments", response_class=JSONResponse)
    async def create_homework_assignment_endpoint(
        payload: CreateHomeworkAssignmentRequest, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Lecturer-protected endpoint to create a homework assignment with a
            submission deadline.
        Inputs:
            payload (CreateHomeworkAssignmentRequest): Title, description, deadline, and lesson tag.
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Created homework assignment overview.
        """
        svc: AuthService = request.app.state.auth_service
        svc.require_lecturer_claims(request)

        reviewer: HomeworkReviewer = request.app.state.homework_reviewer
        try:
            return reviewer.create_assignment(
                title=payload.title,
                description=payload.description,
                deadline_ts=payload.deadline_ts,
                lesson_tag=payload.lesson_tag,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    @fastapi_app.get("/api/homework/assignments", response_class=JSONResponse)
    async def list_homework_assignments_endpoint(request: Request) -> dict[str, Any]:
        """
        Purpose:
            List all homework assignments with submission counts and release gate status.
        Inputs:
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Dictionary with `count` and `assignments`.
        """
        caller = _resolve_caller_identity(request)
        reviewer: HomeworkReviewer = request.app.state.homework_reviewer
        items = reviewer.list_assignments(viewer_id=caller["id"])
        return {"count": len(items), "assignments": items}

    @fastapi_app.post(
        "/api/homework/assignments/{assignment_id}/end-now",
        response_class=JSONResponse,
    )
    async def end_homework_submission_now_endpoint(
        assignment_id: str, request: Request
    ) -> dict[str, Any]:
        """
        Purpose:
            Lecturer one-click "End Submission Now" endpoint: immediately closes
            submissions on `assignment_id` and unlocks AI reviews and peer comparisons.
        Inputs:
            assignment_id (str): Target homework assignment ID.
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Updated assignment overview (`review_released=True`).
        """
        svc: AuthService = request.app.state.auth_service
        svc.require_lecturer_claims(request)

        reviewer: HomeworkReviewer = request.app.state.homework_reviewer
        try:
            return await run_in_threadpool(
                reviewer.end_submission_now, assignment_id=assignment_id
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc

    @fastapi_app.post(
        "/api/homework/assignments/{assignment_id}/submit",
        response_class=JSONResponse,
    )
    async def submit_homework_pdf_endpoint(
        assignment_id: str,
        request: Request,
        file: UploadFile | None = File(default=None),
        repo_url: str | None = Query(default=None),
        student_id: str | None = Query(default=None),
        student_name: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """
        Purpose:
            Student homework submission endpoint supporting both multi-format file uploads
            (`.pdf`, `.docx`, `.txt`, `.md`, `.zip`, `.py`, `.java`, `.c`, `.cpp`, `.h`,
            `.js`, `.ts`, `.html`, `.css`, `.json`) and public GitHub repository URLs (`repo_url`),
            while enforcing the deadline check and Gated Release Rule.
        Inputs:
            assignment_id (str): Target homework assignment ID.
            request (Request): Incoming FastAPI request.
            file (UploadFile | None): Uploaded homework file.
            repo_url (str | None): Optional public GitHub repository URL.
            student_id (str | None): Optional student ID override.
            student_name (str | None): Optional student display name override.
        Outputs:
            dict[str, Any]: Gated submission confirmation receipt.
        """
        caller = _resolve_caller_identity(request, prefer_role="student")
        effective_id = (student_id or "").strip() or caller["id"]
        effective_name = (student_name or "").strip() or caller["name"]
        header_api_key = request.headers.get("X-Gemini-Api-Key")
        header_model = request.headers.get("X-Gemini-Model") or "gemini-3.5-flash-lite"

        reviewer: HomeworkReviewer = request.app.state.homework_reviewer
        try:
            if repo_url and repo_url.strip():
                return await run_in_threadpool(
                    reviewer.submit_homework_pdf,
                    assignment_id=assignment_id,
                    student_id=effective_id,
                    student_name=effective_name,
                    repo_url=repo_url.strip(),
                    gemini_api_key=header_api_key,
                    gemini_model_id=header_model,
                    require_student_key=True,
                )
            if file is None:
                raise ValueError(
                    "Please upload a supported homework file or provide a public Git repository URL."
                )
            pdf_bytes = await file.read()
            return await run_in_threadpool(
                reviewer.submit_homework_pdf,
                assignment_id=assignment_id,
                student_id=effective_id,
                student_name=effective_name,
                filename=file.filename or "",
                pdf_bytes=pdf_bytes,
                content_type=file.content_type,
                gemini_api_key=header_api_key,
                gemini_model_id=header_model,
                require_student_key=True,
            )
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
            ) from exc
        except ValueError as exc:
            status_code = (
                status.HTTP_404_NOT_FOUND
                if "not found" in str(exc).lower()
                else status.HTTP_400_BAD_REQUEST
            )
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        except GeminiRateLimitError as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
            ) from exc
        except GeminiHighDemandError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
            ) from exc
        except Exception as exc:
            logger.exception("Unexpected error during homework submission: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Homework submission failed: {exc}",
            ) from exc

    @fastapi_app.post(
        "/api/homework/assignments/{assignment_id}/submit-repo",
        response_class=JSONResponse,
    )
    async def submit_homework_repo_endpoint(
        assignment_id: str,
        payload: SubmitHomeworkRepoRequest,
        request: Request,
    ) -> dict[str, Any]:
        """
        Purpose:
            Student endpoint for submitting a public GitHub / Git repository URL
            for homework review under the Gated Release Rule.
        Inputs:
            assignment_id (str): Target homework assignment ID.
            payload (SubmitHomeworkRepoRequest): Public repository URL and optional student identity.
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Gated submission confirmation receipt.
        """
        caller = _resolve_caller_identity(request, prefer_role="student")
        effective_id = (payload.student_id or "").strip() or caller["id"]
        effective_name = (payload.student_name or "").strip() or caller["name"]
        header_api_key = request.headers.get("X-Gemini-Api-Key")
        header_model = request.headers.get("X-Gemini-Model") or "gemini-3.5-flash-lite"

        reviewer: HomeworkReviewer = request.app.state.homework_reviewer
        try:
            return await run_in_threadpool(
                reviewer.submit_homework_pdf,
                assignment_id=assignment_id,
                student_id=effective_id,
                student_name=effective_name,
                repo_url=payload.repo_url,
                gemini_api_key=header_api_key,
                gemini_model_id=header_model,
                require_student_key=True,
            )
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
            ) from exc
        except ValueError as exc:
            status_code = (
                status.HTTP_404_NOT_FOUND
                if "not found" in str(exc).lower()
                else status.HTTP_400_BAD_REQUEST
            )
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        except GeminiRateLimitError as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
            ) from exc
        except GeminiHighDemandError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
            ) from exc
        except Exception as exc:
            logger.exception("Unexpected error during Git repo homework submission: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Git repository homework submission failed: {exc}",
            ) from exc

    @fastapi_app.get(
        "/api/homework/assignments/{assignment_id}/review",
        response_class=JSONResponse,
    )
    async def get_homework_review_and_comparison_endpoint(
        assignment_id: str,
        request: Request,
        student_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """
        Purpose:
            Retrieve a student's homework Review & Summary Card and Peer Comparison
            Column. Enforces the Gated Release Rule (returns `review_released=False`
            with `review=None` and `peer_comparison=None` while submissions are open)
            and returns the First-Submission Edge Case notice when `total_submissions == 1`.
        Inputs:
            assignment_id (str): Target homework assignment ID.
            request (Request): Incoming FastAPI request.
            student_id (str | None): Optional student ID to inspect.
        Outputs:
            dict[str, Any]: Gated or unlocked Review + Peer Comparison payload.
        """
        caller = _resolve_caller_identity(request)
        target_student_id = (student_id or "").strip() or caller["id"]
        header_api_key = request.headers.get("X-Gemini-Api-Key")
        header_model = request.headers.get("X-Gemini-Model") or "gemini-3.5-flash-lite"

        reviewer: HomeworkReviewer = request.app.state.homework_reviewer
        try:
            return await run_in_threadpool(
                reviewer.get_student_review_and_comparison,
                assignment_id=assignment_id,
                student_id=target_student_id,
                viewer_role=caller["role"],
                gemini_api_key=header_api_key,
                gemini_model_id=header_model,
            )
        except ValueError as exc:
            status_code = (
                status.HTTP_404_NOT_FOUND
                if "not found" in str(exc).lower() or "no homework" in str(exc).lower()
                else status.HTTP_400_BAD_REQUEST
            )
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        except GeminiRateLimitError as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
            ) from exc
        except GeminiHighDemandError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
            ) from exc
        except Exception as exc:
            logger.exception("Unexpected error fetching homework review: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to retrieve homework review: {exc}",
            ) from exc

    return fastapi_app


app = create_app()
