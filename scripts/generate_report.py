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
    lines = []
    lines.append(f"# {meta.get('name', '群聊')} 总结")
    lines.append("")
    lines.append(f"- 日期: {meta.get('date', 'N/A')}")
    lines.append(f"- 统计周期: {meta.get('time_range', 'N/A')}")
    lines.append(f"- 总消息数: {meta.get('total_count', 0)}")
    lines.append("")

    # NOTE: "话唠榜" intentionally hidden in report output.
    # Stats are still computed and can be used by downstream logic if needed.

    summary = ai_content.get("summary", {})
    if isinstance(summary, dict) and summary:
        lines.append("## 本段概览")
        if summary.get("overview"):
            lines.append(f"- 摘要：{summary.get('overview')}")
        if summary.get("group_sentiment"):
            lines.append(f"- 情绪：{summary.get('group_sentiment')}")
        kws = summary.get("keywords", [])
        if kws:
            lines.append(f"- 关键词：{_fmt_list(kws)}")
        lines.append("")

    if _ai_narrative_empty(ai_content) and stats_json_path:
        excerpt = _read_simplified_excerpt(stats, stats_json_path)
        if excerpt:
            lines.append("## 群聊原文摘录（节选）")
            lines.append("")
            lines.append("> 本段来自 `analyze_chat` 导出的压缩原文，**不是**模型摘要。")
            lines.append(
                "> 若已配置 AI provider（如 `cursor_cli`）并成功生成 `ai_content`，"
                "将优先展示「讨论热点」等章节。"
            )
            lines.append("")
            lines.append("```text")
            lines.append(excerpt)
            lines.append("```")
            lines.append("")

    topics = ai_content.get("topics", [])
    if topics:
        lines.append("## 讨论热点")
        for idx, topic in enumerate(topics, 1):
            lines.append(f"{idx}. {topic.get('title', '未命名话题')}（{topic.get('category', '未分类')}）")
            lines.append(f"   - 摘要：{topic.get('summary', '')}")
            related_people = topic.get("related_people", [])
            if related_people:
                lines.append(f"   - 相关成员：{_fmt_list(related_people)}")
            if topic.get("heat") not in (None, ""):
                lines.append(f"   - 热度：{topic.get('heat')}")
        lines.append("")

    member_sentiment = ai_content.get("member_sentiment", [])
    if member_sentiment:
        lines.append("## 成员情绪")
        for idx, row in enumerate(member_sentiment, 1):
            lines.append(
                f"{idx}. {row.get('name', '未知')}：{row.get('sentiment', '中性')} / {row.get('stance', '观望')}"
            )
            if row.get("evidence"):
                lines.append(f"   - 依据：{row.get('evidence')}")
        lines.append("")

    resources = ai_content.get("knowledge_and_resources", [])
    if not resources:
        resources = ai_content.get("resources", [])
    if resources:
        lines.append("## 资源分享")
        for idx, res in enumerate(resources, 1):
            lines.append(f"{idx}. [{res.get('type', '资源')}] {res.get('title', '未命名资源')}")
            lines.append(
                f"   - 分享者：{res.get('sharer', '未知')} | 时间：{res.get('time', 'N/A')} | 分类：{res.get('category', '未分类')}"
            )
            if res.get("description"):
                lines.append(f"   - 简介：{res.get('description')}")
            kp = res.get("key_points", [])
            if kp:
                lines.append(f"   - 要点：{_fmt_list(kp)}")
            if res.get("url"):
                lines.append(f"   - 链接：{res.get('url')}")
        lines.append("")

    focus_list = ai_content.get("investment_meme_focus", [])
    if not focus_list:
        focus_list = ai_content.get("watchlist", [])
    if focus_list:
        lines.append("## 投资与 Meme 重点")
        for idx, w in enumerate(focus_list, 1):
            symbol = w.get("symbol_or_theme", w.get("symbol", "UNKNOWN"))
            bias = w.get("market_bias", w.get("bias", "观望"))
            confidence = w.get("confidence", "")
            lines.append(f"{idx}. {symbol}（{bias}）")
            w_type = w.get("type")
            if w_type:
                lines.append(f"   - 类型：{w_type}")
            if confidence != "":
                lines.append(f"   - 置信度：{confidence}")
            reason = w.get("why_mentioned", w.get("reason", ""))
            if reason:
                lines.append(f"   - 原因：{reason}")
            conditions = w.get("key_signals", w.get("conditions", []))
            if conditions:
                lines.append(f"   - 观察条件：{_fmt_list(conditions)}")
            risk = w.get("risks", w.get("risk", []))
            if risk:
                lines.append(f"   - 风险：{_fmt_list(risk)}")
        lines.append("")

    key_info = ai_content.get("key_information", [])
    if key_info:
        lines.append("## 关键信息与重点信息")
        for idx, item in enumerate(key_info, 1):
            lines.append(f"{idx}. [{item.get('level', '重点')}] {item.get('title', '未命名信息')}")
            if item.get("detail"):
                lines.append(f"   - 详情：{item.get('detail')}")
            source_people = item.get("source_people", [])
            if source_people:
                lines.append(f"   - 来源成员：{_fmt_list(source_people)}")
            if item.get("time_range"):
                lines.append(f"   - 时间段：{item.get('time_range')}")
            if item.get("action_or_followup"):
                lines.append(f"   - 后续动作：{item.get('action_or_followup')}")
        lines.append("")

    important_messages = ai_content.get("important_messages", [])
    if important_messages:
        lines.append("## 重要消息")
        for idx, msg in enumerate(important_messages, 1):
            sender = msg.get("sender", msg.get("speaker", "未知"))
            summary = msg.get("summary", msg.get("message", ""))
            lines.append(
                f"{idx}. [{msg.get('priority', '中')}] {sender} @ {msg.get('time', 'N/A')}: {summary}"
            )
            content = msg.get("content", msg.get("message", ""))
            if content:
                lines.append(f"   - 内容：{content}")
        lines.append("")

    # Optional compatibility blocks (older schema)
    qas = ai_content.get("qas", [])
    if qas:
        lines.append("## 问答精选")
        for idx, qa in enumerate(qas, 1):
            q = qa.get("question", qa.get("q", ""))
            a = qa.get("answer", qa.get("a", ""))
            lines.append(f"{idx}. Q: {q}")
            lines.append(f"   - A: {a}")
        lines.append("")

    topic_heat = ai_content.get("topic_heat", [])
    if topic_heat:
        lines.append("## 话题热度")
        for idx, heat in enumerate(topic_heat, 1):
            lines.append(
                f"{idx}. {heat.get('name', '未命名话题')}：{heat.get('count', 0)} 条（{heat.get('percent', 0)}%）"
            )
        lines.append("")

    night_owl = stats.get("night_owl")
    if isinstance(night_owl, dict):
        lines.append("## 深夜活跃")
        lines.append(
            f"- {night_owl.get('name', '未知')}（最晚活跃 {night_owl.get('last_time', 'N/A')}，深夜消息 {night_owl.get('msg_count', 0)} 条）"
        )
        if night_owl.get("last_msg"):
            lines.append(f"- 最后一条：{night_owl.get('last_msg')}")
        lines.append("")

    word_cloud = stats.get("word_cloud", [])
    if word_cloud:
        lines.append("## 词云高频词")
        sorted_words = sorted(word_cloud, key=lambda x: x.get("count", 0), reverse=True)
        for idx, item in enumerate(sorted_words[:15], 1):
            lines.append(f"{idx}. {item.get('text', '')}（{item.get('count', 0)}）")
        lines.append("")

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
