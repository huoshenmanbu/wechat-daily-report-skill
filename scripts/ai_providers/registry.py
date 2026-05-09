# -*- coding: utf-8 -*-
"""Provider registry."""

from __future__ import annotations

from typing import Any, Dict, Optional

from ai_providers.cursor_cli import generate_via_cursor_cli
from ai_providers.dashscope import generate_dashscope
from ai_providers.stub import build_stub_ai_content
from ai_providers.volc_ark import generate_volc_ark

REGISTERED = {"stub", "cursor_cli", "dashscope", "volc_ark"}


def run_provider(
    name: str,
    *,
    stats: Dict[str, Any],
    stats_path: str,
    output_path: str,
    chat_text: str,
    repo_root: str,
    provider_options: Optional[Dict[str, Any]] = None,
    prompt_text: str,
) -> Dict[str, Any]:
    key = (name or "stub").strip().lower()
    if key not in REGISTERED:
        raise RuntimeError(
            f"Unsupported provider: {key}. Supported: {', '.join(sorted(REGISTERED))}"
        )

    if key == "stub":
        return build_stub_ai_content(stats)

    if key == "cursor_cli":
        return generate_via_cursor_cli(prompt_text, repo_root, provider_options)

    if key == "dashscope":
        generate_dashscope()
        raise RuntimeError("Provider 'dashscope' returned unexpectedly")

    if key == "volc_ark":
        generate_volc_ark()
        raise RuntimeError("Provider 'volc_ark' returned unexpectedly")

    raise RuntimeError(f"Provider not wired: {key}")
