# V1 已完成任务归档

> 归档时间：2026-08-20（触发条件：已完成任务超过 30 条 + V1 主体闭环）
> V1 结论：台式机↔笔记本全链路真机验证通过（Alt+2 → 闪电说 → 剪贴板拦截 → UDP 发现 → HTTP 推送 → 笔记本自动粘贴），67 单测全绿。

## v1 代码实现（2026-08-19，长城任务）

- [x] 项目脚手架与配置模块 `config.py` + `config.json`。
- [x] SQLite 存储引擎 `storage.py`（transcript_logs 表 + WAL）。
- [x] 设备发现 `discovery.py`（UDP 心跳 + 在线表 + 子网扫描兜底，ADR-2）。
- [x] 笔记本/桌面接收端 `receiver.py`（HTTP 收文 + 心跳广播 + 平台粘贴后端）。
- [x] 平板 Termux 接收端 `tablet_server.py`（纯标准库 HTTP + root 粘贴）。
- [x] 粘滞目标状态机 `state.py`（ADR-1）。
- [x] 剪贴板监控 `clipboard_monitor.py`（事件驱动 + 基线/去抖/过期，ADR-4）。
- [x] 全局热键注册表 `hotkey.py` + Windows 后端 `win_backend.py`（热键/剪贴板/托盘/自启）。
- [x] 路由编排 `router.py` + 传输层 `transport.py`（本地记录 / HTTP 推送）。
- [x] Web 仪表盘 `web.py`（FastAPI 单文件 HTML + WebSocket，ADR-3）。
- [x] 主入口 `main.py` + 编排层 `orchestrator.py`（单向数据流组装）。
- [x] README 运行指引 + requirements 补齐（httpx）。

## M0 记录结构与规划奠基（2026-08-19）

- [x] 建立 CLAUDE.md / PLAN.md / TASKS.md / docs/PRD.md / .gitignore。

## M1 环境与 API 校验

- [x] 【前置决策】热键耦合方案定案 → 方案B 粘滞目标（零侵入），ADR-1。（2026-08-19）
- [x] 【前置决策】网络方案定案 → UDP 心跳 + 子网扫描兜底（自动发现），ADR-2。（2026-08-19）
- [x] 【前置决策】转录完成判定定案 → 事件驱动 + 基线/去抖/过期，ADR-4。（2026-08-19）
- [x] 【前置决策】闪电说自动粘贴处理定案 → 保留原生，远程接受台式机重复，ADR-5。（2026-08-19）
- [x] 搭建 Windows 主控机原生环境：Python 3.11 venv + 全量依赖 + 单测全绿 + daemon 冒烟启动。（2026-08-20）
- [x] 真机验证 ADR-2 前提：局域网多端互通、UDP 报到可被台式机接收（source=heartbeat）。（2026-08-20）
- [x] 热键验证：Alt+N 是否误触闪电说录音 → 实测**会误触**，定案接受"按下即录"，暂不换键。（2026-08-20）

## M2 目标端接收服务

- [x] 编写笔记本/桌面接收端 `receiver.py`。（2026-08-19）
- [x] 编写平板 Termux 接收端 `tablet_server.py`。（2026-08-19）
- [x] 真机运行笔记本接收端，验证局域网 POST 自动打字。（2026-08-20，全链路通过）
  - 修复记录：POST /paste 恒 422 —— `from __future__ import annotations` 下 `req: Request`
    注解为字符串，FastAPI 经 get_type_hints 在模块全局解析不到 make_app() 内延迟导入的
    Request，被误判为 query 参数。改为模块级 try-import（Termux 回退 None）+ TestClient
    HTTP 层回归测试。教训：路由处理函数必须有走 HTTP 层的测试。

## M3 台式机主控服务

- [x] 实现粘滞目标状态机 / 剪贴板监听 / 设备发现 / 热键注册 / 路由与传输 / SQLite 集成。（2026-08-19）
- [x] Windows 真机验证：热键 hook、WM_CLIPBOARDUPDATE、托盘。（2026-08-20，交互式全链路通过）
  - 修复记录：main.py 组装 Router 漏注入 transport（HttpPusher），daemon 内所有远程路由恒失败
    "no transport"（直发测试与单测均绕过 Router 分发路径未发现）。修复接线 + test_main 回归断言
    + router 失败告警日志。教训：组装层必须有依赖注入完整性断言。

## M4 Web 仪表盘

- [x] FastAPI Web 服务 + WebSocket 推送。（2026-08-19）
- [x] 前端 Dashboard 单文件 HTML（Tailwind + Vue CDN）。（2026-08-19）
- [x] 浏览器联调：仪表盘不刷新自动更新（WS 实时推送），用户实测通过。（2026-08-20）

## M5 体验调优

- [x] 剪贴板去抖/过期参数化进 config。（2026-08-19）
- [x] Windows 托盘 + 自启代码。（2026-08-19）
- [x] 真机调优：`stability_ms` 600→300（降延迟）；`pending_timeout_sec` 30→300（>30s 长听写
  被粘滞过期整体丢弃，日志无记录证实未进路由）。（2026-08-20）
