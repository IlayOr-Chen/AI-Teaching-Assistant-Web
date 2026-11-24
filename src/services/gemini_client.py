"""
Purpose:
    Implements `GeminiClient` encapsulating Google Gemini API configuration
    (Lecturer API key and configurable model ID stored in Firebase RTDB under
    `/settings/gemini`, defaulting to `gemini-2.5-flash` with valid model presets
    `gemini-2.5-flash`, `gemini-2.5-flash-lite`, `gemini-2.5-pro`, `gemini-2.0-flash`)
    and real multimodal inference via the official `google-genai` SDK (including the
    Gemini Files API for large MP4 lecture recordings).
Inputs:
    `FirebaseRepository` persistence instance, `AppConfig` defaults, and optional
    custom transport callable strictly for automated `pytest` unit tests.
Outputs:
    `GeminiClient` class managing Gemini credentials, model selection, Files API video upload,
    and structured JSON generation without any silent fake production fallbacks.
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

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
VALID_GEMINI_PRESETS = (
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
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
        Persists Lecturer API key and model overrides in Firebase RTDB (`/settings/gemini`),
        uploads MP4 videos via the Gemini Files API (`client.files.upload`), and
        surfaces real errors whenever the API key is missing or Gemini returns an error.
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
            (e.g., `AIza...9xYz` or `""` when unconfigured).
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

    def resolve_credentials(self) -> tuple[str, str]:
        """
        Purpose:
            Resolve the currently active `(api_key, model_id)` by checking Firebase RTDB
            `/settings/gemini` first and falling back to `AppConfig` defaults, while
            automatically healing any invalid legacy `gemini-3.5-*` model IDs to `gemini-2.5-flash`.
        Inputs:
            None.
        Outputs:
            tuple[str, str]: `(active_api_key, active_model_id)`.
        """
        stored = self.repository.get("/settings/gemini")
        stored_dict = stored if isinstance(stored, dict) else {}

        raw_key = str(
            stored_dict.get("api_key") or self.config.gemini_api_key or ""
        ).strip()
        active_key = "" if raw_key.lower() in PLACEHOLDER_KEYS else raw_key

        raw_model = str(
            stored_dict.get("model_id")
            or self.config.gemini_model
            or DEFAULT_GEMINI_MODEL
        ).strip()
        if not raw_model or raw_model.lower().startswith("gemini-3.5"):
            active_model = DEFAULT_GEMINI_MODEL
            if isinstance(stored, dict) and str(stored.get("model_id", "")).lower().startswith("gemini-3.5"):
                self.repository.update("/settings/gemini", {"model_id": DEFAULT_GEMINI_MODEL})
        else:
            active_model = raw_model
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
            under `/settings/gemini`. Rejects invalid `gemini-3.5-*` model IDs.
        Inputs:
            api_key (str | None): New Gemini API key (pass None to keep existing key,
                or empty string `""` to clear).
            model_id (str | None): Desired Gemini model ID (e.g., `gemini-2.5-flash`,
                `gemini-2.5-flash-lite`, `gemini-2.5-pro`, `gemini-2.0-flash`).
        Outputs:
            dict[str, Any]: Updated public settings dictionary with masked key.
        """
        current_key, current_model = self.resolve_credentials()

        if model_id is not None:
            clean_model = model_id.strip()
            if not clean_model:
                raise ValueError("Gemini model_id cannot be empty.")
            if clean_model.lower().startswith("gemini-3.5"):
                raise ValueError(
                    f"Invalid Gemini model ID '{clean_model}'. Valid models include: "
                    f"{', '.join(VALID_GEMINI_PRESETS)}."
                )
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
    ) -> dict[str, Any]:
        """
        Purpose:
            Execute a structured JSON generation request against the real Google Gemini API
            (or custom test transport in `pytest`). For MP4 video payloads (`mime_type == "video/mp4"`),
            writes an ASCII-safe temporary file, uploads it via the Gemini Files API
            (`client.files.upload`), polls until `ACTIVE`, generates the structured JSON content,
            and cleans up both local and remote files in a `finally:` block.
            Never returns silent fake/hardcoded fallback content in production: raises `RuntimeError`
            with a clear message if `GEMINI_API_KEY` is missing or the Gemini API fails.
        Inputs:
            prompt (str): Structured instruction prompt requesting JSON output.
            fallback_builder (Callable[[], dict[str, Any]] | None): Deprecated/unused in production;
                only called if explicitly invoked by a custom unit-test transport.
            media_bytes (bytes | None): Optional binary payload (MP4 video or document bytes).
            mime_type (str | None): Optional MIME type (`"video/mp4"`, `"application/pdf"`, etc.).
        Outputs:
            dict[str, Any]: Structured result dictionary enriched with `model_used` and `provider`.
            Raises `RuntimeError` on missing API key or Gemini API errors.
        """
        api_key, model_id = self.resolve_credentials()

        if self._transport is not None:
            result = self._transport(
                prompt=prompt,
                model_id=model_id,
                api_key=api_key,
                media_bytes=media_bytes,
                mime_type=mime_type,
            )
            return {**result, "model_used": model_id, "provider": "custom_transport"}

        if not api_key:
            msg = (
                "GEMINI_API_KEY is missing or not configured. Please set a valid Google Gemini API key "
                "in the Lecturer Gemini AI Settings panel or in your .env file."
            )
            logger.error(msg)
            raise RuntimeError(msg)

        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)

            if media_bytes and mime_type and mime_type.lower().startswith("video/"):
                temp_path: str | None = None
                uploaded_file: Any = None
                try:
                    # Use an ASCII-safe temporary filename so Hebrew/Unicode video filenames
                    # never cause filesystem or HTTP header encoding errors during Files API upload.
                    with tempfile.NamedTemporaryFile(
                        prefix="gemini_lecture_", suffix=".mp4", delete=False
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
                        if time.time() - poll_start > 300:
                            raise RuntimeError(
                                "Timed out waiting for Gemini Files API to finish processing the MP4 video."
                            )
                        time.sleep(2.5)
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
                            logger.warning("Failed to delete local temp MP4 %s: %s", temp_path, cleanup_err)
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
