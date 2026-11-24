"""
Purpose:
    Implements `GeminiClient` encapsulating Google Gemini API configuration
    (BYOK per-user API key & configurable model ID defaulting to `gemini-3.5-flash-lite`,
    with quick presets `gemini-3.5-flash-lite`, `gemini-3.5-flash`, `gemini-2.5-flash`,
    while allowing any custom model ID) and real multimodal inference via the official
    `google-genai` SDK — supporting standard Developer API keys (`AIza...` and `AQ....`),
    direct YouTube URI analysis (`types.Part` with `VideoMetadata` `content_layers`
    `["TRANSCRIPT"]` / `["AUDIO"]` or `fps=0.2` for fast 45-90 min lectures), and
    automatic retry + model fallback on transient `503 UNAVAILABLE` / `429 RESOURCE_EXHAUSTED`.
Inputs:
    `FirebaseRepository` persistence instance, `AppConfig` defaults, per-request BYOK
    key/model overrides (`X-Gemini-Api-Key`, `X-Gemini-Model`), and optional custom
    transport callable strictly for automated `pytest` unit tests.
Outputs:
    `GeminiClient` class managing Gemini credentials, model selection, YouTube video
    analysis, and structured JSON generation with zero silent fake fallbacks.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any

from src.config import AppConfig
from src.repository.firebase_repository import FirebaseRepository

logger = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
VALID_GEMINI_PRESETS = (
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
)
FALLBACK_MODEL_PRIORITY = (
    "gemini-3.5-flash",
    "gemini-2.5-flash",
    "gemini-3.5-flash-lite",
)

PLACEHOLDER_KEYS = {
    "",
    "your-google-gemini-api-key-here",
    "none",
    "null",
}

YOUTUBE_VIDEO_MODES = ("transcript", "audio", "full_video")


class GeminiHighDemandError(RuntimeError):
    """
    Purpose:
        Specialized runtime exception raised when all candidate Gemini models return
        transient `503 UNAVAILABLE` or `429 RESOURCE_EXHAUSTED` high-demand errors
        after automatic retry and fallback attempts.
    Inputs:
        message (str): Human-readable high-demand error detail for the HTTP 503 response.
    Outputs:
        `GeminiHighDemandError` instance.
    """


class GeminiClient:
    """
    Purpose:
        Centralized OOP wrapper for Google Gemini multimodal and text generation.
        Supports Bring-Your-Own-Key (BYOK) per-request credentials (`X-Gemini-Api-Key`,
        `X-Gemini-Model`) for students as well as Lecturer credentials persisted in
        Firebase RTDB (`/settings/gemini`), initializes the official `google-genai`
        SDK using the standard Gemini Developer API for all key types (`AIza...` and
        `AQ....`), supports fast YouTube lecture analysis via `VideoMetadata(content_layers=...)`
        with automatic `TRANSCRIPT -> AUDIO` fallback, and retries/falls back across
        Gemini models when transient `503 UNAVAILABLE` or `429` spikes occur.
    Inputs:
        repository (FirebaseRepository): Firebase RTDB persistence layer.
        config (AppConfig): Application configuration providing default key and model.
        transport (Callable[..., dict[str, Any]] | None): Optional injected generation
            callable strictly for deterministic unit testing in `tests/`.
    Outputs:
        `GeminiClient` service instance.
    """

    def __init__(
        self,
        repository: FirebaseRepository,
        config: AppConfig,
        transport: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        """
        Purpose:
            Initialize `GeminiClient` with repository, runtime config, and optional test transport.
        Inputs:
            repository (FirebaseRepository): Target Firebase RTDB repository.
            config (AppConfig): Default configuration object.
            transport (Callable[..., dict[str, Any]] | None): Optional mock transport for unit tests.
        Outputs:
            None.
        """
        self.repository = repository
        self.config = config
        self._transport = transport

    @staticmethod
    def mask_api_key(api_key: str) -> str:
        """
        Purpose:
            Mask a secret Gemini API key for safe display in the Lecturer Settings UI
            (e.g., `AIza...9xYz` or `AQ.A...9xYz` or `""` when unconfigured).
        Inputs:
            api_key (str): Raw API key string.
        Outputs:
            str: Masked API key string that never exposes the full secret.
        """
        clean = (api_key or "").strip()
        if not clean or clean.lower() in PLACEHOLDER_KEYS:
            return ""
        if len(clean) <= 8:
            return "****" + clean[-2:]
        return f"{clean[:4]}...{clean[-4:]}"

    @staticmethod
    def _build_genai_client(api_key: str, use_vertexai: bool = False) -> Any:
        """
        Purpose:
            Instantiate a `google.genai.Client` using the standard Google Gemini Developer
            API (`generativelanguage.googleapis.com`) for ALL key types — both AI Studio
            keys (`AIza...`) and `AQ....` keys. Vertex AI routing (`aiplatform.googleapis.com`)
            is opt-in only via `use_vertexai=True` to avoid `403 PERMISSION_DENIED` on
            standard projects. Configures an explicit 75 s HTTP timeout under Cloudflare's
            100 s gateway limit.
        Inputs:
            api_key (str): Valid Gemini API key.
            use_vertexai (bool): When True, routes through Vertex AI; default False.
        Outputs:
            Any: Configured `google.genai.Client` instance.
        """
        from google import genai
        from google.genai import types

        clean_key = (api_key or "").strip()
        http_opts = types.HttpOptions(timeout=75_000)
        try:
            if use_vertexai:
                return genai.Client(
                    vertexai=True, api_key=clean_key, http_options=http_opts
                )
            return genai.Client(api_key=clean_key, http_options=http_opts)
        except TypeError:
            if use_vertexai:
                return genai.Client(vertexai=True, api_key=clean_key)
            return genai.Client(api_key=clean_key)

    def resolve_credentials(
        self,
        api_key_override: str | None = None,
        model_id_override: str | None = None,
        allow_stored_key: bool = True,
    ) -> tuple[str, str]:
        """
        Purpose:
            Resolve `(api_key, model_id)`. If `allow_stored_key=True` (Lecturer mode), checks
            per-request overrides -> Firebase RTDB `/settings/gemini` -> `AppConfig` fallback.
            When the Lecturer explicitly clears their key (`key_cleared=True` or `"api_key": ""`
            exists in `/settings/gemini`), never silently falls back to `.env` `GEMINI_API_KEY`.
            If `allow_stored_key=False` (Student BYOK mode), strictly uses the student's
            request override without ever falling back to Lecturer `/settings/gemini` or `.env`.
        Inputs:
            api_key_override (str | None): Optional per-request BYOK API key (`X-Gemini-Api-Key`).
            model_id_override (str | None): Optional per-request model ID (`X-Gemini-Model`).
            allow_stored_key (bool): Whether to fall back to Lecturer/env key when override is omitted.
        Outputs:
            tuple[str, str]: `(active_api_key, active_model_id)`.
        """
        raw_override_key = (api_key_override or "").strip()
        clean_override_key = (
            "" if raw_override_key.lower() in PLACEHOLDER_KEYS else raw_override_key
        )
        raw_override_model = (model_id_override or "").strip()

        if not allow_stored_key:
            active_model = raw_override_model or DEFAULT_GEMINI_MODEL
            return clean_override_key, active_model

        stored = self.repository.get("/settings/gemini")
        stored_dict = stored if isinstance(stored, dict) else {}
        explicitly_cleared = bool(stored_dict.get("key_cleared", False)) or (
            "api_key" in stored_dict
            and not str(stored_dict.get("api_key") or "").strip()
        )

        if clean_override_key:
            active_key = clean_override_key
        elif explicitly_cleared:
            active_key = ""
        else:
            raw_key = str(
                stored_dict.get("api_key") or self.config.gemini_api_key or ""
            ).strip()
            active_key = "" if raw_key.lower() in PLACEHOLDER_KEYS else raw_key

        if raw_override_model:
            active_model = raw_override_model
        else:
            raw_model = str(
                stored_dict.get("model_id")
                or self.config.gemini_model
                or DEFAULT_GEMINI_MODEL
            ).strip()
            active_model = raw_model or DEFAULT_GEMINI_MODEL

        return active_key, active_model

    def get_settings(self) -> dict[str, Any]:
        """
        Purpose:
            Return the Lecturer's active Gemini configuration (with the API key masked)
            for the Lecturer Dashboard settings panel. Includes both `has_api_key` /
            `masked_api_key` and `has_key` / `masked_key` fields for clean UI binding.
        Inputs:
            None.
        Outputs:
            dict[str, Any]: Dictionary with `model_id`, `available_models`, `has_api_key`,
            `has_key`, `masked_api_key`, `masked_key`, and `updated_at`.
        """
        stored = self.repository.get("/settings/gemini")
        stored_dict = stored if isinstance(stored, dict) else {}
        api_key, model_id = self.resolve_credentials()
        has_key = bool(api_key)
        masked = self.mask_api_key(api_key) if has_key else ""
        return {
            "model_id": model_id,
            "available_models": list(VALID_GEMINI_PRESETS),
            "has_api_key": has_key,
            "has_key": has_key,
            "masked_api_key": masked,
            "masked_key": masked if has_key else None,
            "updated_at": stored_dict.get("updated_at"),
        }

    def update_settings(
        self,
        api_key: str | None = None,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Purpose:
            Update the Lecturer's Gemini API Key and/or Gemini Model ID in Firebase RTDB
            under `/settings/gemini`. Passing `api_key=""` explicitly clears the stored
            API key in Firebase (`key_cleared=True`, `api_key=""`) so `get_settings()`
            immediately reports `has_api_key=False` / `has_key=False` without falling back
            to `.env`.
        Inputs:
            api_key (str | None): New Gemini API key (pass None to keep existing key,
                or empty string `""` to clear).
            model_id (str | None): Desired Gemini model ID.
        Outputs:
            dict[str, Any]: Updated public settings dictionary with masked key.
        """
        stored = self.repository.get("/settings/gemini")
        stored_dict = stored if isinstance(stored, dict) else {}
        current_key, current_model = self.resolve_credentials()

        if model_id is not None:
            clean_model = model_id.strip()
            if not clean_model:
                raise ValueError("Gemini model_id cannot be empty.")
            next_model = clean_model
        else:
            next_model = current_model

        if api_key is not None:
            clean_key = api_key.strip()
            next_key = "" if clean_key.lower() in PLACEHOLDER_KEYS else clean_key
            key_cleared = not bool(next_key)
        else:
            next_key = current_key
            key_cleared = bool(stored_dict.get("key_cleared", False)) and not bool(
                next_key
            )

        record = {
            "api_key": next_key,
            "key_cleared": key_cleared,
            "model_id": next_model,
            "updated_at": int(time.time()),
        }
        self.repository.set("/settings/gemini", record)
        return self.get_settings()

    @staticmethod
    def _extract_json_dict(raw_text: str) -> dict[str, Any] | None:
        """
        Purpose:
            Parse a JSON object from an LLM text response, stripping optional
            Markdown ```json code fences if present.
        Inputs:
            raw_text (str): Raw text returned by Gemini.
        Outputs:
            dict[str, Any] | None: Parsed dictionary if valid JSON object, else None.
        """
        if not raw_text:
            return None
        cleaned = raw_text.strip()
        fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, re.DOTALL)
        if fence_match:
            cleaned = fence_match.group(1)
        else:
            brace_start = cleaned.find("{")
            brace_end = cleaned.rfind("}")
            if brace_start != -1 and brace_end > brace_start:
                cleaned = cleaned[brace_start : brace_end + 1]
        try:
            parsed = json.loads(cleaned)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    @staticmethod
    def _is_transient_overload_error(exc: Exception) -> bool:
        """
        Purpose:
            Detect whether a Gemini SDK exception represents a transient high-demand
            or rate-limit condition (`503 UNAVAILABLE`, `429 RESOURCE_EXHAUSTED`,
            `overloaded`, `high demand`, `temporarily unavailable`).
        Inputs:
            exc (Exception): Raised exception from `client.models.generate_content`.
        Outputs:
            bool: True if the error is a transient 503/429 overload condition.
        """
        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        if code in (503, 429):
            return True
        text = str(exc).upper()
        if "400" in text or "INVALID_ARGUMENT" in text:
            return False
        transient_markers = (
            "503",
            "UNAVAILABLE",
            "429",
            "RESOURCE_EXHAUSTED",
            "HIGH DEMAND",
            "OVERLOADED",
        )
        return any(marker in text for marker in transient_markers)

    @staticmethod
    def _build_youtube_part(
        file_uri: str,
        mime_type: str,
        video_mode: str,
    ) -> tuple[Any, str]:
        """
        Purpose:
            Construct a `google.genai.types.Part` for a YouTube video URI configured with
            `types.VideoMetadata` according to `video_mode`:
            - `"transcript"` (default): `VideoMetadata(content_layers=["TRANSCRIPT"])`
            - `"audio"`: `VideoMetadata(content_layers=["AUDIO"])`
            - `"full_video"`: `VideoMetadata(fps=0.2)`
        Inputs:
            file_uri (str): Canonical YouTube URL (`https://www.youtube.com/watch?v=...`).
            mime_type (str): Video MIME type (`"video/mp4"`).
            video_mode (str): Selected mode (`"transcript"`, `"audio"`, or `"full_video"`).
        Outputs:
            tuple[Any, str]: `(types.Part, normalized_mode)`.
        """
        from google.genai import types

        clean_mode = (video_mode or "transcript").strip().lower()
        if clean_mode not in YOUTUBE_VIDEO_MODES:
            clean_mode = "transcript"

        file_data = types.FileData(file_uri=file_uri, mime_type=mime_type or "video/mp4")
        try:
            if clean_mode == "transcript":
                meta = types.VideoMetadata(content_layers=["TRANSCRIPT"])
            elif clean_mode == "audio":
                meta = types.VideoMetadata(content_layers=["AUDIO"])
            else:
                meta = types.VideoMetadata(fps=0.2)
            return types.Part(file_data=file_data, video_metadata=meta), clean_mode
        except Exception:
            return (
                types.Part.from_uri(
                    file_uri=file_uri, mime_type=mime_type or "video/mp4"
                ),
                clean_mode,
            )

    def generate_structured_json(
        self,
        prompt: str,
        fallback_builder: Callable[[], dict[str, Any]] | None = None,
        media_bytes: bytes | None = None,
        mime_type: str | None = None,
        file_uri: str | None = None,
        video_mode: str = "transcript",
        api_key_override: str | None = None,
        model_id_override: str | None = None,
        allow_stored_key: bool = True,
        missing_key_error: str | None = None,
    ) -> dict[str, Any]:
        """
        Purpose:
            Execute a structured JSON generation request against the real Google Gemini API
            (or custom test transport in `pytest`).
            - For YouTube URLs (`file_uri`): uses `VideoMetadata(content_layers=["TRANSCRIPT"])`
              in `"transcript"` mode (falling back automatically to `content_layers=["AUDIO"]`
              if the video has no transcript or `TRANSCRIPT` fails for a non-overload reason),
              `content_layers=["AUDIO"]` in `"audio"` mode, or `VideoMetadata(fps=0.2)` in
              `"full_video"` mode.
            - Automatic 503/429 Retry & Model Fallback: if the selected model returns
              `503 UNAVAILABLE` or `429 RESOURCE_EXHAUSTED`, retries once after ~1.5s and then
              cycles through `[selected_model, "gemini-3.5-flash", "gemini-2.5-flash",
              "gemini-3.5-flash-lite"]` (deduplicated). If all candidate models are overloaded,
              raises `GeminiHighDemandError` (mapped to HTTP 503).
            - Never returns silent fake/hardcoded fallback content in production.
        Inputs:
            prompt (str): Structured instruction prompt requesting JSON output.
            fallback_builder (Callable[[], dict[str, Any]] | None): Unused legacy parameter.
            media_bytes (bytes | None): Optional binary payload (PDF/document bytes).
            mime_type (str | None): Optional MIME type (`"video/mp4"`, `"application/pdf"`, etc.).
            file_uri (str | None): Optional public YouTube URL.
            video_mode (str): YouTube analysis mode (`"transcript"`, `"audio"`, `"full_video"`).
            api_key_override (str | None): Optional BYOK API key header override.
            model_id_override (str | None): Optional BYOK model ID header override.
            allow_stored_key (bool): False for student BYOK mode so Lecturer/env keys are never used.
            missing_key_error (str | None): Custom error message when API key is missing.
        Outputs:
            dict[str, Any]: Structured result dictionary enriched with `model_used`, `provider`,
            and `video_mode_used` (when `file_uri` is provided).
        """
        api_key, primary_model_id = self.resolve_credentials(
            api_key_override=api_key_override,
            model_id_override=model_id_override,
            allow_stored_key=allow_stored_key,
        )

        clean_mode = (video_mode or "transcript").strip().lower()
        if clean_mode not in YOUTUBE_VIDEO_MODES:
            clean_mode = "transcript"

        if self._transport is not None:
            result = self._transport(
                prompt=prompt,
                model_id=primary_model_id,
                api_key=api_key,
                media_bytes=media_bytes,
                mime_type=mime_type,
                file_uri=file_uri,
            )
            out = {
                **result,
                "model_used": primary_model_id,
                "provider": "custom_transport",
            }
            if file_uri:
                out["video_mode_used"] = clean_mode
            return out

        if not api_key:
            msg = missing_key_error or (
                "Gemini API Key is missing. Please enter your Google Gemini API key "
                "(AIza... or AQ....) in the Gemini AI Settings panel above."
            )
            logger.error(msg)
            raise RuntimeError(msg)

        from google.genai import types

        client = self._build_genai_client(api_key)

        candidate_models: list[str] = []
        for m in (primary_model_id, *FALLBACK_MODEL_PRIORITY):
            clean_m = (m or "").strip()
            if clean_m and clean_m not in candidate_models:
                candidate_models.append(clean_m)

        def _invoke_with_contents(
            target_model: str, contents: list[Any]
        ) -> dict[str, Any]:
            response = client.models.generate_content(
                model=target_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            raw_text = getattr(response, "text", "") or ""
            parsed = self._extract_json_dict(raw_text)
            if parsed is None:
                raise RuntimeError(
                    f"Gemini model '{target_model}' did not return valid JSON. Raw response: {raw_text[:250]}"
                )
            return parsed

        def _build_request_contents(active_video_mode: str) -> tuple[list[Any], str]:
            if file_uri and file_uri.strip():
                uri_part, used_mode = self._build_youtube_part(
                    file_uri=file_uri.strip(),
                    mime_type=mime_type or "video/mp4",
                    video_mode=active_video_mode,
                )
                return [uri_part, prompt], used_mode
            contents: list[Any] = []
            if media_bytes and mime_type:
                contents.append(
                    types.Part.from_bytes(data=media_bytes, mime_type=mime_type)
                )
            contents.append(prompt)
            return contents, active_video_mode

        def _run_across_models_with_retry(
            active_video_mode: str,
        ) -> tuple[dict[str, Any], str, str]:
            contents, used_mode = _build_request_contents(active_video_mode)
            last_overload_exc: Exception | None = None

            for model_idx, candidate_model in enumerate(candidate_models):
                # For the primary selected model, retry once after ~1.5s before moving to fallbacks
                max_attempts = 2 if model_idx == 0 else 1
                for attempt in range(max_attempts):
                    try:
                        parsed = _invoke_with_contents(candidate_model, contents)
                        if model_idx > 0 or attempt > 0:
                            logger.info(
                                "Gemini request succeeded using model '%s' (attempt %d, fallback_index=%d).",
                                candidate_model,
                                attempt + 1,
                                model_idx,
                            )
                        return parsed, candidate_model, used_mode
                    except Exception as exc:
                        if self._is_transient_overload_error(exc):
                            last_overload_exc = exc
                            logger.warning(
                                "Gemini model '%s' returned transient 503/429 overload (attempt %d/%d): %s",
                                candidate_model,
                                attempt + 1,
                                max_attempts,
                                exc,
                            )
                            if attempt + 1 < max_attempts:
                                time.sleep(1.5)
                                continue
                            # Move to next fallback model in candidate_models
                            break
                        raise

            logger.error(
                "All candidate Gemini models (%s) returned 503/429 high-demand errors: %s",
                ", ".join(candidate_models),
                last_overload_exc,
            )
            raise GeminiHighDemandError(
                "Gemini models are temporarily experiencing high demand (503). Please retry in a few seconds."
            ) from last_overload_exc

        try:
            if file_uri and file_uri.strip() and clean_mode == "transcript":
                try:
                    parsed, used_model, used_mode = _run_across_models_with_retry(
                        "transcript"
                    )
                except GeminiHighDemandError:
                    raise
                except Exception as transcript_exc:
                    logger.warning(
                        "YouTube TRANSCRIPT layer unavailable or failed (%s); falling back automatically to AUDIO layer.",
                        transcript_exc,
                    )
                    try:
                        parsed, used_model, used_mode = _run_across_models_with_retry(
                            "audio"
                        )
                    except GeminiHighDemandError:
                        raise
                    except Exception as audio_exc:
                        logger.warning(
                            "YouTube AUDIO layer unavailable or failed (%s); falling back automatically to Low FPS (0.2) video.",
                            audio_exc,
                        )
                        parsed, used_model, used_mode = _run_across_models_with_retry(
                            "full_video"
                        )
            else:
                parsed, used_model, used_mode = _run_across_models_with_retry(
                    clean_mode
                )

            out: dict[str, Any] = {
                **parsed,
                "model_used": used_model,
                "provider": "gemini_live",
            }
            if file_uri and file_uri.strip():
                out["video_mode_used"] = used_mode
            return out
        except GeminiHighDemandError:
            raise
        except RuntimeError:
            raise
        except Exception as exc:
            err_msg = f"Gemini API request failed (model '{primary_model_id}'): {exc}"
            logger.error(err_msg, exc_info=True)
            raise RuntimeError(err_msg) from exc
