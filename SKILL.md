---
name: wechat-daily-report
description: 基于本机已登录微信的本地数据库生成微信群聊总结文本。用于用户要求“生成某个微信群的今日日报/昨日报/群聊总结文字版/聊天总结”这类任务时。流程包含：检查并刷新本地微信解密数据、选择群聊、分析消息、生成 AI 内容、输出文本报告（可选 HTML）。
---

# 微信群聊日报生成 Skill

目标：基于本机微信解密后的数据库，生成一个最终可交付的 `report.md` 群聊总结文本。

## 执行步骤

必须按下面顺序执行，不能跳过第 5 步。

### 1. 准备解密环境

```bash
python scripts/setup_check.py --ensure-decryptor
```

### 2. 刷新微信解密数据

```bash
python scripts/decrypt_wechat.py
```

### 3. 列出群聊并确认目标群

```bash
python scripts/list_wechat_groups.py
```

### 4. 分析群聊，生成统计文件

```bash
python scripts/analyze_chat.py --chatroom "<群名或 chatroom id>" --date 2026-04-11 --output-stats stats.json --output-text simplified_chat.txt
```

产物：
- `stats.json`
- `simplified_chat.txt` 或 `simplified_chat_*.txt`

### 5. 读取提示词，生成 `ai_content.json`

必须读取：
- [`references/ai_prompt.md`]
- `stats.json`
- `simplified_chat*.txt`

必须产出：
- `ai_content.json`

要求：
- 输出必须是合法 JSON
- 不能输出 Markdown 代码块
- 不能跳过这一步

### 6. 生成最终文本报告

只有在 `ai_content.json` 已生成后，才能执行：

```bash
python scripts/generate_report.py --stats stats.json --ai-content ai_content.json --output report.md --clean-temp
```

最终产物：
- `report.md`（默认文本）

## 约束

- 输出目标默认是 `.md` 文本（如需可视化，可改为 `.html`）
- 必须先用 `list_wechat_groups.py` 确认群名，再执行分析
- 不要跳过“读取 `ai_prompt.md` 并生成 `ai_content.json`”
- 如果没有 `ai_content.json`，不要执行 `generate_report.py`

## 周期调度（可选）

如需每小时（或每 N 分钟）自动执行一次总结，可使用 run-once 调度器并交给系统任务计划定时触发：

```bash
python scripts/schedule_report.py --config config/report_schedule.yaml
```

说明：

- 周期、群名、输出格式、阈值等在 `config/report_schedule.yaml` 配置
- 脚本内置并发锁、过期锁回收、状态原子写、窗口幂等与补跑
- 调度脚本 `schedule_report.py` 支持 `provider`: `stub` | `cursor_cli` | `dashscope`（预留）| `volc_ark`（预留）；详见 `config/report_schedule.yaml` 与 `config/ai_providers.example.json`。`stub` 为离线占位；`cursor_cli` 需本机 Cursor CLI 与 `CURSOR_API_KEY`。高质量摘要仍可由 Skill 手工步骤生成 `ai_content.json`，或使用 `cursor_cli` / 未来 HTTP Provider。
