"""Deterministic first-pass extraction for actionable chat messages.

This module intentionally ranks evidence present in the chat.  It does not
claim that a message is true or that an asset is investable.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Dict, Iterable, List, Sequence


_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_EVM_ADDRESS_RE = re.compile(r"(?<![a-fA-F0-9])0x[a-fA-F0-9]{40}(?![a-fA-F0-9])")
_BASE58_ADDRESS_RE = re.compile(
    r"(?<![1-9A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{32,44}(?![1-9A-HJ-NP-Za-km-z])"
)
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|万|亿|k|m|b|usdt|usd|u)", re.IGNORECASE)
_CATALYST_RE = re.compile(
    r"上所|上线|融资|空投|解锁|回购|销毁|合作|公告|财报|代币经济|主网|测试网|"
    r"etf|listing|launch|funding|airdrop|unlock|buyback|burn|partnership",
    re.IGNORECASE,
)
_RISK_RE = re.compile(r"风险|rug|跑路|钓鱼|假合约|流动性不足|解锁", re.IGNORECASE)
_SOCIAL_ONLY_RE = re.compile(
    r"(?:早上好|早安|晚安|大家好|收到|好的|谢谢|哈哈+|呵呵+|今天吃什么|吃什么|睡觉了)+$",
    re.IGNORECASE,
)


def _normalized(values: Iterable[Any]) -> set[str]:
    return {str(value).strip().casefold() for value in values if str(value).strip()}


def _display_name(message: Dict[str, Any]) -> str:
    return str(message.get("groupNickname") or message.get("accountName") or message.get("sender") or "未知")


def _format_time(timestamp: Any) -> str:
    try:
        return dt.datetime.fromtimestamp(float(timestamp)).strftime("%H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _is_social_only(content: str) -> bool:
    compact = re.sub(r"[\s,，.!！?？~～。]+", "", content)
    return bool(_SOCIAL_ONLY_RE.fullmatch(compact))


def extract_alpha_candidates(
    messages: Sequence[Dict[str, Any]],
    *,
    focus_members: Sequence[str] | None = None,
    min_score: int = 5,
    max_candidates: int = 30,
) -> List[Dict[str, Any]]:
    """Return evidence-bearing messages, ordered by deterministic priority."""
    focus = _normalized(focus_members or [])
    candidates: List[Dict[str, Any]] = []

    for message in messages:
        if message.get("type") not in (0, 2):
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            continue

        name = _display_name(message)
        identities = _normalized((message.get("sender"), message.get("accountName"), message.get("groupNickname")))
        is_focus_member = bool(focus & identities)
        signals: List[str] = []
        score = 0
        if _URL_RE.search(content):
            signals.append("链接")
            score += 2
        if _EVM_ADDRESS_RE.search(content) or _BASE58_ADDRESS_RE.search(content):
            signals.append("链上地址")
            score += 5
        if _CATALYST_RE.search(content):
            signals.append("催化剂")
            score += 3
        if _NUMBER_RE.search(content):
            signals.append("关键数字")
            score += 1
        if _RISK_RE.search(content):
            signals.append("风险提示")
            score += 1
        if is_focus_member and not _is_social_only(content):
            signals.insert(0, "重点成员")
            score += 5

        if score < min_score:
            continue
        candidates.append(
            {
                "sender": name,
                "sender_id": message.get("sender") or "",
                "time": _format_time(message.get("timestamp")),
                "content": content[:800],
                "signals": signals,
                "score": score,
                "priority": "高" if score >= 7 else "中",
                "is_focus_member": is_focus_member,
            }
        )

    candidates.sort(key=lambda row: (-row["score"], row["time"], row["sender"]))
    return candidates[:max(0, int(max_candidates))]
