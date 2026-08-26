# VoiceHub 项目规划

> 最后更新：2026-08-25 晚（**V4 立项：自建云端转写内核**，ASR 统一走云（ADR-9），M11 待开工；
> 此前 openKylin 随用随修第一轮完成并已 push：SNI 托盘 / 热键打包修复 / 端口避让，见 ADR-8）
> 当前焦点：**V4/M11 最小闭环竖切**（录音 → 云 ASR → 直通路由）；openKylin 随用随修继续；
> V2 用户侧验证随用随验；V1 尾巴：平板部署

## 产品目标

**单点输入，多端分发**：一台麦克风收声，经「闪电说」（ASR + LLM 润色）写入剪贴板，VoiceHub 守护进程按快捷键
将文本路由注入到台式机 / 笔记本 / 平板的光标位置。全量转写文本持久化入 SQLite，为后续向量化与个人 Agent
记忆中枢预留接口。

完整需求规格见 [docs/PRD.md](./docs/PRD.md)。

## 当前状态

- **V1 主体闭环（2026-08-20）**：全链路真机验证通过——台式机（Windows）Alt+2 → 闪电说转写 →
  剪贴板拦截 → UDP 发现笔记本 → HTTP 推送 → 笔记本自动粘贴；67 单测全绿。
- 架构分层落地：入口 / 编排 / 状态机 / 剪贴板 / 发现 / 路由 / 存储 / 接收端 / 仪表盘各司其职。
- V1 剩余：平板 Termux 部署；闪电说模型配置（用户自办）；Alt 不透传验证（随用随验）。
- **V2 已完成并归档（M6，2026-08-20）**：双击即用 exe（daemon/receiver）+ 配置界面 +
  pywebview 原生窗口 + 开机自启 + 开源工程化（MIT/CI/Release）；94 单测全绿，
  真机修复白屏/僵尸进程/关窗语义三事故。详见 [docs/tasks-archive/v2-completed.md](./docs/tasks-archive/v2-completed.md)。
- **V3 已立项（2026-08-20）**：Linux 双端适配——openKylin SP2 主控（与 Windows 双系统分时共存，
  同一物理机无双主控冲突）+ Linux 接收端体验。契机：闪电说 Linux 版（AppImage universal，内测
  v0.7.5）使转写源跨平台，零侵入架构（ADR-1/4/5）原样延续；自建转写管道转为挂起的远期方向
  （见 ADR-7 占位）。里程碑 M7–M10，见「当前里程碑」。
- **V3 先行项完成（2026-08-20，开发基准 Ubuntu 22.04 / WSL2）**：CI 加 linux job；
  `linux_backend.py`（xclip 读取 + 武装期轮询监听 + pynput 热键降级）落地，测试 94→104，
  Windows/WSL 双平台全绿；WSL 端到端冒烟全绿（热键武装→剪贴板拦截→路由→接收端粘贴→落库）。
  可复用工具：`scripts/spike_linux_stack.sh`、`scripts/smoke_linux_e2e.sh`。
- **V3 验证策略定案（2026-08-20，用户决策）**：**取消 openKylin 真机前置验证**——依据历史经验
  （Ubuntu 22.04 构建的 AppImage 在 openKylin 均可正常运行；AppImage 自包含，glibc 向后兼容），
  以 AppImage 产物交付，openKylin 差异随用随修。**WSL2 为唯一开发/验证环境，直至 AppImage 打包
  完成**；闪电说 Linux 版行为四问转为 WSLg 内选做，不阻塞主线。ADR-7 就此定案。
- **openKylin 实机随用随修第一轮（2026-08-25）**：v0.3.0 AppImage（openKylin SP2，Wayland 会话）
  实测定位三问题并全部修复——① 托盘缺失系 M8 降级决策（非故障），补齐 SNI 直写托盘（ADR-8）；
  ② 仪表盘 8000 端口被系统自带 kytensor（Triton，AI 推理）占用导致 bind 失败，默认端口改 8765；
  ③ Release AppImage 缺 `pynput.keyboard._xorg` 热键全灭——pynput≥1.8 import 期即连 X server，
  headless CI 上 `collect_submodules("pynput")` 静默返回空表，spec 改显式 hiddenimports + CI 加
  xvfb 冒烟断言。本机重打包 + 冒烟 + 托盘 DBus 实测全绿（110 单测）。
- **V4 立项（2026-08-25 晚，用户决策）**：自建转写内核由远期占位**升格为正式里程碑**——催化事件：
  闪电说 Linux 版（Tauri/GTK）在 openKylin（kylin-wlcom 合成器）UI 渲染 0 帧不可用且 glibc 2.39
  基线过高（调查记录见 TASKS.md V3 章节），openKylin 语音链路单点依赖被打破。**ASR 统一走云**
  （本地模型明确不做），选型与架构见 ADR-9，里程碑 M11–M13 见「当前里程碑」。

当前主要风险：

- ~~闪电说与 VoiceHub 共享 Alt 键的热键耦合方案~~ → 已定案：粘滞目标（零侵入），见 ADR-1。
- ~~「转录完成」的剪贴板判定语义~~ → 已定案：事件驱动 + 基线/去抖/过期，见 ADR-4。
- 目标端（笔记本 / 平板 / ESP32）为多平台运维，联调成本高，建议每阶段单独冒烟。
- 闪电说触发键是 Alt：真机实测**确认会误触**（按 Alt+N 即开始录音，2026-08-20）——因祸得福省了一步
  （按下即录）。定案（2026-08-20）：**暂不换键**，接受现状（曾考虑 Shift+N，与 @#$%^ 符号输入冲突，弃）。
- 粘滞目标方案依赖"武装期间剪贴板变化即转录"的假设，手动复制会被误判（ADR-4 已知边界）。

## 当前里程碑

### ✅ Milestone 0：记录结构与规划奠基（2026-08-19）

已建立 CLAUDE.md / PLAN.md / TASKS.md / docs/PRD.md / .gitignore。

### ✅ Milestone 1：环境与 API 校验（2026-08-20 基本完成）

- Windows 原生环境搭建（Python 3.11 venv + 依赖 + 单测全绿 + 冒烟）。
- ADR-2 前提真机验证：局域网多端互通、UDP 报到可被台式机接收。
- 热键实测：Alt+N **会**误触闪电说录音 → 定案接受"按下即录"，暂不换键。
- WM_CLIPBOARDUPDATE 随全链路验证可靠触发。
- 转出：闪电说模型配置由用户自行处理；Alt 不透传随用随验。

### 🔶 Milestone 2：目标端接收服务部署（笔记本 ✅ 2026-08-20，平板待部署）

- 笔记本端 `receiver.py` 真机验证通过（心跳发现 + HTTP 收文 + 自动粘贴；联调修复 FastAPI 注解 422）。
- 平板 Termux `tablet_server.py` 已实现，待 Root 粘贴与后台保活实测。

### ✅ Milestone 3：台式机主控服务实现（2026-08-20 真机验证通过）

- 全局热键组合监听（Alt+1/2/3/4 目标选择，粘滞语义见 ADR-1）。
- 剪贴板监听与文本提取管道（事件驱动，见 ADR-4）。
- 设备发现（UDP 报到 + 子网扫描兜底，见 ADR-2）。
- 集成 SQLite 数据库读写引擎（transcript_logs）。
- 真机联调修复两项：接收端 FastAPI 注解 422、主入口 Router 漏注入 transport。

### ✅ Milestone 4：Web 仪表盘与 WebSocket 联调（2026-08-20 验证通过）

- FastAPI Web 服务与 WebSocket 广播通道。
- 前端 Dashboard（纯 Web 页面，见 ADR-3），打通实时状态同步与历史日志查看。
- 真机验证：仪表盘不刷新自动更新（WS 实时推送正常）。

### ✅ Milestone 5：体验调优与开机自启（2026-08-20 调优完成；自启并入 V2/M6）

- 剪贴板防抖与延迟调优：`stability_ms` 600→300。
- 长听写支持：`pending_timeout_sec` 30→300（>30s 听写曾被粘滞过期整体丢弃）。
- 托盘/自启代码已就绪，自启实测并入 V2 打包任务一并做。

### ✅ Milestone 6（V2）：桌面化打包与开源分发（2026-08-20 六项完成并归档，见 ADR-6）

> 任务明细与三次真机事故修复记录：[docs/tasks-archive/v2-completed.md](./docs/tasks-archive/v2-completed.md)。
> 用户侧实测收尾项见 TASKS.md「V2 收尾」。

- ~~PyInstaller 打包台式机 daemon 为 exe（托盘 + 图标 + 自动开窗，双击即用）~~ ✅ 2026-08-20
  （one-dir 产物 + frozen 路径锚定 + conda DLL 收编，冒烟通过；详见 TASKS.md M6-① 记录）。
- ~~接收端 receiver 打包（笔记本双击即用；平板维持 Termux 脚本）~~ ✅ 2026-08-20
  （console 版 exe，HTTP/心跳冒烟通过；剪贴板写入受助手会话 UI 限制，转用户侧验证，
  详见 TASKS.md M6-② 记录）。
- ~~配置界面：config 读写 API + 仪表盘"设置"页~~ ✅ 2026-08-20
  （ConfigService 校验/原子写回/热应用；打包 exe 内端到端冒烟通过）。
- ~~pywebview 原生窗口包裹仪表盘（关窗不退程序，退出走托盘）~~ ✅ 2026-08-20
  （WindowController 关窗否决/退出放行；pywebview 缺失自动回退托盘+浏览器；
  打包 exe 内启动冒烟通过，窗口交互转用户侧验证）。
- ~~托盘开机自启实测~~ ✅ 2026-08-20（frozen 命令修复 + 托盘「开机自启」勾选项 +
  注册表真机回环验证；重启实测转用户侧）。
- ~~开源工程化：README、License、GitHub Actions 自动构建~~ ✅ 2026-08-20
  （MIT + CI 构建/发布工作流 + README 打包版说明；Actions 首跑与首发 tag 转用户侧）。

### ✅ Milestone 7（V3）：Linux 预研与选型（2026-08-20 定案）

> ~~真机：openKylin SP2~~ 验证策略变更（2026-08-20 用户决策）：取消 openKylin 真机前置验证，
> WSL2 为唯一验证环境（理由见「当前状态」），选型据此定案。

- ~~输入栈 spike（WSL 可先行）~~ ✅ 2026-08-20（xclip UTF-8 读写 / xdotool 注入 / pynput 热键
  均真跑验证；`scripts/spike_linux_stack.sh`）。
- ~~openKylin 真机三确认 + 闪电说四问~~ 取消（策略变更）；闪电说 AppImage 行为四问转为
  WSLg 内**选做**（不阻塞主线，遇到问题随用随修）。
- ~~产出 ADR-7~~ ✅ 定案（见 ADR 索引）。

### 🔶 Milestone 8（V3）：Linux 主控后端

- ~~`linux_backend.py`（对位 win_backend）~~ ✅ 2026-08-20（实现 + 双平台 104 测试全绿 +
  WSL 端到端冒烟全绿，见 TASKS.md 先行项记录）。
- ~~全链路真机联调~~ 改为 WSL 内完成 ✅ 2026-08-20（原 openKylin 真机项随策略变更取消）。
- AppImage 形态验证（随 M10 打包一并做）：AppImage 版 daemon 在 WSL 复跑冒烟脚本。
- 双系统数据连续性（可选）：config/db 放共享分区（NTFS/exFAT）方案与文档。

### ⏳ Milestone 9（V3）：接收端 Linux 体验完善

- 粘贴后端补齐（X11 优先；Wayland wl-copy + ydotool 按需）。
- systemd user service + 一键安装脚本（依赖检查 + 服务装卸 + 自启）。
- README「Linux 接收端」章节。

### ✅ Milestone 10（V3）：工程化收尾与 v0.3.0（2026-08-25 完成）

- ~~daemon / receiver 打包为 AppImage~~ ✅ 2026-08-20（`packaging/build_linux.sh` 一键链：
  PyInstaller one-dir → AppRun/.desktop/PNG → appimagetool；AppRun 播种
  config + 依赖检测，`paths.py` 新增 `VOICEHUB_HOME` 便携目录锚定；AppImage 冒烟
  `scripts/smoke_appimage.sh` 全绿，见 TASKS.md M10 记录）。
- ~~CI ubuntu job 扩展打包并上传产物~~ ✅ 2026-08-20（2026-08-25 push 后 CI 首跑通过，
  linux job 含打包全绿）。
- ~~发 v0.3.0 Release~~ ✅ 2026-08-25（四产物：Windows 双 zip + Linux 双 AppImage，
  main 与 tag 流水线全绿）。

### 🔶 V4：自建转写内核·云端 ASR（2026-08-25 立项；M11 代码完成 2026-08-26，待有效 key 全链验收）

> 原「远期占位」升格立项。选型定案见 ADR-9（ASR 统一云端 / 直通注入不经剪贴板 /
> 双引擎开关与闪电说共存 / 触发键独立）。任务明细与勾选进度在 TASKS.md「V4」章节。

#### Milestone 11：最小闭环竖切（录音 → 云 ASR → 直通路由）——代码与实机冒烟完成 ✅ 2026-08-26

- ~~provider spike~~ ✅（结论见 TASKS.md M11-①：火山豆包 = openspeech WS v3 二进制协议，
  非 OpenAI 兼容端点；默认 `sauc/bigmodel_nostream` + `volc.seedasr.sauc.duration`；
  新版控制台仅需 `X-Api-Key` 单头鉴权。DeepSeek 润色实测可用）。
- ~~config `transcription` 段~~ ✅（engine/provider/base_url/resource_id/api_key/language/
  trigger_key/default_target/VAD 参数；config.local.json 深度合并 + 环境变量
  `VOICEHUB_ASR_API_KEY` 最高优先；种子 config 永不含密钥）。
- ~~recorder + asr_client + engine~~ ✅（`voicehub/dictation/` 包：VAD 纯逻辑 /
  sounddevice 录音 **+ arecord 子进程回退**（openKylin 无 PortAudio 且无 sudo，实测可用）/
  WS v3 帧编解码纯函数 + 可注入连接 / 状态机 idle→recording→processing 带会话令牌）。
- ~~route_direct 直通路径~~ ✅（粘滞目标优先 → default_target → 首个 local；
  local 投递 = 写系统剪贴板（xclip/ctypes），落库 metadata source=builtin）。
- ~~触发双通道~~ ✅（托盘菜单「开始听写/停止听写」动态标签 + pynput Ctrl+Alt+V；
  实测两通道均可触发完整录音循环，Wayland 下 X11 热键仍受焦点限制，托盘为主通道）。
- ~~测试~~ ✅ 154 全绿（VAD/帧协议/引擎状态机/接线 43 项新增）；openKylin 实机冒烟：
  麦克风采集 ✓ VAD 自动停 ✓ 引擎全循环 ✓（ASR 到达服务端鉴权层，因 key 无效止于
  401 错误路径）直通路由→剪贴板+DB ✓ 托盘点击触发 ✓ 热键触发 ✓。
- ⏳ 遗留：**有效 API key 后的真语音全链验收**（说一句话 → 识别文本 → 剪贴板/远端路由），
  及仪表盘记录可见性人工确认。

#### Milestone 12：体验完善（润色 / 状态可视化 / Wayland 稳触发）

- LLM 润色可选开关（OpenAI 兼容 chat/completions，prompt 可配，原文/润色双落库）。
- 录音状态可视化：托盘 SNI NeedsAttention / tooltip + 仪表盘实时状态（WS 通道已有）。
- UKUI 系统快捷键 CLI（`--dictate` 子命令 + UKUI 设置指引）：Wayland 下最稳触发方式。
- VAD 参数可调（静音阈值 / 最长时长 / 最短时长）。

#### Milestone 13：工程化收尾 + v0.4.0

- 打包：sounddevice/PortAudio 收编进 AppImage；spec/CI 冒烟扩展（无麦克风的 CI 跳过录音项）。
- Windows 侧可用性验证（引擎跨平台：sounddevice + 云 API，天然双平台）。
- README 双引擎说明（engine 开关 / 密钥配置 / 触发键配置）；发 v0.4.0 Release。

#### V4 增值方向（内核落地后启用）

- 接入记忆系统与项目管理系统，按项目隔离管理对话/转写历史（原占位登记内容）。

## 近阶段工作重点

1. **V4/M11 最小闭环竖切（当前最高优先）**：provider spike → 录音器/ASR 客户端 →
   直通路由 → 双触发 → openKylin 实机验收（不装闪电说跑通一次语音）。明细见 TASKS.md V4。
2. ~~V3 先行项 / M7 选型 / M8 Linux 主控 / M10 AppImage + v0.3.0~~ ✅ 2026-08-20~25
   （全链详见 TASKS.md V3 章节记录；随用随修第一轮修复已 push）。
3. openKylin 侧实际使用：VoiceHub 侧已通（托盘/热键/端口）；闪电说侧等官方修复
   kylin-wlcom 渲染问题（glibc 侧载方案已就位，TASKS.md 有调查记录与反馈材料）。
4. M9 接收端体验随用随做（systemd/自启脚本可选，AppImage 已是主交付形态）。
5. V2 收尾用户侧验证随用随验（窗口三步 / 笔记本真链路 / 自启重启）；V1 尾巴按需再启。

## 架构约束（精简版）

- 单向数据流：热键事件 → 编排层更新状态 → WebSocket 广播 → 前端被动渲染。
- 前端只展示，复杂流程下沉到可测试的纯逻辑编排层。
- 副作用通过 service / repository 注入，不把网络、文件、系统调用散落到 UI。
- 异步流程必须带 session / token guard，防止旧回调污染新状态。
- 新增能力优先补状态流转测试与关键回归测试。
- 单点真相源：核心状态机统一持有，剪贴板 / 热键 / WS 均只读不重复维护。

## 待决策设计点（各里程碑启动前定案，记入 ADR）

1. ~~热键耦合方案~~ ✅ 已定案（2026-08-19）：粘滞目标（零侵入），见 ADR-1。
2. ~~转录完成判定~~ ✅ 已定案（2026-08-19）：事件驱动 + 基线/去抖/过期，见 ADR-4。
3. ~~设备发现~~ ✅ 已定案（2026-08-19）：DHCP 保留 + 固定 IP 端点，见 ADR-2。
4. ~~ESP32 兜底通道~~ ⏸ 挂起（M5 或按需再议）：BLE HID 固件选型与串口协议，不影响第一版。
5. ~~闪电说自动粘贴 vs 远程路由撞车~~ ✅ 已定案（2026-08-19）：保留原生 + 接受远程本地重复，见 ADR-5。
6. ~~Linux 输入栈选型~~ ⏳ 待 M7 真机预研定案（热键 / 剪贴板监听 / 粘贴注入），记入 ADR-7。

## 关键 ADR 索引

- **ADR-1 热键耦合：粘滞目标（零侵入，2026-08-19）**
  闪电说保留原生 Alt tap-toggle（点一下开始、再点一下结束、写剪贴板），VoiceHub 不改动它；
  VoiceHub 只注册 `Alt+1/2/3/4` 作为**目标选择键**：按下即设置粘滞目标（带超时），随后任意一次
  闪电说转写产生的剪贴板新文本被拦截后，路由到该目标并清除粘滞。组合键保留 PRD 原案 `Alt+1/2/3/4`，
  接受浏览器 Alt+数字切标签冲突，通过低层 hook `suppress` 不透传来缓解；需在 M1 实测
  `Alt+N` 组合不会误触闪电说录音（因 Alt 是闪电说触发键）。

- **ADR-5 闪电说自动粘贴：保留原生，远程接受台式机重复（2026-08-19）**
  闪电说**无「自动粘贴」开关**（仅热键切换），转写后必然自动贴到台式机当前焦点 → "VoiceHub 统一注入"
  方案的前提（可关自动粘贴）不存在，故**保持方案 B（零侵入）不变**。行为定案：
  1. 本地（台式机）：闪电说原生自动贴，VoiceHub 只记录不注入（Alt+1 台式机目标为冗余/仅日志用途，
     避免二次粘贴）。
  2. 目标端（笔记本/平板）：接收端本就是"写入目标设备剪贴板 + 模拟粘贴到当前焦点窗口"，体验与闪电说本地一致。
  3. **v1 已知限制**：发远程时闪电说会在台式机当前焦点也贴一份，接受。
     后续可选缓解：远程路由后 VoiceHub 补发 Ctrl+Z 撤销（应用差异大、不可靠，暂缓）；
     或"路由中立窗口"先切走台式机焦点（多一步操作，不太顺手）。

- **ADR-2 网络与设备发现：UDP 心跳 + 子网扫描兜底（2026-08-19，修订）**
  原案（DHCP 保留 + 固定 IP）在"无固定 WiFi、常换手机热点"场景下不成立（手机热点无管理后台、
  各手机网段不一，如安卓常 `192.168.43.x`、iPhone 常 `172.20.10.x`）。修订为**自动发现**：
  1. 接收端（laptop/tablet）每 3~5s 向子网广播 UDP 报到包：`{"svc":"voicehub","name":"<设备名>","port":5050}`。
  2. 台式机 daemon 监听报到 → 维护**在线设备表**（name → 当前 ip:port + last_seen），超时未报到标记离线。
  3. 兜底：台式机周期性扫描当前子网（按自身 IP 推导网段，试 5050 端口），发现未广播的设备。
  4. 路由时查在线表取当前 IP；查不到 → 路由失败并记录。
  5. 固定网络仍可手动在 config 写死 `endpoint` 覆盖发现结果（可选）。
  前提/验证（M1）：所有目标设备须在同一热点（同网段）；热点须允许客户端互访
  （多数手机默认允许，个别需开开关）。仪表盘 Web 默认绑 `127.0.0.1:8000`（仅本机，安全）。

- **ADR-3 仪表盘前端形态：纯 Web 页面（2026-08-19）**
  仪表盘是监控视图，非交互主界面（主交互是热键）。第一版用纯 Web 页面：FastAPI 直接托管单文件 HTML
  （Tailwind + Vue 走 CDN，无构建链），WebSocket 广播状态，浏览器打开即用。守护进程负责托盘图标 +
  启动自动打开 `localhost:8000` 提供"软件感"。不做 Tauri（Rust 工具链 + 双进程，对个人工具过重）；
  如需原生窗口，后续用 pywebview 包裹同一份 HTML 升级，前端本身不变。此决策不改变守护进程架构
  （前端始终是 daemon 的薄客户端）。

- **ADR-4 转录完成判定：事件驱动 + 基线/去抖/过期（2026-08-19）**
  剪贴板监听用 `WM_CLIPBOARDUPDATE`（`AddClipboardFormatListener`）事件驱动，不忙轮询。按 Alt+N
  武装（设粘滞目标）时快照当前剪贴板文本作基线；收到变化事件 → 读文本与基线对比，不同则作为候选，
  等 `stability_ms`（默认 600ms）稳定后路由并清除粘滞；武装后 `pending_timeout_sec`（默认 30s）
  内无新文本则清除粘滞（作废）。`latency_ms` 定义 = 检测到新文本 → 分发完成；Alt+N 按下到路由的
  总时长另存 metadata（闪电说停止时刻不可测，不硬算）。已知边界：武装期间用户手动复制会被误判为
  转录，第一版接受并记 metadata，后续可升级"转写完成签名"判断。config 字段变更：
  PRD §6 的 `shandianshuo.polling_timeout_sec`（6s 当总超时不成立）改为 `pending_timeout_sec`
  （默认 30）+ 新增 `stability_ms`（默认 600）。

- **ADR-6 V2 桌面化路线：PyInstaller + pywebview，不做独立前端工程（2026-08-20）**
  目标是"一键启动 + 可配置 + 可开源分发"，**不推翻现有结构**（daemon 依旧是无头核心、
  唯一状态源，界面是薄客户端）。路线：① PyInstaller 打包 daemon/receiver 为双击即用的 exe；
  ② 仪表盘内加"设置"页（FastAPI config 读写 API + Vue 表单）；③ pywebview 把同一份 HTML
  包进原生窗口（ADR-3 预留路径，关窗不退程序、退出走托盘）。明确**不做** Tauri/Electron
  独立前端工程：双工具链/双进程/贡献门槛高，对个人工具过重（ADR-3 的理由依然成立），
  等确有重度定制 UI 需求再议。

- **ADR-7 Linux 输入栈选型（2026-08-20 定案）**
  选型：剪贴板读写 = **xclip**（显式 UTF-8，防无 LANG 环境中文乱码）；变化监听 = **X11 轮询**
  （`X11ClipboardPoller`，仅武装期轮询，空闲零开销；武装沿登记基线防假事件）；粘贴注入 =
  **xdotool**；全局热键 = **pynput**（不可用时降级日志，仪表盘照常）。以上均已在 WSL2
  Ubuntu 22.04 真跑验证（spike + 端到端冒烟全绿；工具 `scripts/spike_linux_stack.sh` /
  `scripts/smoke_linux_e2e.sh`）。
  验证策略（用户决策 2026-08-20）：**取消 openKylin 真机前置验证**——依据历史经验（Ubuntu
  22.04 构建的 AppImage 在 openKylin 均可正常运行，AppImage 自包含 + glibc 向后兼容），
  以 AppImage 产物交付，openKylin 差异随用随修；WSL2 为唯一开发/验证环境。
  前提变化记录（2026-08-20，V3 立项）：闪电说 Linux 版（AppImage universal，内测 v0.7.5）出现后，
  V3 从"自建转写管道（换心脏）"回归为"零侵入移植"——ADR-1/4/5 原样延续。**自建转写管道
  转为挂起的远期方向**（保留价值：彻底消除 ADR-4 武装期误判与 ADR-5 本地重复贴；
  闪电说三平台齐备后必要性大降；2026-08-20 已登记为 V4 方向，见「V4（远期占位）」段）。
  实机补充（2026-08-25，openKylin SP2 = Wayland 会话）：X11 全局热键依赖 XWayland，焦点在
  XWayland 应用（含闪电说 Electron）时可用；焦点在原生 Wayland 应用时收不到（已知限制）。

- **ADR-8 Linux 托盘：SNI 直写，修订 M8「无托盘」降级（2026-08-25 定案）**
  M8 曾定案 Linux 无托盘（仪表盘走浏览器）。openKylin 实机使用暴露其体验问题（程序完全不可见），
  且该机为 **Wayland 会话（kylin-wlcom）**：pystray 的 xorg 后端（XEmbed 托盘）不可用、
  appindicator 后端需把 GTK/PyGObject 整套打进 AppImage（体积大、跨发行版脆）。实测 UKUI 面板
  运行 `org.kde.StatusNotifierWatcher`（SNI 宿主在线），故选 **jeepney（纯 Python DBus）直写
  StatusNotifierItem + DBusMenu 协议**（`linux_tray.py`）：菜单「打开仪表盘 / 退出」、左键单击
  开仪表盘、图标走 IconPixmap（assets 收编，Pillow 缺失时 PIL 兜底画）；无 watcher / 无会话
  总线 / 无 jeepney 均降级日志不阻塞。开机自启（.desktop）留作后续项。
  同轮关联修复：默认端口 8000→8765（避让 Triton/kytensor）；pynput 打包改显式
  hiddenimports（headless CI 上 collect_submodules 静默拿空表的事故，ADR-7 工具链层）。

- **ADR-9 自建转写内核：云端 ASR + 直通注入 + 双引擎开关（2026-08-25 定案；M11 附录 2026-08-26）**
  决策背景：闪电说 Linux 版（Tauri/GTK）在 openKylin（kylin-wlcom）UI 渲染 0 帧不可用、
  glibc 2.39 基线过高（调查见 TASKS.md V3），语音链路单点依赖被打破，V4 由远期占位升格立项。
  - **ASR 统一走云**（用户决策 2026-08-25；本地模型明确不做）：~~优先 OpenAI 兼容
    `/v1/audio/transcriptions` 接口体系~~ spike 证伪——火山豆包 ASR 实为 **openspeech
    WebSocket v3 二进制协议**（默认 `sauc/bigmodel_nostream`，resource id
    `volc.seedasr.sauc.duration`）。鉴权双方案（2026-08-26 实测定案）：旧版控制台
    app_key+access_key 双头（用户凭证实测可用）| 新版控制台 X-Api-Key 单头；
    豆包 2.0 仅开放 nostream/async 端点。详见 TASKS.md M11-①；AsrProvider 协议
    留多厂商适配位。
  - **直通注入**：内置引擎转写文本走 `orchestrator.route_direct()`，不经剪贴板——
    ADR-4 武装期误判、ADR-5 本地重复贴对内置引擎天然免疫；剪贴板链路原样保留给闪电说引擎。
    local 目标投递 = VoiceHub 写系统剪贴板（Wayland 焦点内直接 Ctrl+V；自动注入留 M12+）。
  - **双引擎共存**：config `transcription.engine: shandianshuo | builtin`，默认
    shandianshuo（产品成熟度更高），闪电说不可用平台（如 openKylin）切 builtin。
  - **触发独立（附录，2026-08-26 实测）**：定案 **Ctrl+Alt+V**（右 Alt 弃用——与闪电说
    触发键物理同键，双引擎并存时会互相抢触发）。pynput X11 热键在 Wayland 下仍受
    「XWayland 窗口聚焦」限制（xdotool 合成键实测可触发）；托盘菜单「开始听写/停止听写」
    为 Wayland 无关主通道（DBus 点击实测可触发完整循环）；M12 补 UKUI 系统快捷键 CLI。
  - **密钥管理**：API key 走环境变量 `VOICEHUB_ASR_API_KEY` 或 config.local.json
    （gitignore 已覆盖，与种子 config 深度合并，环境变量优先），种子 config 永不含密钥。
  - **实现附录（M11 实机）**：openKylin 无 PortAudio 且无 sudo → 录音器加 **arecord
    子进程回退**（ALSA 最底层公共依赖，亦免 AppImage 收编 PortAudio）；xclip 写剪贴板
    必须 DEVNULL 重定向（xclip fork 守护进程持管道，capture_output 会误报超时失败）。

## 参考

- 日常执行请结合 [TASKS.md](./TASKS.md) 与 [CLAUDE.md](./CLAUDE.md)
- 本项目的规划结构沿用 Echo-EG-Learning 仓库的约定（PLAN.md / TASKS.md / CLAUDE.md / docs 分层）
