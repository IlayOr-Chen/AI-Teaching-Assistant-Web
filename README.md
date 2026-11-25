# Web-Based AI Teaching Assistant

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Single--Server-009688?logo=fastapi&logoColor=white)
![Firebase RTDB](https://img.shields.io/badge/Firebase-Realtime%20DB-FFCA28?logo=firebase&logoColor=black)
![Ultralytics YOLO](https://img.shields.io/badge/YOLO-v8n%20Local-7C8CFF)
![Google Gemini](https://img.shields.io/badge/Google%20Gemini-BYOK%20Multi--Model-4285F4?logo=google&logoColor=white)
![Pytest](https://img.shields.io/badge/Pytest-52%2F52%20Passing-34D399?logo=pytest&logoColor=white)

> **Course:** Introduction to Software Engineering (OOP / OOD — **Ex2: Vibe Coding & Deep Designing**), Ariel University  
> **Team:** Ilay (`IlayOr-Chen`) & Roie (`RoieBenyamin`)

---

## 1. Problem Statement

University lectures suffer from four recurring operational and pedagogical bottlenecks during live classes:

1. **Slow & Easily Gamed Attendance Tracking:** Manual roll calls consume valuable lecture time, while unprotected links or static codes without location/device checks are easily forwarded to absent classmates or reused on a single phone ("buddy punching").
2. **Low-Signal Classroom Feedback:** Students hesitate to speak up publicly during large lectures, and lecturers lack a lightweight, lesson-scoped channel for public/private feedback and instant comprehension checks.
3. **Verbose, Unfocused Lecture Summaries:** Standard automated meeting recaps (such as Zoom's AI Companion auto-summary, which produces repetitive walls of prose with ASR name hallucinations and zero pedagogical structure) fail to give students concise key concepts/review questions or give lecturers feedback on delivery (pacing, clarity, audio quality).
4. **Isolated Homework Reviews Without Peer Context:** Conventional homework grading either leaks early feedback before the deadline closes or evaluates each submission in isolation without showing what a student missed compared to class peers.

---

## 2. Project Scope & 5 Core Features (KISS + Deep OOD)

This platform is a self-contained, single-server, mobile-first **Web-Based AI Teaching Assistant** engineered with a strict 4-tier Object-Oriented Python (**FastAPI**) backend, **Firebase Realtime Database (RTDB)** persistence, local **Ultralytics YOLOv8n** computer vision, interactive **Leaflet.js + OpenStreetMap** geofencing, and **Google Gemini (BYOK)** multimodal analysis.

### How Each of the 5 Core Features Is Implemented

| # | Feature | Domain Classes | Actual Engineering Implementation & Key Decisions |
| :-: | :--- | :--- | :--- |
| **1** | **Smart Multi-Layer Attendance** *(Main Feature)* | `AttendanceManager`, `PhotoHeadCounter`, `RosterImporter`, `AuthService` | • **Multi-Lesson Sessions & Static Session QR:** Generates one short, clean 16-char URL-safe session token per active lesson (valid while `LIVE`, invalidated immediately on close) with a centered QR layout and full-screen **Projector Mode**.<br/>• **Interactive Leaflet.js + OpenStreetMap GPS Geofencing:** Lecturer picks the classroom pin ("Use My Current Location", click/drag, or Ariel preset) and selects **`30m` / `50m` / `100m`** radius pills with a live visual circle; check-ins are verified via the **Haversine great-circle formula** (`calculate_distance_meters`), plus a live **"Enforce GPS Geofencing"** toggle fallback.<br/>• **15-Minute Device Cooldown + One-Click Reset:** Dual enforcement via HMAC-SHA256 signed HTTP-only cookie (`ta_device_id`) and Firebase `/device_cooldowns` (`900s` countdown), plus Lecturer **"Reset 15-Min Device Cooldowns"** (`POST /api/attendance/reset-cooldowns`).<br/>• **Local Ultralytics YOLOv8n Headcount:** Runs local `yolov8n.pt` (`imgsz=1280, conf=0.30, classes=[0]`) on uploaded classroom photos, persists the annotated bounding-box JPEG Data URL per lesson in RTDB (`Reset YOLO` clears count + photo), and displays **GREEN / ORANGE / RED** discrepancy vs. self-registered count.<br/>• **Hebrew Excel Roster, History & Export:** Auto-seeds the 104-student Hebrew roster (`courseid_130158_participants.xlsx` — no student ID# required, safely normalizing `2` empty/NaN group cells), shows Student **My Attendance History**, and exports UTF-8-BOM `.csv` and `.xlsx` sheets. |
| **2** | **Real-Time / Offline Comments & Suggestions** | `CommentService` | Persisted in Firebase RTDB `/comments` with **Public (Everyone)** vs. **Private (Lecturer Only)** visibility isolation, **With Name** vs. **Anonymous** identity masking (`"Anonymous Student"`), `Lesson 1..N` / `General` scope tagging & filtering, and chronological threaded replies. |
| **3** | **On-the-Spot KISS Live Polls** | `PollService` | Instant multiple-choice poll creation (`2..8` options) persisted in `/polls`, live open/close controls, animated percentage bars, and **mutable student voting** (cast a vote, click another option to switch, or click the selected option again to cancel/unvote). |
| **4** | **AI Lecture Video Summarizer** *(YouTube-Only KISS Pipeline)* | `VideoSummarizer`, `GeminiClient` | • **Canonical URL + Real Title:** Accepts public YouTube URLs (`youtube.com/watch?v=...`, `youtu.be/...`, Shorts), strips `&list=`/`&index=` playlist parameters, and fetches the real UTF-8/Hebrew video title via YouTube's public oEmbed API (shown in the card header with a clickable **Watch on YouTube** link at the bottom).<br/>• **Automatic 2-Step Fast Strategy (45–90 Min Lectures):** (1) Fetches the spoken transcript via `youtube-transcript-api` (`he`, `iw`, `en`, including auto-generated captions) and summarizes in seconds with Gemini; (2) if captions are unavailable, falls back automatically to direct YouTube URI analysis with `VideoMetadata(fps=0.1)` (and plain `Part.from_uri` fallback on HTTP 400).<br/>• **Rich Academic Recap:** Generates **Executive Summary (TL;DR)**, **Lecture Overview & Walkthrough** (`detailed_overview`), **Key Concepts & Definitions** (`key_concepts`), **Key Topics**, **Core Learning Takeaways**, **Quick Review Questions** (`study_questions`), **Action Items**, and **Lecturer Delivery Insights** (`Pacing`, `Clarity`, `Audio Quality`), with **Copy** (Markdown) and **Delete** buttons — directly outperforming raw Zoom auto-summaries. |
| **5** | **Deep LLM Homework Review & Peer Comparison** | `HomeworkReviewer`, `GeminiClient` | • **Multi-Format Files + Public GitHub Repo URLs:** Supports `.pdf`, `.docx`, `.txt`, `.md`, `.zip`, and source code (`.py`, `.java`, `.c`, `.cpp`, `.h`, `.js`, `.ts`, `.html`, `.css`, `.json`) including Hebrew/sparse PDFs, plus public GitHub repositories (`https://github.com/owner/repo` via HTTPS zipball with zero local `git` binary needed).<br/>• **Pure BYOK Architecture:** Students save their own Gemini key & model locally in browser `localStorage` and send them per request via `X-Gemini-Api-Key` / `X-Gemini-Model` headers (never stored on the server; no `.env` `GEMINI_API_KEY` required). Includes inline loading states during submission & review generation.<br/>• **Gated Release Rule & Rich Peer Comparison:** Reviews stay strictly locked (`review_released=False`) until the deadline passes or the Lecturer clicks **"End Submission Now"**. Once unlocked, displays **(1) Concise Review & Summary Card** and **(2) Dedicated Peer Comparison Column** (`Standing vs. Peers` tier + explanation, `Missing or Underdeveloped Compared to Peers`, `Strengths vs. Class Peers`, `Actionable Improvements`, and `Unique Ideas`) — plus the explicit **First-Submission Edge Case** notice when only 1 student submitted.<br/>• **Automatic Gemini Model Fallback:** Automatically retries and falls back across `[selected_model, gemini-3.8-flash, gemini-3.5-flash, gemini-3.5-flash-lite]` on `503`/`429`/`404`, surfacing clear user-facing JSON error details. |

### Out-of-Scope Constraints (Strict KISS Demo Boundaries)
* No Moodle / university LMS integration, no Zoom REST API integration, no student government/university ID numbers (students authenticate by roster email/name only), and no facial recognition / biometric identification.
* Restricted Lecturer authentication (`adminL@gmail.com` / `12345`, case-insensitive email, salted PBKDF2-HMAC-SHA256 + signed HTTP-only JWT cookie; public lecturer self-registration disabled with `403 Forbidden`).

---

## 3. Repository Structure & Software Engineering Documentation

```text
AI-Teaching-Assistant-Web/
├── assets/
│   └── courseid_130158_participants.xlsx   # 104-student Hebrew course roster (2 empty/NaN groups)
├── docs/
│   ├── 01_tools_report.md                  # Technical Tools Report, Empirical Benchmarks & Real Pivots
│   ├── 02_SoW.md                           # Statement of Work (Objectives, Scope, Roles, Milestones, AC)
│   ├── 03_SRS.md                           # Software Requirements Specification, RTDB Schema & REST API
│   ├── 04_use_cases.md                     # Main Use Cases & Client Stories (Happy & Edge Cases)
│   ├── 05_architecture_and_diagrams.md     # Component, Low-Level Class (13 classes) & Sequence Diagrams
│   ├── 06_testing_strategy.md              # Class-by-Class Testing Strategy & 52-Test Pytest Report
│   └── 07_conclusion.md                    # Conclusion, Real-Life Results, Vibe Coding Reflection & Future Work
├── src/
│   ├── config.py                           # AppConfig immutable environment & runtime settings
│   ├── main.py                             # Single-server FastAPI factory & REST API endpoints
│   ├── repository/
│   │   └── firebase_repository.py          # FirebaseRepository + thread-safe InMemoryFirebaseAdapter
│   ├── services/
│   │   ├── auth_service.py                 # AuthService (PBKDF2-HMAC-SHA256 + JWT cookies)
│   │   ├── roster_importer.py              # RosterImporter (openpyxl Hebrew/NaN Excel parser)
│   │   ├── attendance_manager.py           # AttendanceManager (Static QR + Haversine GPS + 15m Cooldown + Exports)
│   │   ├── photo_head_counter.py           # PhotoHeadCounter (Local YOLOv8n + GREEN/ORANGE/RED logic)
│   │   ├── poll_service.py                 # PollService (Live polls + mutable vote switching/unvoting)
│   │   ├── comment_service.py              # CommentService (Public/Private + Anonymous + Threaded replies)
│   │   ├── gemini_client.py                # GeminiClient (BYOK, fps=0.1 YouTube URI, 503/429/404 model fallback)
│   │   ├── video_summarizer.py             # VideoSummarizer (YouTube-only, oEmbed titles, 2-step transcript/video)
│   │   └── homework_reviewer.py            # HomeworkReviewer (Multi-format/GitHub, BYOK, gated rich Peer Comparison)
│   ├── static/
│   │   ├── index.html                      # Mobile-first single-page shell (Leaflet CDN + ES modules)
│   │   ├── css/styles.css                  # Responsive dark glassmorphic design system
│   │   └── js/                             # Modular Vanilla ES Modules (app, lecturer, student, ai_tools, etc.)
│   └── utils/
│       └── ip_resolver.py                  # ClientIPResolver (Cloudflare/Render/Local header priority)
├── tests/                                  # 10 pytest modules (52 automated tests, 100% passing)
├── .env.example                            # Environment configuration template (no Gemini key required!)
├── Procfile                                # Render / PaaS start command
├── render.yaml                             # Render Blueprint deployment spec
└── requirements.txt                        # Python dependencies
```

### Direct Links to Every Ex2 Deliverable
1. [Technical Tools Report & Real-Life Benchmarks (`docs/01_tools_report.md`)](docs/01_tools_report.md) — **To-Do #3**
2. [Statement of Work (`docs/02_SoW.md`)](docs/02_SoW.md) — **To-Do #4**
3. [Software Requirements Specification (`docs/03_SRS.md`)](docs/03_SRS.md) — **To-Do #5**
4. [Main Use Cases — Happy & Edge Cases (`docs/04_use_cases.md`)](docs/04_use_cases.md) — **To-Do #6**
5. [Architecture, Low-Level Class Diagram & Sequence Diagrams (`docs/05_architecture_and_diagrams.md`)](docs/05_architecture_and_diagrams.md) — **To-Do #7 & #8**
6. [Testing Strategy Mapped to Classes (`docs/06_testing_strategy.md`)](docs/06_testing_strategy.md) — **To-Do #9**
7. [Conclusion, Real-Life Outcomes & Reflection (`docs/07_conclusion.md`)](docs/07_conclusion.md) — **To-Do #12**

---

## 4. Quick-Start Guide (Local `venv`, BYOK Setup, Cloudflare Tunnel & Render)

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
   * **Windows PowerShell:** `Copy-Item .env.example .env`
   * **Git Bash / macOS / Linux:** `cp .env.example .env`
   * Target Firebase RTDB URL: `https://ai-teaching-assistant-web-default-rtdb.firebaseio.com/`
   * Place `serviceAccountKey.json` in the project root directory (matching `FIREBASE_CREDENTIALS_PATH=serviceAccountKey.json` in `.env`).
   * **Pure BYOK (No `.env` Gemini Key Needed):** No `GEMINI_API_KEY` is needed in `.env`. Lecturers enter (and can clear) their own key directly in the **Video AI** panel, and each Student enters their own key in the **Homework** panel (stored strictly in browser `localStorage`).
   * `.env`, `serviceAccountKey.json`, and `venv/` are excluded via [.gitignore](.gitignore).

4. **Run the automated `pytest` suite (`52/52` passing):**
   ```bash
   python -m pytest -v
   ```

5. **Start the single-server FastAPI app (`http://localhost:8000`):**
   ```bash
   # Recommended for Cloudflare Tunnel & live classroom demo (no mid-request reload drops):
   python -m uvicorn src.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="*"
   ```

### 4.2 Live Classroom Exposure via Cloudflare Tunnel (`cloudflared`)

Mobile browsers require **HTTPS** for `navigator.geolocation.getCurrentPosition()` (GPS Geofencing). Expose port `8000` with one command:

```bash
cloudflared tunnel --url http://localhost:8000
```

Open the generated `https://<random>.trycloudflare.com` URL on the classroom projector. FastAPI automatically detects `X-Forwarded-Proto` / `X-Forwarded-Host` so the static session QR embeds the live HTTPS tunnel URL.

### 4.3 Cloud Deployment on Render (`render.yaml` & `Procfile`)

1. Connect the repository on [Render](https://render.com) using [render.yaml](render.yaml) and [Procfile](Procfile).
2. Add `serviceAccountKey.json` as a Render Secret File at `/etc/secrets/serviceAccountKey.json` and set `FIREBASE_CREDENTIALS_PATH=/etc/secrets/serviceAccountKey.json`.
3. Render automatically provisions HTTPS and health-checks `GET /api/health`.

---

## 5. 10-Minute Live Classroom Demo Plan (To-Do #11)

| Time | Actor & Screen | Action to Perform | Key Engineering Highlights |
| :--- | :--- | :--- | :--- |
| **00:00 – 01:30** | **Lecturer** (Projector) | Open `https://<tunnel-url>/#/lecturer`, sign in (`adminL@gmail.com` / `12345`), and view the **Overview** tab (or click **"Sync from Course Excel"**). | **104 Hebrew student records** from `courseid_130158_participants.xlsx` are auto-seeded into Firebase RTDB without ID numbers; the **2 empty/NaN group rows** (`בועז בן משה` & `נדב סודרי`) normalize cleanly with zero crashes. |
| **01:30 – 04:00** | **Lecturer & Students** (Projector + Phones) | Switch to **Attendance**, place the pin on the **Leaflet.js + OSM Geofence Map**, select **`30m` / `50m` / `100m`**, click **"Start attendance session"**, and open **Projector mode**. Students scan the QR on their phones. | **(1)** Clean static session QR valid while `LIVE` (invalidated on close); **(2)** **Haversine GPS Geofencing** checks phone distance; **(3)** Immediate 2nd check-in on the same phone triggers the **15-minute cooldown (`MM:SS`)**; **(4)** Lecturer clicks **"Reset 15-Min Device Cooldowns"** to unblock instantly; **(5)** Toggle **Enforce GPS Geofencing OFF** live as indoor fallback. |
| **04:00 – 05:30** | **Lecturer & Students** | In **Roster**, select the lesson, click **"Upload classroom photo"** (view persisted YOLO boxes + **Reset YOLO**), toggle **Mark Present / Absent**, export **CSV/Excel**, and view **My Attendance History** on a student phone. | Local **YOLOv8n (`imgsz=1280`)** counts physical students privately on-device, persists annotated photos per lesson, flags **GREEN / ORANGE / RED** discrepancies, and Student Portal displays per-lesson attendance rate. |
| **05:30 – 07:00** | **Lecturer & Students** | In **Polls**, launch a live question; students vote, switch options, and unvote. In **Comments**, post an **Anonymous Public** comment and a **Private (Lecturer-Only)** comment; Lecturer replies. | Animated poll bars update immediately on vote/switch/unvote; private comments are isolated from other students; anonymous identities are masked at the API tier. |
| **07:00 – 08:30** | **Lecturer** (Projector) | Open **Video AI**, enter a Gemini key (`gemini-3.5-flash-lite` or `gemini-3.8-flash`), paste a **public YouTube lecture URL**, click **`Analyze YouTube Video`**, then test **Copy** (Markdown) and **Delete**. | Strips playlist params, fetches real Hebrew/English title via oEmbed, summarizes 45–90 min lectures in seconds via `youtube-transcript-api` (falling back to `fps=0.1` video), displays rich academic sections + clickable YouTube footer link. |
| **08:30 – 10:00** | **Lecturer & Students** | Open **Homework**, create an assignment, have 2 Students enter their own BYOK key (`localStorage`) and submit a file or **Public GitHub Repo URL** (observing the inline loading card). Show reviews are **LOCKED**, click **"End Submission Now"**, and inspect the two-column **Review Card + Peer Comparison**. | Proves pure BYOK (`X-Gemini-Api-Key`), **Gated Release Rule**, **First-Submission Edge Case**, automatic `503`/`429`/`404` model fallback, and concrete **Standing vs. Peers / Missing vs. Peers / Strengths vs. Peers** insights. |

---

## 6. Conclusion & Results Summary (To-Do #12)

Combining **Deep OOD planning** (4-tier architecture, 13 cohesive OOP classes, deterministic `InMemoryFirebaseAdapter` testing) with **Vibe Coding & real-world classroom testing** produced a reliable, zero-build, single-server platform that passes **52/52 automated `pytest` tests** with **100% diagram-to-code parity**. Real-life testing drove our strongest pivots — replacing External IP checks with Haversine GPS geofencing, replacing rotating QR tokens with clean static session QR + geofence/cooldown protection, and replacing heavy MP4 uploads with our fast YouTube transcript + low-FPS pipeline. See [docs/07_conclusion.md](docs/07_conclusion.md) for the complete analysis and reflection.
