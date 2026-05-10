# -*- coding: utf-8 -*-
"""Normalize ai_content dict so generate_report / templates get stable shapes."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List


_LEGACY_TOP_KEYS = (
    "topics",
    "resources",
    "important_messages",
    "dialogues",
    "qas",
    "topic_heat",
    "talker_profiles",
)

_NEW_TOP_KEYS = (
    "summary",
    "member_sentiment",
    "investment_meme_focus",
    "key_information",
)


def normalize_ai_content(ai: Dict[str, Any], stats: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(ai) if ai else {}

    for key in _LEGACY_TOP_KEYS:
        if key not in out:
            if key == "talker_profiles":
                out[key] = {}
            else:
                out[key] = []

    for key in _NEW_TOP_KEYS:
        if key not in out:
            if key == "summary":
                out[key] = {}
            else:
                out[key] = []

    # Backward compatibility: map older schema into new compact schema when available.
    if (not out.get("summary")) and isinstance(out.get("snapshot"), dict):
        snap = out.get("snapshot", {})
        out["summary"] = {
            "overview": snap.get("summary", ""),
            "group_sentiment": snap.get("sentiment", ""),
            "keywords": snap.get("top_keywords", []),
        }

    if (not out.get("investment_meme_focus")) and isinstance(out.get("watchlist"), list):
        mapped_focus = []
        for w in out.get("watchlist", []):
            if not isinstance(w, dict):
                continue
            mapped_focus.append(
                {
                    "symbol_or_theme": w.get("symbol", ""),
                    "type": "投资标的",
                    "why_mentioned": w.get("reason", ""),
                    "market_bias": w.get("bias", "观望"),
                    "key_signals": w.get("conditions", []),
                    "risks": w.get("risk", []),
                    "confidence": w.get("confidence", 1),
                }
            )
        out["investment_meme_focus"] = mapped_focus

    if (not out.get("key_information")) and isinstance(out.get("highlights"), list):
        mapped_info = []
        for h in out.get("highlights", []):
            if not isinstance(h, dict):
                continue
            mapped_info.append(
                {
                    "level": "重点",
                    "title": h.get("type", "观点"),
                    "detail": h.get("point", ""),
                    "source_people": [h.get("speaker", "未知")],
                    "time_range": "",
                    "action_or_followup": "",
                }
            )
        out["key_information"] = mapped_info

    qas = out.get("qas")
    if not isinstance(qas, list):
        qas = []
    normalized_qas = []
    for qa in qas:
        if not isinstance(qa, dict):
            continue
        if "question" not in qa and "q" in qa:
            qa["question"] = qa.get("q", "")
        if "answer" not in qa and "a" in qa:
            qa["answer"] = qa.get("a", "")
        if "tags" not in qa and "tag" in qa:
            tag = qa.get("tag")
            qa["tags"] = [tag] if isinstance(tag, str) and tag.strip() else []
        qa.setdefault("questioner", qa.get("questioner") or "未知")
        qa.setdefault("answerer", qa.get("answerer") or "未知")
        qa.setdefault("question_time", qa.get("question_time") or "")
        qa.setdefault("answer_time", qa.get("answer_time") or "")
        qa.setdefault("is_best", False)
        normalized_qas.append(qa)
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
    while len(normalized_qas) < 3:
        row = dict(placeholder)
        row["is_best"] = len(normalized_qas) == 0
        normalized_qas.append(row)
    out["qas"] = normalized_qas

    important_messages = out.get("important_messages")
    if not isinstance(important_messages, list):
        important_messages = []
    normalized_important = []
    for row in important_messages:
        if not isinstance(row, dict):
            continue
        if "sender" not in row and "speaker" in row:
            row["sender"] = row.get("speaker", "未知")
        if "summary" not in row:
            row["summary"] = row.get("message") or row.get("content") or ""
        if "content" not in row:
            row["content"] = row.get("message") or ""
        row.setdefault("priority", row.get("priority") or "中")
        row.setdefault("time", row.get("time") or "")
        normalized_important.append(row)
    out["important_messages"] = normalized_important

    dialogues = out.get("dialogues")
    if not isinstance(dialogues, list):
        dialogues = []
    normalized_dialogues = []
    for d in dialogues:
        if not isinstance(d, dict):
            continue
        if "topic" not in d and "thread" in d:
            d["topic"] = d.get("thread", "未命名")
        turns = d.get("turns")
        if ("messages" not in d or not isinstance(d.get("messages"), list)) and isinstance(turns, list):
            d["messages"] = []
            for turn in turns:
                if not isinstance(turn, dict):
                    continue
                d["messages"].append(
                    {
                        "name": turn.get("speaker", "未知"),
                        "time": turn.get("time", ""),
                        "content": turn.get("text", ""),
                    }
                )
        d.setdefault("highlight", d.get("highlight") or "")
        normalized_dialogues.append(d)
    out["dialogues"] = normalized_dialogues

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
            profiles[name] = {"traits": ["活跃参与", "信息补充积极"]}

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
