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
  * Protected Lecturer registration/login (`LECTURER_ACCESS_CODE` + salted PBKDF2-HMAC-SHA256 `200,000` iterations + signed HTTP-only JWT cookie stored under `/users/lecturers`).
  * Frictionless Student roster verification against `/students`.
  * Direct import of the course Excel roster ([courseid_130158_participants.xlsx](file:///c:/Users/ILAYO/Documents/Projects/AI-Teaching-Assistant-Web/assets/courseid_130158_participants.xlsx) — `104` students across Hebrew columns `שם פרטי`, `שם משפחה`, `דוא"ל`, `קבוצות`, gracefully normalizing `3.0` -> `"3"` and handling the `2` empty/NaN group rows for `בועז בן משה` and `נדב סודרי` without crashing), manual Present/Absent overrides, and UTF-8-BOM `.csv` / `.xlsx` exports.
* **Feature 1 — Smart Multi-Layer Attendance (`AttendanceManager` & `PhotoHeadCounter`):**
  * **Rotating Animated QR Code:** Cryptographically signed short-lived tokens rotating every **`8` seconds** on the UI with a **`7`-second server-side grace window (`15` seconds total validity)**, animated 60fps SVG countdown ring, and full-screen Projector Mode.
  * **GPS Geofencing via Browser Geolocation & Haversine Distance (Real-World Pivot from External IP Matching):**
    * *Why GPS Geofencing:* University Wi-Fi networks use multi-IP NAT / load-balancing pools and many students in class use cellular `4G/5G`, causing false rejections under an External IP check. We replaced IP matching with **Browser Geolocation (`navigator.geolocation`)** and the **Haversine great-circle formula** (`calculate_distance_meters(lat1, lon1, lat2, lon2)`).
    * *How it works:* Enforces `distance <= geofence_radius_meters` (default `100m`, configurable `30m`–`500m`), includes manual classroom coordinate inputs + Ariel Campus preset, and provides a live **"Enforce GPS Geofencing"** lecturer toggle fallback if a student denies location permission.
  * **15-Minute Device Cooldown:** Dual enforcement via HMAC-SHA256 signed HTTP-only cookies (`ta_device_id`) and Firebase RTDB (`/device_cooldowns`) blocking a single device from checking in multiple students for 15 minutes (`900s`) with a live `MM:SS` countdown.
  * **Local YOLOv8n Classroom Headcount:** Runs local `yolov8n.pt` (`imgsz=1280, conf=0.30, classes=[0]`) on uploaded classroom photos (tested on [p3.jpeg](file:///c:/Users/ILAYO/Documents/Projects/AI-Teaching-Assistant-Web/assets/p3.jpeg): **`22` persons detected**, `avg_conf = 0.634`), renders an annotated bounding-box JPEG, displays YOLO Detected Count side-by-side with Self-Registered Count, and computes a **GREEN / ORANGE / RED** confidence & discrepancy status.
* **Feature 2 — Threaded Comments & Suggestions (`CommentService`):**
  * Persisted under `/comments` with `Public (Everyone)` vs. `Private (Lecturer Only)` visibility isolation, `With Name` vs. `Anonymous` identity masking, `Lesson 1..N` vs. `General` scope tags, scope filtering, and threaded replies.
* **Feature 3 — On-the-Spot KISS Polls (`PollService`):**
  * Persisted under `/polls` with instant multiple-choice creation (`2..8` options), single-vote-per-student enforcement, live open/close controls, and animated live percentage bars.
* **Feature 4 — ML Lecture Video Summary (`GeminiClient` & `VideoSummarizer`):**
  * Lecturer Settings panel (`/settings/gemini`) to enter/update a Gemini API Key (masked in UI) and configure the Gemini Model ID (defaulting to `gemini-2.5-flash-lite` while allowing custom IDs such as `gemini-3.5-flash-lite`).
  * Strict `.mp4`-only validation (`video/mp4`), live processing indicator, and concise structured output (**Executive Summary**, **Key Topics**, **Core Takeaways**, **Action Items**, and **Lecturer Delivery Insights** on `Pacing`, `Clarity`, and `Audio Quality`).
* **Feature 5 — Deep Homework Review & Peer Comparison (`HomeworkReviewer`):**
  * Strict `.pdf`-only upload + `pypdf` text extraction (`/homework_assignments`).
  * **Gated Release Rule:** Students can upload/replace their PDF while submissions are open, but AI reviews and peer comparisons remain strictly locked (`review_released=False`) until the deadline passes OR the Lecturer clicks **"End Submission Now"**.
  * **Polished Two-Column Review Layout:** (1) **Concise Review & Summary Card** and (2) **Dedicated Peer Comparison Column** across all submitted PDFs — plus the explicit **First-Submission Edge Case** notice when `total_submissions == 1`.

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
│   │   ├── poll_service.py                 # PollService (Live polls + single-vote guard)
│   │   ├── comment_service.py              # CommentService (Public/Private + Anonymous + Threads)
│   │   ├── gemini_client.py                # GeminiClient (Settings + structured JSON generator)
│   │   ├── video_summarizer.py             # VideoSummarizer (Strict MP4 + delivery insights)
│   │   └── homework_reviewer.py            # HomeworkReviewer (Strict PDF + gated peer comparison)
│   ├── static/
│   │   ├── index.html                      # Single-page application shell
│   │   ├── css/styles.css                  # Responsive dark glassmorphic design system
│   │   └── js/                             # Modular Vanilla ES Modules (app, lecturer, student, etc.)
│   └── utils/
│       └── ip_resolver.py                  # ClientIPResolver (Cloudflare/Render/Local priority)
├── tests/                                  # 10 pytest modules (44 automated tests, 100% passing)
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

1. **Create and activate a local `venv`:**
   * **Windows (Git Bash):**
     ```bash
     python -m venv venv
     source venv/Scripts/activate
     ```
   * **Windows (PowerShell):**
     ```powershell
     python -m venv venv
     .\venv\Scripts\Activate.ps1
     ```
   * **macOS / Linux:**
     ```bash
     python3 -m venv venv
     source venv/bin/activate
     ```

2. **Install dependencies inside `venv`:**
   ```bash
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

3. **Configure `.env` and Firebase `serviceAccountKey.json`:**
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
   python -m uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
   ```

### 4.2 Live Classroom Exposure via Cloudflare Tunnel (`cloudflared`)

Because smartphone cameras must reach the server and mobile browsers require an **HTTPS** origin for `navigator.geolocation.getCurrentPosition()` (GPS Geofencing), expose port `8000` with a single command:

```bash
cloudflared tunnel --url http://localhost:8000
```

* Cloudflare Tunnel outputs a public HTTPS URL such as `https://random-words.trycloudflare.com`.
* Open that HTTPS URL on the classroom projector computer. FastAPI automatically reads `X-Forwarded-Proto` and `X-Forwarded-Host` (or `PUBLIC_BASE_URL` in `.env`) so the rotating QR code embeds the public HTTPS tunnel URL for students' phones!

### 4.3 Cloud Deployment on Render (`render.yaml` & `Procfile`)

1. Push the repository to GitHub and connect it as a **Blueprint** or **Web Service** on [Render](https://render.com) using [render.yaml](file:///c:/Users/ILAYO/Documents/Projects/AI-Teaching-Assistant-Web/render.yaml) and [Procfile](file:///c:/Users/ILAYO/Documents/Projects/AI-Teaching-Assistant-Web/Procfile).
2. In the Render Dashboard, upload `serviceAccountKey.json` as a **Secret File** at `/etc/secrets/serviceAccountKey.json` (and set `FIREBASE_CREDENTIALS_PATH=/etc/secrets/serviceAccountKey.json`) and set `GEMINI_API_KEY`.
3. Render automatically provisions HTTPS and monitors `/api/health`.

---

## 5. 10-Minute Live Classroom Demo Script

| Time | Actor & Screen | Action to Perform | What to Highlight to the Audience |
| :--- | :--- | :--- | :--- |
| **00:00 – 01:30** | **Lecturer** (Projector) | Open `https://<tunnel-url>/#/lecturer`, register/sign in with `LECTURER_ACCESS_CODE`, and on the **Overview** tab click **"Use bundled course roster"** (or upload `courseid_130158_participants.xlsx`). | **104 students** imported into Firebase RTDB `/students` in `< 20 ms`; the **2 rows with empty `קבוצות`** (`בועז בן משה` & `נדב סודרי`) are normalized cleanly without crashing. |
| **01:30 – 04:00** | **Lecturer & Students** (Projector + Phones) | Switch to **Live attendance**, click **"Use browser GPS"** (or **"Ariel Campus preset"** `32.1044, 35.2078`), keep radius at `100m`, and click **"Start attendance session"**. Open **Projector mode**. Have students scan the rotating QR on their phones. | **(1)** QR rotates every **8s** with a **7s grace window (15s total)**; **(2)** Student inside `100m` checks in via **Haversine GPS Geofencing**; **(3)** Immediate 2nd check-in on the same phone is blocked by the **15-minute device cooldown (`MM:SS` timer)**; **(4)** Toggle **Enforce GPS Geofencing OFF** live to demonstrate the permission-denied edge-case fallback. |
| **04:00 – 05:30** | **Lecturer** (Projector) | Open **Roster & YOLO Headcount**, click **"Test on Classroom Photo (p3.jpeg)"**, toggle a student with **Mark Present / Mark Absent**, and click **Export Excel (.xlsx)** / **Export CSV (.csv)**. | Local **YOLOv8n (`imgsz=1280`)** detects **22 physical persons** (`avg_conf = 0.634`), renders annotated bounding boxes, displays the **GREEN / ORANGE / RED** confidence banner against self-registered attendance, and downloads a UTF-8-BOM spreadsheet preserving Hebrew names. |
| **05:30 – 07:00** | **Lecturer & Students** | In **Live Polls**, launch a multiple-choice poll; students vote on their phones and try voting twice. In **Comments & Suggestions**, a student posts an **Anonymous Public** comment and a **Private (Lecturer-Only)** comment; Lecturer replies. | Animated live poll percentage bars update in real time; duplicate votes are blocked (`403`); private comments are invisible to other students; anonymous identity is masked at the API layer. |
| **07:00 – 08:30** | **Lecturer** (Projector) | Open **MP4 Lecture Summary**, show the configurable **Gemini API Key & Model ID** (`gemini-2.5-flash-lite` / custom `gemini-3.5-flash-lite`), try selecting a non-MP4 file (`.docx` rejected), then upload an `.mp4` clip. | Strict `.mp4` validation, live processing pulse, and a concise card with **Executive Summary**, **Key Topics**, **Core Takeaways**, **Action Items**, and **Lecturer Delivery Insights** (`Pacing`, `Clarity`, `Audio Quality`) improving on the verbose Zoom summary. |
| **08:30 – 10:00** | **Lecturer & Students** | Open **PDF Homework Review**, create an assignment, and have 1 student upload a `.pdf` file. Show that the review is **LOCKED (Gated Release)**. Click **"End Submission Now"** as Lecturer, inspect the **First-Submission Edge Case** notice, then have a 2nd student submit on an open assignment and unlock to show the **Peer Comparison Column**. | Proves the **Gated Release Rule** (no leaks while open), the **First-Submission Edge Case** notice when `total_submissions == 1`, and the two-column **Review Card + Peer Comparison Column** when multiple students submit. |
