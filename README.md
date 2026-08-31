# VoiceHub

**单点语音输入，多端分发。** 一台麦克风经「闪电说」（ASR + LLM 润色）完成转写并写入剪贴板，
VoiceHub 守护进程按热键把文本一键注入台式机 / 笔记本 / 平板的光标位置，全量转写持久化 SQLite，
为后续向量化与个人 Agent 记忆中枢预留接口。

> 🌐 **官网**（特性总览 / 一键下载）：<https://xizaizai.github.io/VoiceHub/>

## 架构

```
麦克风 → 闪电说（转写+润色 → 写剪贴板） → VoiceHub daemon（台式机，后台常驻）
                                          ├─ Alt+1 台式机：闪电说原生粘贴，VoiceHub 仅记录
                                          ├─ Alt+2 笔记本：HTTP 推送 → 接收端自动粘贴
                                          └─ Alt+3 平板：  HTTP 推送 → Termux 接收端粘贴
```

- **台式机（主控）**：全局热键（`keyboard`）、剪贴板监听（`WM_CLIPBOARDUPDATE` 事件驱动）、
  设备发现（UDP 心跳 + 子网扫描兜底）、路由分发、SQLite 存储、Web 仪表盘（`127.0.0.1:8765`）、
  托盘 + 原生窗口（pywebview）+ 可视化设置页 + 开机自启（Linux 为托盘 + 浏览器仪表盘）。
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

从 [Releases](../../releases) 下载（CI 自动构建），解压/赋权后：

- 台式机（Windows）：双击 `VoiceHub.exe`（托盘常驻 + 原生窗口仪表盘；`config.json` 在同目录可直接编辑，
  也可在仪表盘「设置」页改；托盘可勾选开机自启）。日志在 `logs\voicehub.log`。
- 笔记本（Windows）：双击 `VoiceHubReceiver.exe`（默认名称 `laptop`；与 config 的 target key 不同时，
  给快捷方式加参数 `--name <key>`）。
- **Linux（V3）**：主控 `VoiceHub-x86_64.AppImage`、接收端 `VoiceHubReceiver-x86_64.AppImage`
  （Ubuntu 22.04 构建，openKylin 等兼容发行版可用）：

  ```bash
  sudo apt install xclip xdotool      # 运行期系统依赖（AppRun 启动时也会检测提示）
  chmod +x VoiceHub-x86_64.AppImage
  ./VoiceHub-x86_64.AppImage          # config/db/logs 锚定 AppImage 旁边，首启自动播种 config
  ```

  启动后系统托盘出现 VoiceHub 图标（SNI 协议，菜单「打开仪表盘 / 退出」，左键单击亦可打开）；
  仪表盘默认 <http://127.0.0.1:8765>（8000 易与 Triton 等常见服务冲突，已避开）。
  注意：Wayland 会话下全局热键依赖 XWayland——焦点在 XWayland 应用（含闪电说）时可用，
  焦点在原生 Wayland 应用时收不到（已知限制；自建内核的系统快捷键通道不受此限）。

### 双引擎：自建听写内核（V4，无需闪电说）

VoiceHub 内置一套独立的听写引擎（`transcription.engine: builtin`），不依赖闪电说客户端：
**录音（麦克风）→ 云端 ASR（火山豆包·流式语音识别 2.0）→ LLM 润色（可选）→ 剪贴板/多端分发**，
全程由 VoiceHub 完成，UI 上有波形悬浮框与识别状态动画。

**启用三步：**

1. **切引擎**：仪表盘「设置」→ 转写引擎 → 选「自建内核」→ 保存后重启程序。
2. **配凭证**：`config.local.json`（与程序同目录，已被 gitignore）写入火山凭证：
   ```json
   { "transcription": { "app_key": "<数字APP ID>", "access_key": "<Access Token>" } }
   ```
   （新版控制台用户则填 `"api_key": "<API Key>"`；也可用环境变量
   `VOICEHUB_ASR_APP_KEY` / `VOICEHUB_ASR_ACCESS_KEY` / `VOICEHUB_ASR_API_KEY`。
   需在火山引擎控制台开通「豆包流式语音识别模型 2.0」。）
3. **注册快捷键**：仪表盘「设置」→ 听写快捷键 → 一键注册（写入 UKUI 自定义快捷键，
   任何界面下生效；按一下开始、再按一下结束）。

**使用与反馈**：按热键 → 屏幕底部波形框出现（说话时跳动、可拖动、标题实时显示热键）
→ 再按热键结束（或说完静音自动结束，参数可配）→ 框切「RECOGNIZING...」扫光
→ 结果进剪贴板 + 桌面通知 + 仪表盘记录，`Ctrl+V` 粘贴。润色模式
（关闭/轻整理/结构化整理/自定义，需 DeepSeek key）同在设置页配置。

**Linux 系统依赖**：`xclip`（剪贴板必装）、`alsa-utils`（arecord 录音兜底，基本预装）、
`wl-clipboard`（Wayland 剪贴板推荐）、`xdotool`（可选）。**发行版兼容**：AppImage
自包含，openKylin / Ubuntu / Debian 等 x86_64 发行版通用（低 glibc 构建，向下兼容
新版系统）。

**双引擎关系**：`shandianshuo`（默认）与 `builtin` 共用 Alt+N 目标粘滞与分发链路，
设置页随时切换；闪电说不可用的平台（如 openKylin 上其 UI 渲染异常）用 builtin 即可
完全替代。

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

启动后自动打开 <http://127.0.0.1:8765> 仪表盘（实时状态 + 历史记录，WebSocket 自动刷新；
Linux 无自动开窗，走托盘菜单或手动打开）。

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
bash packaging/build_linux.sh  # Linux：测试 + 构建双 AppImage → dist/*.AppImage
python scripts/make_icon.py    # 重新生成应用图标 assets/voicehub.ico + voicehub.png
```

CI（GitHub Actions）在 push / PR 时自动跑测试（Windows + Linux）并构建 Windows 双 exe +
Linux 双 AppImage，打 `v*` tag 发布 Release。

Linux 开发/验证环境为 WSL2 Ubuntu 22.04（与 CI runner 一致）；验证工具：
`scripts/spike_linux_stack.sh`（输入栈）、`scripts/smoke_linux_e2e.sh`（源码全链冒烟）、
`scripts/smoke_appimage.sh`（AppImage 冒烟）。

文档：[PLAN.md](./PLAN.md)（规划与 ADR）· [TASKS.md](./TASKS.md)（任务）· [docs/PRD.md](./docs/PRD.md)（需求）· [CLAUDE.md](./CLAUDE.md)（工作规范）

## 致谢

- [闪电说](https://shandianshuo.cn/)（前身「代体 Daiti」）：优秀的本地 AI 离线语音输入法
  （Windows / macOS）。VoiceHub 的剪贴板监听分发链路兼容其输出，检测到在线时优先使用；
  二者为独立产品，无隶属关系。感谢其对个人语音输入体验的贡献。
- [Vue.js](https://vuejs.org/) 与 [Tailwind CSS](https://tailwindcss.com/)（均为 MIT）：
  Web 仪表盘前端依赖，已随包本地分发（`voicehub/static/vendor/`），不依赖公网 CDN。

## License

[MIT](./LICENSE)
