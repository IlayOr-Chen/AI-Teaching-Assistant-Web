# Software Requirements Specification (SRS)

> **Project:** Web-Based AI Teaching Assistant (Ex2: Vibe Coding & Deep Designing)  
> **Team:** Ilay (`IlayOr-Chen`) & Roie (`RoieBenyamin`)

---

## 1. System Overview & Actors

The Web-Based AI Teaching Assistant is a modular Python **FastAPI** web application backed by **Firebase Realtime Database (RTDB)**, **Ultralytics YOLOv8n**, and **Google Gemini**, serving a responsive mobile-first Vanilla HTML/CSS/ES-Module frontend.

### Primary Actors
* **Lecturer:** Authenticates via `LECTURER_ACCESS_CODE` and password; imports the Excel class roster; starts/closes attendance sessions; configures GPS geofencing and YOLO headcounts; manages live polls and comments; configures Gemini API settings; summarizes MP4 lecture recordings; and creates/unlocks PDF homework assignments.
* **Student:** Scans the rotating classroom QR code on a smartphone to check in via Browser Geolocation; authenticates frictionlessly against the imported class roster; votes in live polls; posts public/private/anonymous comments; and uploads PDF homework submissions to view gated AI reviews and peer comparisons.

---

## 2. Functional Requirements

### 2.1 Authentication & Role Separation (`AuthService`)
* **FR-AUTH-01 (Lecturer Registration Gate):** The system shall require a valid `LECTURER_ACCESS_CODE` (verified in constant time via `hmac.compare_digest`) before creating a lecturer account under `/users/lecturers/{lecturer_id}`.
* **FR-AUTH-02 (Password Hashing):** Lecturer passwords shall be hashed using **PBKDF2-HMAC-SHA256** with `200,000` iterations and a 16-byte cryptographic random salt (`pbkdf2_sha256$200000$<salt>$<hex>`).
* **FR-AUTH-03 (JWT Session Cookies):** Upon Lecturer or Student login, the system shall issue a signed JWT (`HS256`) stored in an HTTP-only cookie (`ta_lecturer_session` or `ta_student_session`) and accepted via `Authorization: Bearer <token>`.
* **FR-AUTH-04 (Frictionless Student Auth):** Students shall authenticate by matching their `student_id`, case-insensitive `email`, or `full_name` against `/students` in Firebase RTDB.

### 2.2 Class Roster Import & Attendance Export (`RosterImporter` & `AttendanceManager`)
* **FR-ROSTER-01 (Hebrew & English Excel Parsing):** The system shall parse `.xlsx` files containing Hebrew headers (`שם פרטי`, `שם משפחה`, `דוא"ל`, `קבוצות`) or English equivalents, concatenating first and last names into `full_name`.
* **FR-ROSTER-02 (Empty/NaN Group Handling):** Empty, `None`, `float('nan')`, or `"NaN"` cells in `קבוצות` shall normalize cleanly to `""` without crashing, and whole-number floats such as `3.0` shall normalize to `"3"`.
* **FR-ROSTER-03 (Manual Attendance Override):** The Lecturer shall be able to manually toggle any student's status between `"present"` and `"absent"` (`method="manual"`).
* **FR-ROSTER-04 (Spreadsheet Export):** The Lecturer shall be able to export any lesson's attendance matrix as a UTF-8 with BOM (`.csv`) file or an Excel (`.xlsx`) workbook.

### 2.3 Feature 1 — Smart Multi-Layer Attendance (`AttendanceManager` & `PhotoHeadCounter`)
* **FR-ATT-01 (Static Session QR Code):** The system shall generate one short, clean 16-character URL-safe session QR token (`secrets.token_urlsafe(12)`) when the Lecturer opens an attendance session, remaining valid for the entire duration that the session is `LIVE` (`active=True`) and invalidated immediately when the Lecturer closes the session.
* **FR-ATT-02 (QR-Gated Student Roster Dropdown):** The mobile check-in endpoint (`GET /api/attendance/checkin-context`) shall return student roster entries (`student_id`, `full_name`, and `email`) ONLY when presented with the valid session QR token for an active session.
* **FR-ATT-03 (Haversine GPS Geofencing & Interactive Map):** Before opening a session, the Lecturer shall configure the classroom pin and radius (`30m`, `50m`, or `100m`) on an interactive Leaflet.js + OpenStreetMap map. When `enforce_geofence=True` and classroom coordinates (`classroom_lat`, `classroom_lng`) are set, the system shall compute the great-circle distance in meters using `calculate_distance_meters(lat1, lon1, lat2, lon2)` and allow check-in ONLY if `distance <= geofence_radius_meters`.
* **FR-ATT-04 (Live Geofence Toggle & Missing-GPS Handling):** If a student omits GPS coordinates while `enforce_geofence=True`, the system shall reject check-in with an English error explaining that Browser Geolocation (over HTTPS / `localhost`) is required. The Lecturer shall be able to toggle `enforce_geofence` OFF live during class.
* **FR-ATT-05 (15-Minute Device Cooldown & Lecturer Reset):** Upon check-in, the system shall issue an HMAC-SHA256 signed `ta_device_id` cookie and record `last_checkin_at` and `expires_at` (`+900` seconds) in `/device_cooldowns/{device_id}`, blocking any subsequent check-in from that device for 15 minutes with a formatted `MMm SSs` remaining countdown. The Lecturer shall be able to clear all device cooldowns immediately via `POST /api/attendance/reset-cooldowns` (`/settings/cooldown_reset_at`).
* **FR-ATT-06 (Local YOLO Classroom Headcount):** `PhotoHeadCounter` shall run local Ultralytics YOLOv8n (`yolov8n.pt`, `imgsz=1280`, COCO class `0` `person` only) on uploaded classroom photos (tested on `/assets/p3.jpeg`), producing a Base64 annotated JPEG with bounding boxes and confidence labels.
* **FR-ATT-07 (Color-Coded Confidence Indicator):** `PhotoHeadCounter` shall classify headcount reliability as:
  * **GREEN:** `average_confidence >= 0.60`, low-confidence box ratio `<= 0.25`, and `abs(detected_count - registered_count) <= 1`.
  * **ORANGE:** `2 <= abs_discrepancy <= 3`, `0.45 <= average_confidence < 0.60`, or low-confidence box ratio `> 0.25` (back-row occlusion warning).
  * **RED:** `abs_discrepancy >= 4`, `average_confidence < 0.45`, or `detected_count == 0` when students are registered.

### 2.4 Feature 2 — Comments & Suggestions (`CommentService`)
* **FR-CMT-01 (Visibility Isolation):** Comments marked `private` (`Lecturer Only`) shall be returned only to the Lecturer and the authoring student; other students shall never receive or reply to them.
* **FR-CMT-02 (Anonymous Identity Masking):** Comments and replies posted with `is_anonymous=True` by students shall serialize `display_name` as `"Anonymous Student"` and strip raw author identifiers from API responses.
* **FR-CMT-03 (Scope Tagging & Threaded Replies):** Comments shall support scope tags (`General`, `Lesson 1..N`), scope filtering, and chronological threaded replies.

### 2.5 Feature 3 — On-the-Spot KISS Polls (`PollService`)
* **FR-POLL-01 (Multiple-Choice Poll Lifecycle):** The Lecturer shall create polls with `2..8` distinct options and toggle voting open (`active=True`) or closed (`active=False`).
* **FR-POLL-02 (Mutable Voting & Live Percentages):** Each student (`voter_id`) can cast a vote, click a different option to switch their vote, or click their currently selected option again to cancel/unvote (`vote_action`: `cast` / `switched` / `removed`); votes on closed polls are rejected with HTTP `403`, and responses include `viewer_vote_index` plus live option counts and percentages (`0.0`..`100.0%`).

### 2.6 Feature 4 — Video AI Lecture Summary (`GeminiClient` & `VideoSummarizer`)
* **FR-VID-01 (Lecturer Gemini Settings):** The Lecturer shall configure their Gemini API Key (`AIza...` AI Studio or `AQ....` Vertex AI Express, masked in GET responses) and a free-text Gemini Model ID (defaulting to `gemini-3.5-flash-lite` with quick-select pills `gemini-3.5-flash-lite`, `gemini-3.5-flash`, `gemini-2.5-flash`) under `/settings/gemini`.
* **FR-VID-02 (YouTube URL URI, `<= 20 MB` MP4 Validation & Structured Summary):** `VideoSummarizer` shall accept **either** public YouTube URLs (normalized and sent directly to Gemini via `types.Part.from_uri(file_uri=..., mime_type="video/mp4")` with zero local download) **or** `.mp4` (`video/mp4`) files up to `20 MB` (via Gemini Files API for `AIza...` or inline `Part.from_bytes` for `AQ....`), raising explicit errors on missing/failing API keys (zero fake fallbacks) and outputting `executive_summary`, `key_topics`, `core_takeaways`, `action_items`, and `delivery_insights`.

### 2.7 Feature 5 — Deep Homework Review & Peer Comparison (`HomeworkReviewer`)
* **FR-HW-01 (Per-Student BYOK + Multi-Format Upload & Public Git Repo Ingestion):** Student submissions require the student's own browser-stored Gemini API key via `X-Gemini-Api-Key` / `X-Gemini-Model` headers (rejecting missing keys with HTTP `400` rather than falling back to Lecturer/env keys) and support multi-format files (`.pdf`, `.docx`, `.txt`, `.md`, `.zip`, source code `.py`, `.java`, `.c`, `.cpp`, `.h`, `.js`, `.ts`, `.html`, `.css`, `.json`) including Hebrew/sparse PDFs, as well as public GitHub repository URLs (`https://github.com/owner/repo`).
* **FR-HW-02 (Gated Release Rule):** While `is_review_released` is `False` (before `deadline_ts` and before *"End Submission Now"*), students can upload/replace their submission, but `review` and `peer_comparison` shall be strictly withheld (`None`).
* **FR-HW-03 (Deadline & Manual Release Unlock):** Once `now_ts >= deadline_ts` OR the Lecturer clicks **"End Submission Now"** (`ended_manually=True`), new submissions are blocked (`403`) and students unlock their **Concise Review Card** and **Dedicated Peer Comparison Column**.
* **FR-HW-04 (First-Submission Edge Case):** When `total_submissions == 1`, `peer_comparison` shall set `is_first_submission_only=True` and return the exact notice: *"This is the first submission received for this assignment, so peer comparison is not yet available. Comparative insights will appear once additional students submit."*

---

## 3. Non-Functional Requirements

* **NFR-PERF-01:** QR countdown ring animation shall render at 60fps via `requestAnimationFrame`; local YOLOv8n inference (`imgsz=1280`) shall cache loaded PyTorch weights in memory (`_SHARED_MODELS`) to avoid repeated disk deserialization.
* **NFR-SEC-01:** All user-generated text in the frontend shall be mounted via safe DOM text nodes (`textContent` / `el()`), never `innerHTML`, preventing XSS injection. Secrets (`.env`, `serviceAccountKey.json`) shall never be committed to Git.
* **NFR-TEST-01:** All automated `pytest` tests shall execute deterministically against `InMemoryFirebaseAdapter` (`use_mock=True`) inside `./venv` with zero production database side effects.

---

## 4. Firebase Realtime Database (RTDB) Data Schema

Target RTDB Instance: `https://ai-teaching-assistant-web-default-rtdb.firebaseio.com/`

```json
{
  "users": {
    "lecturers": {
      "lec_boaz_ariel_ac_il": {
        "lecturer_id": "lec_boaz_ariel_ac_il",
        "full_name": "Prof. Boaz Ben-Moshe",
        "email": "boaz@ariel.ac.il",
        "password_hash": "pbkdf2_sha256$200000$<salt>$<hex_digest>",
        "role": "lecturer",
        "created_at": 1732400000
      }
    }
  },
  "students": {
    "stu_guygg197_gmail_com": {
      "student_id": "stu_guygg197_gmail_com",
      "first_name": "גיא",
      "last_name": "אביב",
      "full_name": "גיא אביב",
      "email": "guygg197@gmail.com",
      "group": "3",
      "imported_at": 1732400100
    }
  },
  "attendance_sessions": {
    "lesson_1": {
      "lesson_id": "lesson_1",
      "lesson_title": "Lesson 1: OOP & OOD",
      "active": true,
      "classroom_lat": 32.1044,
      "classroom_lng": 35.2078,
      "geofence_radius_meters": 100,
      "enforce_geofence": true,
      "rotation_seconds": 8,
      "started_at": 1732400200,
      "closed_at": null,
      "records": {
        "stu_guygg197_gmail_com": {
          "student_id": "stu_guygg197_gmail_com",
          "full_name": "גיא אביב",
          "email": "guygg197@gmail.com",
          "group": "3",
          "status": "present",
          "method": "qr",
          "checked_in_at": 1732400215,
          "student_lat": 32.1046,
          "student_lng": 35.20792,
          "distance_meters": 24.95,
          "geofence_status": "verified",
          "device_id": "dev_a1b2c3d4e5f6"
        }
      },
      "yolo_headcount": {
        "detected_count": 22,
        "registered_count": 22,
        "discrepancy": 0,
        "status_color": "GREEN",
        "average_confidence": 0.634,
        "high_confidence_count": 15,
        "low_confidence_count": 4
      }
    }
  },
  "device_cooldowns": {
    "dev_a1b2c3d4e5f6": {
      "device_id": "dev_a1b2c3d4e5f6",
      "last_student_id": "stu_guygg197_gmail_com",
      "last_lesson_id": "lesson_1",
      "last_checkin_at": 1732400215,
      "expires_at": 1732401115
    }
  },
  "comments": {
    "cmt_1732400300_a1b2": {
      "comment_id": "cmt_1732400300_a1b2",
      "text": "Please share the sequence diagram examples.",
      "author_id": "stu_guygg197_gmail_com",
      "author_name": "גיא אביב",
      "author_role": "student",
      "visibility": "public",
      "is_anonymous": true,
      "scope_tag": "Lesson 1",
      "created_at": 1732400300,
      "replies": {}
    }
  },
  "polls": {
    "poll_1732400400_c3d4": {
      "poll_id": "poll_1732400400_c3d4",
      "question": "How clear was the Haversine GPS Geofencing pivot?",
      "options": ["Very clear", "Need one more example"],
      "lesson_tag": "Lesson 1",
      "active": true,
      "created_by": "Prof. Boaz",
      "created_at": 1732400400,
      "votes": {
        "stu_guygg197_gmail_com": { "option_index": 0, "voted_at": 1732400420 }
      }
    }
  },
  "settings": {
    "gemini": {
      "api_key": "<lecturer_gemini_api_key>",
      "model_id": "gemini-3.5-flash-lite",
      "updated_at": 1732400500
    }
  },
  "video_summaries": {
    "vid_1732400600_e5f6": {
      "summary_id": "vid_1732400600_e5f6",
      "filename": "lecture_01.mp4",
      "lesson_tag": "Lesson 1",
      "executive_summary": "...",
      "key_topics": ["..."],
      "core_takeaways": ["..."],
      "action_items": ["..."],
      "delivery_insights": {
        "pacing": "...",
        "clarity": "...",
        "audio_quality": "...",
        "overall_rating": "Excellent"
      }
    }
  },
  "homework_assignments": {
    "hw_1732400700_7a8b": {
      "assignment_id": "hw_1732400700_7a8b",
      "title": "Ex2 Final Architecture PDF",
      "deadline_ts": 1732500000,
      "ended_manually": false,
      "submissions": {}
    }
  }
}
```

---

## 5. External & REST API Interfaces

| Method & Path | Auth Guard | Purpose |
| :--- | :--- | :--- |
| `GET /api/health` | Public | Health check & Firebase mode (`mock` vs `live`). |
| `GET /api/network-info` | Public | Resolved client IP via `CF-Connecting-IP` -> `X-Forwarded-For` -> `client.host`. |
| `POST /api/auth/lecturer/register` | `LECTURER_ACCESS_CODE` | Register lecturer & set `ta_lecturer_session` cookie. |
| `POST /api/auth/lecturer/login` | Public | Authenticate lecturer & set `ta_lecturer_session` cookie. |
| `POST /api/auth/student/login` | Public | Authenticate student against `/students` roster. |
| `POST /api/auth/logout` | Public | Clear session cookies. |
| `GET /api/auth/me` | Authenticated | Return active JWT session claims. |
| `POST /api/roster/import` | Lecturer | Import `.xlsx` class roster (or `use_default_asset=true`). |
| `GET /api/roster/students` | Lecturer | List/filter full student roster. |
| `POST /api/attendance/sessions` | Lecturer | Start attendance session with GPS coordinates, radius, and QR rotation. |
| `GET /api/attendance/sessions` | Lecturer | List all attendance sessions. |
| `GET /api/attendance/sessions/{lesson_id}` | Lecturer | Get per-student attendance matrix & summary counts. |
| `POST /api/attendance/sessions/{lesson_id}/close` | Lecturer | Close active attendance session. |
| `PATCH /api/attendance/sessions/{lesson_id}/geofence-toggle` | Lecturer | Toggle `enforce_geofence` or update radius/coordinates live. |
| `GET /api/attendance/sessions/{lesson_id}/qr` | Lecturer | Generate rotating signed QR token & Base64 PNG image. |
| `GET /api/attendance/checkin-context` | Valid QR Token | Return lesson title, geofence info, and roster names (no emails). |
| `POST /api/attendance/checkin` | Valid QR Token | Validate QR token, Haversine GPS geofence, and 15-min device cooldown. |
| `POST /api/attendance/sessions/{lesson_id}/manual` | Lecturer | Manually mark student `present` or `absent`. |
| `GET /api/attendance/sessions/{lesson_id}/export` | Lecturer | Download `.csv` (UTF-8-BOM) or `.xlsx` attendance spreadsheet. |
| `POST /api/attendance/headcount` | Lecturer | Run local YOLOv8n person detection on uploaded photo or `/assets/p3.jpeg`. |
| `POST /api/polls` / `GET /api/polls` | Lecturer / Public | Create multiple-choice poll / list polls with live percentages. |
| `PATCH /api/polls/{poll_id}/status` | Lecturer | Open or close poll voting. |
| `POST /api/polls/{poll_id}/vote` | Student | Cast single vote per student on an active poll. |
| `POST /api/comments` / `GET /api/comments` | Authenticated / Public | Post comment / list visible comments filtered by `scope_tag`. |
| `POST /api/comments/{comment_id}/replies` | Authenticated / Public | Append threaded reply (enforcing private comment isolation). |
| `GET /api/settings/gemini` / `PUT /api/settings/gemini` | Lecturer | View masked / update Lecturer Gemini API Key and Model ID. |
| `POST /api/video/summarize` / `GET /api/video/summaries` | Lecturer / Public | Upload `.mp4` lecture video for Gemini summary / list summaries. |
| `POST /api/homework/assignments` / `GET /api/homework/assignments` | Lecturer / Public | Create homework assignment with deadline / list assignments. |
| `POST /api/homework/assignments/{id}/end-now` | Lecturer | Immediately close submissions and unlock AI reviews & peer comparisons. |
| `POST /api/homework/assignments/{id}/submit` | Student | Upload `.pdf`-only homework submission (gated before release). |
| `GET /api/homework/assignments/{id}/review` | Student / Lecturer | Retrieve Review Card + Peer Comparison Column (or First-Submission notice). |
