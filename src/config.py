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
        smart attendance, YOLO headcount, and Gemini LLM parameters.
    Inputs:
        firebase_db_url (str): Target Firebase Realtime Database root URL.
        firebase_credentials_path (str): File path to `serviceAccountKey.json`.
        use_mock_firebase (bool): When True, uses the deterministic in-memory
            Firebase adapter instead of hitting production Firebase RTDB.
        jwt_secret (str): Secret key used to sign and verify JWT cookies and QR tokens.
        jwt_algorithm (str): HMAC signing algorithm (default `HS256`).
        jwt_expiration_hours (int): Lecturer session token validity duration in hours.
        lecturer_access_code (str): Shared access gate preventing unauthorized lecturer signups.
        qr_rotation_seconds (int): Lifetime of each rotating attendance QR token in seconds.
        device_cooldown_minutes (int): Cooldown period in minutes enforced per student device.
        enforce_same_network_default (bool): Default state for same-network external IP check.
        gemini_api_key (str): Google Gemini API key for lecture video and homework analysis.
        gemini_model (str): Default Gemini model identifier (e.g., `gemini-2.5-flash-lite`).
        yolo_model_path (str): Path or model name for Ultralytics YOLO weights.
        yolo_confidence_threshold (float): Minimum detection confidence for person bounding boxes.
    Outputs:
        An immutable `AppConfig` instance accessible across services and repositories.
    """

    firebase_db_url: str = "https://ai-teaching-assistant-web-default-rtdb.firebaseio.com/"
    firebase_credentials_path: str = "serviceAccountKey.json"
    use_mock_firebase: bool = False
    jwt_secret: str = "dev-fallback-secret-key-change-in-production-2026"
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 12
    lecturer_access_code: str = "TA-ADMIN-2026"
    qr_rotation_seconds: int = 12
    device_cooldown_minutes: int = 15
    enforce_same_network_default: bool = True
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash-lite"
    yolo_model_path: str = "yolov8n.pt"
    yolo_confidence_threshold: float = 0.30

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

        # Parse boolean strings ("true", "1", "yes") safely into Python booleans
        use_mock_raw = os.getenv("USE_MOCK_FIREBASE", "false").strip().lower()
        use_mock = use_mock_raw in {"true", "1", "yes"}

        same_net_raw = os.getenv("ENFORCE_SAME_NETWORK_DEFAULT", "true").strip().lower()
        enforce_same_net = same_net_raw in {"true", "1", "yes"}

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
            lecturer_access_code=os.getenv("LECTURER_ACCESS_CODE", "TA-ADMIN-2026").strip(),
            qr_rotation_seconds=int(os.getenv("QR_ROTATION_SECONDS", "12")),
            device_cooldown_minutes=int(os.getenv("DEVICE_COOLDOWN_MINUTES", "15")),
            enforce_same_network_default=enforce_same_net,
            gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite").strip(),
            yolo_model_path=os.getenv("YOLO_MODEL_PATH", "yolov8n.pt").strip(),
            yolo_confidence_threshold=float(
                os.getenv("YOLO_CONFIDENCE_THRESHOLD", "0.30")
            ),
        )
