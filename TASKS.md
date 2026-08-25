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

## V4：自建转写内核·云端 ASR（2026-08-25 立项；M11 代码+实机冒烟完成 2026-08-26）

> 原「远期占位」升格立项（用户决策）。催化：闪电说 Linux 版在 openKylin UI 渲染 0 帧
> 不可用 + glibc 基线过高（调查见 V3 章节），openKylin 语音链路单点依赖被打破。
> **定案（ADR-9）**：ASR 统一走云（本地不做）；文本直通 orchestrator 不经剪贴板
> （ADR-4/ADR-5 痛点天然免疫）；双引擎开关与闪电说共存；触发键独立；密钥不进种子 config。
> 方向全景见 PLAN.md「V4」段；以下为可勾选执行明细。

- [ ] **M11：最小闭环竖切**（录音 → 云 ASR → 直通路由，目标 2–4 个工作日）
  - [x] ① provider spike（2026-08-25 完成，协议/端点/鉴权全部查清并实测）：
    - **火山 ASR 不走 Ark OpenAI 兼容端点**（`/api/v3/audio/transcriptions` 不存在），
      正确路径 = 豆包语音 openspeech **WebSocket v3 二进制协议**：
      `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream`（录完统一返回，
      准确率优先，5s 音频 300–400ms 返回，**M11 首选**）/ `bigmodel_async`（双流优化）
      / `bigmodel`（双向流式）。
    - 鉴权（官方文档 docs/6561/1354869 + 1816214）：新版控制台只需
      `X-Api-Key: <API Key>`（不填 appid）；旧版 = `X-Api-App-Key`(数字 APP ID) +
      `X-Api-Access-Key`。`X-Api-Resource-Id: volc.seedasr.sauc.duration`
      （豆包流式 2.0；1.0 是 volc.bigasr.sauc.*）。Ark Agent Plan 另有
      `/api/v3/plan/sauc/*` 镜像端点（需专属 API Key）。
    - 二进制帧协议已按文档实现并跑通到鉴权层（4B header + gzip JSON / raw PCM，
      末包负包 flags=0b0010），探针实现可移植为 asr_client 骨架。
    - **堵点**：用户现有 key（158 位点分 token，protobuf 内嵌 Key ID，格式为方舟系）
      在 Ark 推理端点与全部语音网关均被拒（Invalid X-Api-Key / 401）→ key 当前无效
      （禁用/轮换/或为闪电说中转 key）。**待用户在控制台核对或新建**：豆包语音新版
      控制台 API Key 页（console.volcengine.com/speech/new/setting/apikeys，新用户含
      免费试用礼包）拿一把新 key 换入 config.local.json 即可，其余全部就绪。
    - 模型串 `Doubao_Seed_ASR_Streaming_2.0…` 非调用参数（resource id 才是）。
    - DeepSeek 润色已实测可用（deepseek-v4-flash；httpx 需 trust_env=False 绕 socks 代理）。
  - [x] ② config 扩展 `transcription` 段 ✅ 2026-08-26：TranscriptionConfig
    （engine/provider/base_url/resource_id/api_key/language/trigger_key/default_target/
    sample_rate/VAD 四参数）；Config.load 深度合并 config.local.json + 环境变量
    `VOICEHUB_ASR_API_KEY` 最高优先；种子 config 已含无密钥模板段。
  - [x] ③ `voicehub/dictation/` 包 ✅ 2026-08-26：vad.py（纯逻辑，静音/未开口/
    最长三停条件）+ recorder.py（sounddevice + **arecord 子进程回退**——openKylin 无
    PortAudio 无 sudo 实测可用，亦免 AppImage 收编 PortAudio）+ engine.py（状态机
    idle→recording→processing，会话令牌防旧回调污染，no_speech 跳过 ASR 省调用）。
  - [x] ④ `asr_client.py` ✅ 2026-08-26：火山豆包 WS v3 二进制协议（帧编解码纯函数，
    自 spike 移植）；连接可注入（假 WS 单测）；瞬时失败重试 1 次，鉴权错不重试；
    AsrProvider 协议留多厂商适配位。修复探针同源的 read() 缓冲切片 bug（整条消息
    会被当 n 字节返回，多帧消息尾部丢失——单测抓住）。
  - [x] ⑤ orchestrator `route_direct(text)` ✅ 2026-08-26：粘滞目标优先 →
    default_target → 首个 local；local 投递 = 写系统剪贴板（Router.deliver_local +
    xclip/ctypes 写入器，xclip fork 守护进程坑见 ADR-9 附录）；落库 source=builtin。
  - [x] ⑥ 触发双通道 ✅ 2026-08-26：托盘菜单「开始听写/停止听写」（动态标签 +
    LayoutUpdated 广播）+ pynput **Ctrl+Alt+V**（定案，右 Alt 弃用——与闪电说物理同键
    会互抢；实测 xdotool 合成键可触发）；Windows 侧热键+托盘菜单同步接线。
  - [x] ⑦ 测试与冒烟 ✅ 2026-08-26：单测 110→154 全绿（dictation 23 + 接线 20 新增）；
    openKylin 实机冒烟：麦克风采集 ✓（环境底噪 RMS 0.0002）/ VAD 4s 自动停 ✓ /
    引擎全循环 ✓（ASR 到服务端鉴权层，key 无效止于 401 错误路径，无崩溃）/ 直通路由
    →剪贴板+DB 落库(10ms) ✓ / 托盘 DBus 点击触发录音 ✓ / 热键触发 ✓。
  - [ ] ⑧ openKylin 实机验收（**待有效 API key**）：不装闪电说，说一句话 → 识别文本
    → 剪贴板/远端路由 → 仪表盘可见记录。换 key 后一条命令复验：
    `python scripts/spike/volc_asr_v3_probe.py` 输出「✅ 鉴权+协议全链路通」即可人工验收。
- [ ] **M12：体验完善**（润色 / 状态可视化 / Wayland 稳触发）
  - [ ] LLM 润色可选开关（OpenAI 兼容 chat/completions，prompt 可配，原文/润色双落库）。
  - [ ] 录音状态可视化：托盘 SNI NeedsAttention / tooltip + 仪表盘实时状态。
  - [ ] UKUI 系统快捷键 CLI：`--dictate` 子命令 + UKUI 设置指引（Wayland 最稳触发）。
  - [ ] VAD 参数可调（静音阈值 / 最长时长 / 最短时长）。
- [ ] **M13：工程化收尾 + v0.4.0**
  - [ ] 打包：sounddevice/PortAudio 收编 AppImage；spec/CI 冒烟扩展（无麦克风 CI 跳过录音项）。
  - [ ] Windows 侧可用性验证（引擎跨平台：sounddevice + 云 API）。
  - [ ] README 双引擎章节（engine 开关 / 密钥配置 / 触发键说明）。
  - [ ] 发 v0.4.0 Release（Windows 双 zip + Linux 双 AppImage）。

> V4 增值方向（内核落地后启用）：记忆系统 + 项目管理接入，按项目隔离转写历史。

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
