"""DeepSeek Chat Completions provider."""

from typing import Any, Dict

from ai_providers.compatible_api import generate_openai_compatible


def generate_deepseek(prompt: str, provider_options: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return generate_openai_compatible(
        prompt,
        provider_options,
        provider_name="deepseek",
        default_base_url="https://api.deepseek.com",
        default_model="deepseek-v4-flash",
        default_api_key_env="DEEPSEEK_API_KEY",
    )
