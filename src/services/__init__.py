"""
Purpose:
    Domain services package for authentication, roster import, smart attendance,
    YOLO headcount, live polls, threaded comments, Gemini client, MP4 lecture video
    summarization, and PDF homework review & peer comparison.
Inputs:
    None.
Outputs:
    Exports all domain OOP service classes and constants.
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
from src.services.gemini_client import GeminiClient
from src.services.homework_reviewer import (
    FIRST_SUBMISSION_NOTICE,
    GATED_RELEASE_NOTICE,
    HomeworkReviewer,
)
from src.services.photo_head_counter import PhotoHeadCounter
from src.services.poll_service import PollService
from src.services.roster_importer import RosterImporter
from src.services.video_summarizer import VideoSummarizer

__all__ = [
    "AttendanceManager",
    "AuthService",
    "CommentService",
    "DEVICE_COOKIE_NAME",
    "FIRST_SUBMISSION_NOTICE",
    "GATED_RELEASE_NOTICE",
    "GeminiClient",
    "HomeworkReviewer",
    "LECTURER_COOKIE_NAME",
    "PhotoHeadCounter",
    "PollService",
    "RosterImporter",
    "STUDENT_COOKIE_NAME",
    "VideoSummarizer",
    "calculate_distance_meters",
]
