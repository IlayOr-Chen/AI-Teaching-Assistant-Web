"""
Purpose:
    Domain services package for authentication, roster import, smart attendance,
    YOLO headcount, live polls, threaded comments, video summarization, and homework review.
Inputs:
    None.
Outputs:
    Exports `AuthService`, `RosterImporter`, `AttendanceManager`, `PhotoHeadCounter`,
    `calculate_distance_meters`, and cookie constants.
"""

from src.services.attendance_manager import (
    DEVICE_COOKIE_NAME,
    AttendanceManager,
    calculate_distance_meters,
)
from src.services.auth_service import (
    LECTURER_COOKIE_NAME,
    STUDENT_COOKIE_NAME,
    AuthService,
)
from src.services.photo_head_counter import PhotoHeadCounter
from src.services.roster_importer import RosterImporter

__all__ = [
    "AttendanceManager",
    "AuthService",
    "DEVICE_COOKIE_NAME",
    "LECTURER_COOKIE_NAME",
    "PhotoHeadCounter",
    "RosterImporter",
    "STUDENT_COOKIE_NAME",
    "calculate_distance_meters",
]
