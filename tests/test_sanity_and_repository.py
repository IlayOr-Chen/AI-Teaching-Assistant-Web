"""
Purpose:
    Checkpoint 1 sanity, configuration, IP resolution, FirebaseRepository mock adapter,
    and FastAPI application skeleton unit and integration tests.
Inputs:
    Pytest fixtures and in-memory mock adapters.
Outputs:
    Deterministic test assertions verifying core infrastructure with zero production side effects.
"""

from __future__ import annotations

from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from src.config import AppConfig
from src.main import create_app
from src.repository.firebase_repository import (
    FirebaseRepository,
    InMemoryFirebaseAdapter,
)
from src.utils.ip_resolver import ClientIPResolver

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_required_course_assets_exist() -> None:
    """
    Purpose:
        Verify that all required real-world evaluation assets exist in `/assets`.
    Inputs:
        None.
    Outputs:
        None (asserts existence and non-zero size of Excel, JPEG, and DOCX assets).
    """
    assets_dir = PROJECT_ROOT / "assets"
    assert assets_dir.is_dir(), "Expected /assets directory to exist."

    excel_file = assets_dir / "courseid_130158_participants.xlsx"
    photo_file = assets_dir / "p3.jpeg"
    zoom_docx_candidates = list(assets_dir.glob("Example_ Class*by zoom).docx"))

    assert excel_file.is_file() and excel_file.stat().st_size > 0
    assert photo_file.is_file() and photo_file.stat().st_size > 0
    assert len(zoom_docx_candidates) == 1 and zoom_docx_candidates[0].stat().st_size > 0


def test_app_config_defaults_and_env_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Purpose:
        Verify that `AppConfig.from_env()` parses environment variables and defaults
        accurately into an immutable configuration object.
    Inputs:
        monkeypatch (pytest.MonkeyPatch): Pytest environment variable patcher.
    Outputs:
        None.
    """
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.setenv("USE_MOCK_FIREBASE", "true")
    monkeypatch.setenv("QR_ROTATION_SECONDS", "15")
    monkeypatch.setenv("DEVICE_COOLDOWN_MINUTES", "15")

    cfg = AppConfig.from_env()
    assert cfg.use_mock_firebase is True
    assert cfg.qr_rotation_seconds == 15
    assert cfg.device_cooldown_minutes == 15
    assert cfg.gemini_model == "gemini-3.5-flash-lite"
    assert "ai-teaching-assistant-web-default-rtdb.firebaseio.com" in cfg.firebase_db_url


def test_firebase_repository_in_memory_crud_and_isolation() -> None:
    """
    Purpose:
        Verify `FirebaseRepository` with `use_mock=True` supports full hierarchical
        CRUD (`set`, `get`, `update`, `push`, `delete`, `reset_mock`) with deep-copy isolation.
    Inputs:
        None.
    Outputs:
        None.
    """
    repo = FirebaseRepository(use_mock=True)

    # 1. Non-existent path returns None
    assert repo.get("/students/non_existent") is None

    # 2. Set creates nested hierarchy and returns deep copy
    student_payload = {
        "full_name": "Guy Aviv",
        "email": "guygg197@gmail.com",
        "group": "3.0",
    }
    repo.set("/students/stu_001", student_payload)
    fetched = repo.get("/students/stu_001")
    assert fetched == student_payload

    # Mutating returned dictionary must not mutate internal repository store
    fetched["group"] = "MUTATED"
    assert repo.get("/students/stu_001")["group"] == "3.0"

    # 3. Update performs shallow merge and removes keys set to None
    repo.update(
        "/students/stu_001",
        {"group": "4.0", "checked_in": True, "email": None},
    )
    updated = repo.get("/students/stu_001")
    assert updated["group"] == "4.0"
    assert updated["checked_in"] is True
    assert "email" not in updated

    # 4. Push appends uniquely keyed entries under collection path
    key1 = repo.push("/comments", {"author": "Anonymous", "text": "Great lecture!"})
    key2 = repo.push("/comments", {"author": "Liel", "text": "Please share slides."})
    assert key1 != key2
    all_comments = repo.get("/comments")
    assert len(all_comments) == 2
    assert all_comments[key1]["text"] == "Great lecture!"
    assert all_comments[key2]["author"] == "Liel"

    # 5. Setting None deletes the node (Firebase RTDB convention)
    repo.set(f"/comments/{key1}", None)
    assert repo.get(f"/comments/{key1}") is None

    # 6. Delete removes existing node and returns False when already deleted
    assert repo.delete("/students/stu_001") is True
    assert repo.delete("/students/stu_001") is False

    # 7. reset_mock wipes the entire mock store
    repo.reset_mock()
    assert repo.get("/comments") is None


def test_firebase_repository_live_guards_and_rest_url_formatting() -> None:
    """
    Purpose:
        Verify that `FirebaseRepository` formats REST URLs accurately and prevents
        `reset_mock()` from executing when `use_mock=False`.
    Inputs:
        None.
    Outputs:
        None.
    """
    live_repo = FirebaseRepository(
        db_url="https://ai-teaching-assistant-web-default-rtdb.firebaseio.com/",
        credentials_path="non_existent_key.json",
        use_mock=False,
    )
    assert (
        live_repo._build_rest_url("/users/lecturers")
        == "https://ai-teaching-assistant-web-default-rtdb.firebaseio.com/users/lecturers.json"
    )
    assert (
        live_repo._build_rest_url("/")
        == "https://ai-teaching-assistant-web-default-rtdb.firebaseio.com/.json"
    )
    with pytest.raises(RuntimeError, match="only permitted when use_mock=True"):
        live_repo.reset_mock()


def test_client_ip_resolver_strict_priority_order() -> None:
    """
    Purpose:
        Verify that `ClientIPResolver` extracts client IPs in the strict priority order:
        1) `CF-Connecting-IP` -> 2) `X-Forwarded-For` (first IP) -> 3) `client.host`.
    Inputs:
        None.
    Outputs:
        None.
    """
    # Priority 1: CF-Connecting-IP overrides X-Forwarded-For and fallback host
    ip, source = ClientIPResolver.resolve_from_headers(
        {
            "CF-Connecting-IP": "147.235.200.15",
            "X-Forwarded-For": "10.0.0.1, 172.16.0.1",
        },
        fallback_host="127.0.0.1",
    )
    assert ip == "147.235.200.15"
    assert source == "CF-Connecting-IP"

    # Priority 2: X-Forwarded-For extracts first IP in comma-separated chain
    ip, source = ClientIPResolver.resolve_from_headers(
        {"X-Forwarded-For": "84.111.22.33, 10.10.0.5"},
        fallback_host="127.0.0.1",
    )
    assert ip == "84.111.22.33"
    assert source == "X-Forwarded-For"

    # Priority 3: Local socket fallback when no proxy headers exist
    ip, source = ClientIPResolver.resolve_from_headers(
        {},
        fallback_host="192.168.1.50",
    )
    assert ip == "192.168.1.50"
    assert source == "client.host"


def test_fastapi_skeleton_endpoints_and_static_assets() -> None:
    """
    Purpose:
        Verify that the FastAPI application boots cleanly via `create_app`, serves
        the HTML landing shell (`/`), static file (`/static/index.html`), `/api/health`,
        and `/api/network-info` with header-based IP resolution.
    Inputs:
        None.
    Outputs:
        None.
    """
    mock_cfg = AppConfig(use_mock_firebase=True)
    mock_repo = FirebaseRepository(use_mock=True, mock_adapter=InMemoryFirebaseAdapter())
    test_app = create_app(config=mock_cfg, repository=mock_repo)
    client = TestClient(test_app)

    # Root HTML page
    root_res = client.get("/")
    assert root_res.status_code == 200
    assert "Web-Based AI Teaching Assistant" in root_res.text

    # Static file mounting
    static_res = client.get("/static/index.html")
    assert static_res.status_code == 200
    assert "Smart Classroom Platform" in static_res.text

    # Health check endpoint
    health_res = client.get("/api/health")
    assert health_res.status_code == 200
    health_data = health_res.json()
    assert health_data["status"] == "ok"
    assert health_data["firebase_mode"] == "mock"

    # Network info endpoint with Cloudflare header
    net_res = client.get(
        "/api/network-info",
        headers={"CF-Connecting-IP": "213.57.120.88"},
    )
    assert net_res.status_code == 200
    assert net_res.json() == {"ip": "213.57.120.88", "source": "CF-Connecting-IP"}
