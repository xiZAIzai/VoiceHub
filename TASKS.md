# VoiceHub 任务清单

> 最后更新：2026-08-19（第一版代码完成：v1 骨架 65 单测全绿；M2-M4 代码就绪，真机验证待做）
> 当前焦点：Windows 真机验证（M1 前置 + M2/M3 部署联调）

## v1 代码实现（✅ 2026-08-19，长城任务）

- [x] 项目脚手架与配置模块 `config.py` + `config.json`。
  **完成时间**: 2026-08-19
- [x] SQLite 存储引擎 `storage.py`（transcript_logs 表 + WAL）。
  **完成时间**: 2026-08-19
- [x] 设备发现 `discovery.py`（UDP 心跳 + 在线表 + 子网扫描兜底，ADR-2）。
  **完成时间**: 2026-08-19
- [x] 笔记本/桌面接收端 `receiver.py`（HTTP 收文 + 心跳广播 + 平台粘贴后端）。
  **完成时间**: 2026-08-19
- [x] 平板 Termux 接收端 `tablet_server.py`（纯标准库 HTTP + root 粘贴）。
  **完成时间**: 2026-08-19
- [x] 粘滞目标状态机 `state.py`（ADR-1）。
  **完成时间**: 2026-08-19
- [x] 剪贴板监控 `clipboard_monitor.py`（事件驱动 + 基线/去抖/过期，ADR-4）。
  **完成时间**: 2026-08-19
- [x] 全局热键注册表 `hotkey.py` + Windows 后端 `win_backend.py`（热键/剪贴板/托盘/自启）。
  **完成时间**: 2026-08-19
- [x] 路由编排 `router.py` + 传输层 `transport.py`（本地记录 / HTTP 推送）。
  **完成时间**: 2026-08-19
- [x] Web 仪表盘 `web.py`（FastAPI 单文件 HTML + WebSocket，ADR-3）。
  **完成时间**: 2026-08-19
- [x] 主入口 `main.py` + 编排层 `orchestrator.py`（单向数据流组装）。
  **完成时间**: 2026-08-19
- [x] README 运行指引 + requirements 补齐（httpx）。
  **完成时间**: 2026-08-19

## M0 记录结构与规划奠基（✅ 2026-08-19）

- [x] 建立规划/记录结构：CLAUDE.md（工作规范）、PLAN.md（规划）、TASKS.md（本文件）、docs/PRD.md（需求文档）、.gitignore。
  **完成时间**: 2026-08-19

## M1 环境与 API 校验（🔶 前置决策完成，真机验证待做）

- [x] 【前置决策】热键耦合方案定案 → **方案B 粘滞目标（零侵入）**，见 PLAN.md ADR-1。
  **完成时间**: 2026-08-19
- [ ] 闪电说完成模型配置（SenseVoice/Whisper + DeepSeek），验证 Alt tap-toggle 转录可用性。
- [x] 【前置决策】网络方案定案 → **UDP 心跳 + 子网扫描兜底（自动发现）**，见 PLAN.md ADR-2。
  **完成时间**: 2026-08-19
- [ ] 真机验证 ADR-2 前提：同一手机热点下多端互通、UDP 报到广播可被台式机收到（热点允许客户端互访）。
- [ ] 热键验证：Alt+1/2/3/4 不透传前台应用（浏览器不切标签）。
- [ ] 热键验证：Alt+N 组合不会误触闪电说录音（Alt 是闪电说触发键）。

## M2 目标端接收服务（🔶 代码完成，真机部署待做）

- [x] 编写笔记本/桌面接收端 `receiver.py`（HTTP 收文 + UDP 心跳广播 + 平台粘贴后端）。
  **完成时间**: 2026-08-19
- [x] 编写平板 Termux 接收端 `tablet_server.py`（纯标准库 HTTP + root 粘贴）。
  **完成时间**: 2026-08-19
- [ ] 真机运行笔记本接收端，验证局域网 POST 自动打字（需 Windows/macOS/Linux 真机）。
- [ ] 平板 Termux 部署 + Root 粘贴 + 后台保活（需 Termux 真机）。

## M3 台式机主控服务（🔶 代码完成，Windows 真机验证待做）

- [x] 【前置决策】转录完成判定定案 → **事件驱动 + 基线/去抖/过期**，见 PLAN.md ADR-4。
  **完成时间**: 2026-08-19
- [x] 【前置决策】闪电说自动粘贴处理定案 → **保留原生，远程接受台式机重复**，见 PLAN.md ADR-5。
  **完成时间**: 2026-08-19
- [x] 实现粘滞目标状态机 `state.py`（Alt+1/2/3/4 目标选择，ADR-1）。
  **完成时间**: 2026-08-19
- [x] 实现剪贴板监听 `clipboard_monitor.py`（WM_CLIPBOARDUPDATE 事件驱动，ADR-4）。
  **完成时间**: 2026-08-19
- [x] 实现设备发现 `discovery.py`（UDP 报到 + 在线表 + 子网扫描兜底，ADR-2）。
  **完成时间**: 2026-08-19
- [x] 实现全局热键注册表 `hotkey.py` + Windows 后端 `win_backend.py`。
  **完成时间**: 2026-08-19
- [x] 实现路由编排 `router.py` + 传输层 `transport.py`。
  **完成时间**: 2026-08-19
- [x] 集成 SQLite 数据库读写引擎 `storage.py`（transcript_logs 表）。
  **完成时间**: 2026-08-19
- [ ] Windows 真机验证：热键 hook、WM_CLIPBOARDUPDATE、托盘（需 Windows Python）。

## M4 Web 仪表盘（🔶 代码完成，浏览器联调待做）

- [x] 构建 FastAPI Web 服务 `web.py`（状态 API + WebSocket 推送）。
  **完成时间**: 2026-08-19
- [x] 编写前端 Dashboard（单文件 HTML：Tailwind + Vue CDN，ADR-3）。
  **完成时间**: 2026-08-19
- [ ] 浏览器联调：实时状态同步 + 历史日志查看（随 M3 真机一起验证）。

## M5 体验调优与开机自启（🔶 部分代码完成，真机调优待做）

- [x] 剪贴板去抖/过期参数化（`stability_ms` / `pending_timeout_sec` 进 config）。
  **完成时间**: 2026-08-19
- [x] Windows 托盘 + 自启代码（`win_backend.py`）。
  **完成时间**: 2026-08-19
- [ ] 真机调优：去抖时长、托盘自启实测。

## 维护规则

- 新任务写到对应里程碑下；未启动的里程碑只登记方向，不预列细节。
- 完成记录写在任务条目下（含 **完成时间**），大段过程性记录写入 `docs/tasks-archive/`，不把主文件写成长流水账。
- 里程碑状态变化时同步更新 `PLAN.md`。
