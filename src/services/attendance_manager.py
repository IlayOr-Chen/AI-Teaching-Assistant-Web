"""
Purpose:
    Implements `AttendanceManager` and `calculate_distance_meters` for Feature 1 (Smart Attendance):
    1) Short-lived cryptographically signed rotating QR tokens (~8s UI rotation + ~15s total grace validity)
       and Base64 PNG QR image generation,
    2) GPS Geofencing validation via Browser Geolocation coordinates and the Haversine great-circle
       distance formula, with a live Lecturer toggle (`enforce_geofence`) and configurable radius,
    3) 15-Minute Device Cooldown enforced via signed HTTP cookies and Firebase RTDB (`/device_cooldowns`),
    4) Student mobile check-in, manual lecturer attendance overrides, and CSV/Excel exports.
Inputs:
    `FirebaseRepository` persistence instance and `AppConfig` runtime settings.
Outputs:
    `AttendanceManager` class and `calculate_distance_meters` helper function.
"""

from __future__ import annotations

import base64
import csv
from datetime import datetime, timezone
import hashlib
import hmac
import io
import math
import secrets
import time
from typing import Any

import jwt
import openpyxl
import qrcode

from src.config import AppConfig
from src.repository.firebase_repository import FirebaseRepository

DEVICE_COOKIE_NAME = "ta_device_id"
EARTH_RADIUS_METERS = 6_371_000.0


def calculate_distance_meters(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """
    Purpose:
        Calculate the great-circle distance in meters between two WGS84 GPS coordinates
        (`(lat1, lon1)` and `(lat2, lon2)`) using the Haversine formula.
    Inputs:
        lat1 (float): Latitude of point 1 in decimal degrees (`-90.0` to `90.0`).
        lon1 (float): Longitude of point 1 in decimal degrees (`-180.0` to `180.0`).
        lat2 (float): Latitude of point 2 in decimal degrees (`-90.0` to `90.0`).
        lon2 (float): Longitude of point 2 in decimal degrees (`-180.0` to `180.0`).
    Outputs:
        float: Great-circle distance between the two points in meters (rounded to 2 decimals).
    """
    f_lat1, f_lon1 = float(lat1), float(lon1)
    f_lat2, f_lon2 = float(lat2), float(lon2)

    if not (-90.0 <= f_lat1 <= 90.0 and -90.0 <= f_lat2 <= 90.0):
        raise ValueError("Latitude values must be between -90.0 and 90.0 degrees.")
    if not (-180.0 <= f_lon1 <= 180.0 and -180.0 <= f_lon2 <= 180.0):
        raise ValueError("Longitude values must be between -180.0 and 180.0 degrees.")

    # Convert decimal degrees to radians for Haversine spherical trigonometry
    phi1 = math.radians(f_lat1)
    phi2 = math.radians(f_lat2)
    delta_phi = math.radians(f_lat2 - f_lat1)
    delta_lambda = math.radians(f_lon2 - f_lon1)

    # Haversine formula: a = sin^2(dlat/2) + cos(lat1)*cos(lat2)*sin^2(dlon/2)
    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * (math.sin(delta_lambda / 2.0) ** 2)
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return round(EARTH_RADIUS_METERS * c, 2)


class AttendanceManager:
    """
    Purpose:
        Core domain service orchestrating smart classroom attendance sessions,
        rotating signed QR codes, Haversine GPS geofencing verification, and
        15-minute device cooldowns in Firebase RTDB.
    Inputs:
        repository (FirebaseRepository): Firebase RTDB persistence adapter.
        config (AppConfig): Configuration holding QR rotation, geofence radius, cooldown, and JWT secrets.
    Outputs:
        `AttendanceManager` service instance.
    """

    def __init__(self, repository: FirebaseRepository, config: AppConfig) -> None:
        """
        Purpose:
            Initialize `AttendanceManager` with a repository and application configuration.
        Inputs:
            repository (FirebaseRepository): Target Firebase RTDB repository.
            config (AppConfig): Application configuration parameters.
        Outputs:
            None.
        """
        self.repository = repository
        self.config = config

    @staticmethod
    def calculate_distance_meters(
        lat1: float, lon1: float, lat2: float, lon2: float
    ) -> float:
        """
        Purpose:
            Delegate to `calculate_distance_meters` to compute the Haversine distance
            in meters between classroom and student GPS coordinates.
        Inputs:
            lat1 (float): Classroom latitude in decimal degrees.
            lon1 (float): Classroom longitude in decimal degrees.
            lat2 (float): Student latitude in decimal degrees.
            lon2 (float): Student longitude in decimal degrees.
        Outputs:
            float: Distance in meters between the two GPS coordinates.
        """
        return calculate_distance_meters(lat1, lon1, lat2, lon2)

    @staticmethod
    def _validate_optional_coords(
        lat: float | None, lng: float | None
    ) -> tuple[float | None, float | None]:
        """
        Purpose:
            Validate that optional latitude/longitude coordinates are either both None
            or both valid WGS84 decimal degrees.
        Inputs:
            lat (float | None): Optional latitude.
            lng (float | None): Optional longitude.
        Outputs:
            tuple[float | None, float | None]: Validated `(lat, lng)` tuple.
        """
        if lat is None and lng is None:
            return None, None
        if lat is None or lng is None:
            raise ValueError(
                "Both latitude and longitude must be provided together."
            )
        f_lat, f_lng = float(lat), float(lng)
        if not (-90.0 <= f_lat <= 90.0):
            raise ValueError("Latitude must be between -90.0 and 90.0 degrees.")
        if not (-180.0 <= f_lng <= 180.0):
            raise ValueError("Longitude must be between -180.0 and 180.0 degrees.")
        return round(f_lat, 6), round(f_lng, 6)

    def create_signed_device_cookie(
        self, device_id: str | None = None
    ) -> tuple[str, str]:
        """
        Purpose:
            Generate or reuse a unique device identifier and sign it with HMAC-SHA256
            so client cookies cannot be forged or modified to bypass the 15-minute cooldown.
        Inputs:
            device_id (str | None): Existing verified device ID, or None to mint a new ID.
        Outputs:
            tuple[str, str]: `(device_id, signed_cookie_value)` in format `dev_<hex>.<hmac_sig>`.
        """
        clean_id = (
            device_id.strip()
            if device_id and device_id.strip()
            else f"dev_{secrets.token_hex(12)}"
        )
        signature = hmac.new(
            self.config.jwt_secret.encode("utf-8"),
            clean_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return clean_id, f"{clean_id}.{signature}"

    def verify_signed_device_cookie(self, cookie_value: str | None) -> str | None:
        """
        Purpose:
            Verify the HMAC-SHA256 signature on an incoming `ta_device_id` cookie
            in constant time and extract the underlying `device_id`.
        Inputs:
            cookie_value (str | None): Raw cookie string from the HTTP request.
        Outputs:
            str | None: Verified `device_id` string if valid, or `None` if missing/tampered.
        """
        if not cookie_value or "." not in cookie_value:
            return None
        device_id, provided_sig = cookie_value.strip().rsplit(".", 1)
        if not device_id:
            return None
        _, expected_cookie = self.create_signed_device_cookie(device_id)
        expected_sig = expected_cookie.rsplit(".", 1)[1]
        # Constant-time signature verification prevents cookie forgery
        if hmac.compare_digest(provided_sig, expected_sig):
            return device_id
        return None

    def start_session(
        self,
        lesson_id: str,
        lesson_title: str,
        classroom_lat: float | None = None,
        classroom_lng: float | None = None,
        geofence_radius_meters: int | None = None,
        enforce_geofence: bool | None = None,
        rotation_seconds: int | None = None,
    ) -> dict[str, Any]:
        """
        Purpose:
            Create or re-open an attendance session in Firebase RTDB under
            `/attendance_sessions/{lesson_id}`, storing the classroom GPS coordinates,
            allowed geofence radius in meters, and geofence enforcement flag.
        Inputs:
            lesson_id (str): Unique lesson identifier (e.g., `"lesson_1"`).
            lesson_title (str): Human-readable title (e.g., `"Lesson 1: OOP & OOD"`).
            classroom_lat (float | None): Classroom GPS latitude (browser GPS or manual fallback).
            classroom_lng (float | None): Classroom GPS longitude (browser GPS or manual fallback).
            geofence_radius_meters (int | None): Allowed radius in meters (default 100m).
            enforce_geofence (bool | None): Whether student GPS distance must be within
                `geofence_radius_meters` (defaults to `config.enforce_geofence_default`).
            rotation_seconds (int | None): UI QR token rotation interval in seconds (default 8s).
        Outputs:
            dict[str, Any]: Persisted attendance session state.
        """
        clean_id = lesson_id.strip()
        if not clean_id:
            raise ValueError("lesson_id is required to start an attendance session.")

        valid_lat, valid_lng = self._validate_optional_coords(
            classroom_lat, classroom_lng
        )

        existing = self.repository.get(f"/attendance_sessions/{clean_id}")
        existing_records = (
            existing.get("records", {}) if isinstance(existing, dict) else {}
        )
        existing_yolo = (
            existing.get("yolo_headcount") if isinstance(existing, dict) else None
        )

        enforce_flag = (
            enforce_geofence
            if enforce_geofence is not None
            else self.config.enforce_geofence_default
        )
        radius_m = (
            int(geofence_radius_meters)
            if geofence_radius_meters is not None
            else self.config.geofence_radius_meters
        )
        if radius_m < 10 or radius_m > 5000:
            raise ValueError(
                "geofence_radius_meters must be between 10 and 5000 meters."
            )

        qr_ttl = (
            int(rotation_seconds)
            if rotation_seconds is not None and int(rotation_seconds) >= 3
            else self.config.qr_rotation_seconds
        )

        now_ts = int(time.time())
        session_data: dict[str, Any] = {
            "lesson_id": clean_id,
            "lesson_title": lesson_title.strip() or clean_id,
            "active": True,
            "classroom_lat": valid_lat,
            "classroom_lng": valid_lng,
            "geofence_radius_meters": radius_m,
            "enforce_geofence": bool(enforce_flag),
            "rotation_seconds": qr_ttl,
            "started_at": now_ts,
            "closed_at": None,
            "records": existing_records,
            "yolo_headcount": existing_yolo,
        }
        self.repository.set(f"/attendance_sessions/{clean_id}", session_data)
        return session_data

    def close_session(self, lesson_id: str) -> dict[str, Any]:
        """
        Purpose:
            Deactivate an attendance session so no further QR check-ins are accepted.
        Inputs:
            lesson_id (str): Target lesson identifier.
        Outputs:
            dict[str, Any]: Updated attendance session dictionary.
        """
        session = self.get_session(lesson_id)
        if session is None:
            raise ValueError(f"Attendance session '{lesson_id}' was not found.")

        updates = {"active": False, "closed_at": int(time.time())}
        return self.repository.update(f"/attendance_sessions/{lesson_id.strip()}", updates)

    def set_geofence_enforcement(
        self,
        lesson_id: str,
        enforce_geofence: bool,
        geofence_radius_meters: int | None = None,
        classroom_lat: float | None = None,
        classroom_lng: float | None = None,
    ) -> dict[str, Any]:
        """
        Purpose:
            Toggle the Lecturer's "Enforce GPS Geofencing" setting (and optionally update
            the allowed radius or classroom coordinates) live during an attendance session.
        Inputs:
            lesson_id (str): Target lesson session ID.
            enforce_geofence (bool): True to require student GPS within radius, False to bypass.
            geofence_radius_meters (int | None): Optional updated radius in meters.
            classroom_lat (float | None): Optional updated classroom latitude.
            classroom_lng (float | None): Optional updated classroom longitude.
        Outputs:
            dict[str, Any]: Updated attendance session dictionary.
        """
        session = self.get_session(lesson_id)
        if session is None:
            raise ValueError(f"Attendance session '{lesson_id}' was not found.")

        updates: dict[str, Any] = {"enforce_geofence": bool(enforce_geofence)}
        if geofence_radius_meters is not None:
            radius_val = int(geofence_radius_meters)
            if radius_val < 10 or radius_val > 5000:
                raise ValueError(
                    "geofence_radius_meters must be between 10 and 5000 meters."
                )
            updates["geofence_radius_meters"] = radius_val

        if classroom_lat is not None or classroom_lng is not None:
            valid_lat, valid_lng = self._validate_optional_coords(
                classroom_lat, classroom_lng
            )
            updates["classroom_lat"] = valid_lat
            updates["classroom_lng"] = valid_lng

        return self.repository.update(f"/attendance_sessions/{lesson_id.strip()}", updates)

    def get_session(self, lesson_id: str) -> dict[str, Any] | None:
        """
        Purpose:
            Retrieve a single attendance session record from `/attendance_sessions/{lesson_id}`.
        Inputs:
            lesson_id (str): Target lesson identifier.
        Outputs:
            dict[str, Any] | None: Session dictionary if found, else `None`.
        """
        if not lesson_id or not lesson_id.strip():
            return None
        data = self.repository.get(f"/attendance_sessions/{lesson_id.strip()}")
        return data if isinstance(data, dict) else None

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        Purpose:
            List all attendance sessions stored in `/attendance_sessions`, ordered by `started_at`.
        Inputs:
            None.
        Outputs:
            list[dict[str, Any]]: Ordered list of attendance session records.
        """
        raw = self.repository.get("/attendance_sessions")
        if not isinstance(raw, dict):
            return []
        sessions = [s for s in raw.values() if isinstance(s, dict)]
        sessions.sort(key=lambda item: int(item.get("started_at", 0)), reverse=True)
        return sessions

    @staticmethod
    def _render_qr_data_url(target_url: str) -> str:
        """
        Purpose:
            Render a scannable QR code image in memory and encode it as a Base64 PNG Data URL.
        Inputs:
            target_url (str): URL or token payload encoded inside the QR matrix.
        Outputs:
            str: Data URL (`data:image/png;base64,...`) ready for `<img src="...">`.
        """
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
        )
        qr.add_data(target_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#0f172a", back_color="#ffffff")
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        b64_png = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64_png}"

    def generate_rotating_qr_token(
        self,
        lesson_id: str,
        base_url: str = "",
        ttl_override_seconds: int | None = None,
        now_ts: int | None = None,
    ) -> dict[str, Any]:
        """
        Purpose:
            Generate a short-lived, cryptographically signed QR attendance token and
            accompanying Base64 PNG QR code for the active lesson session (~8s UI rotation
            with ~15s total server-side token validity including grace window).
        Inputs:
            lesson_id (str): Active lesson identifier.
            base_url (str): Optional public origin URL (Cloudflare Tunnel or Render URL).
            ttl_override_seconds (int | None): Optional custom lifetime in seconds (used in tests);
                when set it replaces both the rotation period and the validity window.
            now_ts (int | None): Optional UNIX timestamp override for deterministic testing.
        Outputs:
            dict[str, Any]: Payload containing `lesson_id`, `qr_token`, `checkin_url`,
            `qr_image_data_url`, `issued_at`, `expires_at`, `ttl_seconds` (UI rotation interval),
            `valid_for_seconds` (total acceptance window), and geofence settings.
        """
        session = self.get_session(lesson_id)
        if session is None:
            raise ValueError(f"Attendance session '{lesson_id}' does not exist.")
        if not session.get("active", False):
            raise ValueError(f"Attendance session '{lesson_id}' is currently closed.")

        issued_at = int(now_ts) if now_ts is not None else int(time.time())
        if ttl_override_seconds is not None:
            rotation_seconds = int(ttl_override_seconds)
            valid_for_seconds = rotation_seconds
        else:
            rotation_seconds = int(
                session.get("rotation_seconds", self.config.qr_rotation_seconds)
            )
            # Keep UI rotation fast (~8s) while granting a short server-side grace window
            # (~7s extra -> ~15s total validity) so students scanning right before rotation succeed.
            valid_for_seconds = rotation_seconds + max(0, self.config.qr_grace_seconds)
        ttl_seconds = rotation_seconds
        expires_at = issued_at + valid_for_seconds

        claims = {
            "type": "qr_attendance",
            "lesson_id": session["lesson_id"],
            "nonce": secrets.token_hex(6),
            "iat": issued_at,
            "exp": expires_at,
        }
        qr_token = jwt.encode(
            claims,
            self.config.jwt_secret,
            algorithm=self.config.jwt_algorithm,
        )
        clean_base = base_url.rstrip("/")
        checkin_path = f"/?lesson_id={session['lesson_id']}&qr_token={qr_token}"
        checkin_url = f"{clean_base}{checkin_path}" if clean_base else checkin_path

        return {
            "lesson_id": session["lesson_id"],
            "lesson_title": session.get("lesson_title", session["lesson_id"]),
            "qr_token": qr_token,
            "checkin_url": checkin_url,
            "qr_image_data_url": self._render_qr_data_url(checkin_url),
            "issued_at": issued_at,
            "expires_at": expires_at,
            "ttl_seconds": ttl_seconds,
            "valid_for_seconds": valid_for_seconds,
            "enforce_geofence": bool(session.get("enforce_geofence", True)),
            "geofence_radius_meters": int(
                session.get("geofence_radius_meters", self.config.geofence_radius_meters)
            ),
            "classroom_lat": session.get("classroom_lat"),
            "classroom_lng": session.get("classroom_lng"),
        }

    def verify_qr_token(
        self, qr_token: str, expected_lesson_id: str | None = None
    ) -> dict[str, Any]:
        """
        Purpose:
            Verify the cryptographic signature, token type, lesson binding, and
            expiration timestamp of a rotating QR attendance token.
        Inputs:
            qr_token (str): Signed JWT token scanned from the rotating QR code.
            expected_lesson_id (str | None): Expected lesson ID to validate against.
        Outputs:
            dict[str, Any]: Decoded QR token claims. Raises `ValueError` if expired or invalid.
        """
        if not qr_token or not qr_token.strip():
            raise ValueError("QR attendance token is required.")

        try:
            claims = jwt.decode(
                qr_token.strip(),
                self.config.jwt_secret,
                algorithms=[self.config.jwt_algorithm],
            )
        except jwt.ExpiredSignatureError as exc:
            raise ValueError(
                "QR code has expired. Please scan the current rotating QR code displayed by the lecturer."
            ) from exc
        except jwt.InvalidTokenError as exc:
            raise ValueError("Invalid or corrupted QR attendance token.") from exc

        if claims.get("type") != "qr_attendance":
            raise ValueError("Invalid token type: expected 'qr_attendance'.")

        if expected_lesson_id and claims.get("lesson_id") != expected_lesson_id.strip():
            raise ValueError(
                "QR token belongs to a different lesson session."
            )

        return dict(claims)

    def check_device_cooldown(
        self, device_id: str, now_ts: int | None = None
    ) -> dict[str, Any] | None:
        """
        Purpose:
            Inspect `/device_cooldowns/{device_id}` in Firebase RTDB and enforce the
            strict 15-minute cooldown per device so a student cannot immediately check in
            again under a friend's name.
        Inputs:
            device_id (str): Verified device identifier from the signed device cookie.
            now_ts (int | None): Optional current UNIX timestamp for deterministic testing.
        Outputs:
            dict[str, Any] | None: Returns None if no cooldown is active; raises
            `PermissionError` with the remaining cooldown countdown if blocked.
        """
        if not device_id:
            return None

        record = self.repository.get(f"/device_cooldowns/{device_id}")
        if not isinstance(record, dict):
            return None

        current_ts = int(now_ts) if now_ts is not None else int(time.time())
        cooldown_seconds = self.config.device_cooldown_minutes * 60
        last_checkin_at = int(record.get("last_checkin_at", 0))
        elapsed = current_ts - last_checkin_at

        # Cooldown math: block if elapsed seconds since last device check-in < 15 * 60 (900s)
        if 0 <= elapsed < cooldown_seconds:
            remaining_seconds = cooldown_seconds - elapsed
            mins, secs = divmod(remaining_seconds, 60)
            raise PermissionError(
                f"15-minute device cooldown active: this device already submitted attendance recently. "
                f"Remaining cooldown: {mins}m {secs:02d}s ({remaining_seconds} seconds)."
            )
        return None

    def record_device_cooldown(
        self,
        device_id: str,
        student_id: str,
        lesson_id: str,
        now_ts: int | None = None,
    ) -> dict[str, Any]:
        """
        Purpose:
            Persist the device check-in timestamp in Firebase RTDB under
            `/device_cooldowns/{device_id}` to enforce the 15-minute cooldown window.
        Inputs:
            device_id (str): Unique device identifier.
            student_id (str): Student ID that checked in from this device.
            lesson_id (str): Lesson ID of the check-in.
            now_ts (int | None): Optional UNIX timestamp override.
        Outputs:
            dict[str, Any]: Stored cooldown entry.
        """
        current_ts = int(now_ts) if now_ts is not None else int(time.time())
        cooldown_seconds = self.config.device_cooldown_minutes * 60
        entry = {
            "device_id": device_id,
            "last_student_id": student_id,
            "last_lesson_id": lesson_id,
            "last_checkin_at": current_ts,
            "expires_at": current_ts + cooldown_seconds,
        }
        self.repository.set(f"/device_cooldowns/{device_id}", entry)
        return entry

    def submit_qr_checkin(
        self,
        lesson_id: str,
        student_id: str,
        qr_token: str,
        student_lat: float | None = None,
        student_lng: float | None = None,
        device_cookie_value: str | None = None,
        now_ts: int | None = None,
    ) -> dict[str, Any]:
        """
        Purpose:
            Execute the full multi-layer Student QR Attendance Check-In pipeline:
            1) Validate active session & rotating signed QR token (~8s rotation + grace window),
            2) Validate 15-minute device cooldown (`/device_cooldowns` + signed cookie),
            3) Validate student exists in `/students` roster,
            4) Validate GPS Geofencing via Haversine distance (`distance <= geofence_radius_meters`
               when `enforce_geofence` is True and classroom coordinates are configured),
            5) Persist attendance record (with distance & geofence status) and lock device for 15 minutes.
        Inputs:
            lesson_id (str): Target lesson session ID.
            student_id (str): Selected student ID from the Firebase class roster.
            qr_token (str): Short-lived rotating QR token.
            student_lat (float | None): Student's GPS latitude from Browser Geolocation API.
            student_lng (float | None): Student's GPS longitude from Browser Geolocation API.
            device_cookie_value (str | None): Incoming `ta_device_id` signed cookie if present.
            now_ts (int | None): Optional UNIX timestamp override for deterministic tests.
        Outputs:
            dict[str, Any]: Check-in confirmation containing `record`, `device_id`,
            `signed_device_cookie`, and `cooldown_expires_at`.
        """
        clean_lesson_id = lesson_id.strip()
        clean_student_id = student_id.strip()

        # Step 1: Verify active session
        session = self.get_session(clean_lesson_id)
        if session is None:
            raise ValueError(f"Attendance session '{clean_lesson_id}' does not exist.")
        if not session.get("active", False):
            raise ValueError(
                f"Attendance session '{clean_lesson_id}' is closed and no longer accepting check-ins."
            )

        # Step 2: Verify rotating QR token signature, expiration, and lesson binding
        self.verify_qr_token(qr_token, expected_lesson_id=clean_lesson_id)

        # Step 3: Resolve or mint signed device ID and enforce 15-minute device cooldown
        verified_device_id = self.verify_signed_device_cookie(device_cookie_value)
        if verified_device_id is not None:
            self.check_device_cooldown(verified_device_id, now_ts=now_ts)

        device_id, signed_cookie = self.create_signed_device_cookie(verified_device_id)

        # Step 4: Verify student exists in imported class roster (/students)
        student = self.repository.get(f"/students/{clean_student_id}")
        if not isinstance(student, dict):
            raise ValueError(
                f"Student '{clean_student_id}' is not registered in the class roster."
            )

        # Step 5: Enforce GPS Geofencing (Haversine distance against classroom coordinates)
        enforce_geofence = bool(session.get("enforce_geofence", True))
        classroom_lat = session.get("classroom_lat")
        classroom_lng = session.get("classroom_lng")
        allowed_radius_m = int(
            session.get("geofence_radius_meters", self.config.geofence_radius_meters)
        )
        has_classroom_coords = (
            classroom_lat is not None and classroom_lng is not None
        )
        has_student_coords = (
            student_lat is not None and student_lng is not None
        )

        distance_meters: float | None = None
        if has_classroom_coords and has_student_coords:
            distance_meters = calculate_distance_meters(
                float(classroom_lat),
                float(classroom_lng),
                float(student_lat),
                float(student_lng),
            )

        if enforce_geofence and has_classroom_coords:
            if not has_student_coords:
                raise PermissionError(
                    "GPS location permission is required to check in while classroom geofencing "
                    "is enabled. Please allow Browser Geolocation (note: HTTPS via Cloudflare "
                    "Tunnel / Render or localhost is required for Browser Geolocation) or ask "
                    "the lecturer to disable GPS Geofencing."
                )
            assert distance_meters is not None
            if distance_meters > allowed_radius_m:
                raise PermissionError(
                    f"GPS Geofence check failed: You appear to be {round(distance_meters)}m away "
                    f"from the classroom (allowed: {allowed_radius_m}m)."
                )
            geofence_status = "verified"
        elif not enforce_geofence:
            geofence_status = "bypassed"
        else:
            geofence_status = "unconfigured"

        # Step 6: Persist student attendance record & 15-minute device cooldown
        checkin_ts = int(now_ts) if now_ts is not None else int(time.time())
        attendance_record = {
            "student_id": clean_student_id,
            "full_name": student.get("full_name", ""),
            "email": student.get("email", ""),
            "group": student.get("group", ""),
            "status": "present",
            "method": "qr",
            "checked_in_at": checkin_ts,
            "student_lat": round(float(student_lat), 6) if student_lat is not None else None,
            "student_lng": round(float(student_lng), 6) if student_lng is not None else None,
            "distance_meters": distance_meters,
            "geofence_status": geofence_status,
            "device_id": device_id,
        }
        self.repository.set(
            f"/attendance_sessions/{clean_lesson_id}/records/{clean_student_id}",
            attendance_record,
        )
        cooldown_entry = self.record_device_cooldown(
            device_id=device_id,
            student_id=clean_student_id,
            lesson_id=clean_lesson_id,
            now_ts=checkin_ts,
        )

        return {
            "lesson_id": clean_lesson_id,
            "record": attendance_record,
            "device_id": device_id,
            "signed_device_cookie": signed_cookie,
            "cooldown_expires_at": cooldown_entry["expires_at"],
        }

    def set_manual_attendance(
        self,
        lesson_id: str,
        student_id: str,
        status: str,
    ) -> dict[str, Any]:
        """
        Purpose:
            Allow the Lecturer to manually mark or toggle a student's attendance
            status (`"present"` or `"absent"`) in any lesson session.
        Inputs:
            lesson_id (str): Target lesson identifier.
            student_id (str): Student identifier from `/students`.
            status (str): Desired status (`"present"` or `"absent"`).
        Outputs:
            dict[str, Any]: Updated attendance record for the student.
        """
        normalized_status = status.strip().lower()
        if normalized_status not in {"present", "absent"}:
            raise ValueError("Attendance status must be 'present' or 'absent'.")

        student = self.repository.get(f"/students/{student_id.strip()}")
        if not isinstance(student, dict):
            raise ValueError(f"Student '{student_id}' not found in roster.")

        session = self.get_session(lesson_id)
        if session is None:
            session = self.start_session(
                lesson_id=lesson_id,
                lesson_title=lesson_id,
            )

        now_ts = int(time.time())
        record = {
            "student_id": student["student_id"],
            "full_name": student.get("full_name", ""),
            "email": student.get("email", ""),
            "group": student.get("group", ""),
            "status": normalized_status,
            "method": "manual",
            "checked_in_at": now_ts if normalized_status == "present" else None,
            "student_lat": None,
            "student_lng": None,
            "distance_meters": None,
            "geofence_status": "manual",
            "device_id": "lecturer_manual",
        }
        self.repository.set(
            f"/attendance_sessions/{lesson_id.strip()}/records/{student['student_id']}",
            record,
        )
        return record

    def get_lesson_attendance_report(self, lesson_id: str) -> dict[str, Any]:
        """
        Purpose:
            Combine the full `/students` roster with the lesson's attendance records
            to return a complete per-student attendance matrix and summary counts.
        Inputs:
            lesson_id (str): Target lesson identifier.
        Outputs:
            dict[str, Any]: Complete report with `session`, `present_count`,
            `absent_count`, `total_students`, and per-student `roster_status` rows.
        """
        session = self.get_session(lesson_id)
        records = (
            session.get("records", {})
            if isinstance(session, dict) and isinstance(session.get("records"), dict)
            else {}
        )

        raw_students = self.repository.get("/students")
        students_map = raw_students if isinstance(raw_students, dict) else {}

        roster_rows: list[dict[str, Any]] = []
        present_count = 0

        for stu_id, stu in students_map.items():
            if not isinstance(stu, dict):
                continue
            rec = records.get(stu_id)
            is_present = (
                isinstance(rec, dict) and rec.get("status") == "present"
            )
            if is_present:
                present_count += 1

            roster_rows.append(
                {
                    "student_id": stu_id,
                    "full_name": stu.get("full_name", ""),
                    "email": stu.get("email", ""),
                    "group": stu.get("group", ""),
                    "status": "present" if is_present else "absent",
                    "method": rec.get("method", "-") if isinstance(rec, dict) else "-",
                    "checked_in_at": (
                        rec.get("checked_in_at") if isinstance(rec, dict) else None
                    ),
                    "distance_meters": (
                        rec.get("distance_meters") if isinstance(rec, dict) else None
                    ),
                    "geofence_status": (
                        rec.get("geofence_status", "-")
                        if isinstance(rec, dict)
                        else "-"
                    ),
                }
            )

        roster_rows.sort(key=lambda item: str(item.get("full_name", "")))
        total_students = len(roster_rows)
        return {
            "lesson_id": lesson_id,
            "session": session,
            "present_count": present_count,
            "absent_count": total_students - present_count,
            "total_students": total_students,
            "roster_status": roster_rows,
        }

    @staticmethod
    def _format_timestamp_iso(unix_ts: int | float | None) -> str:
        """
        Purpose:
            Format a UNIX timestamp in seconds into a human-readable UTC ISO-8601 string
            for CSV and Excel attendance spreadsheet exports.
        Inputs:
            unix_ts (int | float | None): UNIX timestamp in seconds or None.
        Outputs:
            str: Formatted timestamp string (`YYYY-MM-DD HH:MM:SS UTC`) or `""` when None.
        """
        if not unix_ts:
            return ""
        dt = datetime.fromtimestamp(float(unix_ts), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")

    def export_attendance_csv(self, lesson_id: str) -> bytes:
        """
        Purpose:
            Generate a UTF-8 with BOM (`utf-8-sig`) CSV attendance report for a lesson
            so spreadsheet tools (including Microsoft Excel) render Hebrew student names cleanly.
        Inputs:
            lesson_id (str): Target lesson identifier.
        Outputs:
            bytes: Encoded CSV spreadsheet bytes.
        """
        report = self.get_lesson_attendance_report(lesson_id)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
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
        )
        for row in report["roster_status"]:
            dist_str = (
                f"{row['distance_meters']:.1f}"
                if row["distance_meters"] is not None
                else "-"
            )
            writer.writerow(
                [
                    row["student_id"],
                    row["full_name"],
                    row["email"],
                    row["group"] or "Unassigned",
                    lesson_id,
                    row["status"],
                    row["method"],
                    self._format_timestamp_iso(row["checked_in_at"]),
                    dist_str,
                    row["geofence_status"],
                ]
            )
        return output.getvalue().encode("utf-8-sig")

    def export_attendance_xlsx(self, lesson_id: str) -> bytes:
        """
        Purpose:
            Generate an Excel (`.xlsx`) workbook containing the full per-student
            attendance matrix and GPS geofence verification status for `lesson_id`.
        Inputs:
            lesson_id (str): Target lesson identifier.
        Outputs:
            bytes: Binary `.xlsx` workbook bytes ready for HTTP download.
        """
        report = self.get_lesson_attendance_report(lesson_id)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"Attendance {lesson_id}"[:31]

        ws.append(
            [
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
        )
        for row in report["roster_status"]:
            dist_val = (
                round(float(row["distance_meters"]), 1)
                if row["distance_meters"] is not None
                else "-"
            )
            ws.append(
                [
                    row["student_id"],
                    row["full_name"],
                    row["email"],
                    row["group"] or "Unassigned",
                    lesson_id,
                    row["status"],
                    row["method"],
                    self._format_timestamp_iso(row["checked_in_at"]),
                    dist_val,
                    row["geofence_status"],
                ]
            )

        buffer = io.BytesIO()
        wb.save(buffer)
        return buffer.getvalue()
