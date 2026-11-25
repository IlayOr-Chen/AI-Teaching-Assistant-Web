"""
Purpose:
    Implements `GeminiClient` encapsulating Google Gemini API configuration
    (BYOK per-user API key & configurable model ID defaulting to `gemini-3.5-flash-lite`,
    with verified presets `gemini-3.8-flash`, `gemini-3.5-flash-lite`, and `gemini-3.5-flash`,
    while allowing any custom free-text model ID) and real multimodal inference via the
    official `google-genai` SDK — supporting standard Developer API keys (`AIza...` and
    `AQ....`), YouTube video URI analysis (`types.Part` with `VideoMetadata(fps=0.1)` and
    automatic `Part.from_uri` fallback if `video_metadata` returns HTTP 400), and
    automatic retry + model fallback across `[selected_model, "gemini-3.8-flash",
    "gemini-3.5-flash", "gemini-3.5-flash-lite"]` on transient `503 UNAVAILABLE`,
    `429 RESOURCE_EXHAUSTED`, or `404 NOT_FOUND` errors.
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
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
)
FALLBACK_MODEL_PRIORITY = (
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
)

PLACEHOLDER_KEYS = {
    "",
    "your-google-gemini-api-key-here",
    "none",
    "null",
}

GEMINI_503_HIGH_DEMAND_MESSAGE = (
    "Google's Gemini servers are currently experiencing temporary high demand (503). "
    "Please retry in a few seconds or switch models (e.g., gemini-3.8-flash, "
    "gemini-3.5-flash-lite, or gemini-3.5-flash)."
)

GEMINI_429_RATE_LIMIT_MESSAGE = (
    "Gemini API rate limit / quota reached (429) for this API key. "
    "Please wait a moment or try switching models."
)


class GeminiHighDemandError(RuntimeError):
    """
    Purpose:
        Specialized runtime exception raised when all candidate Gemini models return
        transient `503 UNAVAILABLE` high-demand errors after automatic retry and
        fallback attempts.
    Inputs:
        message (str): Human-readable high-demand error detail for the HTTP 503 response.
    Outputs:
        `GeminiHighDemandError` instance.
    """


class GeminiRateLimitError(RuntimeError):
    """
    Purpose:
        Specialized runtime exception raised when all candidate Gemini models return
        `429 RESOURCE_EXHAUSTED` rate-limit / quota errors after automatic retry and
        fallback attempts.
    Inputs:
        message (str): Human-readable rate-limit error detail for the HTTP 429 response.
    Outputs:
        `GeminiRateLimitError` instance.
    """


class GeminiClient:
    """
    Purpose:
        Centralized OOP wrapper for Google Gemini multimodal and text generation.
        Supports Bring-Your-Own-Key (BYOK) per-request credentials (`X-Gemini-Api-Key`,
        `X-Gemini-Model`) for students as well as Lecturer credentials persisted in
        Firebase RTDB (`/settings/gemini`), initializes the official `google-genai`
        SDK using the standard Gemini Developer API for all key types (`AIza...` and
        `AQ....`), supports direct YouTube lecture analysis via `VideoMetadata(fps=0.1)`
        with automatic fallback to plain `Part.from_uri` on 400 errors, and retries/falls
        back across `[selected_model, "gemini-3.8-flash", "gemini-3.5-flash",
        "gemini-3.5-flash-lite"]` whenever a model returns `503`, `429`, or `404`.
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
    def _classify_fallback_error(exc: Exception) -> str | None:
        """
        Purpose:
            Classify whether a Gemini SDK exception should trigger fallback to the next
            candidate model in `FALLBACK_MODEL_PRIORITY`:
            - `"503"`: transient high-demand / unavailable (`503 UNAVAILABLE`, `OVERLOADED`)
            - `"429"`: rate-limit / quota exhausted (`429 RESOURCE_EXHAUSTED`, `QUOTA`)
            - `"404"`: model unavailable / deprecated (`404 NOT_FOUND`, `NO LONGER AVAILABLE`)
            - `None`: non-fallback error (e.g., `400 INVALID_ARGUMENT`, `403 PERMISSION_DENIED`)
        Inputs:
            exc (Exception): Raised exception from `client.models.generate_content`.
        Outputs:
            str | None: `"503"`, `"429"`, `"404"`, or `None`.
        """
        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        if code == 503:
            return "503"
        if code == 429:
            return "429"
        if code == 404:
            return "404"

        text = str(exc).upper()
        if "400" in text or "INVALID_ARGUMENT" in text:
            return None
        if any(k in text for k in ("404", "NOT_FOUND", "NO LONGER AVAILABLE", "IS NOT FOUND")):
            return "404"
        if any(k in text for k in ("429", "RESOURCE_EXHAUSTED", "RATE LIMIT", "QUOTA")):
            return "429"
        if any(k in text for k in ("503", "UNAVAILABLE", "HIGH DEMAND", "OVERLOADED")):
            return "503"
        return None

    @staticmethod
    def _build_youtube_part(
        file_uri: str,
        mime_type: str = "video/mp4",
        use_low_fps: bool = True,
    ) -> Any:
        """
        Purpose:
            Construct a `google.genai.types.Part` for a YouTube video URI.
            When `use_low_fps=True`, attaches `types.VideoMetadata(fps=0.1)` (1 frame every
            10 seconds — valid Gemini Developer API field) to keep token count low on long
            lectures. Never sets `content_layers`.
            When `use_low_fps=False`, builds a plain `types.Part.from_uri(file_uri=..., mime_type=...)`.
        Inputs:
            file_uri (str): Canonical YouTube URL (`https://www.youtube.com/watch?v=...`).
            mime_type (str): Video MIME type (`"video/mp4"`).
            use_low_fps (bool): Whether to include `VideoMetadata(fps=0.1)`.
        Outputs:
            Any: Configured `google.genai.types.Part` instance.
        """
        from google.genai import types

        clean_mime = mime_type or "video/mp4"
        if use_low_fps:
            try:
                file_data = types.FileData(file_uri=file_uri, mime_type=clean_mime)
                meta = types.VideoMetadata(fps=0.1)
                return types.Part(file_data=file_data, video_metadata=meta)
            except Exception:
                pass
        return types.Part.from_uri(file_uri=file_uri, mime_type=clean_mime)

    def generate_structured_json(
        self,
        prompt: str,
        fallback_builder: Callable[[], dict[str, Any]] | None = None,
        media_bytes: bytes | None = None,
        mime_type: str | None = None,
        file_uri: str | None = None,
        api_key_override: str | None = None,
        model_id_override: str | None = None,
        allow_stored_key: bool = True,
        missing_key_error: str | None = None,
    ) -> dict[str, Any]:
        """
        Purpose:
            Execute a structured JSON generation request against the real Google Gemini API
            (or custom test transport in `pytest`).
            - For YouTube URLs (`file_uri`): passes `types.Part` with `VideoMetadata(fps=0.1)`
              first, and if the API returns a `400` on `video_metadata`, retries once with
              plain `types.Part.from_uri(file_uri=..., mime_type="video/mp4")`.
            - Automatic 503/429/404 Retry & Model Fallback: if a candidate model returns
              `503 UNAVAILABLE`, `429 RESOURCE_EXHAUSTED`, or `404 NOT_FOUND`, logs it and
              cycles through `[selected_model, "gemini-3.8-flash", "gemini-3.5-flash",
              "gemini-3.5-flash-lite"]` (deduplicated; retries primary once on 503/429 before
              moving on). If all candidate models are overloaded or rate-limited, raises
              `GeminiHighDemandError` (HTTP 503) or `GeminiRateLimitError` (HTTP 429) with a
              clear user-facing message.
            - Never returns silent fake/hardcoded fallback content in production.
        Inputs:
            prompt (str): Structured instruction prompt requesting JSON output.
            fallback_builder (Callable[[], dict[str, Any]] | None): Unused legacy parameter.
            media_bytes (bytes | None): Optional binary payload (PDF/document bytes).
            mime_type (str | None): Optional MIME type (`"video/mp4"`, `"application/pdf"`, etc.).
            file_uri (str | None): Optional public YouTube URL.
            api_key_override (str | None): Optional BYOK API key header override.
            model_id_override (str | None): Optional BYOK model ID header override.
            allow_stored_key (bool): False for student BYOK mode so Lecturer/env keys are never used.
            missing_key_error (str | None): Custom error message when API key is missing.
        Outputs:
            dict[str, Any]: Structured result dictionary enriched with `model_used` and `provider`.
        """
        api_key, primary_model_id = self.resolve_credentials(
            api_key_override=api_key_override,
            model_id_override=model_id_override,
            allow_stored_key=allow_stored_key,
        )

        if self._transport is not None:
            result = self._transport(
                prompt=prompt,
                model_id=primary_model_id,
                api_key=api_key,
                media_bytes=media_bytes,
                mime_type=mime_type,
                file_uri=file_uri,
            )
            return {
                **result,
                "model_used": primary_model_id,
                "provider": "custom_transport",
            }

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

        def _build_request_contents(use_low_fps: bool = True) -> list[Any]:
            if file_uri and file_uri.strip():
                uri_part = self._build_youtube_part(
                    file_uri=file_uri.strip(),
                    mime_type=mime_type or "video/mp4",
                    use_low_fps=use_low_fps,
                )
                return [uri_part, prompt]
            contents: list[Any] = []
            if media_bytes and mime_type:
                contents.append(
                    types.Part.from_bytes(data=media_bytes, mime_type=mime_type)
                )
            contents.append(prompt)
            return contents

        def _run_across_models_with_retry(
            use_low_fps: bool = True,
        ) -> tuple[dict[str, Any], str]:
            contents = _build_request_contents(use_low_fps=use_low_fps)
            last_exc: Exception | None = None
            seen_kinds: set[str] = set()

            for model_idx, candidate_model in enumerate(candidate_models):
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
                        return parsed, candidate_model
                    except Exception as exc:
                        kind = self._classify_fallback_error(exc)
                        if kind is not None:
                            last_exc = exc
                            seen_kinds.add(kind)
                            logger.warning(
                                "Gemini model '%s' returned %s error (attempt %d/%d): %s",
                                candidate_model,
                                kind,
                                attempt + 1,
                                max_attempts,
                                exc,
                            )
                            # On 404 (model deprecated/not found), skip straight to next model without retrying
                            if kind == "404":
                                break
                            if attempt + 1 < max_attempts:
                                time.sleep(1.5)
                                continue
                            break
                        raise

            if "503" in seen_kinds:
                logger.error(
                    "All candidate Gemini models (%s) failed (last error: %s)",
                    ", ".join(candidate_models),
                    last_exc,
                )
                raise GeminiHighDemandError(GEMINI_503_HIGH_DEMAND_MESSAGE) from last_exc
            if "429" in seen_kinds:
                logger.error(
                    "All candidate Gemini models (%s) hit 429 rate limits (last error: %s)",
                    ", ".join(candidate_models),
                    last_exc,
                )
                raise GeminiRateLimitError(GEMINI_429_RATE_LIMIT_MESSAGE) from last_exc

            raise RuntimeError(
                f"All candidate Gemini models ({', '.join(candidate_models)}) failed: {last_exc}"
            ) from last_exc

        try:
            if file_uri and file_uri.strip():
                try:
                    parsed, used_model = _run_across_models_with_retry(use_low_fps=True)
                except (GeminiHighDemandError, GeminiRateLimitError):
                    raise
                except Exception as meta_exc:
                    exc_text = str(meta_exc).upper()
                    if "400" in exc_text or "INVALID_ARGUMENT" in exc_text or "VIDEO_METADATA" in exc_text:
                        logger.warning(
                            "Gemini rejected VideoMetadata(fps=0.1) (%s); retrying with plain Part.from_uri.",
                            meta_exc,
                        )
                        parsed, used_model = _run_across_models_with_retry(
                            use_low_fps=False
                        )
                    else:
                        raise
            else:
                parsed, used_model = _run_across_models_with_retry(use_low_fps=False)

            return {
                **parsed,
                "model_used": used_model,
                "provider": "gemini_live",
            }
        except (GeminiHighDemandError, GeminiRateLimitError):
            raise
        except RuntimeError:
            raise
        except Exception as exc:
            err_msg = f"Gemini API request failed (model '{primary_model_id}'): {exc}"
            logger.error(err_msg, exc_info=True)
            raise RuntimeError(err_msg) from exc
