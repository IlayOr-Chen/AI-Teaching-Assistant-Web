"""
Purpose:
    Implements `GeminiClient` encapsulating Google Gemini API configuration
    (BYOK per-user API key & configurable model ID defaulting to `gemini-3.5-flash-lite`,
    with quick presets `gemini-3.5-flash-lite`, `gemini-3.5-flash`, `gemini-2.5-flash`,
    while allowing any custom model ID) and real multimodal inference via the official
    `google-genai` SDK — supporting both AI Studio keys (`AIza...`) and Vertex AI
    Express keys (`AQ....`), direct YouTube URI analysis (`Part.from_uri`), and
    MP4 uploads (`<= 20 MB`) via the Gemini Files API.
Inputs:
    `FirebaseRepository` persistence instance, `AppConfig` defaults, per-request BYOK
    key/model overrides (`X-Gemini-Api-Key`, `X-Gemini-Model`), and optional custom
    transport callable strictly for automated `pytest` unit tests.
Outputs:
    `GeminiClient` class managing Gemini credentials, model selection, Video/YouTube
    analysis, and structured JSON generation with zero silent fake fallbacks.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import tempfile
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

PLACEHOLDER_KEYS = {
    "",
    "your-google-gemini-api-key-here",
    "none",
    "null",
}


class GeminiClient:
    """
    Purpose:
        Centralized OOP wrapper for Google Gemini multimodal and text generation.
        Supports Bring-Your-Own-Key (BYOK) per-request credentials (`X-Gemini-Api-Key`,
        `X-Gemini-Model`) for students as well as Lecturer credentials persisted in
        Firebase RTDB (`/settings/gemini`), and initializes the official `google-genai`
        SDK for both AI Studio (`AIza...`) and Vertex AI Express (`AQ....`) keys.
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
    def _build_genai_client(api_key: str) -> Any:
        """
        Purpose:
            Instantiate a `google.genai.Client` supporting both Vertex AI Express API keys
            (prefix `AQ.`) and Google AI Studio API keys (prefix `AIza` / standard keys),
            configured with an explicit 75s HTTP timeout under Cloudflare's 100s gateway limit.
        Inputs:
            api_key (str): Valid Gemini or Vertex AI Express API key.
        Outputs:
            Any: Configured `google.genai.Client` instance.
        """
        from google import genai
        from google.genai import types

        clean_key = (api_key or "").strip()
        http_opts = types.HttpOptions(timeout=75_000)
        try:
            if clean_key.startswith("AQ."):
                return genai.Client(
                    vertexai=True, api_key=clean_key, http_options=http_opts
                )
            return genai.Client(api_key=clean_key, http_options=http_opts)
        except TypeError:
            # Minimal test doubles in unit tests may omit http_options
            if clean_key.startswith("AQ."):
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

        if clean_override_key:
            active_key = clean_override_key
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
            for the Lecturer Dashboard settings panel.
        Inputs:
            None.
        Outputs:
            dict[str, Any]: Dictionary with `model_id`, `available_models`, `has_api_key`,
            `masked_api_key`, and `updated_at`.
        """
        stored = self.repository.get("/settings/gemini")
        stored_dict = stored if isinstance(stored, dict) else {}
        api_key, model_id = self.resolve_credentials()
        return {
            "model_id": model_id,
            "available_models": list(VALID_GEMINI_PRESETS),
            "has_api_key": bool(api_key),
            "masked_api_key": self.mask_api_key(api_key),
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
            under `/settings/gemini`. Supports any valid custom model ID string
            (e.g., `gemini-3.5-flash-lite`, `gemini-3.5-flash`, `gemini-2.5-flash`).
        Inputs:
            api_key (str | None): New Gemini API key (pass None to keep existing key,
                or empty string `""` to clear).
            model_id (str | None): Desired Gemini model ID.
        Outputs:
            dict[str, Any]: Updated public settings dictionary with masked key.
        """
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
        else:
            next_key = current_key

        record = {
            "api_key": next_key,
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
            (or custom test transport in `pytest`). Supports both AI Studio (`AIza...`)
            and Vertex AI Express (`AQ....`) keys, direct YouTube URI analysis
            (`types.Part.from_uri(file_uri=..., mime_type="video/mp4")`), MP4 uploads
            (`<= 20 MB`, using Gemini Files API for AI Studio or `Part.from_bytes` for
            Vertex AI Express), and multimodal PDF/text evaluation.
            Never returns silent fake/hardcoded fallback content in production.
        Inputs:
            prompt (str): Structured instruction prompt requesting JSON output.
            fallback_builder (Callable[[], dict[str, Any]] | None): Deprecated/unused in production.
            media_bytes (bytes | None): Optional binary payload (MP4 video or document bytes).
            mime_type (str | None): Optional MIME type (`"video/mp4"`, `"application/pdf"`, etc.).
            file_uri (str | None): Optional public YouTube URL passed via `Part.from_uri`.
            api_key_override (str | None): Optional BYOK API key header override.
            model_id_override (str | None): Optional BYOK model ID header override.
            allow_stored_key (bool): False for student BYOK mode so Lecturer/env keys are never used.
            missing_key_error (str | None): Custom error message when API key is missing.
        Outputs:
            dict[str, Any]: Structured result dictionary enriched with `model_used` and `provider`.
            Raises `ValueError` or `RuntimeError` if API key is missing or Gemini returns an error.
        """
        api_key, model_id = self.resolve_credentials(
            api_key_override=api_key_override,
            model_id_override=model_id_override,
            allow_stored_key=allow_stored_key,
        )

        if self._transport is not None:
            result = self._transport(
                prompt=prompt,
                model_id=model_id,
                api_key=api_key,
                media_bytes=media_bytes,
                mime_type=mime_type,
                file_uri=file_uri,
            )
            return {**result, "model_used": model_id, "provider": "custom_transport"}

        if not api_key:
            msg = missing_key_error or (
                "Gemini API Key is missing. Please enter your Google Gemini API key "
                "(AIza... or AQ....) in the Gemini AI Settings panel above."
            )
            logger.error(msg)
            raise RuntimeError(msg)

        try:
            from google.genai import types

            client = self._build_genai_client(api_key)

            if file_uri and file_uri.strip():
                logger.info(
                    "Analyzing video URI '%s' directly with Gemini model '%s'...",
                    file_uri.strip(),
                    model_id,
                )
                uri_part = types.Part.from_uri(
                    file_uri=file_uri.strip(),
                    mime_type=mime_type or "video/mp4",
                )
                response = client.models.generate_content(
                    model=model_id,
                    contents=[uri_part, prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.2,
                    ),
                )
            elif media_bytes and mime_type and mime_type.lower().startswith("video/"):
                # Vertex AI Express keys (AQ....) use inline Part.from_bytes for <= 20 MB MP4 clips,
                # whereas Gemini AI Studio keys (AIza...) upload via the official Gemini Files API.
                if getattr(client, "vertexai", False):
                    logger.info(
                        "Analyzing MP4 video (%d bytes) inline via Vertex AI Express model '%s'...",
                        len(media_bytes),
                        model_id,
                    )
                    video_part = types.Part.from_bytes(
                        data=media_bytes, mime_type=mime_type
                    )
                    response = client.models.generate_content(
                        model=model_id,
                        contents=[video_part, prompt],
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            temperature=0.2,
                        ),
                    )
                else:
                    temp_path: str | None = None
                    uploaded_file: Any = None
                    try:
                        # Write strictly inside OS temp dir (never watched workspace dirs) with ASCII-safe name
                        with tempfile.NamedTemporaryFile(
                            prefix="gemini_lecture_",
                            suffix=".mp4",
                            dir=tempfile.gettempdir(),
                            delete=False,
                        ) as tmp_file:
                            tmp_file.write(media_bytes)
                            temp_path = tmp_file.name

                        logger.info(
                            "Uploading MP4 video (%d bytes) to Gemini Files API via model '%s'...",
                            len(media_bytes),
                            model_id,
                        )
                        uploaded_file = client.files.upload(file=temp_path)

                        poll_start = time.time()
                        while True:
                            state_obj = getattr(uploaded_file, "state", None)
                            state_name = (
                                getattr(state_obj, "name", None)
                                or str(state_obj or "").upper()
                            )
                            if "PROCESSING" not in state_name:
                                break
                            if time.time() - poll_start > 65:
                                raise RuntimeError(
                                    "Timed out waiting for Gemini Files API to finish processing the MP4 video (please use a shorter clip or a public YouTube URL)."
                                )
                            time.sleep(2.0)
                            uploaded_file = client.files.get(name=uploaded_file.name)

                        final_state_obj = getattr(uploaded_file, "state", None)
                        final_state = (
                            getattr(final_state_obj, "name", None)
                            or str(final_state_obj or "").upper()
                        )
                        if "FAILED" in final_state:
                            raise RuntimeError(
                                f"Gemini Files API failed to process the uploaded MP4 video (state={final_state})."
                            )

                        response = client.models.generate_content(
                            model=model_id,
                            contents=[uploaded_file, prompt],
                            config=types.GenerateContentConfig(
                                response_mime_type="application/json",
                                temperature=0.2,
                            ),
                        )
                    finally:
                        if temp_path:
                            try:
                                Path(temp_path).unlink(missing_ok=True)
                            except Exception as cleanup_err:
                                logger.warning(
                                    "Failed to delete local temp MP4 %s: %s",
                                    temp_path,
                                    cleanup_err,
                                )
                        if uploaded_file is not None and getattr(uploaded_file, "name", None):
                            try:
                                client.files.delete(name=uploaded_file.name)
                            except Exception as remote_err:
                                logger.warning(
                                    "Failed to delete remote Gemini file %s: %s",
                                    getattr(uploaded_file, "name", ""),
                                    remote_err,
                                )
            else:
                contents: list[Any] = []
                if media_bytes and mime_type:
                    contents.append(
                        types.Part.from_bytes(data=media_bytes, mime_type=mime_type)
                    )
                contents.append(prompt)

                response = client.models.generate_content(
                    model=model_id,
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
                    f"Gemini model '{model_id}' did not return valid JSON. Raw response: {raw_text[:250]}"
                )
            return {**parsed, "model_used": model_id, "provider": "gemini_live"}
        except RuntimeError:
            raise
        except Exception as exc:
            err_msg = f"Gemini API request failed (model '{model_id}'): {exc}"
            logger.error(err_msg, exc_info=True)
            raise RuntimeError(err_msg) from exc
