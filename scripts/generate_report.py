#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
generate_report.py - 微信群聊总结生成脚本（文本优先）

整合脚本统计数据和 AI 生成内容，默认输出纯文本/Markdown 总结；
如需 HTML，可继续使用 Jinja2 模板渲染。

使用方式:
    python generate_report.py --stats stats.json --ai-content ai_content.json --output report.md

输出格式：
    - .txt/.md 后缀：生成文本总结（默认，无需 jinja2/playwright）
    - .html 后缀：生成 HTML（需要 jinja2）
"""

import json
import argparse
import datetime
import os
import sys


def parse_arguments():
    parser = argparse.ArgumentParser(description='Generate WeChat summary report (text-first).')
    parser.add_argument('--stats', required=True, help='Path to statistics JSON from analyze_chat.py')
    parser.add_argument('--ai-content', required=False, default=None, help='Path to AI-generated content JSON (optional)')
    parser.add_argument('--template', default=None, help='Path to Jinja2 HTML template')
    parser.add_argument('--output', default='report.md', help='Output file path (.txt/.md/.html)')
    parser.add_argument('--clean-temp', action='store_true', help='Delete temporary files after report generation')
    return parser.parse_args()


def cleanup_temp_files(file_paths):
    """删除临时文件"""
    for path in file_paths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
                print(f"Deleted temp file: {path}")
            except OSError as e:
                print(f"Warning: Failed to delete {path}: {e}")


def load_json(file_path):
    if not file_path or not os.path.exists(file_path):
        return {}
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_display_name(msg):
    return msg.get('groupNickname') or msg.get('accountName') or ''


def build_name_avatar_map_from_chat(chat_json_path):
    data = load_json(chat_json_path)
    if not data:
        return {}

    members = data.get('members', [])
    messages = data.get('messages', [])

    sender_avatar_map = {}
    name_avatar_map = {}

    for member in members:
        sender = member.get('platformId')
        name = member.get('accountName')
        avatar = member.get('avatar')
        if sender and avatar:
            sender_avatar_map[sender] = avatar
        if name and avatar and name not in name_avatar_map:
            name_avatar_map[name] = avatar

    for msg in messages:
        sender = msg.get('sender')
        display_name = get_display_name(msg)
        avatar = sender_avatar_map.get(sender)
        if display_name and avatar and display_name not in name_avatar_map:
            name_avatar_map[display_name] = avatar

    return name_avatar_map


def load_name_avatar_map(stats, stats_path):
    avatar_map = {}

    # 兼容旧版 stats：若已内嵌映射，继续支持
    legacy_map = stats.get('name_avatar_map', {})
    if isinstance(legacy_map, dict):
        avatar_map.update(legacy_map)

    source_chat_path = stats.get('meta', {}).get('source_chat_path')
    if source_chat_path:
        if not os.path.isabs(source_chat_path):
            base_dir = os.path.dirname(os.path.abspath(stats_path))
            source_chat_path = os.path.normpath(os.path.join(base_dir, source_chat_path))
        if os.path.isfile(source_chat_path):
            avatar_map.update(build_name_avatar_map_from_chat(source_chat_path))

    return avatar_map


def get_name_avatar(name, name_avatar_map):
    if not isinstance(name, str):
        return None
    clean_name = name.strip()
    if not clean_name:
        return None
    return name_avatar_map.get(clean_name)


def fill_ai_content_avatars(ai_content, name_avatar_map):
    # 资源分享：按 sharer 补头像
    for res in ai_content.get('resources', []):
        if not res.get('avatar'):
            avatar = get_name_avatar(res.get('sharer'), name_avatar_map)
            if avatar:
                res['avatar'] = avatar

    # 重要消息：按 sender 补头像
    for msg in ai_content.get('important_messages', []):
        if not msg.get('avatar'):
            avatar = get_name_avatar(msg.get('sender'), name_avatar_map)
            if avatar:
                msg['avatar'] = avatar

    # 对话：按 name 补头像
    for dialogue in ai_content.get('dialogues', []):
        for msg in dialogue.get('messages', []):
            if not msg.get('avatar'):
                avatar = get_name_avatar(msg.get('name'), name_avatar_map)
                if avatar:
                    msg['avatar'] = avatar

    # 问答：分别补提问者/回答者头像
    for qa in ai_content.get('qas', []):
        if not qa.get('questioner_avatar'):
            avatar = get_name_avatar(qa.get('questioner'), name_avatar_map)
            if avatar:
                qa['questioner_avatar'] = avatar
        if not qa.get('answerer_avatar'):
            avatar = get_name_avatar(qa.get('answerer'), name_avatar_map)
            if avatar:
                qa['answerer_avatar'] = avatar


def _fmt_list(items, fallback="无"):
    values = [str(i).strip() for i in (items or []) if str(i).strip()]
    return "、".join(values) if values else fallback


def _resolve_alpha_sections(stats, ai_content):
    """Return validated focus, action, and deterministic candidate lists."""
    focus_list = ai_content.get("investment_meme_focus", [])
    if not isinstance(focus_list, list) or not focus_list:
        fallback_focus = ai_content.get("watchlist", [])
        focus_list = fallback_focus if isinstance(fallback_focus, list) else []

    actions = ai_content.get("alpha_actions", [])
    if not isinstance(actions, list):
        actions = []
    if not actions:
        legacy_items = ai_content.get("key_information", [])
        if isinstance(legacy_items, list):
            actions = [
                item
                for item in legacy_items
                if isinstance(item, dict)
                and str(item.get("action_or_followup") or "").strip()
            ]

    alpha_candidates = stats.get("alpha_candidates", [])
    if not isinstance(alpha_candidates, list):
        alpha_candidates = []
    return focus_list, actions, alpha_candidates


def _has_actionable_alpha(stats, ai_content):
    return any(_resolve_alpha_sections(stats, ai_content))


def _ai_narrative_empty(ai_content):
    """无讨论热点/问答/对话等模型产出时视为「叙事为空」（含 ai_content 为 {}）。"""
    if not ai_content:
        return True
    return not any(
        ai_content.get(k)
        for k in (
            "topics",
            "important_messages",
            "summary",
            "member_sentiment",
            "investment_meme_focus",
            "key_information",
        )
    )


def _resolve_raw_text_path(stats_json_path: str, rel: str):
    """raw_text_paths 多为相对仓库根或与 stats 同目录；返回首个存在的文件路径。"""
    if not rel:
        return None
    rel = rel.replace("\\", os.sep)
    tried = []
    stats_dir = os.path.dirname(os.path.abspath(stats_json_path))
    repo_root = os.path.normpath(os.path.join(stats_dir, "..", ".."))
    candidates = []
    if os.path.isabs(rel):
        candidates.append(os.path.normpath(rel))
    candidates.append(os.path.normpath(os.path.join(stats_dir, os.path.basename(rel))))
    candidates.append(os.path.normpath(os.path.join(stats_dir, rel)))
    candidates.append(os.path.normpath(os.path.join(repo_root, rel)))
    candidates.append(os.path.normpath(os.path.join(os.getcwd(), rel)))
    for c in candidates:
        if c in tried:
            continue
        tried.append(c)
        if os.path.isfile(c):
            return c
    return None


def _read_simplified_excerpt(stats, stats_json_path: str, *, max_lines: int = 120, max_chars: int = 16000):
    paths = stats.get("raw_text_paths") or []
    if not paths:
        legacy = stats.get("raw_text_path")
        if legacy:
            paths = [legacy]
    text_path = None
    for p in paths:
        text_path = _resolve_raw_text_path(stats_json_path, p)
        if text_path:
            break
    if not text_path:
        return ""
    try:
        with open(text_path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return ""
    out_lines = []
    i = 0
    if lines and lines[0].strip().startswith("==="):
        i = 1
    char_budget = max_chars
    while i < len(lines) and len(out_lines) < max_lines:
        line = lines[i].rstrip()
        i += 1
        if not line:
            out_lines.append("")
            continue
        if len(line) > char_budget:
            line = line[:char_budget] + "…"
            out_lines.append(line)
            break
        char_budget -= len(line) + 1
        out_lines.append(line)
    return "\n".join(out_lines).strip()


def build_text_report(stats, ai_content, stats_json_path: str = ""):
    meta = stats.get("meta", {})
    focus_list, actions, alpha_candidates = _resolve_alpha_sections(stats, ai_content)

    # Empty Alpha windows are intentionally silent: a report containing only
    # chat metadata or filler would create notification noise.
    if not focus_list and not actions and not alpha_candidates:
        return ""

    lines = []
    lines.append(f"# {meta.get('name', '群聊')} Alpha 快报")
    lines.append("")
    lines.append(f"- 日期: {meta.get('date', 'N/A')}")
    lines.append(f"- 统计周期: {meta.get('time_range', 'N/A')}")

    provider_status = ai_content.get("_provider_status", {})
    if isinstance(provider_status, dict) and provider_status.get("ok") is False:
        lines.append(
            f"> ⚠️ AI 摘要生成失败（provider: {provider_status.get('provider', 'unknown')}）；"
            "以下仅为本地筛选的原始 Alpha 线索。"
        )

    summary = ai_content.get("summary", {})
    if isinstance(summary, dict) and summary:
        if summary.get("overview"):
            lines.append(f"> {summary.get('overview')}")

    lines.append("")
    lines.append("## 交易/标的")
    if focus_list:
        for w in focus_list:
            symbol = w.get("symbol_or_theme", w.get("symbol", "UNKNOWN"))
            bias = w.get("market_bias", w.get("bias", "观望"))
            reason = w.get("why_mentioned", w.get("reason", ""))
            conditions = w.get("key_signals", w.get("conditions", []))
            risk = w.get("risks", w.get("risk", []))
            parts = [f"{symbol}：{bias}"]
            if reason:
                parts.append(str(reason).strip())
            if conditions:
                parts.append(f"观察：{_fmt_list(conditions)}")
            if risk:
                parts.append(f"风险：{_fmt_list(risk)}")
            lines.append(f"- {'；'.join(parts)}")
    else:
        lines.append("- 本时段未识别到明确的投资标的或方向。")

    lines.append("")
    lines.append("## 可行动信号")
    if actions:
        for item in actions:
            if "action" in item:
                action = item.get("action", "待观察")
                detail = item.get("detail", "")
            else:
                action = item.get("action_or_followup", "待观察")
                detail = item.get("detail") or item.get("title", "")
            people = item.get("source_people", [])
            line = str(action).strip()
            if detail:
                line += f"；{str(detail).strip()}"
            if people:
                line += f"（来源：{_fmt_list(people)}）"
            lines.append(f"- {line}")
    else:
        lines.append("- 暂无明确买入、卖出或跟踪动作。")

    other_activity = str(ai_content.get("other_activity", "")).strip()
    lines.append("")
    lines.append("## 其他动态")
    lines.append(f"- {other_activity or '其余为闲聊或低信号内容，无新增投资信息。'}")

    if not focus_list and not actions:
        if isinstance(alpha_candidates, list) and alpha_candidates:
            lines.append("")
            lines.append("## 优先关注线索")
            for item in alpha_candidates:
                signals = _fmt_list(item.get("signals", []))
                lines.append(
                    f"- {item.get('sender', '未知')} @ {item.get('time', 'N/A')}（{signals}）：{item.get('content', '')}"
                )

    lines.append(f"_生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_")
    return "\n".join(lines).rstrip() + "\n"


def main():
    args = parse_arguments()
    
    # Load data
    stats = load_json(args.stats)
    ai_content = load_json(args.ai_content) if args.ai_content else {}
    
    # Merge AI-generated traits into top_talkers
    top_talkers = stats.get('top_talkers', [])
    name_avatar_map = load_name_avatar_map(stats, args.stats)
    talker_profiles = ai_content.get('talker_profiles', {})
    for talker in top_talkers:
        name = talker.get('name')
        if name and name in talker_profiles:
            profile = talker_profiles[name]
            if 'traits' in profile:
                talker['traits'] = profile['traits']
        if not talker.get('avatar'):
            avatar = get_name_avatar(name, name_avatar_map)
            if avatar:
                talker['avatar'] = avatar

    night_owl = stats.get('night_owl')
    if isinstance(night_owl, dict) and not night_owl.get('avatar'):
        avatar = get_name_avatar(night_owl.get('name'), name_avatar_map)
        if avatar:
            night_owl['avatar'] = avatar

    # Fill avatars for AI blocks
    fill_ai_content_avatars(ai_content, name_avatar_map)
    
    # Prepare context
    context = {
        'meta': stats.get('meta', {}),
        'top_talkers': top_talkers,
        'night_owl': night_owl,
        'word_cloud': stats.get('word_cloud', []),
        'ai_content': ai_content,
        'generated_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    # Determine output format
    output_ext = os.path.splitext(args.output)[1].lower()
    if output_ext in ['.png', '.jpg', '.jpeg']:
        raise RuntimeError(
            "Image output is no longer supported in lightweight mode. "
            "Use --output report.md (or .txt/.html)."
        )

    if output_ext in ['.txt', '.md', '']:
        text_output = build_text_report(stats, ai_content, stats_json_path=args.stats)
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(text_output)
        print(f"Text report generated: {args.output}")
    elif output_ext == '.html':
        if not _has_actionable_alpha(stats, ai_content):
            html_output = ""
        else:
            try:
                from jinja2 import Environment, FileSystemLoader
            except ImportError:
                raise RuntimeError(
                    "HTML output requires 'jinja2'. Install with: pip install jinja2"
                )

            if args.template:
                template_dir = os.path.dirname(args.template)
                template_name = os.path.basename(args.template)
            else:
                script_dir = os.path.dirname(os.path.abspath(__file__))
                template_dir = os.path.join(script_dir, '..', 'assets')
                template_name = 'report_template.html'

            env = Environment(loader=FileSystemLoader(template_dir))
            template = env.get_template(template_name)
            html_output = template.render(**context)
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(html_output)
        print(f"HTML report generated: {args.output}")
    else:
        raise RuntimeError("Unsupported output suffix. Use .txt/.md/.html")

    if args.clean_temp:
        text_paths = stats.get('raw_text_paths', [])
        if not text_paths:
            legacy = stats.get('raw_text_path')
            if legacy:
                text_paths = [legacy]
        temp_files = [
            args.stats,
            args.ai_content,
        ] + text_paths
        cleanup_temp_files(temp_files)


if __name__ == "__main__":
    main()
