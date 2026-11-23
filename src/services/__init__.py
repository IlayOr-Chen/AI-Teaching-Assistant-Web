"""
Purpose:
    Domain services package for authentication, roster import, smart attendance,
    YOLO headcount, live polls, threaded comments, video summarization, and homework review.
Inputs:
    None.
Outputs:
    Exports `AuthService`, `RosterImporter`, `AttendanceManager`, and cookie constants.
"""

from src.services.attendance_manager import (
    DEVICE_COOKIE_NAME,
    AttendanceManager,
)
from src.services.auth_service import (
    LECTURER_COOKIE_NAME,
    STUDENT_COOKIE_NAME,
    AuthService,
)
from src.services.roster_importer import RosterImporter

__all__ = [
    "AttendanceManager",
    "AuthService",
    "DEVICE_COOKIE_NAME",
    "LECTURER_COOKIE_NAME",
    "RosterImporter",
    "STUDENT_COOKIE_NAME",
]
