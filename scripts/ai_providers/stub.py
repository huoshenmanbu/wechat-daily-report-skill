# -*- coding: utf-8 -*-
"""Stub provider: no external API; statistics-derived placeholders."""

from __future__ import annotations

from typing import Any, Dict, List


def _build_stub_topics(word_cloud: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    top_words = sorted(word_cloud or [], key=lambda x: x.get("count", 0), reverse=True)[:3]
    topics = []
    for idx, w in enumerate(top_words, 1):
        word = w.get("text", f"话题{idx}")
        count = int(w.get("count", 0))
        topics.append(
            {
                "title": f"{word} 讨论",
                "category": "群聊动态",
                "summary": f"本时段围绕“{word}”有持续讨论，建议结合原文回看重点信息。",
                "keywords": [word],
                "mention_count": count,
            }
        )
    return topics


def _build_stub_talker_profiles(top_talkers: List[Dict[str, Any]]) -> Dict[str, Dict[str, List[str]]]:
    profiles: Dict[str, Dict[str, List[str]]] = {}
    for t in top_talkers or []:
        name = t.get("name")
        if not name:
            continue
        count = int(t.get("count", 0))
        common_words = t.get("common_words", [])
        traits = [f"本时段发言 {count} 条", "活跃参与"]
        if common_words:
            traits.append(f"关注词：{common_words[0]}")
        profiles[name] = {"traits": traits[:3]}
    return profiles


def _build_stub_topic_heat(word_cloud: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    top_words = sorted(word_cloud or [], key=lambda x: x.get("count", 0), reverse=True)[:5]
    total = sum(int(w.get("count", 0)) for w in top_words) or 1
    colors = ["var(--wx-green)", "var(--wx-blue)", "var(--wx-orange)", "#888888", "#FA5151"]
    result = []
    for idx, w in enumerate(top_words):
        count = int(w.get("count", 0))
        percent = round(count * 100 / total)
        result.append(
            {
                "name": str(w.get("text", f"话题{idx+1}")),
                "count": count,
                "percent": percent,
                "color": colors[idx % len(colors)],
            }
        )
    return result


def build_stub_ai_content(stats: Dict[str, Any]) -> Dict[str, Any]:
    word_cloud = stats.get("word_cloud", [])
    top_talkers = stats.get("top_talkers", [])
    stub_qas = [
        {
            "questioner": "（占位）",
            "question_time": "",
            "question": "本时段是否有明确提问？stub 模式下请结合 simplified_chat 人工复核。",
            "tags": ["占位", "stub"],
            "answerer": "（占位）",
            "answer_time": "",
            "answer": "stub provider 未读取全文对话；接入真实 AI provider 后将自动生成问答摘要。",
            "is_best": True,
        },
        {
            "questioner": "（占位）",
            "question_time": "",
            "question": "有无资源/链接值得单独列出？",
            "tags": ["占位"],
            "answerer": "（占位）",
            "answer_time": "",
            "answer": "请查看 stats 与原文导出；stub 未做链接抽取。",
            "is_best": False,
        },
        {
            "questioner": "（占位）",
            "question_time": "",
            "question": "低活跃时段是否需要跳过推送？",
            "tags": ["占位"],
            "answerer": "（占位）",
            "answer_time": "",
            "answer": "可由 schedule 配置 min_messages 控制降级策略。",
            "is_best": False,
        },
    ]
    return {
        "topics": _build_stub_topics(word_cloud),
        "resources": [],
        "important_messages": [],
        "dialogues": [],
        "qas": stub_qas,
        "topic_heat": _build_stub_topic_heat(word_cloud),
        "talker_profiles": _build_stub_talker_profiles(top_talkers),
    }
