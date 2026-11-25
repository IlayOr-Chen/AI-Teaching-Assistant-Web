# Software Architecture, Low-Level Class Diagram & Sequence Diagrams (To-Do #7 & #8)

> **Project:** Web-Based AI Teaching Assistant (Ex2: Vibe Coding & Deep Designing)  
> **Team:** Ilay (`IlayOr-Chen`) & Roie (`RoieBenyamin`)

---

## 1. Main Software Components & 4-Tier Architecture (To-Do #7)

The platform follows a single-server, 4-tier **Object-Oriented Architecture (KISS + Deep OOD)** separating presentation, HTTP routing, domain business logic, and cloud/AI persistence:

| Tier | Files / Modules | Responsibility |
| :--- | :--- | :--- |
| **1. Presentation Tier** | `src/static/index.html`, `styles.css`, `app.js`, `lecturer.js`, `student.js`, `polls_comments.js`, `ai_tools.js`, `ui.js`, `api.js`, `icons.js` | Mobile-first dark glass SPA with Leaflet.js + OSM geofence maps, Projector Mode QR, Student BYOK `localStorage`, inline loading indicators, and XSS-safe DOM rendering (`el()`). Served directly by FastAPI with zero Node build step. |
| **2. API & Config Tier** | `src/main.py`, `src/config.py`, `src/utils/ip_resolver.py` | FastAPI `create_app()` factory wiring Pydantic schemas, HTTP-only JWT/device cookies, `run_in_threadpool` worker offloading, `AppConfig`, and proxy-aware `ClientIPResolver`. |
| **3. Domain OOP Service Tier** | `src/services/` (9 cohesive classes) | `AuthService`, `RosterImporter`, `AttendanceManager`, `PhotoHeadCounter`, `PollService`, `CommentService`, `GeminiClient`, `VideoSummarizer`, `HomeworkReviewer` — encapsulating all business rules, cryptography, Haversine math, YOLO vision, YouTube 2-step summarization, and gated peer comparison. |
| **4. Persistence & AI Tier** | `src/repository/firebase_repository.py`, local `yolov8n.pt`, Google Gemini API, YouTube oEmbed & Transcripts | `FirebaseRepository` abstracting live Firebase RTDB alongside thread-safe `InMemoryFirebaseAdapter` (`use_mock=True`) for deterministic `pytest` execution. |

```mermaid
flowchart TB
    subgraph ClientTier["1. Presentation Tier (Browser / Smartphone)"]
        LecUI["Lecturer Dashboard UI<br/>(lecturer.js, polls_comments.js, ai_tools.js)"]
        StuUI["Student Mobile Portal & QR Check-In UI<br/>(student.js, polls_comments.js, ai_tools.js)"]
        MapGeo["Leaflet.js + OSM Map &<br/>Browser Geolocation API"]
    end

    subgraph APITier["2. Single-Server FastAPI & Config Tier (src/main.py)"]
        FastAPIApp["FastAPI App Factory (create_app)"]
        AppCfg["AppConfig (src/config.py)"]
        IPRes["ClientIPResolver (src/utils/ip_resolver.py)"]
    end

    subgraph DomainTier["3. Domain OOP Service Tier (src/services/)"]
        AuthSvc["AuthService<br/>(Restricted Login + PBKDF2 + JWT)"]
        RosterSvc["RosterImporter<br/>(openpyxl Hebrew/NaN Parser)"]
        AttMgr["AttendanceManager<br/>(Static QR + Haversine GPS + 15m Cooldown)"]
        YoloSvc["PhotoHeadCounter<br/>(Local YOLOv8n + GREEN/ORANGE/RED)"]
        PollSvc["PollService<br/>(Live Polls + Mutable Voting)"]
        CmtSvc["CommentService<br/>(Public/Private + Anonymous + Threads)"]
        GemCli["GeminiClient<br/>(BYOK + fps=0.1 + 503/429/404 Fallback)"]
        VidSvc["VideoSummarizer<br/>(YouTube-Only + oEmbed + 2-Step Pipeline)"]
        HwSvc["HomeworkReviewer<br/>(Multi-Format/GitHub + Gate + Rich Peer Comparison)"]
    end

    subgraph InfraTier["4. Persistence & External AI Tier"]
        FBRepo["FirebaseRepository<br/>(src/repository/firebase_repository.py)"]
        MockFB["InMemoryFirebaseAdapter<br/>(Deterministic pytest Store)"]
        LiveFB[("Firebase Realtime Database<br/>ai-teaching-assistant-web-default-rtdb")]
        YoloWeights[("Local Ultralytics YOLOv8n<br/>(yolov8n.pt, imgsz=1280)")]
        YouTubeSvc[("YouTube oEmbed &<br/>youtube-transcript-api")]
        GeminiAPI[("Google Gemini Developer API<br/>(gemini-3.8-flash / 3.5-flash-lite / 3.5-flash)")]
    end

    StuUI --> MapGeo
    LecUI --> MapGeo
    LecUI --> FastAPIApp
    StuUI --> FastAPIApp
    FastAPIApp --> AppCfg
    FastAPIApp --> IPRes
    FastAPIApp --> AuthSvc
    FastAPIApp --> RosterSvc
    FastAPIApp --> AttMgr
    FastAPIApp --> YoloSvc
    FastAPIApp --> PollSvc
    FastAPIApp --> CmtSvc
    FastAPIApp --> GemCli
    FastAPIApp --> VidSvc
    FastAPIApp --> HwSvc

    VidSvc --> GemCli
    VidSvc --> YouTubeSvc
    HwSvc --> GemCli

    AuthSvc --> FBRepo
    RosterSvc --> FBRepo
    AttMgr --> FBRepo
    YoloSvc --> FBRepo
    PollSvc --> FBRepo
    CmtSvc --> FBRepo
    GemCli --> FBRepo
    VidSvc --> FBRepo
    HwSvc --> FBRepo

    FBRepo --> MockFB
    FBRepo --> LiveFB
    YoloSvc --> YoloWeights
    GemCli --> GeminiAPI
```

---

## 2. Low-Level Class Diagram — 100% Matched to `src/` (To-Do #8)

Every class, attribute, method, and relationship below corresponds 1-to-1 with the **13 Python classes** in `src/` and is verified automatically by `tests/test_architecture_and_edge_cases.py`:

```mermaid
classDiagram
    class AppConfig {
        +str firebase_db_url
        +str firebase_credentials_path
        +bool use_mock_firebase
        +str jwt_secret
        +str jwt_algorithm
        +int jwt_expiration_hours
        +str lecturer_email
        +str lecturer_password
        +str lecturer_name
        +str lecturer_access_code
        +int qr_rotation_seconds
        +int qr_grace_seconds
        +int device_cooldown_minutes
        +bool enforce_geofence_default
        +int geofence_radius_meters
        +str gemini_api_key
        +str gemini_model
        +str yolo_model_path
        +float yolo_confidence_threshold
        +str public_base_url
        +from_env(env_file)$ AppConfig
    }

    class InMemoryFirebaseAdapter {
        -RLock _lock
        -dict _store
        -int _push_counter
        +normalize_path(path)$ list~str~
        +get(path) Any
        +set(path, value) Any
        +update(path, updates) dict
        +push(path, value) str
        +delete(path) bool
        +clear() None
    }

    class FirebaseRepository {
        +str db_url
        +str credentials_path
        +bool use_mock
        +float http_timeout
        -InMemoryFirebaseAdapter _mock
        -Any _admin_db
        -_try_init_firebase_admin() None
        -_build_rest_url(path) str
        +get(path) Any
        +set(path, value) Any
        +update(path, updates) dict
        +push(path, value) str
        +delete(path) bool
        +reset_mock() None
    }

    class ClientIPResolver {
        +resolve_from_headers(headers, fallback_host)$ tuple~str,str~
        +is_loopback_or_private(ip_address)$ bool
        +resolve(request)$ str
        +resolve_with_source(request)$ dict~str,str~
    }

    class AuthService {
        +FirebaseRepository repository
        +AppConfig config
        +email_to_key(email)$ str
        +hash_password(password, salt)$ str
        +verify_password(password, stored_hash)$ bool
        +create_jwt_token(subject_id, role, email, full_name, extra_claims, expires_in_seconds) str
        +verify_jwt_token(token, required_role) dict
        +seed_default_lecturer() dict
        +register_lecturer(full_name, email, password, access_code) dict
        +login_lecturer(email, password) dict
        +authenticate_student(student_id, email, full_name) dict
        +extract_token_from_request(request, prefer_role) str
        +require_lecturer_claims(request) dict
    }

    class RosterImporter {
        +FirebaseRepository repository
        +email_to_student_id(email)$ str
        +normalize_group(raw_group)$ str
        -_resolve_column_indices(header_row)$ dict
        +parse_excel_rows(source) tuple~list,int~
        +import_from_excel(source, replace_existing) dict
        +ensure_roster_seeded(default_asset_path) dict
        +list_students(search_query, group_filter) list~dict~
        +get_student(student_id) dict
    }

    class AttendanceManager {
        +FirebaseRepository repository
        +AppConfig config
        +calculate_distance_meters(lat1, lon1, lat2, lon2)$ float
        -_validate_optional_coords(lat, lng)$ tuple
        +create_signed_device_cookie(device_id) tuple~str,str~
        +verify_signed_device_cookie(cookie_value) str
        +start_session(lesson_id, lesson_title, classroom_lat, classroom_lng, geofence_radius_meters, enforce_geofence, rotation_seconds) dict
        +close_session(lesson_id) dict
        +set_geofence_enforcement(lesson_id, enforce_geofence, geofence_radius_meters, classroom_lat, classroom_lng) dict
        +get_session(lesson_id) dict
        +list_sessions() list~dict~
        -_render_qr_data_url(target_url)$ str
        +generate_rotating_qr_token(lesson_id, base_url, ttl_override_seconds, now_ts) dict
        +verify_qr_token(qr_token, expected_lesson_id) dict
        +check_device_cooldown(device_id, now_ts) dict
        +record_device_cooldown(device_id, student_id, lesson_id, now_ts) dict
        +reset_all_device_cooldowns(now_ts) dict
        +submit_qr_checkin(lesson_id, student_id, qr_token, student_lat, student_lng, device_cookie_value, now_ts) dict
        +set_manual_attendance(lesson_id, student_id, status) dict
        +get_lesson_attendance_report(lesson_id) dict
        -_format_timestamp_iso(unix_ts)$ str
        +export_attendance_csv(lesson_id) bytes
        +export_attendance_xlsx(lesson_id) bytes
        +get_student_attendance_history(student_id) dict
    }

    class PhotoHeadCounter {
        +FirebaseRepository repository
        +AppConfig config
        +int imgsz
        -Any _model
        -_get_model() Any
        -_load_pil_image(image_source)$ Image
        +evaluate_confidence_indicator(detected_count, confidences, registered_count)$ dict
        -_annotate_image(pil_image, detections, output_image_path)$ str
        -_run_yolo_person_detection(pil_image, conf_threshold) list~dict~
        +analyze_classroom_photo(image_source, lesson_id, registered_count, conf_threshold, output_image_path) dict
        +reset_headcount(lesson_id) dict
    }

    class PollService {
        +FirebaseRepository repository
        -_format_poll(raw_poll, viewer_id)$ dict
        +create_poll(question, options, lesson_tag, created_by) dict
        +set_poll_active(poll_id, active) dict
        +submit_vote(poll_id, voter_id, option_index) dict
        +get_poll(poll_id, viewer_id) dict
        +list_polls(active_only, viewer_id) list~dict~
    }

    class CommentService {
        +FirebaseRepository repository
        -_sanitize_for_viewer(raw_comment, viewer_role, viewer_id)$ dict
        +create_comment(text, author_id, author_name, author_role, visibility, is_anonymous, scope_tag) dict
        +add_reply(comment_id, text, author_id, author_name, author_role, is_anonymous) dict
        +list_comments(viewer_role, viewer_id, scope_tag) list~dict~
    }

    class GeminiClient {
        +FirebaseRepository repository
        +AppConfig config
        -Callable _transport
        +mask_api_key(api_key)$ str
        -_build_genai_client(api_key, use_vertexai)$ Any
        +resolve_credentials(api_key_override, model_id_override, allow_stored_key) tuple~str,str~
        +get_settings() dict
        +update_settings(api_key, model_id) dict
        -_extract_json_dict(raw_text)$ dict
        -_classify_fallback_error(exc)$ str
        -_build_youtube_part(file_uri, mime_type, use_low_fps)$ Any
        +generate_structured_json(prompt, media_bytes, mime_type, file_uri, api_key_override, model_id_override, allow_stored_key, missing_key_error) dict
    }

    class VideoSummarizer {
        +FirebaseRepository repository
        +GeminiClient gemini_client
        +validate_youtube_url(youtube_url)$ str
        -_fetch_youtube_title(canonical_url)$ str
        -_fetch_youtube_transcript(video_id)$ str
        -_build_prompt(canonical_url, lesson_tag, lecturer_notes, transcript_text, video_title)$ str
        -_normalize_key_concepts(raw_concepts)$ list~dict~
        +summarize_video(youtube_url, lesson_tag, lecturer_notes) dict
        +list_summaries() list~dict~
        +delete_summary(summary_id) dict
    }

    class HomeworkReviewer {
        +FirebaseRepository repository
        +GeminiClient gemini_client
        +is_review_released(assignment, now_ts)$ bool
        -_extract_docx_text(docx_bytes)$ str
        -_extract_zip_text(zip_bytes, source_label)$ tuple~str,int~
        +validate_and_extract_pdf_text(filename, pdf_bytes, content_type)$ dict
        +extract_public_git_repo_text(repo_url)$ dict
        +create_assignment(title, description, deadline_ts, lesson_tag) dict
        +end_submission_now(assignment_id, now_ts) dict
        -_generate_peer_comparison(assignment, target_submission, all_submissions) dict
        +submit_homework_pdf(assignment_id, student_id, student_name, filename, pdf_bytes, content_type, now_ts, repo_url) dict
        +get_student_review_and_comparison(assignment_id, student_id, viewer_role, now_ts) dict
        -_format_assignment_overview(raw_assignment, viewer_id, now_ts, assignment_id) dict
        +list_assignments(viewer_id, now_ts) list~dict~
    }

    FirebaseRepository *-- InMemoryFirebaseAdapter : delegates when use_mock=True
    AuthService --> FirebaseRepository : reads/writes /users/lecturers & /students
    AuthService --> AppConfig : reads JWT & fixed lecturer config
    RosterImporter --> FirebaseRepository : writes /students
    AttendanceManager --> FirebaseRepository : writes /attendance_sessions & /device_cooldowns
    AttendanceManager --> AppConfig : reads geofence & cooldown defaults
    PhotoHeadCounter --> FirebaseRepository : persists/resets /attendance_sessions/{id}/yolo_headcount
    PhotoHeadCounter --> AppConfig : reads YOLO weights & confidence threshold
    PollService --> FirebaseRepository : reads/writes /polls
    CommentService --> FirebaseRepository : reads/writes /comments
    GeminiClient --> FirebaseRepository : reads/writes /settings/gemini
    GeminiClient --> AppConfig : reads default model
    VideoSummarizer --> GeminiClient : generates structured YouTube JSON summary
    VideoSummarizer --> FirebaseRepository : writes/deletes /video_summaries
    HomeworkReviewer --> GeminiClient : generates BYOK review & rich peer comparison
    HomeworkReviewer --> FirebaseRepository : writes /homework_assignments
```

---

## 3. Sequence Diagrams (To-Do #8)

### 3.1 Smart Attendance (Static Session QR + Leaflet/Haversine GPS Geofencing + 15-Min Cooldown + Local YOLO)

```mermaid
sequenceDiagram
    actor Lecturer
    actor Student
    participant UI as Frontend UI (lecturer.js / student.js)
    participant API as FastAPI (src/main.py)
    participant Att as AttendanceManager
    participant Yolo as PhotoHeadCounter
    participant DB as FirebaseRepository

    Lecturer->>UI: Pick pin & radius (30m/50m/100m) on Leaflet map & start Lesson 1
    UI->>API: POST /api/attendance/sessions
    API->>Att: start_session(lesson_1, lat, lng, radius=50)
    Att->>DB: set("/attendance_sessions/lesson_1", session_qr_token, active=true)
    DB-->>API: session_data
    UI->>API: GET /api/attendance/sessions/lesson_1/qr
    API->>Att: generate_rotating_qr_token("lesson_1")
    Att-->>UI: static 16-char token + Base64 PNG QR (centered Projector Mode)

    Student->>UI: Scan QR on smartphone -> opens /?lesson_id=lesson_1&qr_token=...
    UI->>API: GET /api/attendance/checkin-context?lesson_id=lesson_1&qr_token=...
    API->>Att: verify_qr_token(qr_token, "lesson_1")
    API-->>UI: lesson_title, geofence config, searchable roster dropdown
    UI->>Student: Request Browser Geolocation (navigator.geolocation)
    Student->>UI: Select name & tap "Check me in" (student_lat, student_lng)
    UI->>API: POST /api/attendance/checkin (Cookie: ta_device_id)
    API->>Att: submit_qr_checkin(lesson_1, student_id, qr_token, student_lat, student_lng)
    Att->>DB: get("/device_cooldowns/{device_id}") & get("/settings/cooldown_reset_at")
    alt Cooldown active (< 900s & not reset)
        Att-->>API: PermissionError ("15-minute device cooldown active: Xm Ys remaining")
        API-->>UI: HTTP 403 Forbidden (live MM:SS cooldown timer)
    else Geofence enabled & (missing GPS or Haversine distance > radius)
        Att->>Att: calculate_distance_meters(classroom_lat, classroom_lng, student_lat, student_lng)
        Att-->>API: PermissionError ("GPS Geofence check failed: You appear to be Xm away")
        API-->>UI: HTTP 403 Forbidden
    else Inside radius & no cooldown
        Att->>DB: set("/attendance_sessions/lesson_1/records/{student_id}", record)
        Att->>DB: set("/device_cooldowns/{device_id}", expires_at=now+900)
        API-->>UI: HTTP 200 OK (Set-Cookie: ta_device_id)
    end

    Lecturer->>UI: Upload classroom photo on Roster tab
    UI->>API: POST /api/attendance/headcount?lesson_id=lesson_1
    API->>Yolo: analyze_classroom_photo(image_bytes, "lesson_1")
    Yolo->>Yolo: _run_yolo_person_detection(imgsz=1280, conf=0.30, classes=[0])
    Yolo->>DB: set("/attendance_sessions/lesson_1/yolo_headcount", count + annotated JPEG)
    Yolo-->>UI: YOLO count vs registered + GREEN/ORANGE/RED badge + persisted image
```

### 3.2 On-the-Spot KISS Polls (Mutable Voting) & Threaded Comments

```mermaid
sequenceDiagram
    actor Lecturer
    actor StudentA as Student A (Author)
    actor StudentB as Student B (Peer)
    participant API as FastAPI (src/main.py)
    participant Poll as PollService
    participant Cmt as CommentService
    participant DB as FirebaseRepository

    Lecturer->>API: POST /api/polls (question, options=["A","B","C"])
    API->>Poll: create_poll()
    Poll->>DB: set("/polls/{poll_id}", active=true)
    Poll-->>Lecturer: active poll (0 votes)

    StudentA->>API: POST /api/polls/{poll_id}/vote (option_index=0)
    API->>Poll: submit_vote(poll_id, "stu_a", 0)
    alt First vote
        Poll->>DB: set("/polls/{poll_id}/votes/stu_a", {option_index: 0})
        Poll-->>StudentA: vote_action="cast"
    else Same option clicked again (unvote)
        Poll->>DB: delete("/polls/{poll_id}/votes/stu_a")
        Poll-->>StudentA: vote_action="removed"
    else Different option clicked (switch vote)
        Poll->>DB: set("/polls/{poll_id}/votes/stu_a", {option_index: 1})
        Poll-->>StudentA: vote_action="switched"
    end

    StudentA->>API: POST /api/comments (visibility="private", scope_tag="Lesson 1")
    API->>Cmt: create_comment()
    Cmt->>DB: set("/comments/{comment_id}", record)

    StudentB->>API: GET /api/comments
    API->>Cmt: list_comments(viewer_role="student", viewer_id="stu_b")
    Note over Cmt: Filters out Student A's private comment from Student B
    Cmt-->>StudentB: Public comments only

    Lecturer->>API: POST /api/comments/{comment_id}/replies
    API->>Cmt: add_reply(author_role="lecturer")
    Cmt->>DB: set("/comments/{comment_id}/replies/{reply_id}", reply_record)
    Cmt-->>Lecturer: Updated private thread
```

### 3.3 YouTube-Only Video AI Summarizer (2-Step Pipeline + oEmbed + 503/429/404 Fallback)

```mermaid
sequenceDiagram
    actor Lecturer
    participant API as FastAPI (src/main.py)
    participant Vid as VideoSummarizer
    participant YT as YouTube oEmbed & Transcript API
    participant Gem as GeminiClient
    participant DB as FirebaseRepository

    Lecturer->>API: POST /api/video/summarize-youtube (youtube_url with &list=...)
    API->>Vid: summarize_video(youtube_url)
    Vid->>Vid: validate_youtube_url() -> clean https://www.youtube.com/watch?v=<id>
    Vid->>YT: _fetch_youtube_title(clean_url) (oEmbed UTF-8 title)
    Vid->>YT: _fetch_youtube_transcript(video_id, languages=["he","iw","en"])
    alt Transcript available (Step 1 — Fastest)
        Vid->>Gem: generate_structured_json(prompt_with_transcript)
    else No captions available (Step 2 — Automatic Fallback)
        Vid->>Gem: generate_structured_json(prompt, file_uri=clean_url, fps=0.1)
    end
    Note over Gem: Auto-fallback across [selected, gemini-3.8-flash, 3.5-flash, 3.5-flash-lite] on 503/429/404
    Gem-->>Vid: Rich JSON (detailed_overview, key_concepts, study_questions, delivery_insights)
    Vid->>DB: set("/video_summaries/{id}", record with video_title & source_url)
    Vid-->>Lecturer: Structured summary card with Copy Markdown, Delete & YouTube footer link
```

### 3.4 Gated BYOK Homework Review, Rich Peer Comparison & First-Submission Edge Case

```mermaid
sequenceDiagram
    actor Lecturer
    actor Student1 as Student 1
    actor Student2 as Student 2
    participant API as FastAPI (src/main.py)
    participant HW as HomeworkReviewer
    participant Gem as GeminiClient
    participant DB as FirebaseRepository

    Lecturer->>API: POST /api/homework/assignments (title, deadline_ts=future)
    API->>HW: create_assignment()
    HW->>DB: set("/homework_assignments/{hw_id}", ended_manually=false)

    Student1->>API: POST /api/homework/assignments/{hw_id}/submit (file or GitHub URL + X-Gemini-Api-Key)
    API->>HW: submit_homework_pdf(..., require_student_key=True)
    HW->>Gem: generate_structured_json(review_prompt, api_key_override=student_key)
    Gem-->>HW: Structured Review Card
    HW->>DB: set("/homework_assignments/{hw_id}/submissions/stu_1", record)
    Note over HW: Gated Release Rule: review_released=False while open
    HW-->>Student1: status="submitted_pending_release", review=None, peer_comparison=None

    Lecturer->>API: POST /api/homework/assignments/{hw_id}/end-now ("End Submission Now")
    API->>HW: end_submission_now(hw_id)
    HW->>DB: update("/homework_assignments/{hw_id}", {assignment_id, ended_manually: true})
    HW-->>Lecturer: review_released=True

    alt Only 1 student submitted (First-Submission Edge Case)
        Student1->>API: GET /api/homework/assignments/{hw_id}/review
        HW-->>Student1: Column 1: Review Card | Column 2: FIRST_SUBMISSION_NOTICE
    else 2+ students submitted (Rich Peer Comparison)
        Student2->>API: GET /api/homework/assignments/{hw_id}/review (X-Gemini-Api-Key)
        HW->>Gem: generate_structured_json(peer_comparison_prompt)
        HW-->>Student2: Column 1: Review Card | Column 2: Standing vs Peers, Missing Items, Strengths & Improvements
    end
```
