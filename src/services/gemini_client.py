"""
Purpose:
    Implements `GeminiClient` encapsulating Google Gemini API configuration
    (Lecturer API key and configurable model ID stored in Firebase RTDB under
    `/settings/gemini`, defaulting to `gemini-2.5-flash-lite` while allowing
    custom IDs such as `gemini-3.5-flash-lite`) and structured JSON multimodal
    inference with deterministic fallback support for automated testing.
Inputs:
    `FirebaseRepository` persistence instance, `AppConfig` defaults, and optional
    custom transport callable for unit tests.
Outputs:
    `GeminiClient` class managing Gemini credentials, model selection, and JSON generation.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

from src.config import AppConfig
from src.repository.firebase_repository import FirebaseRepository

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
        Persists Lecturer API key and model overrides in Firebase RTDB (`/settings/gemini`)
        and provides structured JSON generation for `VideoSummarizer` and `HomeworkReviewer`.
    Inputs:
        repository (FirebaseRepository): Firebase RTDB persistence layer.
        config (AppConfig): Application configuration providing default key and model.
        transport (Callable[..., dict[str, Any]] | None): Optional injected generation
            callable for deterministic unit testing.
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
            transport (Callable[..., dict[str, Any]] | None): Optional mock transport.
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
            `/settings/gemini` first and falling back to `AppConfig` defaults.
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

        active_model = (
            str(
                stored_dict.get("model_id")
                or self.config.gemini_model
                or "gemini-2.5-flash-lite"
            ).strip()
            or "gemini-2.5-flash-lite"
        )
        return active_key, active_model

    def get_settings(self) -> dict[str, Any]:
        """
        Purpose:
            Return the Lecturer's active Gemini configuration (with the API key masked)
            for the Lecturer Dashboard settings panel.
        Inputs:
            None.
        Outputs:
            dict[str, Any]: Dictionary with `model_id`, `has_api_key`, `masked_api_key`,
            and `updated_at`.
        """
        stored = self.repository.get("/settings/gemini")
        stored_dict = stored if isinstance(stored, dict) else {}
        api_key, model_id = self.resolve_credentials()
        return {
            "model_id": model_id,
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
            under `/settings/gemini`.
        Inputs:
            api_key (str | None): New Gemini API key (pass None to keep existing key,
                or empty string `""` to clear).
            model_id (str | None): Desired Gemini model ID (e.g., `gemini-2.5-flash-lite`
                or custom `gemini-3.5-flash-lite`).
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
        fallback_builder: Callable[[], dict[str, Any]],
        media_bytes: bytes | None = None,
        mime_type: str | None = None,
    ) -> dict[str, Any]:
        """
        Purpose:
            Execute a structured JSON generation request against Google Gemini
            (or custom test transport), gracefully falling back to `fallback_builder()`
            when no live external API key is configured or if the remote API is unreachable.
        Inputs:
            prompt (str): Structured instruction prompt requesting JSON output.
            fallback_builder (Callable[[], dict[str, Any]]): Deterministic fallback
                builder ensuring offline/test reliability.
            media_bytes (bytes | None): Optional binary payload (MP4 video or PDF document).
            mime_type (str | None): Optional MIME type (`"video/mp4"`, `"application/pdf"`).
        Outputs:
            dict[str, Any]: Structured result dictionary enriched with `model_used` and `provider`.
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

        if api_key:
            try:
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=api_key)
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
                parsed = self._extract_json_dict(getattr(response, "text", "") or "")
                if parsed is not None:
                    return {**parsed, "model_used": model_id, "provider": "gemini_live"}
            except Exception:
                # Fall through to deterministic structured analysis if network/quota fails
                pass

        fallback_data = fallback_builder()
        return {
            **fallback_data,
            "model_used": model_id,
            "provider": "structured_analyzer",
        }
