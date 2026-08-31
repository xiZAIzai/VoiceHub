# VoiceHub 任务清单

> 最后更新：2026-08-25 晚（**V4 立项：自建云端转写内核（ADR-9，M11–M13）**，任务已细化待开工；
> 此前 openKylin 随用随修第一轮完成并已 push）
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
- [x] openKylin 实机随用随修第一轮（v0.3.0 AppImage 首次真机使用暴露，2026-08-25）。
  **完成时间**: 2026-08-25（三问题全部修复，110 单测 + 本机重打包全链路实测全绿，ADR-8）
  - ① 托盘没出现：**不是故障**——Linux 版 M8 定案无托盘；但 openKylin（Wayland/kylin-wlcom）
    下程序完全不可见不可接受 → 补齐 `linux_tray.py`（jeepney 直写 StatusNotifierItem +
    DBusMenu，菜单「打开仪表盘/退出」，UKUI 面板实测 6 步 DBus 验证全绿；无 watcher/无总线/
    无 jeepney 均降级日志）。
  - ② 仪表盘起不来：8000 端口被 openKylin 自带 kytensor（Triton 内核，AI 推理服务）占用，
    uvicorn bind 失败 → 默认端口 8000→8765（config 默认值 + 种子 + README + 本机已播种 config）。
  - ③ 热键全灭：Release AppImage 缺 `pynput.keyboard._xorg` + `python-xlib`——pynput≥1.8
    import 期即连 X server，headless CI 上 `collect_submodules("pynput")` **静默返回空表**
    （WSL 本地构建因 WSLg 有 DISPLAY 而正常，故当时冒烟全绿、Release 翻车）→ spec 显式
    hiddenimports 写死模块名；CI 加 xvfb + AppImage 冒烟断言「已注册 Linux 热键」；
    smoke 脚本补 EXTRACT_AND_RUN 残留进程清杀。
  - 本机（openKylin）用 conda py3.12 重建 `~/.venvs/voicehub` 重打包：冒烟 7 项全绿（含热键
    武装→路由→落库），桌面真实运行验证托盘注册/仪表盘 8765/托盘「退出」干净收口。
    注：conda py3.14 + venv 跑 PyInstaller 会段错误，py3.12 原生 conda 环境正常。
  - 遗留验证项：闪电说全链路真机联调；焦点在原生 Wayland 应用时 X11 热键收不到
    （已知限制，README 已注明）；Linux 开机自启（.desktop）待做。
- [ ] 闪电说 openKylin 适配调查（2026-08-25 第一轮，**glibc 已解 / UI 渲染待官方**）：
  - ✅ glibc：闪电说 0.7.5 要求 GLIBC_2.39（Ubuntu 24.10+ 基线），本机 2.38，deb/AppImage
    同源同病。**免容器方案已落地并验证**：`~/bin/shandianshuo-launcher`（侧载 Ubuntu
    libc6 2.43 自定义 loader + 复刻 linuxdeploy 环境；组成：`~/bin/sds-runtime/app` 解包
    AppImage + `sds-runtime/glibc`）。Docker 对 glibc 问题无必要；且**对 UI 渲染问题无效**
    （容器里 UI 仍要画到同一个合成器上）。
  - ❌ UI 渲染（定性为闪电说 × kylin-wlcom 兼容 bug，已试尽外部手段）：X11 后端窗口在
    XWayland 映射但用户不可见（合成器忽略 X11 置顶/激活请求；应用用 shaped 圆角窗）；
    Wayland 后端窗口进 Alt+Tab（灰色缩略图）但从不显示，启动报 "No monitor available"，
    WebKit 视图 0 帧渲染。已试：软件渲染全套（GDK_GL=disable / LIBGL_ALWAYS_SOFTWARE /
    WEBKIT_DISABLE_COMPOSITING_MODE / WEBKIT_DISABLE_DMABUF_RENDERER）、沙箱排查
    （bwrap 在、4 个 WebKitWebProcess 存活无崩溃）。待反馈闪电说官方（内测版）。
  - 附带情报：闪电说 Linux 触发键为 **RAlt**（短按语音输入/长按助手），其全局键
    rdev::grab 也被权限拒（降级轮询）；VoiceHub 侧若要对齐改 config
    `shandianshuo.trigger_key: "alt_gr"`。闪电说为 Tauri 应用（非 Electron），GTK 后端。
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

## V4：自建转写内核·云端 ASR（✅ M11 全部完成 + M12 大半，2026-08-27 收口）

> 定案（ADR-9）：ASR 统一走云 / 文本直通 orchestrator 不经剪贴板链路 /
> 双引擎 config.engine 开关共存 / 触发键独立。过程长文归档：
> `docs/tasks-archive/V4-M11-M12上-自建转写内核.md`（spike 鉴权攻防、
> 各实机事故修复、悬浮框 UI 攻坚史全记录）。

- [x] **M11：最小闭环竖切** ✅ 2026-08-26（⑧项全勾，明细与验收见归档）：
  provider spike → config transcription 段（local.json 深合并+环境变量密钥）
  → dictation 包（vad/recorder[arecord 回退]/asr_client[WS v3 二进制协议，
  旧版控制台双头鉴权实测打通]/engine 会话令牌状态机）→ route_direct 直通路由
  （source=builtin 落库）→ 触发三通道 → 测试/实机/用户三重验收。
- [x] **M12-① 润色四模式** ✅ 2026-08-26：off/light/structured/custom +
  DeepSeek 客户端；失败降级原文铁律；raw_text 落专用列仪表盘对照；
  关 thinking + ASR 大块快发提速（10s 音频后处理 12s→3s 级）。
- [x] **体验定稿轮** ✅ 2026-08-27（对齐闪电说体感，用户验收「最终版本」）：
  波形悬浮框终态 = AGC 自适应波形 / 键帽显示真实热键（UKUI 注册绑定优先）/ 
  可拖动 / 跟随鼠标屏 / 持久线程架构（hide=withdraw 多轮回合零崩溃）/
  识别期「RECOGNIZING...」扫光陪跑至结果自动收框；剪贴板改 wl-copy 双写
  （kylin-wlcom 下 xclip 内容原生侧不可见的根治）；快速触发脚本
  （curl 直打实例，Alt+9 响应 2~4s→65ms）；auto_paste 光标注入两轮攻坚未达
  标，用户定案暂缓（机制保留，PLAN 后续清单）。
- [x] **双引擎改进轮** ✅ 2026-08-27（走查后落地）：设置页「转写引擎」下拉
  （ConfigService 链路，engine 归位主配置层、local 只留凭证）；
  直通写剪贴板防抖守卫（rebase_baseline_if_armed，幂等）。
- [ ] **M12 余项**
  - [ ] 仪表盘实时听写状态卡（engine.state WS 推送）+ 托盘 tooltip 阶段文案。
- [ ] **M13：工程化收尾 + v0.4.0**
  - [ ] spec 已收编 sounddevice/websocket/tkinter ✅；CI 冒烟补无麦克风跳过项。
  - [ ] Windows 侧验证（win32_write_text/HotkeyBackend 听写接线已备）。
  - [x] README 双引擎章节 ✅ 2026-08-27（启用三步 / 系统依赖 / 发行版兼容
    / 双引擎关系 / 润色配置说明）。
  - [x] 发 v0.4.0 Release ✅ 2026-08-27（CI 全绿四产物：Windows 双 zip + Linux 双
    AppImage；平台测试三处跨平台问题当场修——触发脚本 UTF-8+LF 强制/双写 which 桩）。
    Release notes 待粘贴（本机无 GitHub 写凭证）。
- [x] **v0.4.1：凭证自助填写通道发版** ✅ 2026-08-31：CredentialsService +
  设置页「API 凭证」卡（脱敏 tail-4，key 永不出服务）；__version__ 归位 0.4.1
  并在仪表盘页脚展示；CI Release 步骤加 generate_release_notes（自动变更日志）。
- [x] **v0.4.0 Release 四产物** ✅ 2026-08-31（CI 全绿，Release 由 bot 自动发布）。
- [ ] **官网（GitHub Pages）**：website/ 静态宣传页 + pages 部署 workflow ✅ 已推送
  （科技风单页：下载/特性/双引擎/隐私/闪电说致谢），**待用户 Settings→Pages 开启
  「GitHub Actions」源后重跑部署**；README 挂官网链接 + 致谢章节 ✅。

### 多厂商开放轮（2026-08-31，用户定案：一次做全不分二期）

- [x] **ASR 多厂商适配器** ✅：`AsrProvider` 协议下新增 `OpenAICompatAsrClient`
  （POST /audio/transcriptions，一套协议覆盖硅基流动 SenseVoice / Groq / OpenAI /
  本地 Whisper；pcm_to_wav 内存转码不落盘，httpx trust_env=False 对齐润色）；
  `build_asr_provider` 工厂（volcengine_sauc 默认 / openai_compat，
  wss 端点忘改时回落硅基流动防呆）；TranscriptionConfig 增 model 字段。
- [x] **润色多厂商预设** ✅：OpenAI 兼容协议本就通用，补上 UI 预设下拉
  （DeepSeek/Kimi/智谱/通义/火山方舟/OpenAI/自定义，选厂自动带出端点+默认模型可手改，
  老配置按 base_url 反查回显）。
- [x] **高级设置卡** ✅（默认收起）：仪表盘端口、采样率、火山端点/资源 ID
  （值本就可配，清理「死配置」感知）。
- [x] **仪表盘美化** ✅：深/浅/护眼三主题（CSS 语义变量 + localStorage 持久化 +
  data-theme 切换）、卡片/输入框/按钮全语义化配色、卡片图标、Logo 渐变、
  保存栏悬浮；科技风对齐官网观感，Tailwind 仅保留布局职责。
- [x] 凭证卡扩容 ✅：转写 API Key（豆包新版/OpenAI 兼容通用）+ 润色 Key
  （按所选厂商）+ 旧版双头（选填），全部 UI 化。
- [x] 测试 ✅ 212 passed（新增 OpenAI 兼容客户端 6 项 + provider 工厂 4 项）。

> V4 增值方向（内核落地后启用）：记忆系统 + 项目管理接入，按项目隔离转写历史。

## 运维纪律（事故存档）

- **2026-08-26 20:25 FUSE 僵死事故**：重部署时 pkill 强杀运行中的 AppImage 实例，
  FUSE 挂载进入 D 状态（不可中断），`ps`/`/proc` 遍历/新进程启动链全部被挂死，
  症状即「系统快捷键触发失灵但运行中实例正常」。D 状态用户态不可恢复，只能重启。
  **纪律**：①重部署前必须让实例优雅退出（托盘「退出」= DBus Event id 90，
  含 tray.stop/storage.close/挂载自动卸载）；②绝不在录音进行中杀进程；
  ③清理命令必须用防自匹配写法（本会话三次 pkill 自杀事故的教训合集）。

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
