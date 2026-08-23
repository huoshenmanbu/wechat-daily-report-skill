"""Small, dependency-free client for OpenAI-compatible chat APIs."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict
from urllib.parse import urlparse

from ai_providers.common.json_extract import extract_first_json_object


def generate_openai_compatible(
    prompt: str,
    provider_options: Dict[str, Any] | None,
    *,
    provider_name: str,
    default_base_url: str,
    default_model: str,
    default_api_key_env: str,
) -> Dict[str, Any]:
    opts = (provider_options or {}).get(provider_name, {})
    if not isinstance(opts, dict):
        opts = {}
    api_key_env = str(opts.get("api_key_env") or default_api_key_env).strip()
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(f"{provider_name}: environment variable {api_key_env} is required.")

    base_url = str(opts.get("base_url") or default_base_url).strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError(f"{provider_name}: base_url must be an HTTPS URL.")
    model = str(opts.get("model") or default_model).strip()
    if not model:
        raise RuntimeError(f"{provider_name}: model is required.")
    timeout_seconds = max(1, int(opts.get("timeout_seconds", 90)))
    retry_times = min(5, max(0, int(opts.get("retry_times", 1))))
    retry_backoff_seconds = max(0.0, float(opts.get("retry_backoff_seconds", 1)))

    payload: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": opts.get("temperature", 0.2),
    }
    if opts.get("response_format_json", False):
        payload["response_format"] = {"type": "json_object"}
    if opts.get("max_tokens") is not None:
        payload["max_tokens"] = max(1, int(opts["max_tokens"]))

    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(retry_times + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if not retryable or attempt >= retry_times:
                raise RuntimeError(
                    f"{provider_name}: API request failed with HTTP {exc.code}."
                ) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt >= retry_times:
                raise RuntimeError(
                    f"{provider_name}: API request failed ({type(exc).__name__})."
                ) from None
        except json.JSONDecodeError:
            raise RuntimeError(f"{provider_name}: API response is not valid JSON.") from None

        if retry_backoff_seconds:
            time.sleep(retry_backoff_seconds * (2**attempt))

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"{provider_name}: API response has no chat completion content.") from None
    if not isinstance(content, str):
        raise RuntimeError(f"{provider_name}: API response content is not text.")
    return extract_first_json_object(content)
