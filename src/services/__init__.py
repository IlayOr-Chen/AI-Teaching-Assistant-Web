"""
Purpose:
    Domain services package for authentication, roster import, smart attendance,
    YOLO headcount, live polls, threaded comments, video summarization, and homework review.
Inputs:
    None.
Outputs:
    Exports `AuthService`, `RosterImporter`, `AttendanceManager`, `PhotoHeadCounter`,
    `PollService`, `CommentService`, `calculate_distance_meters`, and cookie constants.
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
from src.services.comment_service import CommentService
from src.services.photo_head_counter import PhotoHeadCounter
from src.services.poll_service import PollService
from src.services.roster_importer import RosterImporter

__all__ = [
    "AttendanceManager",
    "AuthService",
    "CommentService",
    "DEVICE_COOKIE_NAME",
    "LECTURER_COOKIE_NAME",
    "PhotoHeadCounter",
    "PollService",
    "RosterImporter",
    "STUDENT_COOKIE_NAME",
    "calculate_distance_meters",
]
