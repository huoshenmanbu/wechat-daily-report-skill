# -*- coding: utf-8 -*-
"""Alibaba DashScope / Bailian Chat Completions provider."""

from typing import Any, Dict

from ai_providers.compatible_api import generate_openai_compatible


def generate_dashscope(prompt: str, provider_options: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return generate_openai_compatible(
        prompt,
        provider_options,
        provider_name="dashscope",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen-plus",
        default_api_key_env="DASHSCOPE_API_KEY",
    )
