# WeChat Daily Report Generator (微信群聊日报生成工具)

这是一个用于分析微信群聊天记录，结合 AI 生成内容，并最终输出为结构化文字总结（可选 HTML）的工具。

## ✨ 功能特点

- **数据统计**: 自动分析群聊记录，生成话唠榜、熬夜冠军、词云统计等数据。
- **AI 智能摘要**: 利用 AI 识别讨论热点、提取有价值的资源/教程、捕捉有趣对话和问答。
- **文本优先**: 默认输出 Markdown/TXT 文本总结，更轻量、无需截图依赖。
- **可选 HTML 报告**: 如需可视化，可基于 HTML/CSS 模板渲染 HTML 页面。
- **风格化**: 支持幽默、玩梗的报告风格，提升阅读乐趣。
- **本地原始库直连**: 直接读取解密后的微信数据库分析指定群聊，不再需要 ChatLab JSON 中间文件。

## 🛠️ 依赖环境

- Python 3.8+
- Node.js (可选，仅用于开发调试模板)

### Python 库安装

```bash
pip install jieba
```

如需可选 HTML 输出，再安装：

```bash
pip install jinja2
```

### 可选：本地微信数据库解密依赖

如果你要直接从本机微信数据库导出群聊，而不是手头已有 ChatLab JSON：

```bash
python scripts/setup_check.py --ensure-decryptor
```

这一步会自动把 `wechat-decrypt` 安装到当前项目下的 `vendor/`，并安装其运行依赖。

## 🚀 使用流程

### 第一步：安装 Skill

**自动安装 (推荐)**:
```bash
npx skills add https://github.com/ADVISORYDZ/wechat-daily-report-skill
```

**手动安装**:
克隆本仓库到您的 Claude Skills 目录（如果目录不存在请先创建）：

```bash
cd ~/.claude/skills/
git clone https://github.com/ADVISORYDZ/wechat-daily-report-skill.git
```

### 第二步：基本使用

在 Claude Code 中直接对 Claude 下达指令：

> **“生成 [群名称] 今日日报”**

Claude 将自动调用本项目中的脚本，从解密后的原始数据库读取指定群聊并生成文字总结（可选 HTML）。

---

## ⏱️ 可配置周期总结（每小时/每N分钟）

### 1) 编辑调度配置

参考并修改：`config/report_schedule.yaml`

关键项：

- `interval_minutes`: 周期分钟数（如 `30/60/120`）
- `chatroom`: 群名关键词或 chatroom id
- `output_format`: `md`（默认）或 `html`
- `min_messages`: 低活跃阈值（低于阈值走精简内容）
- `low_activity_skip_threshold`: 可选，默认 `0` 表示关闭。设为大于 `0` 时，若本窗 `0 < total_count < 阈值`，则**不跑** AI 与 `generate_report`（不写 `report_*.md`），仅保留 analyze 产出的 stats/精简文本；若 `sender` 不是 `none`，会发一条简短飞书文本说明跳过（`send_report_with_retry` 与正式报告相同超时/重试）。`last_result` 为 `skipped_low_activity`。`--force` 只影响「报告已存在是否重跑」，**不会**绕过该阈值。若 `low_activity_skip_threshold >= min_messages`，则 `min_messages` 精简分支对「有条数」的窗口永远不会命中，启动时会在 stderr 打出警告。
- `low_activity_skip_message`: 可选，自定义上述通知正文；支持 Python `str.format` 占位符：`{chatroom}`、`{start}`、`{end}`、`{total_count}`、`{threshold}`。格式失败时回退到内置中文模板。
- `max_catchup_windows`: 单次运行最多补跑几个积压窗口（默认 24）
- `analyze_timeout_seconds` / `report_timeout_seconds` / `provider_timeout_seconds`: 子进程与 Provider 超时（秒）
- `provider`: `stub`（默认，离线占位）| `cursor_cli`（调用本机 Cursor CLI）| `dashscope`（千问 API）| `deepseek`（DeepSeek API）| `volc_ark`（预留，尚未实现）
- `provider_config_file`: 可选，指向 JSON（见 `config/ai_providers.example.json`），按后端分区的参数（如 `cursor_cli.command`）
- `max_chat_chars`: 可选，读取精简聊天文本时的总字符上限（默认 `0` 表示不限制；建议按模型能力配置，如 `200000`）
- `sender`: `none`（默认，不发送）| `feishu_cli_webhook`（首期可用）| `feishu_cli_card` / `feishu_im_api`（预留）
- `sender_timeout_seconds` / `sender_retry_times` / `sender_retry_backoff_seconds`: 发送超时与重试参数
- `sender_config_file`: 可选，指向 JSON（见 `config/senders.example.json`）
- `feishu_message_max_chars`: 发飞书正文截断阈值（默认 `3000`）
- `sender_strict`: 发送失败是否让任务失败退出（默认 `false`）

**Cursor CLI（`provider: cursor_cli`）**：需已安装 Cursor CLI，并将 `agent`（或完整可执行文件路径）放在 `PATH`，或把完整命令前缀写在 `provider_config_file` 的 `cursor_cli.command` 数组中。**首次或非交互运行**时 CLI 会要求「信任工作区」：脚本已默认附加 `--trust`（可在 JSON 里设 `cursor_cli.trust_workspace: false` 关闭）。手动测试若出现 “Workspace Trust Required”，在命令中加上 `--trust` 即可。**Windows**：Python 直接以子进程调用 `agent --print` 时，部分 Cursor Agent 版本会出现退出码 0 但 stdout 为空；脚本会自动改用 PowerShell 包装 `agent`，并解析 Cursor CLI 的 `{"type":"result","result":"..."}` 输出 envelope。请不要设置 `cursor_cli.hide_window: true`（默认即为不隐藏）。如 agent 等待工具授权，可尝试 `cursor_cli.force: true`（自动允许命令，有风险）。非交互模式仍可能对工作区产生副作用，若介意请使用专用克隆目录或查阅 Cursor 文档中的 workspace/worktree 选项。

**千问 / DeepSeek（`provider: dashscope` 或 `provider: deepseek`）**：在系统环境变量中分别设置 `DASHSCOPE_API_KEY` 或 `DEEPSEEK_API_KEY`，然后将 `provider` 切换为对应值。模型、HTTPS `base_url`、超时、`max_tokens`、JSON 输出选项，以及 `retry_times` / `retry_backoff_seconds` 可在 `config/ai_providers.json` 中调整；429、5xx 与临时网络错误会自动重试，API Key 不应写入该文件。

**可选：固定会话续聊（`cursor_cli.reuse_session`）**：默认 `false`，每次调度仍为新的 `agent -p` 调用。设为 `true` 时，会在仓库根下（默认 `runtime/cursor_cli_session.json`，已在 `.gitignore`）读写 `chat_id`：若文件中有 id，则附加 `--resume <id>`；若 CLI 返回的 JSON envelope 中含 `sessionId` / `chatId` / `conversationId` / `threadId` 之一，会自动写回文件。`resume_style`：`explicit_id`（默认，依赖上述 id）或 `continue`（附加 `--continue`，需本机 CLI 支持且行为以官方文档为准）。`on_resume_failure`：`clear_and_retry_fresh`（默认，带 resume 时若 agent 非零退出则清空状态并无 resume 再跑一轮）、`raise`（不重试）、`clear_only`（清空状态后仍按原错误失败）。自动化场景下 resume 行为以 Cursor 版本为准；额度仍主要取决于每次请求的上下文与输出 token。重置会话：删除 `runtime/cursor_cli_session.json` 或关掉 `reuse_session`。

**安全与路径**：相对路径 `session_state_path` 会解析在 `repo_root` 之下，禁止 `..` 逃出仓库；`chat_id` 仅接受长度 ≤256 的 ASCII 子集（字母数字与 `._:-`），含空格或其它字符的 id 会被忽略不写盘，以免破坏 argv/PowerShell。若真实 id 含其它符号，请改用官方支持的字符集或等 Cursor CLI 回传可解析字段。

**飞书 CLI 发送（`sender: feishu_cli_webhook`）**：需准备 `feishu-cli` 命令，并在环境变量中配置 `FEISHU_WEBHOOK_URL`（可选 `FEISHU_WEBHOOK_SECRET`）。配置 `sender_config_file: config/senders.example.json` 后，任务会在成功生成 `report_*.md` 后自动发出摘要。默认发送失败不影响日报主流程；若希望发送失败时任务整体失败，设 `sender_strict: true`。

配置里整数项若**写了空值或非数字**，启动时会得到**明确字段名**的错误提示（避免 `ValueError: invalid literal for int()` 难排查）。`analyze` / `generate_report` 为子进程超时；`cursor_cli` 会把 `provider_timeout_seconds` 传给底层 `agent` 子进程，超时后直接失败并写入兜底 `ai_content`。其他非子进程 provider 仍使用线程等待上限；接入真实 HTTP/模型调用时请在客户端再设超时或可中断逻辑。

任务计划请将「起始于」设为 **本仓库根目录**，脚本内部亦会以仓库根为子进程 `cwd`；若锁被占用会**直接退出 0** 避免异常栈。

### 2) 手动执行一次（run-once）

```bash
python scripts/schedule_report.py --config config/report_schedule.yaml
```

若报告文件已存在但你希望**强制重跑同一窗口**（覆盖再生成），可加 `--force`：

```bash
python scripts/schedule_report.py --config config/report_schedule.yaml --start "2026-05-10 10:00:00" --end "2026-05-10 11:00:00" --force
```

`--force` 行为说明：

- 仅影响“目标报告已存在时是否跳过”这一步
- 不会修改 state 断点（手动窗口模式本就禁用 state 更新）
- 适合你调整了 `ai_prompt.md`、渲染逻辑或 provider 配置后，对同一时间窗做回放验证

仅查看本次将处理哪个窗口（不执行）：

```bash
python scripts/schedule_report.py --config config/report_schedule.yaml --dry-run
```

### 2.5) 常驻模式（单进程每小时自动执行）

```bash
python scripts/schedule_report.py --config config/report_schedule.yaml --daemon
```

可选参数：

- `--poll-seconds 30`：等待下一次边界时的轮询间隔（最小 5 秒）
- `--daemon-backoff-seconds 60`：单轮失败后的退避时间（最小 5 秒）

说明：

- 常驻模式按 `interval_minutes` 对齐网格触发，不会因单轮执行耗时造成长期漂移。
- 若某轮执行超过 1 小时，下一轮会顺序补跑积压窗口（不并发）。
- 与 `--dry-run` / `--start --end` 互斥。

### 3) 定时任务（Windows / macOS）

**Windows**：任务计划程序创建任务，按间隔运行上面的 run-once；「起始于」填写仓库根目录，并把 `CURSOR_API_KEY` 等写入任务的环境变量（若使用 `cursor_cli`）。

**macOS**：可用 `launchd`（plist 里设置 `EnvironmentVariables`）或 `cron`，同样先 `cd` 到仓库根再调用 `python scripts/schedule_report.py ...`。

脚本内部已做：

- 锁文件并发保护（防止重叠触发）
- 过期锁自动回收
- 状态原子写入
- 窗口幂等跳过与断点补跑

---

## 🛠️ 详细步骤 (内部逻辑)

## 📂 数据来源

输入来源是当前项目下 `vendor/wechat-decrypt/decrypted/` 的原始 SQLite 数据库：

- `contact/contact.db`
- `message/message_*.db`
- `session/session.db`

`scripts/analyze_chat.py` 会直接从这些库里读取指定群聊，不再经过 JSON 转换；并且默认会在每次分析前先刷新一次解密快照。

## 📁 项目结构

- `scripts/`: 核心 Python 脚本
    - `setup_check.py`: 检查微信解密环境并准备 `wechat-decrypt`
    - `decrypt_wechat.py`: 解密本机微信数据库
    - `list_wechat_groups.py`: 列出解密库中的群聊和消息量
    - `wechat_decrypted_reader.py`: 读取解密后的微信群聊原始数据
    - `analyze_chat.py`: 直接分析解密后的群聊数据库并生成统计
    - `generate_report.py`: 文字总结生成（默认）与可选 HTML 渲染
- `assets/`: 资源文件
    - `report_template.html`: Jinja2 报告模板
- `references/`: 参考文档
    - `ai_prompt.md`: AI 提示词模板
- `SKILL.md`: 技能详细说明

## 📝 License

MIT
