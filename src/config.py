"""
Purpose:
    Centralized application configuration loader and validator backed by
    environment variables and `.env` files.
Inputs:
    Environment variables loaded from `.env` or OS process environment.
Outputs:
    Provides the `AppConfig` class encapsulating all runtime settings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass(frozen=True)
class AppConfig:
    """
    Purpose:
        Immutable configuration value object holding Firebase RTDB, JWT auth,
        smart attendance (static session QR, GPS geofencing, 15-min cooldown),
        YOLO headcount, and default Gemini model parameters.
    Inputs:
        firebase_db_url (str): Target Firebase Realtime Database root URL.
        firebase_credentials_path (str): File path to `serviceAccountKey.json`.
        use_mock_firebase (bool): When True, uses the deterministic in-memory
            Firebase adapter instead of hitting production Firebase RTDB.
        jwt_secret (str): Secret key used to sign and verify JWT cookies and device cookies.
        jwt_algorithm (str): HMAC signing algorithm (default `HS256`).
        jwt_expiration_hours (int): Lecturer session token validity duration in hours.
        lecturer_email (str): Pre-configured lecturer login email (default `adminL@gmail.com`).
        lecturer_password (str): Pre-configured lecturer login password (default `12345`).
        lecturer_name (str): Display name for the seeded lecturer account.
        lecturer_access_code (str): Internal programmatic gate for `AuthService.register_lecturer`
            unit tests (public HTTP self-registration is disabled with 403).
        device_cooldown_minutes (int): Cooldown period in minutes enforced per student device (15m).
        enforce_geofence_default (bool): Default state for GPS Geofencing check (True).
        geofence_radius_meters (int): Default allowed Haversine radius in meters around the classroom (100m).
        gemini_api_key (str): Optional programmatic fallback used only in tests (production is pure BYOK).
        gemini_model (str): Default Gemini model identifier (default `gemini-3.5-flash-lite`).
        yolo_model_path (str): Path or model name for Ultralytics YOLO weights.
        yolo_confidence_threshold (float): Minimum detection confidence for person bounding boxes.
        public_base_url (str): Optional public origin (Cloudflare Tunnel / Render URL) used to
            build QR check-in links; auto-detected from proxy headers when empty.
    Outputs:
        An immutable `AppConfig` instance accessible across services and repositories.
    """

    firebase_db_url: str = "https://ai-teaching-assistant-web-default-rtdb.firebaseio.com/"
    firebase_credentials_path: str = "serviceAccountKey.json"
    use_mock_firebase: bool = False
    jwt_secret: str = "dev-fallback-secret-key-change-in-production-2026"
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 12
    lecturer_email: str = "adminL@gmail.com"
    lecturer_password: str = "12345"
    lecturer_name: str = "Course Lecturer"
    lecturer_access_code: str = "TA-ADMIN-2026"
    device_cooldown_minutes: int = 15
    enforce_geofence_default: bool = True
    geofence_radius_meters: int = 100
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    yolo_model_path: str = "yolov8n.pt"
    yolo_confidence_threshold: float = 0.30
    public_base_url: str = ""

    @classmethod
    def from_env(cls, env_file: str | None = None) -> AppConfig:
        """
        Purpose:
            Construct an `AppConfig` instance by reading environment variables,
            optionally loading a `.env` file first.
        Inputs:
            env_file (str | None): Optional path to a `.env` file to load.
        Outputs:
            AppConfig: Populated and type-coerced configuration instance.
        """
        if env_file:
            load_dotenv(dotenv_path=env_file, override=False)
        else:
            load_dotenv(override=False)

        use_mock_raw = os.getenv("USE_MOCK_FIREBASE", "false").strip().lower()
        use_mock = use_mock_raw in {"true", "1", "yes"}

        geofence_raw = os.getenv("ENFORCE_GEOFENCE_DEFAULT", "true").strip().lower()
        enforce_geofence = geofence_raw in {"true", "1", "yes"}

        return cls(
            firebase_db_url=os.getenv(
                "FIREBASE_DB_URL",
                "https://ai-teaching-assistant-web-default-rtdb.firebaseio.com/",
            ).strip(),
            firebase_credentials_path=os.getenv(
                "FIREBASE_CREDENTIALS_PATH", "serviceAccountKey.json"
            ).strip(),
            use_mock_firebase=use_mock,
            jwt_secret=os.getenv(
                "JWT_SECRET", "dev-fallback-secret-key-change-in-production-2026"
            ).strip(),
            jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256").strip(),
            jwt_expiration_hours=int(os.getenv("JWT_EXPIRATION_HOURS", "12")),
            lecturer_email=os.getenv("LECTURER_EMAIL", "adminL@gmail.com").strip(),
            lecturer_password=os.getenv("LECTURER_PASSWORD", "12345").strip(),
            lecturer_name=os.getenv("LECTURER_NAME", "Course Lecturer").strip(),
            device_cooldown_minutes=int(os.getenv("DEVICE_COOLDOWN_MINUTES", "15")),
            enforce_geofence_default=enforce_geofence,
            geofence_radius_meters=int(os.getenv("GEOFENCE_RADIUS_METERS", "100")),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip(),
            yolo_model_path=os.getenv("YOLO_MODEL_PATH", "yolov8n.pt").strip(),
            yolo_confidence_threshold=float(
                os.getenv("YOLO_CONFIDENCE_THRESHOLD", "0.30")
            ),
            public_base_url=os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/"),
        )
