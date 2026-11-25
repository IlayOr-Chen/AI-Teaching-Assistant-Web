# Testing Strategy Mapped to Classes & Verification Report (To-Do #9)

> **Project:** Web-Based AI Teaching Assistant (Ex2: Vibe Coding & Deep Designing)  
> **Team:** Ilay (`IlayOr-Chen`) & Roie (`RoieBenyamin`)

---

## 1. Testing Philosophy & Deterministic Isolation

Our automated testing strategy enforces **100% 1-to-1 coverage** of every class in our **Low-Level Class Diagram** ([docs/05_architecture_and_diagrams.md](./05_architecture_and_diagrams.md)):

1. **Zero Production Firebase Pollution (`InMemoryFirebaseAdapter`):** Every unit and HTTP integration test injects `FirebaseRepository(use_mock=True)`, routing all RTDB CRUD calls to an isolated, thread-safe in-memory tree with deep-copy isolation.
2. **Deterministic Time & External API Mocking:** Cooldown timers (`900s`), homework deadline gates, YouTube oEmbed / `youtube-transcript-api`, GitHub zipball downloads, and `google-genai` responses are deterministically mocked in `tests/` (with zero mock fallbacks in production `src/`).
3. **Real Course Roster & Real Local YOLO Verification:** Tests directly parse `assets/courseid_130158_participants.xlsx` (`104` Hebrew rows + `2` NaN group cells) and execute local `PhotoHeadCounter` inference & persistence checks.
4. **Automated Diagram-to-Code & Docstring Enforcement:** [tests/test_architecture_and_edge_cases.py](../tests/test_architecture_and_edge_cases.py) uses Python `inspect` to verify that all **13 OOP classes** and every public/internal method match the Low-Level Class Diagram 100% and include `Purpose:`, `Inputs:`, and `Outputs:` docstrings.

---

## 2. Class-by-Class Mapping to the Low-Level Class Diagram

| # | Class in `src/` | Test Modules Covering Class | Happy Cases Verified | Edge Cases & Regression Tests Verified |
| :-: | :--- | :--- | :--- | :--- |
| **1** | `AppConfig` ([config.py](../src/config.py)) | `test_sanity_and_repository.py`, `test_architecture_and_edge_cases.py` | Default values (`geofence_radius_meters=100`, `device_cooldown_minutes=15`, `gemini-3.5-flash-lite`), `.env` parsing via `from_env()`. | Boolean coercion (`"true"`, `"1"`, `"yes"` vs `"false"`), immutable `@dataclass(frozen=True)`. |
| **2** | `InMemoryFirebaseAdapter` ([firebase_repository.py](../src/repository/firebase_repository.py)) | `test_sanity_and_repository.py`, `test_architecture_and_edge_cases.py` | Slash normalization, hierarchical `set`, `get`, shallow `update`, unique `push`, `delete`, `clear`. | Deep-copy isolation, `set(path, None)` deletes node, `update` with `None` removes key. |
| **3** | `FirebaseRepository` ([firebase_repository.py](../src/repository/firebase_repository.py)) | `test_sanity_and_repository.py`, `test_architecture_and_edge_cases.py` | Mock delegation (`use_mock=True`), REST `.json` URL builder (`_build_rest_url`). | Guard blocking `reset_mock()` in live mode (`RuntimeError`), graceful fallback when `serviceAccountKey.json` absent. |
| **4** | `ClientIPResolver` ([ip_resolver.py](../src/utils/ip_resolver.py)) | `test_sanity_and_repository.py`, `test_architecture_and_edge_cases.py` | Strict header priority: `CF-Connecting-IP` -> `X-Forwarded-For` -> `request.client.host`. | Multi-proxy comma chains, private/loopback classification (`is_loopback_or_private`). |
| **5** | `AuthService` ([auth_service.py](../src/services/auth_service.py)) | `test_auth_and_roster.py`, `test_backend_hardening.py` | PBKDF2-HMAC-SHA256 hashing (`200,000` iter), startup seeding & case-insensitive login (`adminL@gmail.com` / `12345`), JWT cookies, frictionless Student roster login. | Public registration disabled (`403`), wrong password (`PermissionError`), expired JWT, student token blocked from lecturer endpoints (`403`). |
| **6** | `RosterImporter` ([roster_importer.py](../src/services/roster_importer.py)) | `test_auth_and_roster.py`, `test_attendance_export_and_ui.py` | Real import of `courseid_130158_participants.xlsx` (`104` students), Hebrew name concatenation, float group `3.0` -> `"3"`, search & group filters. | `2` empty/`NaN` group rows (`בועז בן משה`, `נדב סודרי`) normalized cleanly without crash; invalid workbook (`400`). |
| **7** | `AttendanceManager` ([attendance_manager.py](../src/services/attendance_manager.py)) | `test_attendance_manager.py`, `test_backend_hardening.py`, `test_attendance_export_and_ui.py` | Multi-lesson switching, static 16-char session QR, Haversine `calculate_distance_meters` (`30m`/`50m`/`100m`), 15-min cooldown + instant reset, manual overrides, Student `get_student_attendance_history`, UTF-8-BOM `.csv`/`.xlsx` exports. | Outside geofence (`403`), denied GPS (`403`), live geofence toggle OFF bypass, buddy-punching blocked (`900s`), tampered HMAC cookie, closed session token rejected (`400`). |
| **8** | `PhotoHeadCounter` ([photo_head_counter.py](../src/services/photo_head_counter.py)) | `test_photo_head_counter.py`, `test_architecture_and_edge_cases.py` | Local YOLOv8n person detection on uploaded photo, persisted annotated JPEG Data URL in RTDB, **GREEN** status, `reset_headcount` (`DELETE /api/attendance/headcount`). | **ORANGE** on 2–3 diff or `>25%` low-conf back-row occlusion; **RED** on `>=4` diff, `avg_conf < 0.45`, or `0` detections; invalid bytes (`400`). |
| **9** | `PollService` ([poll_service.py](../src/services/poll_service.py)) | `test_polls_and_comments.py`, `test_architecture_and_edge_cases.py` | Multiple-choice poll creation (`2..8` options), mutable voting (`cast`, `switched`, `removed`), live percentages, open/close toggle. | Vote on closed poll (`403`), `< 2` or `> 8` options (`ValueError`), out-of-range `option_index`. |
| **10** | `CommentService` ([comment_service.py](../src/services/comment_service.py)) | `test_polls_and_comments.py`, `test_architecture_and_edge_cases.py` | Public & private comments, named & anonymous identity, `Lesson 1..N` & `General` scope filter, threaded replies. | Private comments hidden from peers (`403` on peer replies), `"Anonymous Student"` masking, `> 2000` char limit. |
| **11** | `GeminiClient` ([gemini_client.py](../src/services/gemini_client.py)) | `test_gemini_video_and_homework.py`, `test_architecture_and_edge_cases.py` | `/settings/gemini` persistence, `gemini-3.5-flash-lite` default & `gemini-3.8-flash` / `gemini-3.5-flash` presets, standard Developer API for `AIza...` & `AQ....`, explicit **Clear Key** (`has_key=False`, no `.env` fallback), `VideoMetadata(fps=0.1)`. | Automatic retry & fallback across `503`, `429`, and `404`; `GeminiHighDemandError` (`503`) & `GeminiRateLimitError` (`429`); plain `Part.from_uri` fallback on HTTP `400`; missing key raises `RuntimeError` (never fake fallback). |
| **12** | `VideoSummarizer` ([video_summarizer.py](../src/services/video_summarizer.py)) | `test_gemini_video_and_homework.py`, `test_architecture_and_edge_cases.py` | YouTube canonicalization (`&list=`/`&index=` stripped), Hebrew/UTF-8 oEmbed title extraction (`video_title`, `source_url`), 2-step `youtube-transcript-api` -> `fps=0.1` fallback, rich academic schema (`detailed_overview`, `key_concepts`, `study_questions`), `delete_summary`. | Removed MP4 route returns `404`; invalid URL (`400`); oEmbed timeout falls back to Gemini `suggested_title`; backwards compatibility for older RTDB summaries. |
| **13** | `HomeworkReviewer` ([homework_reviewer.py](../src/services/homework_reviewer.py)) | `test_gemini_video_and_homework.py`, `test_architecture_and_edge_cases.py` | Multi-format files (`.pdf`, `.docx`, `.zip`, code) + Public GitHub zipball ingestion, Student BYOK (`X-Gemini-Api-Key`), deadline & **"End Submission Now"** unlock, rich Peer Comparison (`peer_standing`, `missing_compared_to_peers`, `strengths_vs_peers`, `actionable_improvements`). | Missing BYOK key (`400`), **Gated Release Rule**, **First-Submission Edge Case**, Hebrew/sparse PDF resilience, late submission (`403`), **regression test** for `end_submission_now` on legacy Firebase dicts lacking embedded `assignment_id`. |

---

## 3. Automated `pytest` Execution Results (`./venv`)

```bash
.\venv\Scripts\python.exe -m pytest -v
```

| Test Module | Tests | Status | Verification Focus |
| :--- | :---: | :---: | :--- |
| [tests/test_sanity_and_repository.py](../tests/test_sanity_and_repository.py) | 6 | **PASSED** | Required assets, `AppConfig`, `FirebaseRepository` CRUD/isolation, `ClientIPResolver`, FastAPI health. |
| [tests/test_auth_and_roster.py](../tests/test_auth_and_roster.py) | 6 | **PASSED** | PBKDF2 hashing, restricted Lecturer login, JWT roles, real 104-student Hebrew Excel import & 2 NaN groups. |
| [tests/test_attendance_manager.py](../tests/test_attendance_manager.py) | 13 | **PASSED** | Haversine math, static QR tokens, HMAC cookie, `30m/50m/100m` geofencing, 15-min cooldown & reset, multi-lesson switching, Student Attendance History. |
| [tests/test_photo_head_counter.py](../tests/test_photo_head_counter.py) | 3 | **PASSED** | GREEN/ORANGE/RED thresholds, real local YOLOv8n inference, persisted annotated JPEG & Reset YOLO, HTTP endpoints. |
| [tests/test_backend_hardening.py](../tests/test_backend_hardening.py) | 5 | **PASSED** | QR validation, lecturer-only guards, QR-gated check-in context, proxy public origin detection. |
| [tests/test_frontend_ui.py](../tests/test_frontend_ui.py) | 1 | **PASSED** | Static assets, CSS design system, and ES module UI wiring (Leaflet map, YouTube-only single button, oEmbed footer, loading UI, rich Peer Comparison, `gemini-3.8-flash`). |
| [tests/test_attendance_export_and_ui.py](../tests/test_attendance_export_and_ui.py) | 4 | **PASSED** | UTF-8-BOM `.csv` & `.xlsx` Hebrew roster export, role guards, Roster & YOLO UI wiring. |
| [tests/test_polls_and_comments.py](../tests/test_polls_and_comments.py) | 3 | **PASSED** | Mutable poll voting (`cast`/`switched`/`removed`), live percentages, public/private/anonymous comments, scope filter, threaded replies. |
| [tests/test_gemini_video_and_homework.py](../tests/test_gemini_video_and_homework.py) | 7 | **PASSED** | Clear Key (`DELETE`), `gemini-3.8-flash` presets, 2-step `youtube-transcript-api` + `fps=0.1`, oEmbed titles, `503`/`429`/`404` fallback, BYOK homework, rich Peer Comparison, legacy `end_submission_now` regression. |
| [tests/test_architecture_and_edge_cases.py](../tests/test_architecture_and_edge_cases.py) | 4 | **PASSED** | 100% Low-Level Class Diagram method parity across all 13 classes, `Purpose/Inputs/Outputs` docstring audit, boundary cases, and deliverables audit. |
| **TOTAL** | **52** | **52 PASSED (100%)** | **0 Failures · 0 Warnings** |
