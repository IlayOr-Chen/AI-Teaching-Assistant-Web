# Technical Tools Report & Real-Life Empirical Benchmarks (To-Do #3)

> **Project:** Web-Based AI Teaching Assistant (Ex2: Vibe Coding & Deep Designing)  
> **Team:** Ilay (`IlayOr-Chen`) & Roie (`RoieBenyamin`)

---

## 1. Executive Summary

Before and during implementation of the Web-Based AI Teaching Assistant, we empirically tested candidate technologies across every architectural tier against four engineering criteria:
1. **KISS & Deep OOD Modularity:** Clean separation of concerns in a single-server architecture with zero bloated frontend build pipelines.
2. **10-Minute Live Classroom Demo Reliability:** Sub-second responsiveness on student smartphones and the lecturer's projector screen over Cloudflare Tunnel or Render HTTPS.
3. **Real-Life Empirical Validation on Course Data:** Benchmarked directly on the real 104-student Hebrew course Excel roster, real university lecture-hall photos, real 45–90 minute YouTube lectures, and Zoom's AI Companion auto-summary baseline.
4. **Deterministic Automated Testing (`pytest`):** Full unit and integration testability inside a local `./venv` (`52/52` passing tests) with zero production Firebase side effects.

---

## 2. Core Stack, Persistence & Exposure Tool Comparisons

### 2.1 Web Backend & Frontend Architecture
| Candidate Tool | Strengths | Weaknesses / Risks for Live Classroom Demo | Decision |
| :--- | :--- | :--- | :--- |
| **FastAPI + Vanilla HTML5/CSS3/ES-Modules** | Native async REST API, automatic Pydantic validation, `run_in_threadpool` offloading for blocking AI/YOLO calls, HTTP-only JWT/device cookies, zero Node/Webpack build step, single-URL serving on port `8000`. | Requires disciplined modular JS architecture (`app.js`, `lecturer.js`, `student.js`, `polls_comments.js`, `ai_tools.js`, `ui.js`, `api.js`). | **SELECTED** |
| **Streamlit** | Fast Python prototyping for single-user data dashboards. | Full-page script reruns on every click break multi-user mobile UX, Leaflet map state, and HTTP-only device cooldown cookies. | Rejected |
| **Flask + React / Vite SPA** | Rich component ecosystem. | Separate Node.js build pipeline, CORS configuration, and two-process hosting violate KISS single-server deployment. | Rejected |

### 2.2 Database & Deterministic Test Isolation
| Candidate Tool | Strengths | Weaknesses / Risks | Decision |
| :--- | :--- | :--- | :--- |
| **Firebase Realtime Database (RTDB) + `InMemoryFirebaseAdapter`** | Cloud-hosted hierarchical JSON tree (`https://ai-teaching-assistant-web-default-rtdb.firebaseio.com/`) persists cleanly across Render container restarts; paired with our thread-safe `InMemoryFirebaseAdapter` (`use_mock=True`), all **52 `pytest` tests** run deterministically in memory without touching production data. | Requires Firebase-safe key sanitization (replacing `.`, `$`, `#`, `[`, `]`, `/`). | **SELECTED** |
| **Local SQLite** | Zero external setup. | Ephemeral filesystems on cloud PaaS (Render free tier) wipe local `.db` files on redeploy. | Rejected |

### 2.3 Classroom HTTPS Exposure & Deployment
| Tool | Role | Empirical Observation |
| :--- | :--- | :--- |
| **Cloudflare Tunnel (`cloudflared`)** | Instant public HTTPS URL (`https://*.trycloudflare.com`) pointing at `localhost:8000` for live classroom demos. | Required because mobile browsers restrict `navigator.geolocation` (GPS Geofencing) to HTTPS origins. Running `uvicorn` without `--reload` (or scoped with `--reload-dir src`) prevents mid-request restarts and `502` HTML gateway drops. |
| **Render (`render.yaml` & `Procfile`)** | Zero-touch PaaS deployment with managed HTTPS and `/api/health` checks. | Stores `serviceAccountKey.json` safely as a Secret File (`/etc/secrets/serviceAccountKey.json`); no `.env` Gemini key required thanks to pure BYOK. |

---

## 3. Real-Life Benchmark 1 — Excel Class Roster Import (`courseid_130158_participants.xlsx`)

### 3.1 Library Selection
* **`openpyxl` (Selected):** Pure-Python `.xlsx` reader/writer with exact cell-type inspection, UTF-8 Hebrew support, and zero C-compiler overhead on Python 3.15.
* **`pandas` (Rejected):** Heavy ~100 MB dependency footprint for a 104-row table with implicit float/NaN type-coercion pitfalls.

### 3.2 Real-Life Test Results on `assets/courseid_130158_participants.xlsx`
| Metric / Edge Case Inspected | Empirical Result | How `RosterImporter` Handles It |
| :--- | :--- | :--- |
| **Worksheet Dimensions** | `9,846 bytes` · `105` total rows (`1` Hebrew header row + `104` students) | Parsed in `< 20 ms` via `openpyxl.load_workbook(..., data_only=True)` and auto-seeded on startup if `/students` is empty. |
| **Hebrew Column Headers (No ID#)** | `('שם פרטי', 'שם משפחה', 'דוא"ל', 'קבוצות')` | `_resolve_column_indices()` maps Hebrew and English headers, concatenating `שם פרטי` + `שם משפחה` into `full_name` (e.g., `גיא אביב`). Respects privacy constraint: zero student ID numbers required. |
| **Float Group Numbers (`3.0`)** | `102` rows store group numbers as IEEE-754 floats (e.g., `3.0`) | `normalize_group(3.0)` checks `float.is_integer()` and converts `3.0` -> `"3"`. |
| **Empty / `NaN` Group Cells** | **Exactly `2` rows** have empty `קבוצות`: Row 21 (`בועז בן משה`) & Row 75 (`נדב סודרי`) | `normalize_group()` converts `None`, `float('nan')`, and `"NaN"` to `""` (`Unassigned` in UI/exports) with **0 crashes**, reporting `total_imported: 104, unassigned_group_count: 2`. |

---

## 4. Real-Life Architectural Pivots — Smart Attendance (QR + GPS Geofencing + Cooldown)

### 4.1 Pivot 1: External IP Matching -> Interactive Leaflet.js + OSM GPS Geofencing
Real-world classroom testing revealed two failure modes in External IP matching:
1. **Campus Wi-Fi Multi-IP NAT Pools:** University Wi-Fi routes outbound requests through multiple public egress IPs, causing false rejections for students sitting in the same hall.
2. **Cellular 4G/5G Smartphones:** Students scanning on mobile data never share the university's broadband egress IP.

**Selected Solution (`Leaflet.js` + `OpenStreetMap` + Haversine `calculate_distance_meters`):**
* Network-agnostic verification using `navigator.geolocation.getCurrentPosition()` and the Haversine great-circle formula.
* Interactive Leaflet.js map lets the lecturer click **"Use My Current Location"**, drag the pin, or tap the Ariel preset, and choose **`30m` / `50m` / `100m`** radius pills with a live translucent circle.
* Includes a live **"Enforce GPS Geofencing"** lecturer toggle fallback if a student denies browser location permissions indoors.

### 4.2 Pivot 2: Rotating 8-Second QR -> Clean Static Session QR + Geofence + 15-Min Cooldown
During classroom tests, fast-rotating JWT QR codes produced dense high-version QR matrices that were hard to scan from the back row and frequently expired mid-camera-focus.

**Selected Solution (`AttendanceManager`):**
* Generates **one short 16-character URL-safe static session token** (`secrets.token_urlsafe(12)`) when a lesson opens — producing a crisp, low-density QR code valid for the entire duration the session is `LIVE`, and invalidated immediately upon session close.
* Physical presence and anti-buddy-punching are enforced by the combination of **Haversine GPS Geofencing (`30m`/`50m`/`100m`)** + **15-minute signed HTTP-only cookie (`ta_device_id`) & Firebase `/device_cooldowns` lock** (plus one-click Lecturer **"Reset 15-Min Device Cooldowns"**).

---

## 5. Real-Life Benchmark 2 — Local Classroom Headcount (`Ultralytics YOLOv8n`)

### 5.1 Candidate Computer Vision Tools
| Candidate Tool | Pros | Cons | Decision |
| :--- | :--- | :--- | :--- |
| **Local Ultralytics YOLOv8n (`yolov8n.pt`, COCO class `0` `person`)** | 100% local & privacy-preserving (no classroom photos sent to third-party cloud APIs), deterministic bounding-box coordinates & confidence scores, zero API cost. | Default `imgsz=640` misses small occluded back-row students; requires `imgsz=1280` tuning. | **SELECTED (`imgsz=1280`)** |
| **Cloud Vision LLM Headcount** | Qualitative scene description. | Non-deterministic counts, higher latency/cost, and uploads student photos externally. | Rejected |
| **OpenCV Haar / HOG Cascades** | Lightweight dependency. | High false-negative rate on seated students occluded behind desks and laptop lids. | Rejected |

### 5.2 Empirical YOLOv8n Tuning Results (2000 × 1500 Lecture Hall Photo)
| Inference Resolution (`imgsz`) | Confidence Threshold (`conf`) | Detected `person` Count | Average Confidence | High-Conf (`>= 0.60`) | Medium-Conf (`0.45..0.59`) | Low-Conf (`< 0.45`, Back Row) |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `640` (default) | `0.30` | `11` | `0.524` | `5` | `1` | `5` |
| `640` (default) | `0.25` | `12` | `0.502` | `5` | `1` | `6` |
| **`1280` (Production)** | **`0.30`** | **`22`** | **`0.634`** | **`15`** | **`3`** | **`4`** |
| `1280` | `0.20` | `23` | `0.615` | `15` | `3` | `5` |

> **Key Takeaway:** Increasing `imgsz` from `640` to **`1280`** doubled back-row recall (`11` -> `22` persons) and boosted average confidence from `0.524` to **`0.634`**. `PhotoHeadCounter` persists the annotated JPEG Data URL per lesson in Firebase RTDB (cleared via **Reset YOLO**) and displays a traffic-light **GREEN / ORANGE / RED** indicator against self-registered attendance.

---

## 6. Real-Life Pivots & Benchmarks — Video AI Summarizer & Gemini Reliability

### 6.1 Baseline Comparison vs. Zoom AI Companion Auto-Summary
Inspection of a real 93-minute Zoom AI Companion lecture summary (`28` paragraphs / `695` words) revealed three major shortcomings:
1. **ASR Name Hallucinations:** The lecturer's Hebrew name (`בועז`) was mistranscribed inconsistently as `"Bez"`, `"בז"`, and `"בעז"`.
2. **Repetitive Prose Walls:** The same assignment overview was repeated three times across *Quick recap*, *Next steps*, and *Summary* without structured definitions or self-check questions.
3. **Zero Lecturer Delivery Insights:** Provided no feedback on speaking pace, clarity, or audio quality.

### 6.2 Real-Life Pivots Made During Gemini API Testing
| Challenge Encountered in Real Testing | Root Cause Discovered | Final Engineering Solution |
| :--- | :--- | :--- |
| **Heavy MP4 Upload Timeouts & Cloudflare 502 Drops** | Uploading large local `.mp4` lecture recordings through Cloudflare Tunnel hit upload limits and 100s gateway timeouts. | Pivoted to a **YouTube-only KISS pipeline** (`VideoSummarizer`): Lecturer pastes any public YouTube URL, we strip `&list=`/`&index=` playlist parameters, and fetch the real UTF-8/Hebrew title via YouTube's public oEmbed API. |
| **`400 INVALID_ARGUMENT: Unknown name "content_layers"`** | `VideoMetadata(content_layers=...)` is rejected by the Gemini Developer API endpoint (`generativelanguage.googleapis.com`). | Removed `content_layers` completely and adopted an **automatic 2-step strategy**: **(1)** fetch the spoken transcript (`he`, `iw`, `en`, manual or auto-captions) via **`youtube-transcript-api`** and summarize 45–90 min lectures in seconds with Gemini; **(2)** if no transcript exists, automatically fall back to direct YouTube URI analysis with **`VideoMetadata(fps=0.1)`** (and plain `Part.from_uri` fallback if `video_metadata` returns HTTP 400). |
| **`403 PERMISSION_DENIED` on `AQ.` Keys & Deprecated `gemini-2.5-flash`** | Routing `AQ.` keys to `vertexai=True` hit disabled Agent Platform endpoints, and Google retired `gemini-2.5-flash` for new keys (`404 NOT_FOUND`). | Standardized ALL keys (`AIza...` and `AQ....`) on the standard Gemini Developer API client (`genai.Client(api_key=key)`), replaced `gemini-2.5-flash` with **`gemini-3.8-flash`**, and added pure BYOK with working **Clear Key** (`DELETE /api/settings/gemini`). |
| **Temporary Gemini `503 UNAVAILABLE` / `429` Spikes** | Live classroom demos cannot fail on a single model's temporary high demand. | Built automatic retry + model fallback in `GeminiClient` across `[selected_model, "gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]` continuing across `503`, `429`, and `404`, surfacing crystal-clear HTTP `503` / `429` JSON error details if all models are busy. |

---

## 7. Feature 5 Tool Selection — Multi-Format Homework Ingestion, BYOK & Rich Peer Comparison

| Component | Selected Tool | Rationale & Real-Life Edge-Case Handling |
| :--- | :--- | :--- |
| **Multi-Format File & Repo Ingestion** | `pypdf` (`PdfReader`), Python `zipfile` + XML `.docx` parser, `httpx` GitHub zipball downloader | Pure-Python ingestion with zero external binaries (`git`, `poppler`, `tesseract`). Supports `.pdf` (including Hebrew/scanned sparse PDFs without HTTP 500), `.docx`, `.zip` (skipping `node_modules/`, `venv/`, `.git/`, `__pycache__/`), source code files, and **Public GitHub Repository URLs** (`https://github.com/owner/repo`). |
| **Pure Per-Student BYOK** | Browser `localStorage` + `X-Gemini-Api-Key` / `X-Gemini-Model` headers | Keeps every student's key private on their own device (never saved on the server; no shared `.env` key required) and returns a clear HTTP `400` prompt if omitted. |
| **Gated Release & Rich Peer Comparison** | `HomeworkReviewer.is_review_released()` + `_generate_peer_comparison()` | Strictly withholds `review` and `peer_comparison` until `now_ts >= deadline_ts` OR the Lecturer clicks **"End Submission Now"**. Once unlocked, outputs **Standing vs. Peers**, **Missing or Underdeveloped Compared to Peers**, **Strengths vs. Class Peers**, **Actionable Improvements**, and **Unique Ideas** — or the explicit **First-Submission Edge Case** notice when `total_submissions == 1`. |
