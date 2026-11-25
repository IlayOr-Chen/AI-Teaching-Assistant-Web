# Software Requirements Specification (SRS) (To-Do #5)

> **Project:** Web-Based AI Teaching Assistant (Ex2: Vibe Coding & Deep Designing)  
> **Team:** Ilay (`IlayOr-Chen`) & Roie (`RoieBenyamin`)

---

## 1. System Overview & Actors

The Web-Based AI Teaching Assistant is a modular, single-server Python **FastAPI** web application backed by **Firebase Realtime Database (RTDB)**, local **Ultralytics YOLOv8n**, interactive **Leaflet.js + OpenStreetMap** geofencing, **`youtube-transcript-api`**, and the official **`google-genai`** SDK, serving a responsive mobile-first Vanilla HTML5/CSS3/ES-Module frontend.

### Primary Actors
* **Lecturer:** Authenticates via the fixed `.env`-configured demo account (`adminL@gmail.com` / `12345`, case-insensitive; public registration disabled); syncs/manages the Excel class roster; opens/switches/closes multi-lesson attendance sessions with interactive Leaflet GPS geofencing and static session QR codes; runs/resets local YOLO headcounts; manages live polls and comments; configures/clears their Gemini API key & model; summarizes YouTube lecture videos; and creates/unlocks gated homework assignments.
* **Student:** Scans the classroom QR code on a smartphone to check in via Browser Geolocation; signs in frictionlessly using their roster email/name (no ID#); views **My Attendance History**; votes/switches/unvotes in live polls; posts public/private/anonymous comments; configures their own browser-stored BYOK Gemini key (`localStorage`); and submits multi-format homework files or Public GitHub Repo URLs to receive gated AI reviews and rich Peer Comparisons.

---

## 2. Functional Requirements

### 2.1 Authentication & Role Separation (`AuthService`)
* **FR-AUTH-01 (Restricted Lecturer Login):** Public lecturer self-registration (`POST /api/auth/lecturer/register`) shall be disabled (`403 Forbidden`). Allowed lecturer credentials (`LECTURER_EMAIL`, `LECTURER_PASSWORD`, defaulting to `adminL@gmail.com` / `12345`) are seeded automatically on startup and matched case-insensitively on email.
* **FR-AUTH-02 (Password Hashing):** Lecturer passwords shall be hashed with **PBKDF2-HMAC-SHA256** (`200,000` iterations, 16-byte cryptographic salt: `pbkdf2_sha256$200000$<salt>$<hex>`).
* **FR-AUTH-03 (JWT Session Cookies):** Upon Lecturer or Student login, the system shall issue a signed JWT (`HS256`) stored in an HTTP-only cookie (`ta_lecturer_session` or `ta_student_session`) and accepted via `Authorization: Bearer <token>`.
* **FR-AUTH-04 (Frictionless Student Auth — No ID#):** Students authenticate by matching their case-insensitive `email`, `full_name`, or `student_id` against `/students` in Firebase RTDB without any government/university ID number.

### 2.2 Class Roster Import & Attendance Export (`RosterImporter` & `AttendanceManager`)
* **FR-ROSTER-01 (Hebrew & English Excel Parsing):** Parse `.xlsx` files containing Hebrew headers (`שם פרטי`, `שם משפחה`, `דוא"ל`, `קבוצות`) or English equivalents, auto-seeding `assets/courseid_130158_participants.xlsx` on startup if `/students` is empty.
* **FR-ROSTER-02 (Empty/NaN Group Handling):** Empty, `None`, `float('nan')`, or `"NaN"` cells normalize cleanly to `""` (`Unassigned`) without crashing; whole-number floats (`3.0`) normalize to `"3"`.
* **FR-ROSTER-03 (Manual Attendance Override):** Lecturer can toggle any student's lesson status between `"present"` and `"absent"` (`method="manual"`).
* **FR-ROSTER-04 (Spreadsheet Export):** Lecturer can download any lesson's attendance matrix as UTF-8-BOM (`.csv`) or Excel (`.xlsx`), preserving Hebrew typography in Excel.

### 2.3 Feature 1 — Smart Multi-Layer Attendance (`AttendanceManager` & `PhotoHeadCounter`)
* **FR-ATT-01 (Multi-Lesson & Static Session QR):** Generate one clean 16-char URL-safe static session token (`secrets.token_urlsafe(12)`) per active lesson, remaining valid while `active=True` and invalidated immediately on session close. Lecturers can switch between existing lessons or open new lessons.
* **FR-ATT-02 (QR-Gated Student Check-In Context):** `GET /api/attendance/checkin-context` returns lesson info and the searchable student dropdown (`student_id`, `full_name`, `email`) only when presented with the valid token for an active session.
* **FR-ATT-03 (Interactive Leaflet/OSM GPS Geofencing):** Lecturer sets the classroom pin and radius (`30m`, `50m`, or `100m`) on a Leaflet.js + OSM map. When `enforce_geofence=True`, check-ins compute Haversine distance (`calculate_distance_meters`) and succeed only if `distance <= geofence_radius_meters`.
* **FR-ATT-04 (Live Geofence Toggle & Missing-GPS Handling):** Missing GPS coordinates while `enforce_geofence=True` return HTTP `403` with clear HTTPS Geolocation instructions; Lecturer can toggle `enforce_geofence` OFF live (`PATCH /geofence-toggle`).
* **FR-ATT-05 (15-Minute Device Cooldown & Instant Reset):** Check-ins issue an HMAC-SHA256 signed `ta_device_id` cookie and record `expires_at` (`+900s`) in `/device_cooldowns/{device_id}`, blocking repeat check-ins for 15 minutes with a live `MMm SSs` timer. Lecturer can reset all cooldowns immediately via `POST /api/attendance/reset-cooldowns` (`/settings/cooldown_reset_at`).
* **FR-ATT-06 (Local YOLOv8n Headcount & Persistence):** `PhotoHeadCounter` runs local `yolov8n.pt` (`imgsz=1280, conf=0.30, classes=[0]`) on uploaded classroom photos, persists the annotated JPEG Data URL per lesson under `/attendance_sessions/{lesson_id}/yolo_headcount`, and clears it via `DELETE /api/attendance/headcount` (**Reset YOLO**).
* **FR-ATT-07 (Color-Coded Discrepancy Indicator):**
  * **GREEN:** `average_confidence >= 0.60`, low-conf ratio `<= 0.25`, and `abs(detected - registered) <= 1`.
  * **ORANGE:** `2 <= abs_discrepancy <= 3`, `0.45 <= average_confidence < 0.60`, or low-conf ratio `> 0.25`.
  * **RED:** `abs_discrepancy >= 4`, `average_confidence < 0.45`, or `detected_count == 0` when students are registered.
* **FR-ATT-08 (Student Attendance History):** `GET /api/attendance/my-history` returns the authenticated student's chronological per-lesson `Present` / `Absent` status, method, and overall attendance rate.

### 2.4 Feature 2 — Comments & Suggestions (`CommentService`)
* **FR-CMT-01 (Visibility Isolation):** `private` (`Lecturer Only`) comments are returned only to the Lecturer and authoring student; peers receive HTTP `403` if attempting to reply.
* **FR-CMT-02 (Anonymous Identity Masking):** `is_anonymous=True` comments/replies serialize `display_name = "Anonymous Student"` and strip raw author identifiers from API responses.
* **FR-CMT-03 (Scope Tagging & Threaded Replies):** Supports `General` and `Lesson 1..N` scope tags, filtering, and chronological threaded replies.

### 2.5 Feature 3 — On-the-Spot KISS Live Polls (`PollService`)
* **FR-POLL-01 (Poll Lifecycle):** Lecturer creates polls with `2..8` distinct options and toggles voting open/closed (`active`).
* **FR-POLL-02 (Mutable Voting & Live Percentages):** Students can cast a vote, click another option to switch, or click their selected option again to unvote (`vote_action`: `cast` / `switched` / `removed`); closed polls reject votes (`403`).

### 2.6 Feature 4 — AI Lecture Video Summarizer (`GeminiClient` & `VideoSummarizer`)
* **FR-VID-01 (Lecturer BYOK Settings & Clear Key):** Lecturer configures their Gemini API key (`AIza...` or `AQ....`, masked in GET) and free-text Model ID (default `gemini-3.5-flash-lite`, presets `gemini-3.8-flash`, `gemini-3.5-flash-lite`, `gemini-3.5-flash`) under `/settings/gemini`. Clicking **Clear Key** (`DELETE /api/settings/gemini`) sets `key_cleared=True`, clears the stored key, and immediately returns `has_key=False` / `masked_key=None` without falling back to `.env`.
* **FR-VID-02 (YouTube-Only 2-Step Strategy, oEmbed Title & Rich Summary):** Canonicalizes public YouTube URLs (stripping `&list=`/`&index=`), fetches the real UTF-8 video title via YouTube oEmbed (`video_title` + clickable `source_url` footer), and runs an automatic 2-step pipeline: **(1)** `youtube-transcript-api` (`he`, `iw`, `en`) -> Gemini text analysis, or **(2)** direct YouTube URI with `VideoMetadata(fps=0.1)` (with plain `Part.from_uri` fallback on HTTP 400). Produces **Executive Summary (TL;DR)**, **Detailed Narrative Walkthrough** (`detailed_overview`), **Key Concepts & Definitions** (`key_concepts`), **Key Topics**, **Core Takeaways**, **Quick Review Questions** (`study_questions`), **Action Items**, and **Lecturer Delivery Insights**, with **Copy** (Markdown) and **Delete** (`DELETE /api/video/summaries/{summary_id}`).

### 2.7 Feature 5 — Deep Homework Review & Rich Peer Comparison (`HomeworkReviewer`)
* **FR-HW-01 (Per-Student BYOK + Multi-Format & GitHub Repo Ingestion):** Student submissions require browser-stored `X-Gemini-Api-Key` / `X-Gemini-Model` headers (`400` if missing; never falls back to Lecturer or `.env` keys) and accept `.pdf`, `.docx`, `.txt`, `.md`, `.zip`, source code files (`.py`, `.java`, `.c`, `.cpp`, `.h`, `.js`, `.ts`, `.html`, `.css`, `.json`), Hebrew/sparse PDFs, and public GitHub repo URLs (`https://github.com/owner/repo`).
* **FR-HW-02 (Clear Inline Loading UX):** During submission and review loading, the Student UI displays an animated progress card (*"Submitting & generating AI review with Gemini… This usually takes 5–15 seconds."*) and disables upload/submit buttons to prevent double-submissions.
* **FR-HW-03 (Gated Release Rule & Manual Unlock):** While `is_review_released=False` (before `deadline_ts` and before **"End Submission Now"**), `review` and `peer_comparison` remain strictly `None`. Once unlocked, late submissions are blocked (`403`).
* **FR-HW-04 (Rich Peer Comparison & First-Submission Edge Case):** Unlocked reviews display **(1) Concise Review & Summary Card** and **(2) Dedicated Peer Comparison Column** (`peer_standing` tier + explanation, `missing_compared_to_peers`, `strengths_vs_peers`, `actionable_improvements`, and `unique_ideas`). When `total_submissions == 1`, returns `is_first_submission_only=True` with the exact First-Submission notice (never fabricating peer data).
* **FR-HW-05 (Automatic Gemini `503`/`429`/`404` Model Fallback):** `GeminiClient` automatically retries and falls back across `[selected_model, "gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]`, returning HTTP `503` or `429` with clear human-readable JSON `detail` if all models are unavailable.

---

## 3. Non-Functional Requirements

* **NFR-UX-01 (Mobile-First & Hebrew RTL Support):** Responsive dark glassmorphic UI using `dir="auto"` on Hebrew student names, video titles, and feedback; UTF-8 BOM CSV exports for Microsoft Excel.
* **NFR-SEC-01 (XSS Protection & BYOK Privacy):** All user-supplied text mounts via safe DOM text nodes (`textContent` / `el()`), never `innerHTML`; student Gemini keys remain strictly in browser `localStorage`; `.env` and `serviceAccountKey.json` are excluded via `.gitignore`.
* **NFR-PERF-01 (Non-Blocking Workers & Gateway Safety):** Blocking Gemini, GitHub zipball, and YOLO calls execute via `run_in_threadpool` with a `75s` HTTP client timeout (under Cloudflare's `100s` gateway limit); `api.js` detects raw proxy HTML error pages while preserving real FastAPI JSON `detail` strings on `400`/`429`/`502`/`503`.
* **NFR-TEST-01 (Deterministic Isolation):** All `52` `pytest` tests run cleanly in `./venv` against `InMemoryFirebaseAdapter` (`use_mock=True`) with zero live Firebase side effects.

---

## 4. Firebase Realtime Database (RTDB) Schema

Target RTDB URL: `https://ai-teaching-assistant-web-default-rtdb.firebaseio.com/`

```json
{
  "users": {
    "lecturers": {
      "lec_adminl_gmail_com": {
        "lecturer_id": "lec_adminl_gmail_com",
        "full_name": "Course Lecturer",
        "email": "adminl@gmail.com",
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
      "session_qr_token": "Ab3Cd5Ef7Gh9Jk12",
      "classroom_lat": 32.1044,
      "classroom_lng": 35.2078,
      "geofence_radius_meters": 50,
      "enforce_geofence": true,
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
        "annotated_image_data_url": "data:image/jpeg;base64,..."
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
  "settings": {
    "cooldown_reset_at": 1732400250,
    "gemini": {
      "api_key": "",
      "key_cleared": true,
      "model_id": "gemini-3.5-flash-lite",
      "updated_at": 1732400500
    }
  },
  "comments": { "cmt_...": { "visibility": "public|private", "is_anonymous": true, "scope_tag": "Lesson 1", "replies": {} } },
  "polls": { "poll_...": { "question": "...", "options": ["A", "B"], "active": true, "votes": {} } },
  "video_summaries": {
    "vid_1732400600_e5f6": {
      "summary_id": "vid_1732400600_e5f6",
      "video_title": "אלגוריתמים 1 - תרגול 5",
      "source_url": "https://www.youtube.com/watch?v=8UY3LQI529o",
      "analysis_method": "youtube_transcript",
      "lesson_tag": "Lesson 5",
      "executive_summary": "...",
      "detailed_overview": "...",
      "key_concepts": [{ "term": "BFS", "explanation": "..." }],
      "key_topics": ["..."],
      "core_takeaways": ["..."],
      "study_questions": ["..."],
      "action_items": ["..."],
      "delivery_insights": { "pacing": "...", "clarity": "...", "audio_quality": "...", "overall_rating": "Excellent" }
    }
  },
  "homework_assignments": {
    "hw_1732400700_7a8b": {
      "assignment_id": "hw_1732400700_7a8b",
      "title": "Ex2 Final Architecture Report",
      "deadline_ts": 1732500000,
      "ended_manually": false,
      "submissions": {}
    }
  }
}
```

---

## 5. REST API Interface Summary (`src/main.py`)

| Method & Endpoint | Auth Guard | Purpose |
| :--- | :--- | :--- |
| `GET /api/health` | Public | Health check & Firebase mode (`mock` vs `live`). |
| `GET /api/network-info` | Public | Resolved client IP via `CF-Connecting-IP` -> `X-Forwarded-For` -> `client.host`. |
| `POST /api/auth/lecturer/register` | Disabled (`403`) | Public lecturer self-registration disabled. |
| `POST /api/auth/lecturer/login` | Public | Authenticate configured lecturer (`adminL@gmail.com` / `12345`) & set JWT cookie. |
| `POST /api/auth/student/login` | Public | Frictionless student login against `/students` roster. |
| `POST /api/auth/logout` / `GET /api/auth/me` | Public / Auth | Clear cookies / inspect active JWT claims. |
| `POST /api/roster/import` / `GET /api/roster/students` | Lecturer | Import Excel roster (or default asset) / search & filter roster. |
| `POST /api/attendance/sessions` / `GET /api/attendance/sessions` | Lecturer | Start or reopen a lesson session / list all sessions. |
| `GET /api/attendance/sessions/{lesson_id}` | Lecturer | Per-lesson attendance matrix, summary stats, & persisted YOLO headcount. |
| `POST /api/attendance/sessions/{lesson_id}/close` | Lecturer | Close active session and invalidate its static QR token. |
| `PATCH /api/attendance/sessions/{lesson_id}/geofence-toggle` | Lecturer | Toggle `enforce_geofence` or update radius/coords live. |
| `GET /api/attendance/sessions/{lesson_id}/qr` | Lecturer | Return static session QR token, public HTTPS check-in URL, & Base64 PNG QR. |
| `POST /api/attendance/reset-cooldowns` | Lecturer | Immediately clear all 15-minute device cooldowns (`/settings/cooldown_reset_at`). |
| `GET /api/attendance/checkin-context` / `POST /api/attendance/checkin` | Valid QR Token | Return QR-gated roster dropdown / submit GPS-geofenced check-in. |
| `GET /api/attendance/my-history` | Student / Lecturer | Chronological per-lesson attendance history & overall rate for a student. |
| `POST /api/attendance/sessions/{lesson_id}/manual` | Lecturer | Manually toggle student `present` / `absent`. |
| `GET /api/attendance/sessions/{lesson_id}/export` | Lecturer | Download UTF-8-BOM `.csv` or `.xlsx` attendance spreadsheet. |
| `POST /api/attendance/headcount` / `DELETE /api/attendance/headcount` | Lecturer | Run local YOLOv8n headcount on uploaded photo / reset stored YOLO photo & count. |
| `POST /api/polls` / `GET /api/polls` / `PATCH /api/polls/{id}/status` / `POST /api/polls/{id}/vote` | Lecturer / Student | Create poll, list with live percentages, open/close voting, cast/switch/unvote. |
| `POST /api/comments` / `GET /api/comments` / `POST /api/comments/{id}/replies` | Auth / Public | Create public/private/anonymous comment, filter by scope, append threaded reply. |
| `GET /api/settings/gemini` / `PUT` / `POST` / `DELETE /api/settings/gemini` | Lecturer | View masked settings, update key/model, or **Clear Key** (`has_key=False`). |
| `POST /api/video/summarize-youtube` / `GET /api/video/summaries` / `DELETE /api/video/summaries/{id}` | Lecturer / Public | Summarize public YouTube URL (2-step transcript/fps=0.1 + oEmbed title), list, delete. |
| `POST /api/homework/assignments` / `GET /api/homework/assignments` / `POST .../end-now` | Lecturer / Public | Create gated assignment, list assignments, or immediately end submissions & unlock reviews. |
| `POST /api/homework/assignments/{id}/submit` / `POST .../submit-repo` / `GET .../review` | Student (BYOK) / Lecturer | Upload multi-format file or public GitHub URL, and retrieve gated Review Card + rich Peer Comparison. |
