# Statement of Work (SoW) — Web-Based AI Teaching Assistant (To-Do #4)

> **Course:** Introduction to Software Engineering (OOP / OOD) — **Ex2: Vibe Coding & Deep Designing**, Ariel University  
> **Team:** Ilay (`IlayOr-Chen`) & Roie (`RoieBenyamin`)

---

## 1. Project Objectives

The objective of this project is to design, implement, verify, and demonstrate a self-contained, single-server, mobile-first **Web-Based AI Teaching Assistant** built according to **KISS (Keep It Simple, Stupid)** and **Deep Object-Oriented Design (OOP/OOD)** principles for a **10-minute live classroom demo**.

The platform addresses four core university classroom needs:
1. **Fraud-Resistant, Frictionless Smart Attendance:** Replace slow manual roll calls with multi-lesson attendance sessions protected by a static session QR code, interactive Leaflet/OSM Haversine GPS geofencing (`30m`/`50m`/`100m`), a 15-minute device cooldown (with one-click lecturer reset), local Ultralytics YOLOv8n physical headcount verification, Student Attendance History, and CSV/Excel exports.
2. **Real-Time & Offline Classroom Interaction:** Provide a single mobile web portal for lesson-tagged public/private (lecturer-only) comments with optional anonymity, threaded replies, and instant on-the-spot multiple-choice live polls with mutable student voting.
3. **Fast, Rich AI Lecture Video Summaries:** Convert public YouTube lecture URLs (including 45–90 minute Hebrew/English lectures) into structured academic recaps (TL;DR, narrative walkthrough, key concepts, takeaways, study questions, and lecturer delivery insights) that directly improve upon verbose Zoom auto-summaries.
4. **Fair, Gated Homework Reviews & Cohort Peer Comparison:** Accept multi-format files and public GitHub repository URLs under a pure BYOK model, locking AI reviews until the deadline or Lecturer **"End Submission Now"** trigger, and delivering concrete peer standing and gap analysis.

---

## 2. Scope Boundaries (In-Scope vs. Out-of-Scope)

### 2.1 In-Scope Deliverables
| Feature / Area | Implemented Scope |
| :--- | :--- |
| **Role-Based Auth & Roster** | Restricted Lecturer login configured via `.env` (`adminL@gmail.com` / `12345`, case-insensitive email, PBKDF2-HMAC-SHA256 `200,000` iterations, HTTP-only JWT cookie; public lecturer self-registration disabled with `403`). Frictionless Student roster login by email/name (**no student ID#**). Automatic startup seeding + one-click Excel sync of the 104-student Hebrew course roster (`courseid_130158_participants.xlsx`, safely handling 2 empty/NaN group cells). |
| **Feature 1 — Smart Attendance (Main)** | Multi-lesson session switcher/creator, clean 16-char static session QR with centered Projector Mode, interactive Leaflet.js + OSM GPS Geofence Map (`30m`/`50m`/`100m` pills + Haversine check + live toggle), 15-min signed cookie + Firebase RTDB cooldown with one-click reset, local YOLOv8n (`imgsz=1280`) headcount with persisted annotated photo (`Reset YOLO`) & **GREEN / ORANGE / RED** indicator, manual overrides, Student **My Attendance History**, and UTF-8-BOM `.csv` / `.xlsx` exports. |
| **Feature 2 — Comments & Suggestions** | Persistent Firebase RTDB `/comments` supporting `Public (Everyone)` vs. `Private (Lecturer Only)` visibility isolation, `With Name` vs. `Anonymous` identity masking, `Lesson 1..N` / `General` scope tags & filter, and chronological threaded replies. |
| **Feature 3 — On-the-Spot KISS Polls** | Instant multiple-choice poll creation (`2..8` options), live open/close controls, animated percentage bars, and mutable student voting (cast, switch option, or click selected option again to unvote). |
| **Feature 4 — AI Lecture Video Summarizer** | YouTube-only KISS pipeline: canonical URL parsing (strips `&list=`/`&index=`), public oEmbed real title lookup (UTF-8/Hebrew safe), automatic 2-step strategy (`youtube-transcript-api` primary -> `VideoMetadata(fps=0.1)` fallback), rich academic recap cards (`detailed_overview`, `key_concepts`, `study_questions`, `delivery_insights`), **Copy** (Markdown), **Delete**, and clickable **Watch on YouTube** footer link. |
| **Feature 5 — Deep Homework Review & Peer Comparison** | Multi-format upload (`.pdf`, `.docx`, `.zip`, `.txt`, `.md`, source code) + Public GitHub Repo URL ingestion, pure per-student BYOK (`localStorage` + `X-Gemini-Api-Key` / `X-Gemini-Model`), clear inline submission/review loading states, Gated Release Rule (deadline or **"End Submission Now"**), automatic Gemini `503`/`429`/`404` model fallback (`gemini-3.8-flash`, `gemini-3.5-flash-lite`, `gemini-3.5-flash`), and rich Peer Comparison (`Standing vs. Peers`, `Missing Compared to Peers`, `Strengths vs. Peers`, `Actionable Improvements`) + First-Submission Edge Case handling. |

### 2.2 Out-of-Scope Constraints
* **No Moodle / University LMS Integration:** Rosters and homework submissions are handled directly inside the app without LMS OAuth/LTI.
* **No External Zoom REST API Integration:** Video AI operates cleanly on public YouTube lecture links.
* **No Student ID Numbers or Sensitive Personal Info:** Roster and authentication use only student names, emails, and optional group numbers.
* **No Facial Recognition or Biometrics:** Local YOLOv8n counts anonymous `person` bounding boxes only.
* **No Native Mobile App Installation:** 100% browser-based mobile-first web app accessed via smartphone QR scan.

---

## 3. Team Roles, Milestones & Delivery Status

| Milestone | Primary Owner | GitHub Issue | Deliverable Summary | Status |
| :--- | :--- | :---: | :--- | :---: |
| **CP 1** | Ilay (`IlayOr-Chen`) | #1 | OOP scaffolding, `AppConfig`, `FirebaseRepository` + `InMemoryFirebaseAdapter`, FastAPI app factory, initial `README.md`. | Completed |
| **CP 2** | Ilay (`IlayOr-Chen`) | #2 | `AuthService` & `RosterImporter` (`104`-student Hebrew Excel parser with NaN group handling) + unit tests. | Completed |
| **CP 3 & 3.5** | Ilay (`IlayOr-Chen`) | #3, #13 | `AttendanceManager` (QR tokens, Haversine GPS Geofencing pivot, 15-min signed cookie + RTDB cooldown) + tests. | Completed |
| **CP 4** | Ilay (`IlayOr-Chen`) | #4 | `PhotoHeadCounter` (local Ultralytics YOLOv8n `imgsz=1280`, annotated JPEG, GREEN/ORANGE/RED confidence indicator). | Completed |
| **CP 5 & 6** | Roie (`RoieBenyamin`) | #8, #9 | Mobile-first dark glass UI, Attendance Management tab, manual Present/Absent overrides, UTF-8-BOM CSV & `.xlsx` exports, YOLO UI. | Completed |
| **CP 7** | Roie (`RoieBenyamin`) | #10 | `PollService` & `CommentService` backend + animated Live Polls and Threaded Comments UI + tests. | Completed |
| **CP 8** | Ilay (`IlayOr-Chen`) | #5 | `GeminiClient`, `VideoSummarizer`, and `HomeworkReviewer` (Gated Release + Peer Comparison) + UI tabs + tests. | Completed |
| **CP 9–12** | Ilay & Roie | #6, #7, #11, #12 | Full Software Engineering documentation suite (`docs/01`–`07`), Mermaid Class/Sequence diagrams, Render & Cloudflare Tunnel setup. | Completed |
| **Final Polish & E2E** | Ilay & Roie | #14, #15 | Restricted Lecturer login, static session QR + Leaflet GPS map (`30m`/`50m`/`100m`), cooldown reset, mutable polls, persisted YOLO + Reset YOLO, Student Attendance History, YouTube-only 2-step Video AI with oEmbed titles, pure BYOK + `gemini-3.8-flash` `503`/`429`/`404` fallback, rich Homework Peer Comparison & loading UX, and full `52/52` `pytest` verification. | Completed |

---

## 4. Acceptance Criteria

* **AC-1 (Roster & Restricted Auth):** `courseid_130158_participants.xlsx` auto-seeds all `104` students with `0` crashes on the `2` unassigned group rows; public lecturer registration returns `403 Forbidden`; all lecturer-only endpoints reject anonymous (`401`) and student (`403`) callers.
* **AC-2 (Smart Attendance & YOLO):** Static session QR is valid while `LIVE` and rejected immediately after close; Haversine GPS geofencing enforces `30m`/`50m`/`100m` radii (with live toggle fallback); 15-min cooldown blocks repeat same-device check-ins until expired or reset; local YOLOv8n detects physical students, persists the annotated JPEG per lesson, supports **Reset YOLO**, and reports `GREEN`/`ORANGE`/`RED`; Student Portal displays per-lesson **My Attendance History**.
* **AC-3 (Comments & Live Polls):** Private comments are invisible to other students (`403` on peer replies); anonymous comments serialize as `"Anonymous Student"`; polls support mutable vote casting, switching, and unvoting (`cast`/`switched`/`removed`) with live percentages.
* **AC-4 (YouTube Video AI & Clear Key):** Public YouTube URLs are canonicalized, titled via oEmbed, and summarized via `youtube-transcript-api` -> `VideoMetadata(fps=0.1)` fallback with rich academic sections, Copy Markdown, and Delete; **Clear Key** genuinely clears `/settings/gemini` (`has_key=False`) without `.env` fallback.
* **AC-5 (BYOK Homework Review & Rich Peer Comparison):** Student submissions require browser BYOK `X-Gemini-Api-Key` (`400` if missing), support multi-format files & public GitHub URLs with clear inline loading UI, withhold reviews until deadline or **"End Submission Now"** (hardened against missing embedded `assignment_id`), handle `503`/`429`/`404` via automatic model fallback, display concrete peer standing/missing items/strengths/improvements for 2+ submissions, and display the First-Submission notice when `total_submissions == 1`.
