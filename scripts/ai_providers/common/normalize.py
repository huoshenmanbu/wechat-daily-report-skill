# -*- coding: utf-8 -*-
"""Normalize ai_content dict so generate_report / templates get stable shapes."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List


_TOP_KEYS = (
    "topics",
    "resources",
    "important_messages",
    "dialogues",
    "qas",
    "topic_heat",
    "talker_profiles",
)


def normalize_ai_content(ai: Dict[str, Any], stats: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(ai) if ai else {}

    for key in _TOP_KEYS:
        if key not in out:
            if key == "talker_profiles":
                out[key] = {}
            else:
                out[key] = []

    qas = out.get("qas")
    if not isinstance(qas, list):
        qas = []
    placeholder = {
        "questioner": "（占位）",
        "question_time": "",
        "question": "本时段问答占位（模型输出不完整时已补齐）。",
        "tags": ["占位"],
        "answerer": "（占位）",
        "answer_time": "",
        "answer": "请结合 simplified_chat 原文复核。",
        "is_best": False,
    }
    while len(qas) < 3:
        row = dict(placeholder)
        row["is_best"] = len(qas) == 0
        qas.append(row)
    out["qas"] = qas

    profiles = out.get("talker_profiles")
    if not isinstance(profiles, dict):
        profiles = {}

    top_talkers = stats.get("top_talkers") or []
    top_names: List[str] = []
    for t in top_talkers[:3]:
        name = (t or {}).get("name")
        if isinstance(name, str) and name.strip():
            top_names.append(name.strip())

    for name in top_names:
        if name not in profiles:
            profiles[name] = {"traits": ["活跃参与", "（占位特点）"]}

    out["talker_profiles"] = profiles

    if not isinstance(out.get("topics"), list):
        out["topics"] = []
    if not isinstance(out.get("resources"), list):
        out["resources"] = []
    if not isinstance(out.get("important_messages"), list):
        out["important_messages"] = []
    if not isinstance(out.get("dialogues"), list):
        out["dialogues"] = []
    if not isinstance(out.get("topic_heat"), list):
        out["topic_heat"] = []

    return out
