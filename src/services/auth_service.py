"""
Purpose:
    Role-based authentication service supporting secure Lecturer registration/login
    (PBKDF2-HMAC-SHA256 password hashing, LECTURER_ACCESS_CODE verification, and
    signed JWT HTTP-only cookies stored in Firebase RTDB `/users/lecturers`) and
    frictionless KISS Student roster authentication (`/students`).
Inputs:
    `AppConfig` settings and `FirebaseRepository` persistence instance.
Outputs:
    `AuthService` class providing registration, login, password hashing, and JWT verification.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from typing import Any

import jwt
from fastapi import HTTPException, Request, status

from src.config import AppConfig
from src.repository.firebase_repository import FirebaseRepository

LECTURER_COOKIE_NAME = "ta_lecturer_session"
STUDENT_COOKIE_NAME = "ta_student_session"
PBKDF2_ITERATIONS = 200_000


class AuthService:
    """
    Purpose:
        Manages Lecturer and Student authentication, password hashing, Firebase RTDB
        user lookup, and cryptographic JWT creation and validation.
    Inputs:
        repository (FirebaseRepository): Firebase RTDB persistence layer.
        config (AppConfig): Application configuration containing JWT and access code secrets.
    Outputs:
        `AuthService` instance used by API endpoints and security dependencies.
    """

    def __init__(self, repository: FirebaseRepository, config: AppConfig) -> None:
        """
        Purpose:
            Initialize `AuthService` with a Firebase repository and runtime configuration.
        Inputs:
            repository (FirebaseRepository): Target Firebase RTDB repository.
            config (AppConfig): Configuration holding `jwt_secret` and `lecturer_access_code`.
        Outputs:
            None.
        """
        self.repository = repository
        self.config = config

    @staticmethod
    def email_to_key(email: str) -> str:
        """
        Purpose:
            Convert an email address into a deterministic, Firebase-safe key
            (replacing `.`, `@`, and special characters with underscores).
        Inputs:
            email (str): Raw email address.
        Outputs:
            str: Sanitized lowercase key suitable for Firebase RTDB paths.
        """
        normalized = email.strip().lower()
        sanitized = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
        return f"lec_{sanitized}" if sanitized else "lec_unknown"

    @staticmethod
    def hash_password(password: str, salt: str | None = None) -> str:
        """
        Purpose:
            Hash a plaintext password using PBKDF2-HMAC-SHA256 with 200,000 iterations
            and a 16-byte random hex salt.
        Inputs:
            password (str): Plaintext password to hash.
            salt (str | None): Optional hex salt (generated randomly if omitted).
        Outputs:
            str: Encoded hash string in the format `pbkdf2_sha256$<iterations>$<salt>$<hex_digest>`.
        """
        active_salt = salt or secrets.token_hex(16)
        derived = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            active_salt.encode("utf-8"),
            PBKDF2_ITERATIONS,
        )
        return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${active_salt}${derived.hex()}"

    @staticmethod
    def verify_password(password: str, stored_hash: str) -> bool:
        """
        Purpose:
            Verify a candidate plaintext password against a stored PBKDF2-HMAC-SHA256
            hash in constant time using `hmac.compare_digest`.
        Inputs:
            password (str): Candidate plaintext password.
            stored_hash (str): Stored formatted hash string from Firebase RTDB.
        Outputs:
            bool: `True` if the password matches the stored hash, `False` otherwise.
        """
        try:
            algorithm, iter_str, salt, expected_hex = stored_hash.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            iterations = int(iter_str)
            candidate = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt.encode("utf-8"),
                iterations,
            ).hex()
            # Use constant-time comparison to prevent timing side-channel attacks
            return hmac.compare_digest(candidate, expected_hex)
        except Exception:
            return False

    def create_jwt_token(
        self,
        subject_id: str,
        role: str,
        email: str,
        full_name: str,
        extra_claims: dict[str, Any] | None = None,
        expires_in_seconds: int | None = None,
    ) -> str:
        """
        Purpose:
            Create a cryptographically signed JWT session token for a Lecturer or Student.
        Inputs:
            subject_id (str): Unique user ID (`lecturer_id` or `student_id`).
            role (str): Role identifier (`"lecturer"` or `"student"`).
            email (str): User email address.
            full_name (str): User full display name.
            extra_claims (dict[str, Any] | None): Optional additional JWT payload claims.
            expires_in_seconds (int | None): Optional custom lifetime in seconds.
        Outputs:
            str: Encoded JWT token string.
        """
        now = int(time.time())
        lifetime = (
            expires_in_seconds
            if expires_in_seconds is not None
            else self.config.jwt_expiration_hours * 3600
        )
        payload: dict[str, Any] = {
            "sub": subject_id,
            "role": role,
            "email": email,
            "full_name": full_name,
            "iat": now,
            "exp": now + lifetime,
        }
        if extra_claims:
            payload.update(extra_claims)

        return jwt.encode(
            payload,
            self.config.jwt_secret,
            algorithm=self.config.jwt_algorithm,
        )

    def verify_jwt_token(
        self, token: str, required_role: str | None = None
    ) -> dict[str, Any]:
        """
        Purpose:
            Decode and validate a signed JWT token, optionally enforcing a required role.
        Inputs:
            token (str): Encoded JWT string from cookie or Authorization header.
            required_role (str | None): Optional required role (`"lecturer"` or `"student"`).
        Outputs:
            dict[str, Any]: Decoded JWT claims dictionary. Raises `PermissionError` or
            `ValueError` if invalid, expired, or unauthorized.
        """
        if not token or not token.strip():
            raise ValueError("Authentication token is missing.")

        try:
            claims = jwt.decode(
                token.strip(),
                self.config.jwt_secret,
                algorithms=[self.config.jwt_algorithm],
            )
        except jwt.ExpiredSignatureError as exc:
            raise ValueError("Authentication token has expired.") from exc
        except jwt.InvalidTokenError as exc:
            raise ValueError("Invalid authentication token signature.") from exc

        if required_role and claims.get("role") != required_role:
            raise PermissionError(
                f"Access denied: endpoint requires role '{required_role}'."
            )
        return dict(claims)

    def register_lecturer(
        self,
        full_name: str,
        email: str,
        password: str,
        access_code: str,
    ) -> dict[str, Any]:
        """
        Purpose:
            Register a new Lecturer account in Firebase RTDB (`/users/lecturers`)
            after verifying `LECTURER_ACCESS_CODE` and hashing the password.
        Inputs:
            full_name (str): Lecturer's full name.
            email (str): Lecturer's email address.
            password (str): Plaintext password (minimum 6 characters).
            access_code (str): Secret access code restricting lecturer registration.
        Outputs:
            dict[str, Any]: Dictionary containing `lecturer` profile and signed `token`.
        """
        clean_name = full_name.strip()
        clean_email = email.strip().lower()

        # Constant-time check of the lecturer access code against .env setting
        if not access_code or not hmac.compare_digest(
            access_code.strip(), self.config.lecturer_access_code
        ):
            raise PermissionError("Invalid lecturer access code.")

        if not clean_name:
            raise ValueError("Lecturer full name is required.")
        if not clean_email or "@" not in clean_email:
            raise ValueError("A valid lecturer email address is required.")
        if not password or len(password) < 6:
            raise ValueError("Lecturer password must be at least 6 characters long.")

        lecturer_id = self.email_to_key(clean_email)
        rtdb_path = f"/users/lecturers/{lecturer_id}"
        existing = self.repository.get(rtdb_path)
        if existing is not None:
            raise ValueError(f"Lecturer with email '{clean_email}' is already registered.")

        now_ts = int(time.time())
        record = {
            "lecturer_id": lecturer_id,
            "full_name": clean_name,
            "email": clean_email,
            "password_hash": self.hash_password(password),
            "role": "lecturer",
            "created_at": now_ts,
        }
        self.repository.set(rtdb_path, record)

        token = self.create_jwt_token(
            subject_id=lecturer_id,
            role="lecturer",
            email=clean_email,
            full_name=clean_name,
        )
        public_profile = {k: v for k, v in record.items() if k != "password_hash"}
        return {"user": public_profile, "token": token}

    def login_lecturer(self, email: str, password: str) -> dict[str, Any]:
        """
        Purpose:
            Authenticate an existing Lecturer against `/users/lecturers` in Firebase RTDB
            and issue a signed JWT session token.
        Inputs:
            email (str): Lecturer's registered email address.
            password (str): Lecturer's plaintext password.
        Outputs:
            dict[str, Any]: Dictionary containing `user` profile and signed `token`.
        """
        clean_email = email.strip().lower()
        if not clean_email or not password:
            raise ValueError("Email and password are required.")

        lecturer_id = self.email_to_key(clean_email)
        record = self.repository.get(f"/users/lecturers/{lecturer_id}")
        if not isinstance(record, dict) or not self.verify_password(
            password, str(record.get("password_hash", ""))
        ):
            raise PermissionError("Invalid lecturer email or password.")

        token = self.create_jwt_token(
            subject_id=record["lecturer_id"],
            role="lecturer",
            email=record["email"],
            full_name=record["full_name"],
        )
        public_profile = {k: v for k, v in record.items() if k != "password_hash"}
        return {"user": public_profile, "token": token}

    def authenticate_student(
        self,
        student_id: str | None = None,
        email: str | None = None,
        full_name: str | None = None,
    ) -> dict[str, Any]:
        """
        Purpose:
            Provide frictionless KISS student authentication by matching the student's
            `student_id`, `email`, or `full_name` against the imported `/students`
            roster in Firebase RTDB and issuing a signed student session JWT.
        Inputs:
            student_id (str | None): Direct student roster ID if selected from dropdown.
            email (str | None): Student's email address from the roster.
            full_name (str | None): Student's full name from the roster.
        Outputs:
            dict[str, Any]: Dictionary containing matched `user` record and signed `token`.
        """
        students_map = self.repository.get("/students")
        if not isinstance(students_map, dict) or not students_map:
            raise ValueError(
                "No student roster has been imported yet. Please ask the lecturer to import the roster."
            )

        matched_student: dict[str, Any] | None = None

        # 1. Exact lookup by student_id
        if student_id and student_id.strip() in students_map:
            matched_student = students_map[student_id.strip()]

        # 2. Case-insensitive lookup by email
        if matched_student is None and email and email.strip():
            target_email = email.strip().lower()
            for stu in students_map.values():
                if isinstance(stu, dict) and str(stu.get("email", "")).lower() == target_email:
                    matched_student = stu
                    break

        # 3. Exact lookup by full_name
        if matched_student is None and full_name and full_name.strip():
            target_name = full_name.strip()
            for stu in students_map.values():
                if isinstance(stu, dict) and str(stu.get("full_name", "")).strip() == target_name:
                    matched_student = stu
                    break

        if matched_student is None:
            raise PermissionError("Student was not found in the active class roster.")

        stu_id = str(matched_student["student_id"])
        stu_email = str(matched_student.get("email", ""))
        stu_name = str(matched_student.get("full_name", ""))
        stu_group = str(matched_student.get("group", ""))

        token = self.create_jwt_token(
            subject_id=stu_id,
            role="student",
            email=stu_email,
            full_name=stu_name,
            extra_claims={"group": stu_group},
        )
        user_payload = {
            "student_id": stu_id,
            "full_name": stu_name,
            "email": stu_email,
            "group": stu_group,
            "role": "student",
        }
        return {"user": user_payload, "token": token}

    def extract_token_from_request(
        self, request: Request, prefer_role: str | None = None
    ) -> str | None:
        """
        Purpose:
            Extract a JWT token from incoming HTTP request headers (`Authorization: Bearer`)
            or signed HTTP-only session cookies (`ta_lecturer_session` / `ta_student_session`).
        Inputs:
            request (Request): Incoming FastAPI HTTP request.
            prefer_role (str | None): Optional preferred role cookie (`"lecturer"` or `"student"`).
        Outputs:
            str | None: Raw JWT string if found, else `None`.
        """
        auth_header = request.headers.get("Authorization", "").strip()
        if auth_header.lower().startswith("bearer "):
            return auth_header[7:].strip()

        if prefer_role == "lecturer":
            return request.cookies.get(LECTURER_COOKIE_NAME)
        if prefer_role == "student":
            return request.cookies.get(STUDENT_COOKIE_NAME)

        # Default lookup checks lecturer cookie first, then student cookie
        return request.cookies.get(LECTURER_COOKIE_NAME) or request.cookies.get(
            STUDENT_COOKIE_NAME
        )

    def require_lecturer_claims(self, request: Request) -> dict[str, Any]:
        """
        Purpose:
            FastAPI request guard ensuring the caller holds a valid, non-expired
            Lecturer JWT cookie or Bearer token. Raises HTTP 401/403 otherwise.
        Inputs:
            request (Request): Incoming FastAPI request.
        Outputs:
            dict[str, Any]: Verified lecturer JWT claims.
        """
        token = self.extract_token_from_request(request, prefer_role="lecturer")
        if not token:
            # Also check if a Bearer or generic cookie token was supplied with a non-lecturer role
            any_token = self.extract_token_from_request(request)
            if any_token:
                try:
                    self.verify_jwt_token(any_token, required_role="lecturer")
                except PermissionError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
                    ) from exc
                except ValueError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
                    ) from exc
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Lecturer authentication required.",
            )

        try:
            return self.verify_jwt_token(token, required_role="lecturer")
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
            ) from exc
