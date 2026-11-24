# Web-Based AI Teaching Assistant

> **Ex2: Vibe Coding & Deep Designing — Software Engineering (OOP/OOD)**  
> **Team:** Ilay (`IlayOr-Chen`) & Roie (`RoieBenyamin`)

---

## 1. Problem Statement

Modern university lectures face persistent operational and pedagogical bottlenecks during live classroom sessions:

1. **Unreliable & Slow Attendance Tracking:** Manual roll calls waste valuable lecture time, while static QR codes or shared links are easily forwarded to absent friends outside the classroom or used to check in multiple names from a single smartphone.
2. **Passive Classroom Feedback:** Students often hesitate to raise concerns publicly during class, and lecturers lack a lightweight, structured channel for lesson-tagged public/private feedback and instant comprehension checks.
3. **Verbose, Unfocused Lecture Summaries:** Standard automated meeting recaps (such as raw Zoom auto-summaries) produce dense paragraphs of mixed notes without actionable delivery feedback on pacing, clarity, or audio quality.
4. **Isolated Homework Grading Without Peer Context:** Traditional homework feedback evaluates submissions in isolation and leaks early hints if released before the submission deadline closes.

---

## 2. Project Scope & Core Features (KISS + Deep OOD)

This platform provides a self-contained, mobile-first **Web-Based AI Teaching Assistant** engineered with a clean Object-Oriented Python (`FastAPI`) backend, **Firebase Realtime Database (RTDB)** persistence, local **Ultralytics YOLO** computer vision, and **Google Gemini** multimodal analysis.

### In-Scope Features
* **Role-Based Authentication & Excel Roster Management:**
  * Protected Lecturer registration/login (`LECTURER_ACCESS_CODE` + password hashing + signed HTTP-only JWT cookie).
  * Frictionless Student roster selection and session binding.
  * Direct import of the course Excel roster (`/assets/courseid_130158_participants.xlsx`, 104 students across Hebrew columns `שם פרטי`, `שם משפחה`, `דוא"ל`, `קבוצות`, gracefully handling empty/NaN group cells) with manual attendance overrides and CSV/XLSX export.
* **Feature 1 — Smart Multi-Layer Attendance (Core Feature):**
  * **Rotating Animated QR Code:** Cryptographically signed short-lived tokens rotating every ~8 seconds on the UI with a short server-side grace window (~15 seconds total token validity) so students scanning right before rotation are never unfairly rejected.
  * **GPS Geofencing via Browser Geolocation & Haversine Distance (Real-World Pivot from External IP Matching):**
    * *Architectural Rationale:* University Wi-Fi networks use multi-IP NAT / load-balancing pools and many students in class rely on cellular 4G/5G, which would cause false blocks under an External IP comparison. We replaced IP matching with **Browser Geolocation GPS Geofencing** using the **Haversine great-circle distance formula** (`calculate_distance_meters(lat1, lon1, lat2, lon2)`).
    * *How it works:* The Lecturer captures classroom coordinates via Browser GPS (or enters manual fallback coordinates) and sets an allowed radius (`geofence_radius_meters`, default `100m`, configurable `30m`–`500m` for indoor GPS drift). Student check-ins must be within the allowed radius (`distance <= geofence_radius_meters`), with a live Lecturer toggle (`enforce_geofence`) fallback if a student denies location permission. Note: Browser Geolocation requires HTTPS (automatically provided by Cloudflare Tunnel and Render) or `localhost`.
  * **15-Minute Device Cooldown:** Dual enforcement via signed HTTP cookies and Firebase RTDB (`/device_cooldowns`) preventing a single device from checking in multiple students.
  * **Local YOLO Classroom Headcount:** Counts physical `person` detections on uploaded classroom photos (tested on `/assets/p3.jpeg`), generates an annotated bounding-box image, and computes a **GREEN / ORANGE / RED** confidence & discrepancy status against self-registered attendance.
* **Feature 2 — Threaded Comments & Suggestions Tab:**
  * Supports `Public (Everyone)` vs. `Private (Lecturer Only)` visibility, `With Name` vs. `Anonymous` identity, `Lesson X` vs. `General` scope tags, and threaded replies.
* **Feature 3 — On-the-Spot KISS Polls Tab:**
  * Instant multiple-choice poll creation, single-vote enforcement per student, open/close controls, and animated live percentage bars.
* **Feature 4 — ML Lecture Video Summary (MP4 + Gemini):**
  * Strict `.mp4` validation, configurable lecturer Gemini API key and model ID (defaulting to `gemini-2.5-flash-lite`), producing concise structured teaching and delivery insights.
* **Feature 5 — Gated PDF Homework Review & Peer Comparison (PDF + Gemini):**
  * Strict `.pdf` validation, deadline + one-click *"End Submission Now"* release gate, structured AI review card, dedicated **Peer Comparison** column across all submitted PDFs, and explicit **First-Submission Edge Case** notice when only one submission exists.

### Out-of-Scope Constraints
* No integration with Moodle, university SSO portals, or external Zoom REST APIs. Every component is self-contained and live-demoable in a 10-minute classroom presentation.

---

## 3. Repository Structure

```text
AI-Teaching-Assistant-Web/
├── assets/
│   ├── courseid_130158_participants.xlsx   # Real 104-student course roster (Hebrew columns)
│   ├── Example_ Class  by zoom).docx       # Baseline Zoom auto-summary for comparison
│   └── p3.jpeg                             # Real classroom lecture hall photo for YOLO testing
├── docs/                                   # Full Software Engineering documentation suite
├── src/
│   ├── __init__.py
│   ├── config.py                           # AppConfig environment & runtime settings
│   ├── main.py                             # FastAPI application factory & HTTP endpoints
│   ├── repository/
│   │   ├── __init__.py
│   │   └── firebase_repository.py          # FirebaseRepository + InMemoryFirebaseAdapter
│   ├── services/                           # Domain OOP service layer
│   ├── static/                             # Responsive Vanilla HTML/CSS/JS frontend
│   └── utils/
│       ├── __init__.py
│       └── ip_resolver.py                  # ClientIPResolver (Cloudflare/Render/Local priority)
├── tests/
│   └── test_sanity_and_repository.py       # Automated pytest unit & integration tests
├── .env.example                            # Template for environment variables
├── .gitignore
├── requirements.txt
└── README.md
```

---

## 4. Environment & Firebase Setup

1. **Create and activate a local Python virtual environment (`venv`):**

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

2. **Install Python dependencies inside `venv`:**
   ```bash
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

3. **Create your `.env` file:**
   ```bash
   cp .env.example .env
   ```

4. **Configure Firebase Realtime Database (`serviceAccountKey.json`):**
   * Target RTDB URL: `https://ai-teaching-assistant-web-default-rtdb.firebaseio.com/`
   * Place your Firebase Admin SDK private key file named `serviceAccountKey.json` in the project root directory (matching `FIREBASE_CREDENTIALS_PATH=serviceAccountKey.json` in `.env`).
   * `.env`, `serviceAccountKey.json`, `venv/`, and `.venv/` are excluded via `.gitignore` and must **never** be committed to Git.
   * All automated `pytest` tests use `InMemoryFirebaseAdapter` (`use_mock=True`) so test runs are 100% deterministic and never pollute production Firebase data.

5. **Run the automated test suite (using `venv`):**
   ```bash
   python -m pytest -v
   ```

6. **Start the FastAPI development server (using `venv`):**
   ```bash
   python -m uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
   ```
