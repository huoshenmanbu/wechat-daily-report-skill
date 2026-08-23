# WeChat Daily Report Generator

从本机已解密的微信数据库读取指定群聊，生成 Markdown（可选 HTML）日报；可接入 AI 总结和飞书推送。

本文以 **macOS** 为主。首次使用只需要完成「安装、解密、生成一次报告」三步；只有要自动定时运行时，才需要启动守护进程或配置 `launchd`。

## 先弄清三个命令

| 命令 | 做什么 | 会不会定时生成日报 |
| --- | --- | --- |
| `python scripts/setup_check.py --ensure-decryptor` | 检查环境、安装微信解密工具及其依赖 | 不会 |
| `sudo python scripts/decrypt_wechat.py` | 提取微信数据库密钥并解密本机数据 | 不会 |
| `python scripts/schedule_report.py --config config/report_schedule.yaml --daemon` | 常驻运行，按配置周期生成日报 | 会 |

## Mac 最短启动流程

### 1. 准备项目与 Python

先安装 Python 3.9+（推荐 Python 3.10+）和 Xcode Command Line Tools：

```bash
xcode-select --install
python3 --version
```

进入项目并创建虚拟环境：

```bash
cd /path/to/wechat-ai
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install jieba jinja2
```

`jinja2` 只在需要 HTML 报告时使用，但安装它没有问题。

### 2. 安装并检查微信解密工具

打开并登录 macOS 微信，保持微信运行。然后执行：

```bash
python scripts/setup_check.py --ensure-decryptor
```

这一步会准备 `vendor/wechat-decrypt` 和它的 Python 依赖，并检查微信进程；**它不会启动日报，也不会建立定时任务。**

### 3. 解密微信数据库

```bash
sudo .venv/bin/python scripts/decrypt_wechat.py
```

成功后，解密数据默认在：

```text
vendor/wechat-decrypt/decrypted/
```

列出可用群聊，确认目标群名称：

```bash
python scripts/list_wechat_groups.py --decrypted-dir vendor/wechat-decrypt/decrypted
```

### 4. 配置并生成第一份报告

编辑 [config/report_schedule.yaml](config/report_schedule.yaml)，至少设置：

```yaml
chatroom: 你的群名称或关键词
decrypted_dir: vendor/wechat-decrypt/decrypted
output_dir: outputs/reports
provider: stub
sender: none
```

首次建议使用 `provider: stub` 和 `sender: none`，先验证数据读取与报告流程；此模式不调用 AI，也不会发送飞书。

先查看本次会处理的时间窗口：

```bash
python scripts/schedule_report.py --config config/report_schedule.yaml --dry-run
```

生成一次报告：

```bash
python scripts/schedule_report.py --config config/report_schedule.yaml
```

报告会写到 `outputs/reports/`。

## 启动定时日报

调度周期由 `config/report_schedule.yaml` 的 `interval_minutes` 决定。例如设为 `60`，即每小时执行一次。

### 方式一：前台常驻（最适合先验证）

```bash
source .venv/bin/activate
python scripts/schedule_report.py --config config/report_schedule.yaml --daemon
```

终端关闭或电脑重启后会停止。脚本会按整点网格触发，并处理停机期间有限数量的积压窗口。

### 方式二：使用 launchd 开机自启（长期运行）

创建 `~/Library/LaunchAgents/com.wechat.daily-report.plist`，将下面的 `/Users/yourname/path/to/wechat-ai` 改成你的真实项目路径：

```bash
mkdir -p ~/Library/LaunchAgents /Users/yourname/path/to/wechat-ai/runtime
```

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.wechat.daily-report</string>
  <key>WorkingDirectory</key>
  <string>/Users/yourname/path/to/wechat-ai</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/yourname/path/to/wechat-ai/.venv/bin/python</string>
    <string>scripts/schedule_report.py</string>
    <string>--config</string>
    <string>config/report_schedule.yaml</string>
    <string>--daemon</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>EnvironmentVariables</key>
  <dict>
    <key>FEISHU_WEBHOOK_URL</key>
    <string>https://open.feishu.cn/open-apis/bot/v2/hook/替换成你的Webhook</string>
    <key>FEISHU_WEBHOOK_SECRET</key>
    <string>替换成你的签名密钥</string>
  </dict>
  <key>StandardOutPath</key>
  <string>/Users/yourname/path/to/wechat-ai/runtime/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/yourname/path/to/wechat-ai/runtime/launchd.err.log</string>
</dict>
</plist>
```

如果飞书机器人没有开启签名校验，请从 plist 删除 `FEISHU_WEBHOOK_SECRET` 这一组 key/string。plist 包含敏感信息，建议限制为仅当前用户可读：

```bash
chmod 600 ~/Library/LaunchAgents/com.wechat.daily-report.plist
```

加载并启动：

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.wechat.daily-report.plist
launchctl kickstart -k gui/$(id -u)/com.wechat.daily-report
```

查看状态：

```bash
launchctl print gui/$(id -u)/com.wechat.daily-report
```

停止并移除：

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.wechat.daily-report.plist
```

> `launchd` 运行时不会读取你的 `.zshrc`，因此 Webhook、签名密钥和 AI Provider 所需的 API Key 都必须写在 plist 的 `EnvironmentVariables` 中。这个 plist 位于用户目录，不要将包含真实密钥的副本提交到 Git。

## 接入真实 AI 或飞书

### AI Provider

将 `provider` 改为以下之一：

| `provider` | 额外要求 |
| --- | --- |
| `stub` | 离线占位，适合测试 |
| `cursor_cli` | 本机已安装 Cursor CLI，`agent` 在 `PATH` 中 |
| `dashscope` | 设置 `DASHSCOPE_API_KEY` |
| `deepseek` | 设置 `DEEPSEEK_API_KEY` |

Provider 参数参考 [config/ai_providers.example.json](config/ai_providers.example.json)。API Key 只放环境变量，不要写入 JSON 配置。

### 飞书推送

设置：

```yaml
sender: feishu_webhook
sender_config_file: config/senders.json
```

并提供 `FEISHU_WEBHOOK_URL`（可选 `FEISHU_WEBHOOK_SECRET`）。配置格式见 [config/senders.example.json](config/senders.example.json)。首次建议保持 `sender: none`，确认报告内容无误再打开推送。

## macOS 解密失败时再看这里

绝大多数配置不需要手动处理权限。仅当第 3 步报“密钥扫描失败”或无法读取微信进程时，按下面顺序排查：

1. 确认微信正在运行且已登录。
2. 确认在 Mac 本机的 Terminal 中运行了 `sudo .venv/bin/python scripts/decrypt_wechat.py`，不要先通过 SSH 操作。
3. 检查微信签名：

   ```bash
   codesign -dv /Applications/WeChat.app 2>&1 | grep -E "Signature|flags"
   ```

4. 若仍失败，项目附带的解密器可能需要将微信改为 ad-hoc 签名：

   ```bash
   sudo codesign --force --deep --sign - /Applications/WeChat.app
   ```

   重签名可能使微信需要重新登录；微信升级后也可能需要再次处理。执行前请自行确认影响。

更完整的说明见 [macOS 权限指南](vendor/wechat-decrypt/docs/macos-permission-guide.md)。

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `python scripts/setup_check.py --ensure-decryptor` | 安装并检查解密环境，不会开始调度 |
| `sudo .venv/bin/python scripts/decrypt_wechat.py` | 解密本机微信数据库 |
| `python scripts/list_wechat_groups.py --decrypted-dir vendor/wechat-decrypt/decrypted` | 列出可分析的群聊 |
| `python scripts/schedule_report.py --config config/report_schedule.yaml --dry-run` | 查看将处理的时间窗口 |
| `python scripts/schedule_report.py --config config/report_schedule.yaml` | 执行一次日报 |
| `python scripts/schedule_report.py --config config/report_schedule.yaml --daemon` | 常驻定时执行 |

## 项目结构

- `scripts/setup_check.py`：环境检查与解密器安装
- `scripts/decrypt_wechat.py`：微信数据库解密入口
- `scripts/list_wechat_groups.py`：群聊列表与消息量
- `scripts/schedule_report.py`：日报调度入口
- `config/report_schedule.yaml`：群聊、周期、AI 与发送配置
- `outputs/reports/`：生成的日报

## License

MIT
