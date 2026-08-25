# VoiceHub 任务清单

> 最后更新：2026-08-25（**v0.3.0 已发布**：V3 Linux 双端 + 双 AppImage 首发；M7/M8/M10 完成；
> M9 转随用随做；V4 方向登记；V2 收尾随用随验；V1 尾巴平板部署）
> 已完成任务归档：[V1](./docs/tasks-archive/v1-completed.md) · [V2](./docs/tasks-archive/v2-completed.md)

## V3：Linux 双端适配（2026-08-20 立项，未启动）

> 里程碑编号延续全局序列：M7 预研与选型 / M8 Linux 主控后端 / M9 接收端体验 / M10 工程化收尾。
> 方向与明细见 PLAN.md「当前里程碑」；未启动只登记方向，启动后在本节展开。

- [x] 先行项（Ubuntu 22.04 基准，本机 WSL2 即刻开工）：搭 WSL2 Ubuntu 22.04 开发环境
  （python3.10 + xclip/xdotool + systemd）；CI 加 ubuntu-22.04 pytest job；
  输入栈 spike + linux_backend 可测代码按 X11 假设先行开发（TDD）。
  **完成时间**: 2026-08-20（WSL 环境由用户预装，助手完成其余全部）
  - CI：build.yml 加 linux job（ubuntu-22.04 + py3.10 + pytest），push 后生效；
    本地双平台实测 **Windows / WSL 各 104 全绿**（旧 94 + 新增 linux_backend 10 个）。
  - spike（`scripts/spike_linux_stack.sh`，openKylin 可复用）：xclip 中英 UTF-8 读写自洽 PASS、
    xdotool 注入 PASS；**WSLg 剪贴板桥与 Windows 不互通**（WSL 局限，不影响开发，真机无此问题）。
  - 开发（`voicehub/linux_backend.py` 159 行 + `tests/test_linux_backend.py` 10 用例）：
    xclip 版 read_text（显式 UTF-8 防无 LANG 环境乱码）、X11ClipboardPoller 轮询监听
    （仅武装期轮询；武装沿登记基线防假事件）、pynput 热键（不可用降级日志）、
    main.py 平台守卫扩展、requirements 加 `pynput; linux` 标记。
  - 端到端冒烟（`scripts/smoke_linux_e2e.sh`，openKylin 可复用）**全绿**：daemon 启动 →
    pynput 注册 3 热键 → xdotool 合成 Alt+2 武装 → xclip 写"转写" → 拦截路由 →
    接收端粘贴 → SQLite 落库（隔离端口 8100/5051/9998 + /tmp 隔离 db）。
  - 踩坑三条：① Git Bash → wsl.exe 有 MSYS 路径转换/引号双重坑，跨端命令一律
    `MSYS_NO_PATHCONV=1` + 脚本文件执行；② `xdotool key alt+2` 合成方式 pynput 收不到，
    必须 keydown/keyup 显式序列；③ WSL mirrored 网络 + Windows 真身 daemon 同跑会撞端口
    （8000/9898 shared），冒烟必须换独立端口。
- [x] M7：Linux 预研与选型。
  **完成时间**: 2026-08-20（ADR-7 定案：xclip 读写/轮询监听 + xdotool + pynput，WSL 真跑验证全绿）
  - 策略变更（用户决策）：**取消 openKylin 真机前置验证**——Ubuntu 22.04 构建的 AppImage
    历来在 openKylin 正常运行（自包含 + glibc 兼容），以 AppImage 交付、差异随用随修。
  - 原"openKylin 三确认 + 闪电说四问"取消；闪电说四问转 WSLg 内选做，不阻塞主线。
- [x] M8：linux_backend 主控后端 + 全链路验证（WSL 内完成）。
  **完成时间**: 2026-08-20（代码 + 双平台 104 测试 + WSL 端到端冒烟全绿，见先行项记录；
  AppImage 形态验证并入 M10；共享分区数据连续性文档转可选随用随做。）
- [ ] M9：接收端 Linux 体验（systemd service / 一键安装脚本 / README 章节）。
  （xclip/xdotool 粘贴后端已存在且冒烟通过；AppImage 交付后或以 AppImage 自带说明替代脚本。）
- [x] M10：daemon/receiver 打包 **AppImage**（PyInstaller one-dir → AppRun +
  .desktop + 图标 → appimagetool）+ WSL 内 AppImage 冒烟 + CI ubuntu job 扩展打包上传 +
  发 v0.3.0 Release + 文档收口。
  **完成时间**: 2026-08-25（**Release v0.3.0 已发布**，四产物：Windows 双 zip + Linux 双
  AppImage；main 与 tag 流水线均全绿，linux job 含打包首跑通过。）
  - 打包链（2026-08-20）：`packaging/build_linux.sh` 一键产出双 AppImage；
    `paths.py` 加 `VOICEHUB_HOME`（AppImage 只读挂载 → 便携目录），AppRun 播种
    config/检测 xclip、xdotool；`scripts/smoke_appimage.sh` 冒烟全绿；
    CI linux job 打包上传 + tag 时 AppImage 进 Release；README Linux 章节。
    测试 104→106，Windows/WSL 双平台全绿。详见本条目上方进展记录。
  - 提交拆分（4 commits）：feat 主控后端 / feat 打包链 / docs 归档 / chore .gitattributes
    （强制 .sh LF，防 autocrlf 破坏 WSL 执行）。

## V4（远期占位）：自建转写内核（2026-08-20 方向登记，未立项）

> 仅登记方向，核心内容未定，待 V3 收尾或中途再细化讨论，见 PLAN.md「V4（远期占位）」段。
> 动机：摆脱对闪电说（闭源收费 / Pro 会员分层）的依赖，自主可控；核心：自建录音 → ASR →
> LLM 润色内核，逻辑更流畅、配置可自定义；增值：记忆系统 + 项目管理系统接入，
> 按项目隔离管理对话/转写历史。

## V2 收尾（用户侧验证，随用随验）

- [ ] 窗口交互三步：点 X 驻留托盘 → 托盘「打开主窗口」唤回 → 托盘「退出」彻底关闭
  （exe 当前版本为 pywebview 关窗语义修复后的重建版）。
- [x] commit + push 后观察 GitHub Actions 首跑；满意后打 `v0.2.0` 发首个 Release。
  **完成时间**: 2026-08-20（Release v0.2.0 已发布，附双 exe zip）
  - CI 首跑两坑（均在执行本条目时修复）：① 构建脚本写死 `.venv\Scripts\pyinstaller.exe`，
    CI 无 .venv 直接退出 → 改为"本地优先 .venv、否则系统 python -m PyInstaller"；
    ② 默认 GITHUB_TOKEN 只读，发 Release 403 → workflow 加 `permissions: contents: write`。
  - 终态：main / v0.2.0 流水线全绿；产物 VoiceHub-windows-x64.zip（31MB）、
    VoiceHubReceiver-windows-x64.zip（14MB）。
- [ ] 笔记本部署 VoiceHubReceiver exe，Alt+2 跑一轮真链路（发一条语音到笔记本粘贴）。
- [ ] 托盘勾选「开机自启」后重启电脑，验证托盘自动出现（此前已注册过一次）。

## V1 收尾（剩余项）

- [ ] 平板 Termux 部署 + Root 粘贴 + 后台保活（需 Termux 真机）。

## 维护规则

- 新任务写到对应里程碑下；未启动的里程碑只登记方向，不预列细节。
- 完成记录写在任务条目下（含 **完成时间**），大段过程性记录写入 `docs/tasks-archive/`，不把主文件写成长流水账。
- 里程碑状态变化时同步更新 `PLAN.md`。
