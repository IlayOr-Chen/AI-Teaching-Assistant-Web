# Statement of Work (SoW) — Web-Based AI Teaching Assistant

> **Course:** Software Engineering (OOP / OOD) — Ex2: Vibe Coding & Deep Designing  
> **Team:** Ilay (`IlayOr-Chen`) & Roie (`RoieBenyamin`)

---

## 1. Project Objectives

The objective of this project is to design, implement, test, and demonstrate a self-contained, mobile-first **Web-Based AI Teaching Assistant** engineered according to **KISS (Keep It Simple, Stupid)** and **Deep Object-Oriented Design (OOP/OOD)** principles.

The platform resolves four critical classroom pain points during live university lectures:
1. **Fraud-Resistant, Frictionless Classroom Attendance:** Eliminate slow manual roll calls and prevent remote QR link forwarding or single-phone "buddy punching" using rotating signed QR codes, Haversine GPS geofencing, a 15-minute device cooldown, and local YOLO computer-vision headcount verification.
2. **Structured Classroom Communication & Instant Comprehension Checks:** Provide a single web portal where students can post lesson-tagged public or private (lecturer-only) comments (with optional anonymity) and vote in on-the-spot multiple-choice polls.
3. **Actionable Pedagogical & Delivery Lecture Summaries:** Transform raw `.mp4` lecture recordings into concise, structured teaching summaries and delivery insights (pacing, clarity, audio quality) that improve directly upon verbose raw Zoom auto-summaries.
4. **Fair, Gated PDF Homework Review & Cohort Peer Comparison:** Provide automated Gemini AI homework feedback paired with a dedicated Peer Comparison column that remains strictly locked until the submission deadline expires or the lecturer manually closes submissions.

---

## 2. Scope Definition (In-Scope vs. Out-of-Scope)

### 2.1 In-Scope Capabilities
| Area | In-Scope Implementation |
| :--- | :--- |
| **Role-Based Authentication** | Lecturer registration/login gated by `LECTURER_ACCESS_CODE`, salted PBKDF2-HMAC-SHA256 password hashing (`200,000` iterations), and HTTP-only signed JWT cookies (`/users/lecturers`). Frictionless student roster verification (`/students`). |
| **Excel Roster Import & Export** | Direct `.xlsx` import supporting `/assets/courseid_130158_participants.xlsx` (`104` students, Hebrew columns `שם פרטי`, `שם משפחה`, `דוא"ל`, `קבוצות`, gracefully normalizing `2` empty/NaN group cells), manual Present/Absent overrides, and UTF-8-BOM `.csv` / `.xlsx` attendance export. |
| **Feature 1 — Smart Attendance** | **(1)** Rotating signed QR code (~8s UI rotation + ~7s server grace window = ~15s total validity) with animated SVG countdown ring and Projector Mode; **(2)** Browser Geolocation GPS Geofencing via the Haversine formula (`calculate_distance_meters`, default `100m` radius, live lecturer toggle); **(3)** 15-minute device cooldown via HMAC-signed HTTP cookie + Firebase RTDB `/device_cooldowns`; **(4)** Local Ultralytics YOLOv8n (`imgsz=1280`) classroom photo headcount with annotated bounding boxes and **GREEN / ORANGE / RED** confidence & discrepancy indicator. |
| **Feature 2 — Comments & Suggestions** | Threaded comments persisted under `/comments` supporting `Public (Everyone)` vs. `Private (Lecturer Only)` visibility, `With Name` vs. `Anonymous` identity, and `Lesson 1..N` vs. `General` scope tags. |
| **Feature 3 — On-the-Spot KISS Polls** | Instant multiple-choice polls persisted under `/polls` with single-vote-per-student enforcement, live open/close controls, and animated percentage bars. |
| **Feature 4 — ML Lecture Video Summary** | Lecturer Gemini API Key & Model ID settings panel (`/settings/gemini`, defaulting to `gemini-2.5-flash-lite` with custom model ID support such as `gemini-3.5-flash-lite`), strict `.mp4`-only validation, and structured summary + delivery insights (`/video_summaries`). |
| **Feature 5 — Gated PDF Homework Review** | Strict `.pdf`-only upload + `pypdf` text extraction, deadline + one-click *"End Submission Now"* release gate, Concise Review Card + Dedicated Peer Comparison Column, and explicit **First-Submission Edge Case** notice when only 1 submission exists (`/homework_assignments`). |

### 2.2 Out-of-Scope Constraints
* **No Moodle or University Portal Integration:** Rosters and homework PDFs are uploaded directly through the web application without external LMS OAuth/LTI dependencies.
* **No External Zoom REST API Integration:** Lecture recordings are uploaded directly as `.mp4` files; `/assets/Example_ Class  by zoom).docx` is used solely as a baseline comparison benchmark in our Technical Tools Report.
* **No Facial Recognition or Biometric Student Identification:** Classroom photo analysis counts anonymous `person` bounding boxes locally via YOLOv8n without storing biometric templates.
* **No Native Mobile App Installation:** Students interact entirely through a responsive mobile web interface opened via smartphone camera QR scanning.

---

## 3. Deliverables & Milestones

| Milestone / Checkpoint | Owner | GitHub Issue | Deliverable Summary | Status |
| :--- | :--- | :--- | :--- | :--- |
| **CP 1** | Ilay (`IlayOr-Chen`) | Issue #1 | Project OOP scaffolding, `AppConfig`, `FirebaseRepository` + `InMemoryFirebaseAdapter`, FastAPI app factory, initial `README.md`. | Completed |
| **CP 2** | Ilay (`IlayOr-Chen`) | Issue #2 | `AuthService` (Lecturer & Student auth) and `RosterImporter` (`104`-student Hebrew Excel parser with NaN group handling) + unit tests. | Completed |
| **CP 3** | Ilay (`IlayOr-Chen`) | Issue #3 | `AttendanceManager` (rotating signed QR tokens, 15-minute signed cookie + Firebase RTDB device cooldown) + unit tests. | Completed |
| **CP 3.5** | Ilay (`IlayOr-Chen`) | Issue #13 | Real-world architecture refactor: replace external IP check with **Haversine GPS Geofencing** (`calculate_distance_meters`) + 8s/15s QR grace window. | Completed |
| **CP 4** | Ilay (`IlayOr-Chen`) | Issue #4 | `PhotoHeadCounter` (local Ultralytics YOLOv8n person detection on `/assets/p3.jpeg`, annotated image, GREEN/ORANGE/RED confidence indicator). | Completed |
| **CP 5** | Roie (`RoieBenyamin`) | Issue #8 | Mobile-first dark glassmorphic Frontend UI (`index.html`, `styles.css`, `app.js`, `lecturer.js`, `student.js`, `ui.js`, `api.js`, `icons.js`). | Completed |
| **CP 6** | Roie (`RoieBenyamin`) | Issue #9 | Attendance Management UI tab, manual Present/Absent toggle, UTF-8-BOM CSV & `.xlsx` export, and visual YOLO headcount comparison UI. | Completed |
| **CP 7** | Roie (`RoieBenyamin`) | Issue #10 | `PollService` & `CommentService` backend + animated Live Polls and Threaded Comments frontend tabs + unit/integration tests. | Completed |
| **CP 8** | Ilay (`IlayOr-Chen`) | Issue #5 | `GeminiClient`, `VideoSummarizer` (`.mp4`-only), and `HomeworkReviewer` (`.pdf`-only + Gated Release + Peer Comparison) + UI tabs + tests. | Completed |
| **CP 9** | Roie (`RoieBenyamin`) | Issue #7 | Software Engineering specifications: `docs/02_SoW.md`, `docs/03_SRS.md`, and `docs/04_use_cases.md`. | Completed |
| **CP 10** | Ilay (`IlayOr-Chen`) | Issue #6 | `docs/05_architecture_and_diagrams.md` (Component, Low-Level Class, and Sequence Diagrams) & `docs/06_testing_strategy.md`. | Scheduled |
| **CP 11** | Roie (`RoieBenyamin`) | Issue #11 | `docs/01_tools_report.md` with empirical test benchmarks on `p3.jpeg`, `courseid_130158_participants.xlsx`, and `Example_ Class  by zoom).docx`. | Scheduled |
| **CP 12** | Roie (`RoieBenyamin`) | Issue #12 | Cloudflare Tunnel & Render deployment configs (`render.yaml`, `Procfile`), `docs/07_conclusion.md`, and final 10-minute demo script in `README.md`. | Scheduled |

---

## 4. Assumptions & Operational Constraints

1. **HTTPS Requirement for Browser Geolocation API:** Modern mobile browsers permit `navigator.geolocation.getCurrentPosition()` only on secure origins (`https://` or `http://localhost`). Exposing the local server via **Cloudflare Tunnel (`cloudflared`)** or deploying on **Render** automatically provides a valid HTTPS origin.
2. **Indoor GPS Drift & Fallback Controls:** Indoor lecture halls can introduce 20–60 meters of GPS multipath drift or block laptop GPS sensors. The system therefore defaults to a configurable `100m` radius (`30m`–`500m`), provides manual classroom coordinate entry (including a one-click Ariel University preset), and includes a live *"Enforce GPS Geofencing"* toggle if a student's browser blocks location permissions.
3. **Deterministic Test Isolation:** Automated `pytest` suites must never require live external network connectivity or mutate production Firebase RTDB data; all tests run against `InMemoryFirebaseAdapter` (`use_mock=True`) inside the local `./venv` environment.

---

## 5. Acceptance Criteria

* **AC-1 (Roster & Auth):** Uploading `/assets/courseid_130158_participants.xlsx` imports all `104` students into `/students` with `0` crashes on the `2` unassigned group rows (`בועז בן משה` and `נדב סודרי`), and all lecturer write endpoints reject unauthenticated (`401`) or student (`403`) requests.
* **AC-2 (Smart Attendance):** Rotating QR tokens visually refresh every `8` seconds (`15` seconds total validity with grace window); Haversine GPS geofencing permits check-ins inside the radius and rejects check-ins outside the radius with exact meter distance; the 15-minute device cooldown blocks a second check-in from the same device; and YOLOv8n (`imgsz=1280, conf=0.30`) detects `22` physical persons on `/assets/p3.jpeg` with an annotated bounding-box output and `GREEN / ORANGE / RED` status.
* **AC-3 (Comments & Polls):** Private lecturer-only comments are invisible to other students; anonymous comments mask `author_id`/`author_name` as `"Anonymous Student"`; and polls enforce single-vote-per-student while animating live percentage bars.
* **AC-4 (Video Summary & Homework Review):** Non-`.mp4` video uploads and non-`.pdf` homework uploads return HTTP `400`; student homework reviews and peer comparisons remain strictly locked (`review_released=False`) until the deadline passes or the lecturer clicks *"End Submission Now"*; and a single-submission assignment displays the exact First-Submission Edge Case notice.
