# Technical Tools Report & Real-Life Empirical Benchmarks

> **Project:** Web-Based AI Teaching Assistant (Ex2: Vibe Coding & Deep Designing)  
> **Team:** Ilay (`IlayOr-Chen`) & Roie (`RoieBenyamin`)

---

## 1. Executive Summary

Before implementing the Web-Based AI Teaching Assistant, we evaluated candidate technologies across every architectural tier and feature requirement against four engineering criteria:
1. **KISS & Deep OOD Modularity:** Clean separation of concerns without bloated build pipelines or fragile external dependencies.
2. **10-Minute Live Classroom Demo Reliability:** Sub-second responsiveness on student smartphones and the lecturer's projector screen over Cloudflare Tunnel or Render HTTPS.
3. **Real-Life Empirical Accuracy on Course Assets:** Verified directly on `/assets/p3.jpeg` (`2000 x 1500` classroom photo), `/assets/courseid_130158_participants.xlsx` (`104`-student Hebrew course roster), and `/assets/Example_ Class  by zoom).docx` (`01:33:52` raw Zoom AI Companion summary).
4. **Deterministic Automated Testing (`pytest`):** Full unit and integration testability inside a local `./venv` with zero production database side effects.

---

## 2. Core Stack & Persistence Tool Comparisons

### 2.1 Web Backend & Frontend Architecture
| Candidate Tool | Strengths | Weaknesses / Risks for Live Classroom Demo | Decision |
| :--- | :--- | :--- | :--- |
| **FastAPI + Vanilla HTML5/CSS3/ES-Modules** | Native async REST endpoints, automatic Pydantic validation, full control over HTTP-only JWT/device cookies, 60fps SVG/CSS animations, zero Node/Webpack build step. | Requires writing clean modular Vanilla JS (`app.js`, `lecturer.js`, `student.js`, `polls_comments.js`, `ai_tools.js`). | **SELECTED** |
| **Streamlit** | Fast Python prototyping for single-user data dashboards. | Full-page script reruns on every interaction break 60fps rotating QR countdown rings; poor multi-user mobile cookie handling and limited custom mobile UX. | Rejected |
| **Flask + React / Vite SPA** | Rich component ecosystem. | Separate Node.js build pipeline and CORS/proxy complexity violate the KISS single-process deployment goal. | Rejected |

### 2.2 Database & Automated Testing Isolation
| Candidate Tool | Strengths | Weaknesses / Risks | Decision |
| :--- | :--- | :--- | :--- |
| **Firebase Realtime Database (RTDB) + `InMemoryFirebaseAdapter`** | Cloud-hosted hierarchical JSON tree (`https://ai-teaching-assistant-web-default-rtdb.firebaseio.com/`) survives Render container restarts; paired with our `InMemoryFirebaseAdapter` (`use_mock=True`), all 43 `pytest` tests run deterministically in memory without polluting live data. | Requires hierarchical path normalization (`/users/lecturers`, `/students`, `/attendance_sessions`, `/device_cooldowns`, `/comments`, `/polls`, `/homework_assignments`). | **SELECTED** |
| **Local SQLite** | Zero external setup. | Ephemeral filesystems on cloud PaaS (Render free tier) wipe local `.db` files on redeploy. | Rejected |

---

## 3. Real-Life Benchmark 1 — Excel Class Roster Import (`/assets/courseid_130158_participants.xlsx`)

### 3.1 Candidate Libraries
* **`openpyxl` (Selected):** Pure-Python `.xlsx` reader/writer with fine-grained cell-type inspection and zero C-compiler overhead on Python 3.15.
* **`pandas`:** Heavy dependency footprint (~100 MB with dependencies) for reading a 104-row table; introduces `float('nan')` type coercion pitfalls.

### 3.2 Real-Life Test Results on `/assets/courseid_130158_participants.xlsx`
We executed `RosterImporter.import_from_excel("assets/courseid_130158_participants.xlsx")` inside `./venv`:

| Metric / Edge Case Inspected | Empirical Result on `courseid_130158_participants.xlsx` | How `RosterImporter` Handles It |
| :--- | :--- | :--- |
| **File Size & Worksheet Dimensions** | `9,846 bytes` · `105` total rows (`1` Hebrew header row + `104` participant rows) | Parsed in `< 20 ms` via `openpyxl.load_workbook(..., data_only=True)`. |
| **Hebrew Column Headers** | `('שם פרטי', 'שם משפחה', 'דוא"ל', 'קבוצות')` | `_resolve_column_indices()` maps both Hebrew headers and English equivalents (`First Name`, `Last Name`, `Email`, `Group`), concatenating `שם פרטי` + `שם משפחה` into `full_name` (e.g., `גיא אביב`). |
| **Float Group Numbers (`3.0`)** | `102` rows store group numbers as IEEE-754 floats (e.g., `3.0` for `guygg197@gmail.com`) | `RosterImporter.normalize_group(3.0)` detects `float.is_integer()` and normalizes `3.0` -> `"3"`. |
| **Empty / `NaN` Group Cells (Edge Case)** | **Exactly `2` rows** have empty `קבוצות` (`None` / `NaN`):<br/>• **Row 21:** `בועז בן משה` (`benmo@ariel.ac.il`, `None`)<br/>• **Row 75:** `נדב סודרי` (`hpcity123@gmail.com`, `None`) | `RosterImporter.normalize_group()` converts `None`, `float('nan')`, and `"NaN"` to `""` (`Unassigned` in UI/exports) with **0 crashes**, reporting `total_imported: 104, unassigned_group_count: 2`. |

---

## 4. Real-Life Pivot — Physical Presence Verification (External IP Check vs. GPS Geofencing)

### 4.1 Why External IP Same-Network Validation Was Replaced (Checkpoint 3.5 — Issue #13)
We initially implemented External IP matching (`CF-Connecting-IP` / `X-Forwarded-For`). However, real-world classroom testing revealed two critical failure modes:
1. **Campus Wi-Fi Multi-IP NAT & Load Balancing:** University enterprise Wi-Fi networks route outbound traffic across pools of public NAT egress IPs. A student and lecturer connected to the exact same classroom Wi-Fi SSID can appear under different public egress IPs.
2. **Cellular 4G/5G Smartphones in Class:** Many students scan the projector QR code using mobile data (`4G/5G`), which never matches the university's broadband IP.

### 4.2 Selected Solution: Browser Geolocation API + Haversine Great-Circle Distance (`AttendanceManager`)
We refactored `AttendanceManager` in **Checkpoint 3.5** to verify physical proximity using **Browser Geolocation (`navigator.geolocation.getCurrentPosition`)** and the **Haversine formula** (`calculate_distance_meters(lat1, lon1, lat2, lon2)`):
* **Network-Agnostic:** Works identically whether the student is on campus Wi-Fi, eduroam, or 4G/5G cellular data.
* **Indoor Drift Resilience & Fallbacks:** Defaults to a configurable `100m` radius (`30m`–`500m`), includes manual classroom coordinate inputs (with a one-click Ariel University preset `32.104400, 35.207800` if laptop GPS is blocked indoors), and provides a live **"Enforce GPS Geofencing"** lecturer toggle fallback if a student denies location permission.
* **Rotating QR Timing (`8s` UI + `7s` Grace = `15s` Validity) & 15-Minute Cooldown:** The QR code rotates every `8` seconds on the projector screen while accepting tokens for `15` seconds server-side, paired with our HMAC-signed `ta_device_id` cookie and Firebase RTDB `/device_cooldowns` 15-minute lock to prevent buddy-punching.

---

## 5. Real-Life Benchmark 2 — Classroom Student Headcount on `/assets/p3.jpeg`

### 5.1 Candidate Computer Vision Tools
| Candidate Tool | Pros | Cons | Decision |
| :--- | :--- | :--- | :--- |
| **Local Ultralytics YOLOv8n (`yolov8n.pt`, COCO `person` class `0`)** | 100% local & privacy-preserving (no student faces uploaded to third-party cloud APIs), deterministic bounding-box coordinates and per-box confidence scores (`0.0`..`1.0`), zero API cost. | Default `imgsz=640` downscales wide lecture hall photos and misses small occluded back-row students; requires `imgsz=1280` tuning. | **SELECTED (`imgsz=1280`)** |
| **Cloud Vision LLM Headcount** | Can describe scene context qualitatively. | Non-deterministic counts across runs, lacks precise per-person confidence scores, incurs API latency/cost, and uploads student photos externally. | Rejected |
| **OpenCV Haar / HOG Cascades** | Lightweight dependency. | High false-negative rate on seated, partially occluded students behind laptops. | Rejected |

### 5.2 Empirical YOLOv8n Results on `/assets/p3.jpeg` (`2000 x 1500` px, `315,499` bytes)
Visual inspection of `/assets/p3.jpeg` shows a real university lecture hall photographed from the lecturer's podium: students seated across 4 tiered rows behind laptops, plus the lecturer's reflection/webcam preview on the podium laptop screen in the foreground (`22–23` visible human figures).

We benchmarked `yolov8n.pt` (`classes=[0]`) across resolution (`imgsz=640` vs. `imgsz=1280`) and confidence thresholds (`0.20`, `0.25`, `0.30`) inside `./venv`:

| Inference Resolution (`imgsz`) | Confidence Threshold (`conf`) | Detected `person` Count | Average Confidence (`avg_conf`) | High-Conf Boxes (`>= 0.60`) | Medium-Conf Boxes (`0.45..0.59`) | Low-Conf Boxes (`< 0.45`, Back Row) | Min / Max Box Confidence |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `640` (YOLO default) | `0.30` | **11** | `0.524` | `5` | `1` | `5` | `0.342` / `0.760` |
| `640` (YOLO default) | `0.25` | **12** | `0.502` | `5` | `1` | `6` | `0.254` / `0.760` |
| **`1280` (Selected Production)** | **`0.30`** | **22** | **`0.634`** | **`15`** | **`3`** | **`4`** | **`0.325` / `0.844`** |
| `1280` | `0.25` | **22** | `0.634` | `15` | `3` | `4` | `0.325` / `0.844` |
| `1280` | `0.20` | **23** | `0.615` | `15` | `3` | `5` | `0.213` / `0.844` |

### 5.3 Engineering Takeaways from `/assets/p3.jpeg`
1. **Why `imgsz=1280` Doubles Detection Recall (`11` -> `22` persons):** At `2000 x 1500` resolution, students sitting in rows 3 and 4 occupy only `35–55` pixels vertically and are partially occluded from the chest down by desks and open laptop lids. Downscaling to `640` collapses back-row students below the receptive field of YOLOv8n (`11` detections). Running at `imgsz=1280, conf=0.30` detects **22** persons and raises average confidence from `0.524` to **`0.634`**.
2. **Color-Coded Lecturer Confidence Indicator (`GREEN` / `ORANGE` / `RED`):** Because back-row occlusion (`4` boxes below `0.45` confidence) and podium-screen reflections can create a 1–3 person variance in large halls, `PhotoHeadCounter.evaluate_confidence_indicator()` gives the Lecturer an instant traffic-light assessment:
   * **GREEN:** `average_confidence >= 0.60`, low-conf ratio `<= 0.25`, and `abs(detected - registered) <= 1` (e.g., `22` detected vs. `22` registered on `p3.jpeg`).
   * **ORANGE:** Small count discrepancy (`2..3` students) or `> 25%` low-confidence occluded back-row detections.
   * **RED:** Large count discrepancy (`>= 4` students, e.g., `22` detected vs. `15` registered), `average_confidence < 0.45`, or `0` detections — prompting the Lecturer to review manually.

---

## 6. Real-Life Benchmark 3 — Lecture Video Summary vs. Raw Zoom Auto-Summary (`/assets/Example_ Class  by zoom).docx`)

### 6.1 Analysis of the Baseline Zoom Auto-Summary (`Example_ Class  by zoom).docx`)
We inspected `/assets/Example_ Class  by zoom).docx` (`14,837 bytes`, recording duration `01:33:52`), which contains **`28` paragraphs (`695` words, `4,354` characters)** generated by Zoom's built-in AI Companion. We identified three concrete pedagogical shortcomings in the Zoom output:
1. **Transcription Hallucinations & Inconsistent Name Spellings:** Across consecutive paragraphs, Zoom's Hebrew ASR mistranslates the lecturer's name (*Boaz* / `בועז`) into three different tokens: `"Bez"`, `"בז"`, and `"בעז"`.
2. **Dense, Repetitive Narrative Walls:** The document repeats the same description of Task 2 (attendance, comments, polls, lecture analysis, homework text comparison) and Task 4 (autonomous car control loop) three times across *Quick recap*, *Next steps*, and *Summary* without isolating concise student learning takeaways.
3. **Zero Lecturer Delivery Insights:** The Zoom summary provides no feedback to the lecturer on **speaking pace**, **conceptual clarity**, or **microphone/audio quality**.

### 6.2 Comparison: Zoom AI Companion vs. Our `VideoSummarizer` (MP4 + Configurable `GeminiClient`)
| Dimension | Raw Zoom Auto-Summary (`Example_ Class  by zoom).docx`) | Our `VideoSummarizer` (`src/services/video_summarizer.py`) |
| :--- | :--- | :--- |
| **Input Validation** | Tied strictly to Zoom cloud recordings. | Accepts **either** public YouTube URLs (analyzed directly via `types.Part.from_uri` with zero local download) **or** standard `.mp4` (`video/mp4`, `<= 20 MB`) files directly from the Lecturer Dashboard. |
| **Model & Auth Flexibility** | Fixed proprietary Zoom backend. | Configurable Lecturer Gemini API Key (`AIza...` AI Studio or `AQ....` Vertex AI Express) & free-text Model ID (`/settings/gemini`), defaulting to **`gemini-3.5-flash-lite`** with presets `gemini-3.5-flash` and `gemini-2.5-flash`. |
| **Structure & Conciseness** | `28` paragraphs (`695` words) of repetitive prose with `"Bez" / "בז" / "בעז"` ASR artifacts. | Crisp JSON card divided into **Executive Summary** (2–3 sentences), **Key Topics** (3–5 bullets), **Core Takeaways** (3–5 bullets), and **Action Items**. |
| **Lecturer Delivery Feedback** | **None.** | Dedicated **Lecturer Delivery Insights** card evaluating **Pacing**, **Clarity**, **Audio Quality**, and **Overall Rating**. |

---

## 7. Feature 5 Tool Selection — PDF Homework Extraction & Gated Peer Comparison (`HomeworkReviewer`)

| Component | Selected Tool | Rationale & Real-Life Edge-Case Handling |
| :--- | :--- | :--- |
| **PDF Validation & Text Extraction** | `pypdf` (`PdfReader`) + `%PDF` header & `.pdf` extension check | Pure-Python PDF parser that extracts page count, word count, and text cleanly in memory without external binaries (`poppler`/`tesseract`). Non-PDF uploads (`.docx`, `.txt`) are rejected with HTTP `400`. |
| **Anti-Leak Gated Release Engine** | `HomeworkReviewer.is_review_released()` | Prevents students from gaming or copying AI feedback while submissions are open: `review` and `peer_comparison` remain strictly `None` until `now_ts >= deadline_ts` OR the Lecturer clicks **"End Submission Now"**. |
| **Cohort Peer Comparison & First-Submission Edge Case** | `HomeworkReviewer._generate_peer_comparison()` + `GeminiClient` | Compares each student's extracted PDF against all peer PDFs (`Relative Strengths`, `Unique Ideas`, `Common Gaps`). When `total_submissions == 1`, deterministically outputs the exact **First-Submission Edge Case** notice instead of hallucinating non-existent peers. |
