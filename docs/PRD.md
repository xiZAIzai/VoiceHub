# VoiceHub Master 项目技术规格与需求文档 (PRD & Tech Spec)

> 来源：用户提供的 PRD 原文（2026-08-19 入库）
> 规划结构：见 [PLAN.md](../PLAN.md) / [TASKS.md](../TASKS.md)

---

## 1. 项目概述与核心目标

### 1.1 项目背景

在多设备桌面工作流（主力台式机、笔记本、多台平板）中，解决"单一麦克风输入源"与"跨设备快速文本输入"的矛盾。通过将**语音识别（ASR）与大模型（LLM）润色中枢**部署在主力台式机上，实现通过不同组合快捷键，将语音转写并润色后的文本实时、自动、精准注入到指定设备的光标位置。

### 1.2 核心目标

1. **单点输入，多端分发**：一台麦克风收音，按快捷键一键将文本打入台式机、笔记本或平板。
2. **高质量 ASR + LLM 语义清洗**：无缝桥接"闪电说"客户端，调用自有 API（Groq/阿里语音 + DeepSeek/Claude）完成去口癖、标点规范与代码/提示词整理。
3. **低侵入性光标级注入**：目标端无需复杂配置，自动模拟击键/粘贴注入当前焦点输入框。
4. **可视化监控仪表盘**：提供现代化 Web 控台，实时监控录音状态、转录流、设备拓扑与分发延迟。
5. **个人记忆库持久化（Agent Memory Ready）**：所有转写文本与元数据持久化入 SQLite，为后续向量化（Embedding）与个人 Agent 记忆中枢预留接口。

---

## 2. 整体系统架构与数据流

```
                    ┌────────────────────────────────────────────────────────┐
                    │                   物理麦克风 (XLR / USB)                │
                    └───────────────────────────┬────────────────────────────┘
                                                │
                                                ▼
┌────────────────────────────────────────────────────────────────────────────────────────────┐
│                               主力台式机 (Host Central Master)                             │
│                                                                                            │
│  ┌──────────────────────┐        ┌────────────────────────┐        ┌────────────────────┐  │
│  │ 1. 软件/驱动音频矩阵 │ ─────► │ 2. 闪电说 (ASR + LLM)  │ ─────► │ 3. 剪贴板 Hook 拦截│  │
│  │ (MIXLINE 独立通道)   │        │ (调用自有 API 清洗文本)│        │ (Windows API/轮询) │  │
│  └──────────────────────┘        └────────────────────────┘        └─────────┬──────────┘  │
│                                                                              │             │
│  ┌───────────────────────────────────────────────────────────────────────────┼──────────┐  │
│  │ 4. VoiceHub 核心守护中枢 (Python / Tauri Daemon)                           │          │  │
│  │                                                                           ▼          │  │
│  │  - 全局热键监听 (Alt+1/2/3/4) ──────────────────────────────► [ 路由分发器 (Router) ]│  │
│  │  - 状态广播 (WebSocket -> Web Dashboard)                     │   │   │                │  │
│  │  - 记忆持久化 (SQLite voice_memory.db)                        │   │   │                │  │
│  └───────────────────────────────────────────────────────────────┼───┼───┼──────────────┘  │
└──────────────────────────────────────────────────────────────────┼───┼───┼─────────────────┘
                                                                   │   │   │
                  ┌────────────────────────────────────────────────┘   │   └─────────────────┐
                  │ (Local Inject)                                     │ (LAN HTTP)          │ (Root/ADB / BLE)
                  ▼                                                     ▼                     ▼
          [ 目标 1: 台式机 ]                                    [ 目标 2: 笔记本 ]      [ 目标 3/4: 平板 ]
          当前光标直接粘贴                                      后台轻量接收端模拟粘贴   Termux/Root 或 ESP32

```

---

## 3. 各模块详细功能规格

### 模块 A：音频捕获与转写中枢（台式机端）

* **收音接入**：声卡/麦克风接入台式机，通过 MIXLINE 或系统虚拟通道划出独立通道提供给"闪电说"。
* **闪电说配置**：
* **ASR**：接入 Groq Whisper (`whisper-large-v3`) 或 阿里 DashScope。
* **LLM 润色**：接入 DeepSeek-V3 或 Claude 3.5 API，配置代码与日常去口癖 System Prompt。
* **触发机制**：默认长按 `Alt` 录音，松开自动转录并写入剪贴板。

### 模块 B：VoiceHub 守护程序与路由中枢（台式机端）

* **热键接管与状态机**：
* `Alt + 1` (按住说话，松开结束) ➔ 标记目标为【台式机】➔ 触发闪电说录音 ➔ 产生文本后直接放行粘贴。
* `Alt + 2` (按住说话，松开结束) ➔ 标记目标为【笔记本】➔ 触发闪电说录音 ➔ 捕获剪贴板文本 ➔ 局域网推送。
* `Alt + 3 / 4` (按住说话，松开结束) ➔ 标记目标为【平板 A/B】➔ 触发闪电说录音 ➔ 捕获剪贴板文本 ➔ 推送至移动端。

* **剪贴板更新拦截器**：
* 毫秒级监听 Windows 剪贴板变化（`WM_CLIPBOARDUPDATE` 或哈希轮询），在闪电说完成写入后提取最新文本，记录总耗时（Latency）。

* **数据分发与持久化**：
* 将文本组装为标准 JSON 发送给目标设备。
* 写入 SQLite 数据库，并通过 WebSocket 广播给本地前端。

### 模块 C：目标端接收与光标注入实现

#### 1. 便携笔记本（Windows / macOS）

* **形态**：后台轻量 HTTP/WebSocket 接收服务（Python Flask / Go 单文件）。
* **注入逻辑**：
* 监听端口（如 `5050`）。
* 收到 Payload: `{"text": "..."}` 后，写入笔记本本地剪贴板并模拟 `Ctrl + V`（或 macOS `Cmd + V`）注入当前焦点窗口。

#### 2. 平板端（Android Root 方案）

* **形态**：Termux 后台常驻轻量服务。
* **注入逻辑**：
* 收到文本后调用 `termux-clipboard-set` 写入平板剪贴板。
* 调用 Root 权限命令 `su -c "input keyevent 279"` (KEYCODE_PASTE) 触发系统级粘贴。

#### 3. 平板端（非 Root / iPadOS 备用硬件方案）

* **形态**：台式机插一个 ESP32 蓝牙开发板（刷入标准 BLE HID 键盘固件）。
* **注入逻辑**：台式机中控通过 USB 串口将文本发送给 ESP32，ESP32 伪装成蓝牙键盘逐字输入平板光标处。

---

## 4. 数据库设计（面向 Agent 记忆管理）

**数据库文件**：`voice_memory.db` (SQLite3)

```sql
CREATE TABLE IF NOT EXISTS transcript_logs (
    id TEXT PRIMARY KEY,                       -- UUID v4
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- 时间戳
    target_device TEXT NOT NULL,               -- 目标: 'desktop', 'laptop', 'tablet_root'
    raw_text TEXT,                             -- ASR 原始文本 (如有)
    processed_text TEXT NOT NULL,              -- 闪电说/LLM 清洗后的文本
    char_count INTEGER NOT NULL,               -- 字符数
    latency_ms INTEGER NOT NULL,               -- 完整耗时 (毫秒)
    category TEXT DEFAULT 'general',           -- 分类: 'code', 'chat', 'note'
    is_routed_successfully INTEGER NOT NULL,   -- 路由状态 (1: 成功, 0: 失败)
    embedding_status TEXT DEFAULT 'pending',   -- 向量化状态: 'pending', 'indexed'
    metadata_json TEXT                         -- 扩展上下文 (当前活跃窗口名、工程标签等)
);

CREATE INDEX IF NOT EXISTS idx_created_at ON transcript_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_embedding_status ON transcript_logs(embedding_status);
```

---

## 5. Web 仪表盘（UI / 监控规格）

* **技术栈推荐**：HTML5 + TailwindCSS + Vue.js (单文件或集成在 Tauri 前端中)。
* **核心视图卡片**：
1. **实时状态条**：展示系统状态（待命 `Idle` / 录音中 `Recording` / 处理分发中 `Processing`）。
2. **设备拓扑状态**：展示当前激活的目标通道（台式机 / 笔记本 / 平板）及局域网连通性。
3. **实时抓取卡片**：突出展示最新一次转录的文本内容、字数、处理耗时与目标设备。
4. **历史记忆流面板**：按时间倒序展示历史转写列表，支持一键"重新派发至指定设备"与"数据导出为 JSON/Markdown"。

---

## 6. 配置文件结构设计 (`config.json`)

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8000
  },
  "shandianshuo": {
    "trigger_key": "alt",
    "polling_timeout_sec": 6.0
  },
  "targets": {
    "desktop": {
      "name": "主力台式机",
      "hotkey": "1",
      "type": "local"
    },
    "laptop": {
      "name": "便携笔记本",
      "hotkey": "2",
      "type": "network_http",
      "endpoint": "http://192.168.1.102:5050/paste"
    },
    "tablet": {
      "name": "Root 安卓平板",
      "hotkey": "3",
      "type": "network_http",
      "endpoint": "http://192.168.1.103:5050/paste"
    }
  },
  "storage": {
    "db_path": "voice_memory.db",
    "retention_days": 90
  }
}
```

> 注（2026-08-19，ADR-4 定案）：`shandianshuo.polling_timeout_sec`（原 6s）语义不成立——
> 录音时长不限，改为 `pending_timeout_sec`（默认 30s，粘滞目标过期）+ 新增 `stability_ms`
> （默认 600，去抖）。详见 [PLAN.md ADR-4](../PLAN.md)。

> 注（2026-08-19，ADR-2 修订）：设备地址不再依赖固定 IP——目标设备通过 UDP 心跳广播自报家门
> （name+port），台式机自动发现并维护在线设备表，路由查表取当前 IP。config 中 `endpoint` 可省略
> （由发现结果解析），固定网络也可手动写死覆盖。详见 [PLAN.md ADR-2](../PLAN.md)。

---

## 7. 本机开发任务拆解清单 (Todo Checklist)

* [ ] **Phase 1: 环境与 API 校验**
* [ ] 闪电说完成模型配置（SenseVoice/Whisper + DeepSeek），验证长按 `Alt` 转录可用性。
* [ ] 固定台式机、笔记本和平板在局域网内的静态 IP（或配置 mDNS 主机名）。

* [ ] **Phase 2: 目标端接收服务部署**
* [ ] 编写并运行笔记本端 `laptop_receiver.py`，测试局域网 POST 请求自动打字。
* [ ] 在平板 Termux 中部署 `tablet_server.py`，赋予 Root 权限并测试后台保活与粘贴。

* [ ] **Phase 3: 台式机主控服务实现**
* [ ] 实现全局热键组合监听（按住 `Alt + 1/2/3` 触发，松开结束）。
* [ ] 实现剪贴板监听与文本提取管道。
* [ ] 集成 SQLite 数据库读写引擎。

* [ ] **Phase 4: Web 仪表盘与 WebSocket 联调**
* [ ] 构建 FastAPI / Tauri Web 服务与 WebSocket 广播通道。
* [ ] 编写前端 Dashboard 页面，打通实时状态同步与历史日志查看。

* [ ] **Phase 5: 体验调优与开机自启**
* [ ] 优化剪贴板读取防抖与延迟控制。
* [ ] 配置台式机服务为 Windows 后台服务或托盘自启应用。
