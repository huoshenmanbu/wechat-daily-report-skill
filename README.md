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
- `max_catchup_windows`: 单次运行最多补跑几个积压窗口（默认 24）
- `analyze_timeout_seconds` / `report_timeout_seconds` / `provider_timeout_seconds`: 子进程与 Provider 超时（秒）

配置里整数项若**写了空值或非数字**，启动时会得到**明确字段名**的错误提示（避免 `ValueError: invalid literal for int()` 难排查）。`analyze` / `generate_report` 为子进程超时；`provider_timeout_seconds` 用线程等待上限——超时后调度会继续（并写入兜底 `ai_content`），但 **Python 线程无法像子进程一样被强制杀掉**，stub 几乎瞬时无影响；接入真实 HTTP/模型调用时请在客户端再设超时或可中断逻辑。

任务计划请将「起始于」设为 **本仓库根目录**，脚本内部亦会以仓库根为子进程 `cwd`；若锁被占用会**直接退出 0** 避免异常栈。

### 2) 手动执行一次（run-once）

```bash
python scripts/schedule_report.py --config config/report_schedule.yaml
```

仅查看本次将处理哪个窗口（不执行）：

```bash
python scripts/schedule_report.py --config config/report_schedule.yaml --dry-run
```

### 3) Windows 任务计划（推荐）

创建一个任务，每 `N` 分钟执行一次上面的 run-once 命令即可。脚本内部已做：

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
