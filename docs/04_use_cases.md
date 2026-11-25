# Main Use Cases & Client Stories (Happy Cases & Edge Cases — To-Do #6)

> **Project:** Web-Based AI Teaching Assistant (Ex2: Vibe Coding & Deep Designing)  
> **Team:** Ilay (`IlayOr-Chen`) & Roie (`RoieBenyamin`)

---

## Overview

Every feature in the Web-Based AI Teaching Assistant is specified and tested across both **Happy Cases** (normal live lecture operation) and **Edge Cases** (sensor/permission denials, buddy-punching, back-row occlusion, missing captions, Gemini high-demand spikes, and single-submission homework cohorts).

---

## UC-01: Restricted Lecturer Sign-In & Hebrew Course Roster Management

* **Primary Actor:** Lecturer
* **Preconditions:** Server is running with `.env`-configured Lecturer credentials (`adminL@gmail.com` / `12345`).

### Happy Case
1. Lecturer navigates to `/#/lecturer` and signs in with `adminL@gmail.com` (or `adminl@gmail.com`) and password `12345`.
2. On startup (or when clicking **"Sync from Course Excel"** on the **Overview** tab), `RosterImporter` parses `assets/courseid_130158_participants.xlsx` (`שם פרטי`, `שם משפחה`, `דוא"ל`, `קבוצות`), normalizes float group values (`3.0` -> `"3"`), and persists all `104` students under `/students` without requiring student ID numbers.

### Edge Cases
* **EC-01.1 (Empty / `NaN` Group Cells):** Rows 21 (`בועז בן משה`) and 75 (`נדב סודרי`) have empty `קבוצות` cells. `RosterImporter.normalize_group()` converts them safely to `""` (`Unassigned`) with zero crashes.
* **EC-01.2 (Public Self-Registration Blocked):** Any attempt to `POST /api/auth/lecturer/register` returns HTTP `403 Forbidden`, and the UI exposes only Sign In.
* **EC-01.3 (Role Protection):** Anonymous callers (`401`) and logged-in Students (`403`) are blocked from lecturer roster, export, and session-control endpoints.

---

## UC-02: Smart Multi-Lesson QR Attendance with Leaflet GPS Geofencing & 15-Min Cooldown

* **Primary Actors:** Lecturer (projector screen) & Student (smartphone browser)
* **Preconditions:** Roster is seeded; Lecturer opens the **Attendance** tab.

### Happy Case
1. Lecturer selects an existing lesson or creates a new lesson, places the classroom pin on the interactive **Leaflet.js + OpenStreetMap** map ("Use My Current Location", click/drag, or Ariel preset), picks a radius pill (`30m` / `50m` / `100m`), and clicks **"Start attendance session"**.
2. A clean, centered **static session QR code** (short 16-char token) appears; Lecturer can toggle full-screen **Projector mode**.
3. Student scans the QR code on their phone, opening `/?lesson_id=lesson_1&qr_token=<token>`.
4. Mobile browser grants Geolocation (`navigator.geolocation`), loads the QR-gated roster dropdown (Hebrew name + email), Student selects their name and taps **"Check me in"**.
5. `AttendanceManager.submit_qr_checkin` verifies the live token, confirms no active cooldown, verifies Haversine distance (`<= radius`), records `status="present"`, sets the signed `ta_device_id` cookie + `/device_cooldowns` for 15 minutes, and updates Student **My Attendance History**.

### Edge Cases
* **EC-02.1 (Outside Classroom Geofence):** Student checks in from `351m` away (`> 100m`). Server rejects with HTTP `403` and exact meter distance.
* **EC-02.2 (Browser Geolocation Denied / Indoor Drift):** Missing GPS while `enforce_geofence=True` returns HTTP `403` with HTTPS instructions. Lecturer can flip **"Enforce GPS Geofencing" OFF** live (`PATCH /geofence-toggle`) so check-ins proceed with `geofence_status="bypassed"`.
* **EC-02.3 (Buddy-Punching Within 15 Minutes):** Second check-in attempt from the same phone is blocked (`403`) with a live `MM:SS` cooldown countdown.
* **EC-02.4 (Lecturer Cooldown Reset):** Clicking **"Reset 15-Min Device Cooldowns"** (`POST /api/attendance/reset-cooldowns`) clears `/device_cooldowns` and updates `/settings/cooldown_reset_at`, immediately unblocking phones holding an active local cookie.
* **EC-02.5 (Closed Session QR):** Scanning a QR link after the Lecturer closes the session is rejected (`400`: *"This attendance session is not open."*).

---

## UC-03: Local YOLO Classroom Headcount, Persistence & Roster Export

* **Primary Actor:** Lecturer
* **Preconditions:** Attendance session exists; Lecturer opens the **Roster** tab and chooses a lesson in the **Lesson Scope** card.

### Happy Case
1. Lecturer clicks **"Upload classroom photo"** and selects a classroom photograph.
2. `PhotoHeadCounter` executes local Ultralytics YOLOv8n (`imgsz=1280, conf=0.30, classes=[0]`), annotates detected persons with confidence boxes, persists the annotated JPEG Data URL in Firebase RTDB for that lesson, and displays **YOLO Detected Count** vs. **Self-Registered Count** with a **GREEN** badge when counts agree (`<= 1` diff, `avg_conf >= 0.60`).
3. Lecturer exports the lesson roster as UTF-8-BOM **`.csv`** or **`.xlsx`**.

### Edge Cases
* **EC-03.1 (Back-Row Occlusion / Discrepancy — ORANGE / RED):** Small discrepancy (`2..3`) or `>25%` low-conf back-row boxes flags **ORANGE**; large discrepancy (`>= 4`) or `avg_conf < 0.45` flags **RED**, prompting manual **Mark Present / Mark Absent** adjustments.
* **EC-03.2 (Reset YOLO & Persistence Across Refresh):** Switching tabs or refreshing restores the lesson's persisted annotated photo from RTDB; clicking **Reset YOLO** (`DELETE /api/attendance/headcount`) cleanly clears count and photo.

---

## UC-04: Threaded Comments & Suggestions (Public/Private, Named/Anonymous)

* **Primary Actors:** Student & Lecturer

### Happy Case
1. Student opens **Comments**, selects `Lesson 1`, `Public (Everyone)`, and `With Name`, and submits feedback.
2. Classmates and the Lecturer view the comment in real time and post threaded replies underneath it.

### Edge Cases
* **EC-04.1 (Anonymous Feedback):** Selecting `Anonymous` serializes `display_name = "Anonymous Student"` and strips raw author IDs at the API layer.
* **EC-04.2 (Private Lecturer-Only Isolation):** Comments marked `Private (Lecturer Only)` are invisible to other students; any peer attempt to reply returns HTTP `403`.

---

## UC-05: On-the-Spot KISS Live Polls (Mutable Voting)

* **Primary Actors:** Lecturer & Student

### Happy Case
1. Lecturer launches a multiple-choice poll (`2..8` options) on the **Polls** tab.
2. Students tap an option on their phone; animated percentage bars and vote counts update live across Student and Lecturer views.

### Edge Cases
* **EC-05.1 (Switch Vote or Cancel/Unvote):** Tapping a different option switches the student's vote (`vote_action="switched"`); tapping the currently selected option again removes their vote (`vote_action="removed"`).
* **EC-05.2 (Closed Poll):** Voting after the Lecturer clicks **"Close Voting"** returns HTTP `403`.

---

## UC-06: AI Lecture Video Summarizer (YouTube-Only KISS Pipeline)

* **Primary Actor:** Lecturer

### Happy Case
1. Lecturer enters their Gemini API key (`AIza...` or `AQ....`) in **Video AI** settings (keeping default `gemini-3.5-flash-lite` or selecting `gemini-3.8-flash` / `gemini-3.5-flash`), pastes a public YouTube lecture URL, and clicks **`Analyze YouTube Video`**.
2. `VideoSummarizer` strips playlist parameters (`&list=`, `&index=`), fetches the real Hebrew/English video title via YouTube oEmbed, extracts the spoken transcript via `youtube-transcript-api` (`he`, `iw`, `en`), and generates a rich card showing the real video title in the header, **Executive Summary (TL;DR)**, **Lecture Overview & Walkthrough**, **Key Concepts & Definitions**, **Key Topics**, **Core Learning Takeaways**, **Quick Review Questions**, **Action Items**, **Lecturer Delivery Insights**, **Copy** (Markdown), **Delete**, and a clickable **Watch on YouTube** link at the bottom.

### Edge Cases
* **EC-06.1 (No Captions / Transcript Unavailable):** Automatically falls back to passing the canonical YouTube URL directly to Gemini with `VideoMetadata(fps=0.1)` (and plain `Part.from_uri` fallback if `video_metadata` returns HTTP 400).
* **EC-06.2 (Clear Key):** Clicking **Clear Key** (`DELETE /api/settings/gemini`) wipes the stored key in RTDB, returns `has_key=False` without falling back to `.env`, and immediately flips the badge to `No API key configured`.
* **EC-06.3 (Gemini `503` / `429` / `404` Spike):** `GeminiClient` retries and falls back across `[selected_model, gemini-3.8-flash, gemini-3.5-flash, gemini-3.5-flash-lite]`. If all models are overloaded, returns HTTP `503`/`429` with a clear toast message.

---

## UC-07: Deep Homework Review & Rich Peer Comparison (BYOK + Gated Release)

* **Primary Actors:** Lecturer & Student

### Happy Case (Multi-Student Cohort)
1. Lecturer creates an assignment with a future deadline.
2. Two or more Students save their own Gemini API key in the **Your Gemini API Key & Model (BYOK)** browser `localStorage` panel and upload a file (`.pdf`, `.docx`, `.zip`, `.txt`, `.md`, source code) OR submit a **Public GitHub Repository URL**.
3. An unmistakable inline loading card (*"Submitting & generating AI review with Gemini… This usually takes 5–15 seconds."*) displays with disabled buttons while Gemini analyzes the submission.
4. Once the deadline passes OR the Lecturer clicks **"End Submission Now"**, students unlock:
   * **Column 1 — Concise Review & Summary Card:** Brief Summary, Strengths, Areas to Improve, Actionable Recommendations.
   * **Column 2 — Dedicated Peer Comparison Column:** **Standing vs. Peers** badge + explanation, **Missing or Underdeveloped Compared to Peers**, **Strengths vs. Class Peers**, **Actionable Improvements to Close Gaps**, and **Unique Ideas**.

### Edge Cases
* **EC-07.1 (Gated Release Rule):** Checking the review before the deadline / **"End Submission Now"** returns `review_released=False` (`review=None`, `peer_comparison=None`) with a lock banner.
* **EC-07.2 (First-Submission Edge Case):** When `total_submissions == 1`, Column 1 displays the student's review while Column 2 shows:
  > *"This is the first submission received for this assignment, so peer comparison is not yet available. Comparative insights will appear once additional students submit."*
* **EC-07.3 (Missing BYOK Key, Unsupported File, or Late Submission):** Missing `X-Gemini-Api-Key` returns HTTP `400`; unsupported binaries (`.exe`) or private/404 GitHub repos return HTTP `400`; submissions after close return HTTP `403`; Hebrew filenames and scanned/sparse PDFs never cause HTTP `500`.
