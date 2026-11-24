# Main Use Cases & Client Stories (Happy Cases & Edge Cases)

> **Project:** Web-Based AI Teaching Assistant (Ex2: Vibe Coding & Deep Designing)  
> **Team:** Ilay (`IlayOr-Chen`) & Roie (`RoieBenyamin`)

---

## Overview

Every feature in the Web-Based AI Teaching Assistant is designed around explicit **Happy Cases** (normal classroom operation) and **Edge Cases** (sensor/permission denials, network/NAT anomalies, buddy-punching attempts, back-row camera occlusion, and single-submission homework cohorts).

---

## UC-01: Class Excel Roster Import & Management

* **Primary Actor:** Lecturer
* **Preconditions:** Lecturer is registered and authenticated (`ta_lecturer_session` JWT cookie active).

### Happy Case (Main Success Scenario)
1. Lecturer navigates to the **Overview** tab and uploads `/assets/courseid_130158_participants.xlsx` (or clicks *"Use bundled course roster"*).
2. `RosterImporter` parses the Hebrew headers (`שם פרטי`, `שם משפחה`, `דוא"ל`, `קבוצות`), concatenates first and last names into `full_name` (e.g., `גיא אביב`), normalizes float group numbers (`3.0` -> `"3"`), and persists all `104` records under `/students` in Firebase RTDB.
3. The Lecturer Dashboard displays `104` imported students and enables searchable student check-in, manual attendance overrides, and CSV/Excel exports.

### Edge Cases & Exception Flows
* **EC-01.1 (Empty / `NaN` Group Cells in Excel):** In `/assets/courseid_130158_participants.xlsx`, rows 21 (`בועז בן משה`, `benmo@ariel.ac.il`) and 75 (`נדב סודרי`, `hpcity123@gmail.com`) have empty `קבוצות` cells (`None` / `NaN`). `RosterImporter.normalize_group()` converts these safely to `""` (`Unassigned`) without crashing, reporting `total_imported: 104, unassigned_group_count: 2`.
* **EC-01.2 (Invalid File Format or Missing Required Columns):** If the Lecturer uploads a non-`.xlsx` file or a workbook missing name/email headers, the backend rejects the request with HTTP `400 Bad Request` and displays an error toast without corrupting existing `/students` records.
* **EC-01.3 (Unauthorized Student Access to Full Roster):** If an anonymous user (`401`) or logged-in Student (`403`) requests `GET /api/roster/students` or `GET /api/attendance/sessions/{id}/export`, `AuthService.require_lecturer_claims` blocks the request so student email addresses are never exposed.

---

## UC-02: Smart QR Attendance Check-In with Haversine GPS Geofencing & 15-Minute Cooldown

* **Primary Actors:** Lecturer (projector screen) & Student (smartphone browser)
* **Preconditions:** Class roster is imported; Lecturer starts an active session (`lesson_1`) with classroom GPS coordinates (`32.1044, 35.2078`) and `geofence_radius_meters = 100`.

### Happy Case (Main Success Scenario)
1. Lecturer opens the **Live attendance** tab, positions the classroom pin and radius (`30m` / `50m` / `100m`) on the interactive **Leaflet.js + OpenStreetMap** widget, and starts `Lesson 1`. The projector displays a centered, clean static session QR code valid for the duration of the active session.
2. Student scans the QR code with their smartphone camera, opening `/?lesson_id=lesson_1&qr_token=<short_token>`.
3. The mobile page validates the session token via `GET /api/attendance/checkin-context`, requests Browser Geolocation (`navigator.geolocation.getCurrentPosition()`), and displays a searchable dropdown of student Hebrew names and emails.
4. Student selects their name and taps **"Check me in"**.
5. `AttendanceManager.submit_qr_checkin` verifies the active session token, verifies no active 15-minute cooldown exists on the device, calculates the Haversine distance (`24.95m <= 100m`), records `status="present"`, `geofence_status="verified"`, and locks the device in `/device_cooldowns/{device_id}` + signed `ta_device_id` cookie for `15` minutes.

### Edge Cases & Exception Flows
* **EC-02.1 (Lecturer Resets 15-Minute Cooldowns Live):** During testing or a shared-device classroom edge case, the Lecturer clicks **"Reset 15-Min Device Cooldowns"** (`POST /api/attendance/reset-cooldowns`), clearing `/device_cooldowns` and setting `/settings/cooldown_reset_at` so even a phone still holding a local `ta_device_id` cookie is immediately unblocked.
* **EC-02.2 (Closed Session / Old QR Link):** A student attempts to use a QR link after the Lecturer closes the session. `verify_qr_token` rejects the closed session (`400 Bad Request`), and the mobile UI displays *"This attendance session is closed."*
* **EC-02.3 (Student Outside Classroom Geofence Radius):** A student attempts to check in from a dorm or café `351m` away (`> 100m`). `calculate_distance_meters` computes `351m`, blocks the check-in (`403 Forbidden`), and displays: *"GPS Geofence check failed: You appear to be 351m away from the classroom (allowed: 100m)."*
* **EC-02.4 (Student Denies Browser Geolocation Permission / Indoor GPS Failure):** If a student denies location permission (`student_lat=None`), the server blocks check-in (`403`) with instructions on enabling Browser Geolocation over HTTPS (Cloudflare Tunnel / Render). If indoor GPS fails across the hall, the Lecturer toggles **"Enforce GPS Geofencing" OFF** live (`PATCH /geofence-toggle`), allowing check-ins with `geofence_status="bypassed"`.
* **EC-02.5 (Buddy-Punching Attempt Within 15 Minutes):** After checking in, a student immediately tries to check in their absent friend from the same phone. `check_device_cooldown` inspects the HMAC-signed `ta_device_id` cookie and `/device_cooldowns/{device_id}`, blocks the request (`403`), and renders a live countdown timer (`12m 00s remaining`).

---

## UC-03: Automatic Classroom Student Headcount via Local YOLO (`PhotoHeadCounter`)

* **Primary Actor:** Lecturer
* **Preconditions:** An attendance session is active or completed; Lecturer captures/uploads a classroom photo (tested in real life on `/assets/p3.jpeg`).

### Happy Case (Main Success Scenario — GREEN Status)
1. Lecturer opens the **Roster & YOLO Headcount** tab and uploads `/assets/p3.jpeg` (or clicks *"Test on Classroom Photo (p3.jpeg)"*) when `22` students have checked in via QR.
2. `PhotoHeadCounter` runs local Ultralytics YOLOv8n (`imgsz=1280, conf=0.30, classes=[0]`), detecting `22` physical persons with `average_confidence = 0.634` (`15` high-confidence boxes `>= 0.60`).
3. Because `abs(22 - 22) == 0 <= 1` and `average_confidence >= 0.60`, the dashboard displays a **GREEN** confidence indicator (*"High Confidence & Reliable Headcount"*), side-by-side counts (`22` detected vs. `22` registered), and the annotated bounding-box image.

### Edge Cases & Exception Flows
* **EC-03.1 (Back-Row Occlusion or Small Discrepancy — ORANGE Status):** If `20` students registered via QR while YOLO detects `22` persons (`abs_discrepancy == 2`), or if `> 25%` of bounding boxes have low confidence (`< 0.45`) due to heavy back-row desk/laptop occlusion, `PhotoHeadCounter` returns **ORANGE** (*"Moderate Count Discrepancy / Back-Row Occlusion Warning"*).
* **EC-03.2 (Large Attendance Mismatch or Poor Photo — RED Status):** If `22` persons are detected physically (or `15` registered vs. `22` detected, `abs_discrepancy = 7 >= 4`), or if `average_confidence < 0.45` / `0` persons are detected, `PhotoHeadCounter` returns **RED** (*"Large Attendance Discrepancy — Manual Review Required"*). The Lecturer then uses the **Mark Present / Mark Absent** manual toggle buttons in the Roster table to reconcile attendance.

---

## UC-04: Threaded Comments & Suggestions (Public/Private, Named/Anonymous, Lesson/General)

* **Primary Actors:** Student & Lecturer

### Happy Case (Main Success Scenario)
1. Student opens the **Comments & Suggestions** tab, writes feedback tagged `Lesson 1`, selects `Public (Everyone)` and `With Name`, and submits.
2. The comment appears in real time for all students and the Lecturer; classmates and the Lecturer post threaded replies underneath it.

### Edge Cases & Exception Flows
* **EC-04.1 (Anonymous Student Feedback):** Student selects `Anonymous`. `CommentService` stores the comment with `is_anonymous=True` and serializes `display_name = "Anonymous Student"`, ensuring no other student or API response exposes the student's name or ID.
* **EC-04.2 (Private Lecturer-Only Comment Isolation):** Student selects `Private (Lecturer Only)` to report a personal/medical issue. Other students querying `GET /api/comments` never receive the private comment, and any attempt by another student to POST a reply to that `comment_id` is blocked with HTTP `403 Forbidden`. Only the Lecturer and the authoring student can view and reply to the private thread.

---

## UC-05: On-the-Spot KISS Polls

* **Primary Actors:** Lecturer & Student

### Happy Case (Main Success Scenario)
1. Lecturer opens the **Live Polls** tab, enters a multiple-choice question with 2–4 options, and clicks **"Launch Live Poll"**.
2. Students tap their chosen option on their phones; animated percentage bars update live across both Student and Lecturer screens.

### Edge Cases & Exception Flows
* **EC-05.1 (Duplicate Vote Attempt):** A student attempts to vote a second time on the same poll. `PollService.submit_vote` detects `voter_id` in `/polls/{poll_id}/votes` and rejects the request with HTTP `403` (*"You have already voted in this poll."*).
* **EC-05.2 (Late Vote After Lecturer Closes Poll):** Once the Lecturer clicks **"Close Voting"**, any student vote submission is rejected with HTTP `403` (*"Voting is closed for this poll."*).

---

## UC-06: ML Lecture Video Summary (MP4 Only + Configurable Gemini Model)

* **Primary Actor:** Lecturer

### Happy Case (Main Success Scenario)
1. Lecturer opens the **MP4 Lecture Summary** tab, optionally enters/updates their Gemini API Key and Model ID (`gemini-2.5-flash-lite` or custom `gemini-3.5-flash-lite`), and selects an `.mp4` lecture video.
2. A live processing indicator appears while `VideoSummarizer` and `GeminiClient` analyze the recording, producing a structured card with **Executive Summary**, **Key Topics**, **Core Takeaways**, **Action Items**, and **Lecturer Delivery Insights** (`Pacing`, `Clarity`, `Audio Quality`, `Overall Rating`) — directly improving on the verbose paragraphs in `/assets/Example_ Class  by zoom).docx`.

### Edge Cases & Exception Flows
* **EC-06.1 (Unsupported Video Format Uploaded):** Lecturer attempts to upload `.avi`, `.mov`, `.mkv`, or `.docx`. `VideoSummarizer.validate_mp4_upload` rejects the file immediately (`400 Bad Request`: *"Unsupported video format. Only MP4 (.mp4) lecture video files are accepted."*).
* **EC-06.2 (Missing External Gemini API Key / Offline Demo Fallback):** If no external Gemini key is configured or internet quota is unavailable during a live demo, `GeminiClient` seamlessly falls back to its deterministic structured pedagogical analyzer so the classroom demonstration never crashes.

---

## UC-07: Deep Homework Review & Peer Comparison (PDF Only + Gated Release)

* **Primary Actors:** Lecturer & Student

### Happy Case (Main Success Scenario — Multi-Student Cohort)
1. Lecturer creates a homework assignment with a future submission deadline.
2. Multiple students upload their `.pdf` reports (`pypdf` extracts text, page count, and word count).
3. Once the deadline expires OR the Lecturer clicks **"End Submission Now"**, students open the **PDF Homework Review** tab and view:
   * **Column 1 (Concise Review & Summary Card):** Brief Summary, Strengths, Areas to Improve, and Actionable Recommendations.
   * **Column 2 (Dedicated Peer Comparison Column):** Relative Strengths vs. Peers, Unique Ideas, and Common Cohort Gaps across all submitted PDFs.

### Edge Cases & Exception Flows
* **EC-07.1 (Gated Release Rule — Student Checks Before Deadline / End Now):** While submissions are still open (`review_released=False`), a student who uploaded their PDF can replace their PDF file, but `review` and `peer_comparison` are strictly withheld (`None`) with a lock banner explaining that feedback unlocks only after the deadline or when the Lecturer clicks *"End Submission Now"*.
* **EC-07.2 (First-Submission Edge Case — Only 1 Student Submitted):** If only 1 student submitted a PDF before submissions close (`total_submissions == 1`, `peer_count == 0`), Column 1 displays the student's individual AI review card while Column 2 displays the exact notice:
  > *"This is the first submission received for this assignment, so peer comparison is not yet available. Comparative insights will appear once additional students submit."*
* **EC-07.3 (Non-PDF Upload or Late Submission After Gate Closed):** Uploading a `.docx`/`.txt` file is rejected with HTTP `400` (*"Only PDF (.pdf) files are accepted"*); attempting to upload a PDF after the deadline or after *"End Submission Now"* is rejected with HTTP `403` (*"Submissions are closed for this assignment"*).
