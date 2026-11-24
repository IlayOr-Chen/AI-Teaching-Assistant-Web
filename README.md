# Web-Based AI Teaching Assistant

> **Ex2: Vibe Coding & Deep Designing — Software Engineering (OOP/OOD)**  
> **Team:** Ilay (`IlayOr-Chen`) & Roie (`RoieBenyamin`)

---

## 1. Problem Statement

Modern university lectures face four persistent operational and pedagogical bottlenecks during live classroom sessions:

1. **Unreliable & Slow Attendance Tracking:** Manual roll calls waste valuable lecture time, while static QR codes or shared links are easily forwarded to absent friends outside the classroom or used to check in multiple names from a single smartphone ("buddy punching").
2. **Passive Classroom Feedback:** Students often hesitate to raise questions or critiques publicly during class, and lecturers lack a lightweight, structured channel for lesson-tagged public/private feedback and instant comprehension checks.
3. **Verbose, Unfocused Lecture Summaries:** Standard automated meeting recaps (such as the raw Zoom AI Companion summary in `/assets/Example_ Class  by zoom).docx`, which spans 28 paragraphs / 695 words with ASR name mistranslations like `"Bez" / "בז" / "בעז"`) lack concise student takeaways and provide zero feedback on lecturer delivery (pacing, clarity, audio quality).
4. **Isolated Homework Grading Without Peer Context:** Traditional homework feedback evaluates submissions in isolation and leaks early hints if released before the submission deadline closes.

---

## 2. Project Scope & Core Features (KISS + Deep OOD)

This platform provides a self-contained, mobile-first **Web-Based AI Teaching Assistant** engineered with a clean 4-tier Object-Oriented Python (**FastAPI**) backend, **Firebase Realtime Database (RTDB)** persistence, local **Ultralytics YOLOv8n** computer vision, and **Google Gemini** multimodal analysis.

### In-Scope Capabilities
* **Role-Based Authentication & Excel Roster Management (`AuthService` & `RosterImporter`):**
  * Restricted Lecturer authentication (`LECTURER_EMAIL` / `LECTURER_PASSWORD` configured in `.env`, defaulting to `adminL@gmail.com` / `12345` with **case-insensitive** email comparison, salted PBKDF2-HMAC-SHA256 `200,000` iterations, signed HTTP-only JWT cookie, and public self-registration disabled with `403 Forbidden`).
  * Frictionless Student roster verification against `/students`.
  * Automatic startup seeding + one-click **"Sync from Course Excel"** of the Hebrew course roster ([courseid_130158_participants.xlsx](file:///c:/Users/ILAYO/Documents/Projects/AI-Teaching-Assistant-Web/assets/courseid_130158_participants.xlsx) — `104` students across Hebrew columns `שם פרטי`, `שם משפחה`, `דוא"ל`, `קבוצות`, gracefully normalizing `3.0` -> `"3"` and handling the `2` empty/NaN group rows for `בועז בן משה` and `נדב סודרי` without crashing), manual Present/Absent overrides, and UTF-8-BOM (`utf-8-sig`) `.csv` / `.xlsx` exports.
* **Feature 1 — Smart Multi-Layer Attendance (`AttendanceManager` & `PhotoHeadCounter`):**
  * **Static Session QR Code:** Generates ONE short, clean 16-character URL-safe session token when the Lecturer opens a lesson session (valid while the session remains `LIVE`, and invalidated immediately when the Lecturer closes the session), producing a clean, low-density QR code that is easy to scan from the back of a lecture hall, with a centered layout and full-screen Projector Mode.
  * **Interactive Leaflet.js + OpenStreetMap Geofence Map & Haversine Distance (Real-World Pivot from External IP Matching):**
    * *Why GPS Geofencing:* University Wi-Fi networks use multi-IP NAT / load-balancing pools and many students in class use cellular `4G/5G`, causing false rejections under an External IP check. We replaced IP matching with **Browser Geolocation (`navigator.geolocation`)** and the **Haversine great-circle formula** (`calculate_distance_meters(lat1, lon1, lat2, lon2)`).
    * *How it works:* Before opening a QR session, the Lecturer configures the classroom geofence on an **interactive Leaflet.js + OpenStreetMap widget** (**"Use My Current Location"**, click/drag pin on map, **`30m` / `50m` / `100m` radius selector pills**, and a live translucent blue/cyan `L.circle`), enforcing `distance <= geofence_radius_meters` with a live **"Enforce GPS Geofencing"** lecturer toggle fallback if a student denies location permission.
  * **15-Minute Device Cooldown + Lecturer One-Click Reset:** Dual enforcement via HMAC-SHA256 signed HTTP-only cookies (`ta_device_id`) and Firebase RTDB (`/device_cooldowns`) blocking a single device from checking in multiple students for 15 minutes (`900s`) with a live `MM:SS` countdown, plus a protected Lecturer **"Reset 15-Min Device Cooldowns"** button (`POST /api/attendance/reset-cooldowns`) that clears `/device_cooldowns` and records `/settings/cooldown_reset_at` to immediately unblock even smartphones holding an active local cooldown cookie.
  * **Local YOLOv8n Classroom Headcount & Unified Lesson Scope:** Runs local `yolov8n.pt` (`imgsz=1280, conf=0.30, classes=[0]`) on uploaded classroom photos, persists the annotated bounding-box JPEG Data URL per lesson under Firebase RTDB (`Reset YOLO` clears count + photo via `DELETE /api/attendance/headcount`), displays YOLO Detected Count side-by-side with Self-Registered Count (`GREEN` / `ORANGE` / `RED`), and shares one top lesson selector across YOLO and the Roster table.
  * **Student Attendance History:** Student Portal **Check-in** tab includes a **My Attendance History** card (`/api/attendance/my-history`) showing attended lesson counts, percentage bar, and `Present` / `Absent` badges across all course lessons.
* **Feature 2 — Threaded Comments & Suggestions (`CommentService`):**
  * Persisted under `/comments` with `Public (Everyone)` vs. `Private (Lecturer Only)` visibility isolation, `With Name` vs. `Anonymous` identity masking, `Lesson 1..N` vs. `General` scope tags, scope filtering, and threaded replies.
* **Feature 3 — On-the-Spot KISS Polls (`PollService`):**
  * Persisted under `/polls` with instant multiple-choice creation (`2..8` options), mutable student voting (cast a vote, click another option to switch, or click the active option again to cancel/unvote), live open/close controls, and animated live percentage bars.
* **Feature 4 — Video AI Lecture Summary (`GeminiClient` & `VideoSummarizer`):**
  * Lecturer Settings panel (`/settings/gemini`) supporting standard Google Gemini API keys (`AIza...` and `AQ....`, masked in UI, with one-click **Clear Key** via `DELETE /api/settings/gemini`) plus a free-text Gemini Model ID with quick-select pills (`gemini-3.5-flash-lite` default, `gemini-3.5-flash`, `gemini-2.5-flash`) and automatic retry + model fallback on transient `503 UNAVAILABLE` / `429` high-demand spikes.
  * **YouTube URL Only + Native `VideoMetadata(content_layers=...)` (Fast 45–90 Min Lecture Support):** Canonicalizes public YouTube URLs (stripping `&list=...`, `&index=...` playlist parameters) and analyzes lectures via `Fast (Transcript)` mode (`content_layers=["TRANSCRIPT"]` with automatic fallback to `["AUDIO"]`), `Audio Only` (`content_layers=["AUDIO"]`), or `Full Video (Low FPS)` (`fps=0.2`), returning an **Executive Summary (TL;DR)**, **Detailed Narrative Walkthrough** (`detailed_overview`), **Key Concepts & Definitions** (`key_concepts`), **Key Topics**, **Core Takeaways**, **Quick Review Questions** (`study_questions`), **Action Items**, and **Lecturer Delivery Insights** (`Pacing`, `Clarity`, `Audio Quality`), with **Copy** (Markdown) and **Delete** buttons on every card.
* **Feature 5 — Deep Homework Review & Peer Comparison (`HomeworkReviewer`):**
  * **Bring Your Own Key (BYOK):** Students configure their own Gemini API Key and preferred model locally in browser `localStorage` (`Your Gemini API Key & Model`), sent per request via `X-Gemini-Api-Key` / `X-Gemini-Model` headers (never stored on the server; missing key returns a clean HTTP `400` prompt).
  * **Multi-Format File Upload + Public GitHub Repository URL:** Accepts `.pdf`, `.docx`, `.txt`, `.md`, `.zip`, and source-code files (`.py`, `.java`, `.c`, `.cpp`, `.h`, `.js`, `.ts`, `.html`, `.css`, `.json`), OR a **Public GitHub Repository URL** (`https://github.com/owner/repo`, downloaded over HTTPS zipball without requiring a local `git` binary, extracting `README.md` and source files while ignoring `node_modules/`, `venv/`, `.git/`, `__pycache__/`, and binaries).
  * **Gated Release Rule:** Students can submit/replace their work while submissions are open, but AI reviews and peer comparisons remain strictly locked (`review_released=False`) until the deadline passes OR the Lecturer clicks **"End Submission Now"**.
  * **Polished Two-Column Review Layout:** (1) **Concise Review & Summary Card** and (2) **Dedicated Peer Comparison Column** across all submissions — plus the explicit **First-Submission Edge Case** notice when `total_submissions == 1`.

### Out-of-Scope Constraints
* No integration with Moodle, external Zoom REST APIs, university SSO portals, or facial recognition biometrics.

---

## 3. Repository Structure & Documentation Links

```text
AI-Teaching-Assistant-Web/
├── assets/
│   ├── courseid_130158_participants.xlsx   # Real 104-student course roster (Hebrew columns, 2 NaN groups)
│   ├── Example_ Class  by zoom).docx       # Baseline 01:33:52 Zoom auto-summary for benchmark comparison
│   └── p3.jpeg                             # Real 2000x1500 classroom lecture hall photo for YOLO testing
├── docs/
│   ├── 01_tools_report.md                  # Technical Tools Report & Empirical Benchmarks
│   ├── 02_SoW.md                           # Statement of Work (Objectives, Scope, Milestones, AC)
│   ├── 03_SRS.md                           # Software Requirements Specification & Firebase RTDB Schema
│   ├── 04_use_cases.md                     # Main Use Cases & Client Stories (Happy & Edge Cases)
│   ├── 05_architecture_and_diagrams.md     # Component, Low-Level Class (13 classes), & Sequence Diagrams
│   ├── 06_testing_strategy.md              # Class-by-Class Testing Strategy & Pytest Verification Report
│   └── 07_conclusion.md                    # Summary of Results, Limitations & Future Improvements
├── src/
│   ├── config.py                           # AppConfig environment & runtime settings
│   ├── main.py                             # FastAPI application factory & 28 REST endpoints
│   ├── repository/
│   │   └── firebase_repository.py          # FirebaseRepository + InMemoryFirebaseAdapter
│   ├── services/
│   │   ├── auth_service.py                 # AuthService (PBKDF2-HMAC-SHA256 + JWT cookies)
│   │   ├── roster_importer.py              # RosterImporter (openpyxl Hebrew/NaN parser)
│   │   ├── attendance_manager.py           # AttendanceManager (Rotating QR + Haversine GPS + Cooldown)
│   │   ├── photo_head_counter.py           # PhotoHeadCounter (Local YOLOv8n + GREEN/ORANGE/RED logic)
│   │   ├── poll_service.py                 # PollService (Live polls + mutable vote switching/unvoting)
│   │   ├── comment_service.py              # CommentService (Public/Private + Anonymous + Threads)
│   │   ├── gemini_client.py                # GeminiClient (AIza/AQ BYOK, Part.from_uri & JSON generator)
│   │   ├── video_summarizer.py             # VideoSummarizer (YouTube URI & MP4 <=20MB + delivery insights)
│   │   └── homework_reviewer.py            # HomeworkReviewer (Multi-format, GitHub repo, BYOK & gated peer comparison)
│   ├── static/
│   │   ├── index.html                      # Single-page application shell
│   │   ├── css/styles.css                  # Responsive dark glassmorphic design system
│   │   └── js/                             # Modular Vanilla ES Modules (app, lecturer, student, etc.)
│   └── utils/
│       └── ip_resolver.py                  # ClientIPResolver (Cloudflare/Render/Local priority)
├── tests/                                  # 10 pytest modules (52 automated test cases, 100% passing)
├── .env.example                            # Environment configuration template
├── Procfile                                # Render / PaaS process start command
├── render.yaml                             # Render Blueprint deployment spec
└── requirements.txt                        # Pinned Python dependencies
```

### Software Engineering Documentation Suite
1. [Technical Tools Report & Real-Life Benchmarks (`docs/01_tools_report.md`)](file:///c:/Users/ILAYO/Documents/Projects/AI-Teaching-Assistant-Web/docs/01_tools_report.md)
2. [Statement of Work (`docs/02_SoW.md`)](file:///c:/Users/ILAYO/Documents/Projects/AI-Teaching-Assistant-Web/docs/02_SoW.md)
3. [Software Requirements Specification (`docs/03_SRS.md`)](file:///c:/Users/ILAYO/Documents/Projects/AI-Teaching-Assistant-Web/docs/03_SRS.md)
4. [Main Use Cases — Happy & Edge Cases (`docs/04_use_cases.md`)](file:///c:/Users/ILAYO/Documents/Projects/AI-Teaching-Assistant-Web/docs/04_use_cases.md)
5. [Architecture, Low-Level Class Diagram & Sequence Diagrams (`docs/05_architecture_and_diagrams.md`)](file:///c:/Users/ILAYO/Documents/Projects/AI-Teaching-Assistant-Web/docs/05_architecture_and_diagrams.md)
6. [Testing Strategy & Verification Report (`docs/06_testing_strategy.md`)](file:///c:/Users/ILAYO/Documents/Projects/AI-Teaching-Assistant-Web/docs/06_testing_strategy.md)
7. [Conclusion, Real-Life Outcomes & Future Work (`docs/07_conclusion.md`)](file:///c:/Users/ILAYO/Documents/Projects/AI-Teaching-Assistant-Web/docs/07_conclusion.md)

---

## 4. Quick-Start Guide (Local `venv` + Cloudflare Tunnel + Render)

### 4.1 Local Setup with Python Virtual Environment (`venv`)

1. **Create and activate a local `venv` (choose your shell):**

   > **Important for Windows Users:** Do **not** run `source venv/Scripts/activate` inside **Windows PowerShell** (`source` is a Bash built-in, not a PowerShell cmdlet). Use `.\venv\Scripts\Activate.ps1` in PowerShell, or use `source venv/Scripts/activate` inside **Git Bash**.

   * **Option A — Windows PowerShell (Default VS Code / Windows Terminal):**
     ```powershell
     python -m venv venv
     # If PowerShell blocks script execution, run this once for the current session:
     Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
     .\venv\Scripts\Activate.ps1
     ```
   * **Option B — Windows Git Bash (`MINGW64`):**
     ```bash
     python -m venv venv
     source venv/Scripts/activate
     ```
   * **Option C — macOS / Linux (`bash` / `zsh`):**
     ```bash
     python3 -m venv venv
     source venv/bin/activate
     ```

2. **Install dependencies inside the activated `venv`:**
   ```bash
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

3. **Configure `.env` and Firebase `serviceAccountKey.json`:**
   * **Windows PowerShell:**
     ```powershell
     Copy-Item .env.example .env
     ```
   * **Git Bash / macOS / Linux:**
     ```bash
     cp .env.example .env
     ```
   * Target Firebase RTDB URL: `https://ai-teaching-assistant-web-default-rtdb.firebaseio.com/`
   * Place your Firebase Admin SDK private key file named `serviceAccountKey.json` in the project root directory (matching `FIREBASE_CREDENTIALS_PATH=serviceAccountKey.json` in `.env`).
   * `.env`, `serviceAccountKey.json`, `venv/`, and `.venv/` are excluded via [.gitignore](file:///c:/Users/ILAYO/Documents/Projects/AI-Teaching-Assistant-Web/.gitignore) and must **never** be committed to Git.

4. **Run the automated `pytest` suite (`./venv`):**
   ```bash
   python -m pytest -v
   ```

5. **Start the FastAPI server (`./venv`):**
   ```bash
   # Recommended for Cloudflare Tunnel & live classroom demo (no mid-request reload drops):
   python -m uvicorn src.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="*"
   ```
   *(If developing locally with `--reload`, scope it with `--reload-dir src` so temp/cache files never trigger restarts during AI/upload requests.)*

### 4.2 Live Classroom Exposure via Cloudflare Tunnel (`cloudflared`)

Because smartphone cameras must reach the server and mobile browsers require an **HTTPS** origin for `navigator.geolocation.getCurrentPosition()` (GPS Geofencing), expose port `8000` with a single command:

```bash
cloudflared tunnel --url http://localhost:8000
```

* Cloudflare Tunnel outputs a public HTTPS URL such as `https://random-words.trycloudflare.com`.
* Open that HTTPS URL on the classroom projector computer. FastAPI automatically reads `X-Forwarded-Proto` and `X-Forwarded-Host` (or `PUBLIC_BASE_URL` in `.env`) so the static session QR code embeds the public HTTPS tunnel URL for students' phones!

### 4.3 Cloud Deployment on Render (`render.yaml` & `Procfile`)

1. Push the repository to GitHub and connect it as a **Blueprint** or **Web Service** on [Render](https://render.com) using [render.yaml](file:///c:/Users/ILAYO/Documents/Projects/AI-Teaching-Assistant-Web/render.yaml) and [Procfile](file:///c:/Users/ILAYO/Documents/Projects/AI-Teaching-Assistant-Web/Procfile).
2. In the Render Dashboard, upload `serviceAccountKey.json` as a **Secret File** at `/etc/secrets/serviceAccountKey.json` (and set `FIREBASE_CREDENTIALS_PATH=/etc/secrets/serviceAccountKey.json`); `GEMINI_API_KEY` is optional since lecturers and students can enter their own keys directly in the UI.
3. Render automatically provisions HTTPS and monitors `/api/health`.

---

## 5. 10-Minute Live Classroom Demo Script

| Time | Actor & Screen | Action to Perform | What to Highlight to the Audience |
| :--- | :--- | :--- | :--- |
| **00:00 – 01:30** | **Lecturer** (Projector) | Open `https://<tunnel-url>/#/lecturer`, sign in with the fixed Lecturer demo account (`adminL@gmail.com` / `12345`, case-insensitive; public registration disabled), and on the **Overview** tab verify the auto-loaded roster or click **"Sync from Course Excel"**. | **104 Hebrew student records** from `assets/courseid_130158_participants.xlsx` are automatically seeded into Firebase RTDB `/students` on startup; the **2 rows with empty `קבוצות`** (`בועז בן משה` & `נדב סודרי`) are normalized cleanly without crashing. |
| **01:30 – 04:00** | **Lecturer & Students** (Projector + Phones) | Switch to **Attendance**, use the **Interactive Leaflet.js + OpenStreetMap Geofence Map** (**"Use My Current Location"** or click/drag the pin, select **`30m` / `50m` / `100m`** radius pills with live blue/cyan radius circle), and click **"Start attendance session"**. Open **Projector mode**. Have students scan the centered static session QR on their phones. | **(1)** Clean static session QR code with short URL-safe token (valid while session is `LIVE`, invalidated when closed); **(2)** Student inside the chosen radius checks in via **Haversine GPS Geofencing** (selecting their Hebrew name + email); **(3)** Immediate 2nd check-in on the same phone is blocked by the **15-minute device cooldown (`MM:SS` timer)**; **(4)** Lecturer clicks **"Reset 15-Min Device Cooldowns"** to immediately unblock the phone; **(5)** Toggle **Enforce GPS Geofencing OFF** live as the permission-denied edge-case fallback. |
| **04:00 – 05:30** | **Lecturer & Students** | In **Roster**, select the lesson at the top, click **"Upload classroom photo"** (inspect the persisted annotated image & **Reset YOLO**), toggle **Mark Present / Mark Absent**, and check the Student Portal **My Attendance History** card. | Local **YOLOv8n (`imgsz=1280`)** annotates physical persons with confidence scores, persists the photo across refreshes/tabs, displays **GREEN / ORANGE / RED** discrepancy against self-registered attendance, and Student Portal tracks per-lesson `Present` / `Absent` percentages. |
| **05:30 – 07:00** | **Lecturer & Students** | In **Polls**, launch a multiple-choice poll; students vote, switch options, or click their chosen option again to unvote. In **Comments**, a student posts an **Anonymous Public** comment and a **Private (Lecturer-Only)** comment; Lecturer replies. | Animated live poll percentage bars update immediately on vote, switch, or unvote; private comments are invisible to other students; anonymous identity is masked at the API layer. |
| **07:00 – 08:30** | **Lecturer** (Projector) | Open **Video AI**, enter an `AIza...` or `AQ....` Gemini API key with the default **`gemini-3.5-flash-lite`** model, select **`Fast (Transcript)`** (or **`Audio Only`** / **`Full Video (Low FPS)`**), paste a **public YouTube lecture URL**, and test the **Copy** and **Delete** buttons on a summary card. | YouTube URLs are canonicalized and passed directly to Gemini via `VideoMetadata(content_layers=["TRANSCRIPT"])` (with automatic `AUDIO` fallback and 503 model fallback), producing an **Executive Summary (TL;DR)**, **Lecture Overview & Walkthrough**, **Key Concepts & Definitions**, **Quick Review Questions**, **Action Items**, and **Lecturer Delivery Insights**. |
| **08:30 – 10:00** | **Lecturer & Students** | Open **Homework**, create an assignment as Lecturer, have a Student enter their BYOK Gemini key (`localStorage`) and upload a `.pdf`/`.docx`/`.zip`/code file OR **Public GitHub Repo URL**. Show reviews are **LOCKED**, click **"End Submission Now"** to inspect **First-Submission Edge Case**, then submit a 2nd student's work on an open assignment to show **Peer Comparison**. | Proves per-student BYOK (`X-Gemini-Api-Key`), the **Gated Release Rule** (no leaks while open), the **First-Submission Edge Case** notice when `total_submissions == 1`, and the two-column **Review Card + Peer Comparison Column**. |
