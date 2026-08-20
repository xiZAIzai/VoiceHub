# VoiceHub

**单点语音输入，多端分发。** 一台麦克风经「闪电说」（ASR + LLM 润色）完成转写并写入剪贴板，
VoiceHub 守护进程按热键把文本一键注入台式机 / 笔记本 / 平板的光标位置，全量转写持久化 SQLite，
为后续向量化与个人 Agent 记忆中枢预留接口。

## 架构

```
麦克风 → 闪电说（转写+润色 → 写剪贴板） → VoiceHub daemon（台式机，后台常驻）
                                          ├─ Alt+1 台式机：闪电说原生粘贴，VoiceHub 仅记录
                                          ├─ Alt+2 笔记本：HTTP 推送 → 接收端自动粘贴
                                          └─ Alt+3 平板：  HTTP 推送 → Termux 接收端粘贴
```

- **台式机（主控）**：全局热键（`keyboard`）、剪贴板监听（`WM_CLIPBOARDUPDATE` 事件驱动）、
  设备发现（UDP 心跳 + 子网扫描兜底）、路由分发、SQLite 存储、Web 仪表盘（`127.0.0.1:8000`）、
  托盘 + 原生窗口（pywebview）+ 可视化设置页 + 开机自启。
- **笔记本 / 平板（接收端）**：HTTP 收文（端口 5050）→ 写本机剪贴板 → 模拟 `Ctrl+V` 粘贴；
  每 4 秒向子网广播 UDP 心跳（端口 9898）供主控自动发现。

## 目录结构

```
voicehub/
├── main.py               # 主控入口：配置加载、组件组装、平台后端启动
├── win_backend.py        # Windows 热键/剪贴板/托盘/自启
├── orchestrator.py       # 编排层（单向数据流）
├── state.py              # 粘滞目标状态机
├── clipboard_monitor.py  # 剪贴板监听（事件驱动 + 基线/去抖/过期）
├── discovery.py          # 设备发现（UDP 心跳 + 子网扫描）
├── router.py             # 路由编排
├── transport.py          # HTTP 推送客户端
├── receiver.py           # 笔记本/桌面接收端
├── tablet_server.py      # 平板 Termux 接收端（纯标准库 + Root 粘贴）
├── storage.py            # SQLite（transcript_logs）
├── app_window.py         # pywebview 原生窗口（关窗不退程序）
├── settings.py           # config 读写服务（校验/原子写回/热应用）
├── paths.py              # frozen/源码双模式路径锚定
└── web.py                # Web 仪表盘（FastAPI + WebSocket + 设置页）
tests/                    # pytest 测试
packaging/                # PyInstaller spec + 构建脚本（daemon/receiver exe）
scripts/                  # 图标生成等工具
assets/                   # 应用图标
config.json               # 运行配置
```

## 安装

要求 **Python 3.10+**（建议 3.11 / 3.12）：

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Windows 专属依赖（keyboard / pywin32 / pystray / Pillow）已在 requirements.txt 中按平台标记，
Windows 下安装即用；非 Windows 环境自动跳过。

## 使用

**方式一：打包版（免 Python 环境，推荐）**

从 [Releases](../../releases) 下载 zip（CI 自动构建），解压后：

- 台式机：双击 `VoiceHub.exe`（托盘常驻 + 原生窗口仪表盘；`config.json` 在同目录可直接编辑，
  也可在仪表盘「设置」页改；托盘可勾选开机自启）。日志在 `logs\voicehub.log`。
- 笔记本：双击 `VoiceHubReceiver.exe`（默认名称 `laptop`；与 config 的 target key 不同时，
  给快捷方式加参数 `--name <key>`）。

**方式二：源码运行**

**1. 笔记本启动接收端**（`--name` 需与 config.json 的 target key 一致）：

```bash
python -m voicehub.receiver --name laptop
```

**2. 平板（Termux）**：

```bash
python -m voicehub.tablet_server --name tablet   # Root 粘贴需 su
```

**3. 台式机启动主控**：

```bash
python -m voicehub.main
```

启动后自动打开 <http://127.0.0.1:8000> 仪表盘（实时状态 + 历史记录，WebSocket 自动刷新）。

**4. 日常使用**：按 `Alt+2`（选中笔记本目标，闪电说同时开始录音）→ 说话 → 按 `Alt` 结束
→ 转写写入剪贴板 → 自动推送到笔记本光标处。

### 关键配置（config.json）

| 字段 | 默认 | 说明 |
|---|---|---|
| `stability_ms` | 300 | 剪贴板去抖等待（毫秒） |
| `pending_timeout_sec` | 300 | 按热键后的等待窗，需覆盖最长听写时长 |
| `discovery_port` / `receiver_port` | 9898 / 5050 | 心跳 UDP / 接收 HTTP 端口 |
| `targets.*.hotkey` | 1 / 2 / 3 | Alt+N 的 N |
| `targets.*.endpoint` | 空 | 手动写死接收端地址可覆盖自动发现 |

## 已知行为（v1）

- 发远程目标时，闪电说仍会在台式机当前焦点贴一份（其无自动粘贴开关，见 ADR-5）。
- 按 Alt+N 会同时触发闪电说录音（Alt 是其触发键）——当作"按下即录"使用。
- 武装等待期内手动复制的内容会被误判为转写发送（ADR-4 已知边界）。

## 开发

```bash
python -m pytest          # 单元测试
packaging\build_daemon.bat     # 构建主控 exe → dist\VoiceHub\
packaging\build_receiver.bat   # 构建接收端 exe → dist\VoiceHubReceiver\
python scripts/make_icon.py    # 重新生成应用图标 assets/voicehub.ico
```

CI（GitHub Actions）在 push / PR 时自动跑测试并构建双 exe，打 `v*` tag 发布 Release。

文档：[PLAN.md](./PLAN.md)（规划与 ADR）· [TASKS.md](./TASKS.md)（任务）· [docs/PRD.md](./docs/PRD.md)（需求）· [CLAUDE.md](./CLAUDE.md)（工作规范）

## License

[MIT](./LICENSE)
