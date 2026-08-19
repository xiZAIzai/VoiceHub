# VoiceHub 项目规划

> 最后更新：2026-08-19（M0 奠基；ADR-1/2/3/4 已定案，ESP32 挂起）
> 当前焦点：M1 环境与 API 校验

## 产品目标

**单点输入，多端分发**：一台麦克风收声，经「闪电说」（ASR + LLM 润色）写入剪贴板，VoiceHub 守护进程按快捷键
将文本路由注入到台式机 / 笔记本 / 平板的光标位置。全量转写文本持久化入 SQLite，为后续向量化与个人 Agent
记忆中枢预留接口。

完整需求规格见 [docs/PRD.md](./docs/PRD.md)。

## 当前状态

- 全新项目（空仓库），尚无代码。
- M0 记录结构已奠基：CLAUDE.md（工作规范）、PLAN.md（本文件）、TASKS.md（任务清单）、docs/PRD.md（需求文档）、.gitignore。

当前主要风险：

- ~~闪电说与 VoiceHub 共享 Alt 键的热键耦合方案~~ → 已定案：粘滞目标（零侵入），见 ADR-1。
- ~~「转录完成」的剪贴板判定语义~~ → 已定案：事件驱动 + 基线/去抖/过期，见 ADR-4。
- 目标端（笔记本 / 平板 / ESP32）为多平台运维，联调成本高，建议每阶段单独冒烟。
- 闪电说触发键是 Alt：需实测 Alt+1/2/3/4 组合按键不会误触闪电说录音（M1 验证）。
- 粘滞目标方案依赖"武装期间剪贴板变化即转录"的假设，手动复制会被误判（ADR-4 已知边界）。

## 当前里程碑

### ✅ Milestone 0：记录结构与规划奠基（2026-08-19）

已建立 CLAUDE.md / PLAN.md / TASKS.md / docs/PRD.md / .gitignore。

### ⬜ Milestone 1：环境与 API 校验（Phase 1）

- 闪电说完成模型配置（SenseVoice/Whisper + DeepSeek），验证 Alt tap-toggle 转录可用性。
- 按 ADR-2 配置路由器 DHCP 保留，固定各端局域网 IP（并确认 WiFi 非访客网络）。
- 热键验证：Alt+1/2/3/4 不透传前台应用（浏览器不切标签）、且不误触闪电说录音。
- 剪贴板验证：WM_CLIPBOARDUPDATE 事件在闪电说写入时可靠触发（ADR-4 前提）。

### ⬜ Milestone 2：目标端接收服务部署（Phase 2）

- 笔记本端 `laptop_receiver.py`，测试局域网 POST 请求自动打字。
- 平板 Termux 部署 `tablet_server.py`，Root 粘贴与后台保活。

### ⬜ Milestone 3：台式机主控服务实现（Phase 3）

- 全局热键组合监听（Alt+1/2/3/4 目标选择，粘滞语义见 ADR-1）。
- 剪贴板监听与文本提取管道（事件驱动，见 ADR-4）。
- 集成 SQLite 数据库读写引擎（transcript_logs）。

### ⬜ Milestone 4：Web 仪表盘与 WebSocket 联调（Phase 4）

- FastAPI Web 服务与 WebSocket 广播通道。
- 前端 Dashboard（纯 Web 页面，见 ADR-3），打通实时状态同步与历史日志查看。

### ⬜ Milestone 5：体验调优与开机自启（Phase 5）

- 优化剪贴板读取防抖与延迟控制。
- 配置台式机服务为 Windows 后台服务或托盘自启应用。

## 近阶段工作重点

1. ~~M0 记录结构奠基~~ ✅ 2026-08-19。
2. M1 环境与 API 校验：闪电说模型配置 + 设备网络规划。
3. M2 目标端接收服务：先打通笔记本 → 平板，再回主控。

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

## 关键 ADR 索引

- **ADR-1 热键耦合：粘滞目标（零侵入，2026-08-19）**
  闪电说保留原生 Alt tap-toggle（点一下开始、再点一下结束、写剪贴板），VoiceHub 不改动它；
  VoiceHub 只注册 `Alt+1/2/3/4` 作为**目标选择键**：按下即设置粘滞目标（带超时），随后任意一次
  闪电说转写产生的剪贴板新文本被拦截后，路由到该目标并清除粘滞。组合键保留 PRD 原案 `Alt+1/2/3/4`，
  接受浏览器 Alt+数字切标签冲突，通过低层 hook `suppress` 不透传来缓解；需在 M1 实测
  `Alt+N` 组合不会误触闪电说录音（因 Alt 是闪电说触发键）。

- **ADR-2 局域网网络方案：DHCP 保留 + 固定 IP 端点（2026-08-19）**
  所有设备同连一个家庭 WiFi，接收端是固定常驻 HTTP 服务，台式机为主动方（outbound POST），无自动发现需求，
  故不做 mDNS（Termux/Android 支持差、Windows 需 Bonjour，无收益）。方案：
  1. 设备地址用**路由器 DHCP 保留**（按 MAC 绑定固定 IP），不在设备上手动配静态 IP（换网不丢配置、改地址只动路由器）。
  2. `config.json` 直接写 IP 端点（维持 PRD `targets[].endpoint` 结构）。
  3. 接收端服务绑定 `0.0.0.0:5050`；台式机 Windows 防火墙放行对应入站端口。
  4. 台式机仪表盘 Web 默认绑 `127.0.0.1:8000`（仅本机访问，安全）；如需平板远程查看再改 `0.0.0.0`。
  前提/坑：**确认 WiFi 不是访客网络、未开 AP 隔离**，否则同网也互不可达。

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

## 参考

- 日常执行请结合 [TASKS.md](./TASKS.md) 与 [CLAUDE.md](./CLAUDE.md)
- 本项目的规划结构沿用 Echo-EG-Learning 仓库的约定（PLAN.md / TASKS.md / CLAUDE.md / docs 分层）
