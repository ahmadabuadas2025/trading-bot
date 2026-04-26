"""OpenRouter LLM client.

The client tries the primary model first, falls back to a secondary
model on failure, and returns structured JSON (or raises on a final
failure). Prompts are produced by :mod:`core.llm_scanner`.
"""

from __future__ import annotations

import json
from typing import Any

from core.http import HttpClient


class LLMClient:
    """Thin wrapper around OpenRouter's chat-completions endpoint."""

    def __init__(
        self,
        http: HttpClient,
        api_key: str | None,
        base_url: str,
        model: str,
        fallback_model: str,
        request_timeout_seconds: int = 60,
    ) -> None:
        """Create the LLM client.

        Args:
            http: Shared :class:`HttpClient`.
            api_key: OpenRouter API key (``None`` disables the client).
            base_url: OpenRouter base URL.
            model: Preferred model id.
            fallback_model: Backup model id on primary failure.
            request_timeout_seconds: Request timeout (honoured by caller).
        """
        self._http = http
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._fallback_model = fallback_model
        self._timeout = request_timeout_seconds

    @property
    def enabled(self) -> bool:
        """Whether the LLM client has credentials.

        Returns:
            ``True`` when an API key is present.
        """
        return bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        """Build request headers.

        Returns:
            Header mapping.
        """
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://localhost/solana-trading-bot",
            "X-Title": "SolanaTradingBot",
        }

    async def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        """Run a chat completion that must return a JSON object.

        Args:
            system_prompt: System role content.
            user_prompt: User role content.

        Returns:
            Parsed JSON object produced by the model.

        Raises:
            RuntimeError: When the client is disabled or all models fail.
        """
        if not self._api_key:
            raise RuntimeError("LLM client is not configured (no API key)")
        url = f"{self._base_url}/chat/completions"
        last_err: Exception | None = None
        for model in (self._model, self._fallback_model):
            body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            }
            try:
                data = await self._http.request_json(
                    "POST", url, headers=self._headers(), json_body=body
                )
                choices = (data or {}).get("choices") or []
                if not choices:
                    raise RuntimeError("empty choices")
                content = choices[0].get("message", {}).get("content") or "{}"
                return self._parse_json(content)
            except Exception as err:  # noqa: BLE001
                last_err = err
                continue
        raise RuntimeError(f"LLM request failed on all models: {last_err}")

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        """Parse potentially-fenced JSON from an LLM response.

        Args:
            text: Raw model output.

        Returns:
            Parsed JSON dict.
        """
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```", 2)[-2] if cleaned.count("```") >= 2 else cleaned
            cleaned = cleaned.strip().lstrip("json").strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("LLM response did not contain a JSON object")
        return json.loads(cleaned[start : end + 1])
