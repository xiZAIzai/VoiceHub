# VoiceHub 任务清单

> 最后更新：2026-08-31 深夜（**v0.5.0 已发布**，多厂商转写内核 + 仪表盘三主题改版 +
> 官网/文档上线待 Pages 开关；下一里程碑 **M14 移动端员工机**）
> 已完成归档：[V1](./docs/tasks-archive/v1-completed.md) · [V2](./docs/tasks-archive/v2-completed.md)
> · [V3 Linux 双端 + V4 多厂商内核（至 v0.5.0）](./docs/tasks-archive/V3-V4-v0.5.0-Linux双端与多厂商仪表盘.md)
> · [V4-M11-M12上](./docs/tasks-archive/V4-M11-M12上-自建转写内核.md)

## 已完成里程碑索引

- **V1–V2**：核心链路（闪电说剪贴板 → 热键分发 → 多端接收）+ Windows 打包首发（v0.2.0）。
- **V3**（M7–M10，v0.3.0）：Linux 双端 AppImage、openKylin 实机适配、托盘。
- **V4**（M11–M13，v0.4.0/v0.4.1）：自建转写内核（火山豆包 WS v3）、润色四模式、
  波形悬浮窗、凭证自助填写；后经**多厂商开放轮**（OpenAI 兼容 ASR / 润色七厂商预设 /
  高级设置卡 / 三主题仪表盘 / vendor 本地化秒开）收口于 **v0.5.0**。
- 官网 + 使用文档（website/，GitHub Pages 待开启）。

## 活页：未完成项

- [ ] **官网上线**（一次性动作，用户侧）：Settings → Pages → Source 选「GitHub Actions」
  → Actions 重跑 Deploy Website。之后随 main 自动部署。
- [ ] **M12 余项**：仪表盘实时听写状态卡（engine.state WS 推送）+ 托盘 tooltip 阶段文案。
- [ ] **M13 余项**：CI 冒烟补无麦克风跳过项；Windows 侧实机验证
  （win32_write_text/HotkeyBackend 听写接线已备，包为实验性标注）。
- [ ] **外部等待**：闪电说 openKylin UI 渲染兼容（定性为对方 × kylin-wlcom bug，
  已试尽外部手段，待官方内测反馈；glibc 侧载方案已落地 `~/bin/shandianshuo-launcher`）。
- [ ] **V2 收尾（用户侧随用随验）**：托盘三步交互验证；笔记本接收端 Alt+2 真链路；
  开机自启重启验证。

## M14：移动端员工机（下一里程碑，2026-08-31 立项）

> 目标：把 Android 手机纳入「员工机」——一个人调度一支团队的第一块移动拼图。
> 现成资产：`tablet_server.py`（HTTP /paste + /health，v1 已实现，从未真机验证）；
> UDP 心跳协议、分发链路均通用。

- [ ] **M14-① 真机技术验证**（服务端已就绪，**待用户真机验收**）：
  - [x] 代码与桌面冒烟 ✅ 2026-08-31（用户定案路线 c 先行）：tablet_server 升级为
    「剪贴板 + 常驻通知（预览/同 id 替换）」零权限路线，clipboard-only 语义修正
    （无 root 不再报错，mode=clipboard）；有 root 仍自动注入直贴；--no-notify 可关。
    纯标准库（Termux 零 pip）；测试 7 项 + 「假手机」端到端冒烟全通
    （fake termux 命令验证剪贴板落盘/通知/toast/空文本拒绝）。
  - [x] 一键部署脚本 ✅ `scripts/termux_setup.sh`（pkg 依赖 + clone + wake-lock + 启动）。
  - [ ] **真机验收清单（用户）**：① Termux + Termux:API 装好跑 termux_setup.sh；
    ② 主控机发现「员工一号」（同网段 UDP）；③ 分发一句话 → 手机剪贴板有内容 + 收到通知；
    ④ 目标 App 长按粘贴上屏；⑤ 息屏/后台保活观察。 
- [ ] **M14-② 交付形态定案**：Termux 脚本包 / 打包 APK（视 ① 结论），README/文档章节。
- [ ] **M14-③ 主控机识别优化**（顺带）：发现协议区分员工机类型，仪表盘标注平台。

> 关键风险：Android 10+ 限制后台模拟粘贴，b) 是唯一真自动路线但需要写 App；
> 用户偏好与真机实测结论共同定案。

## 运维纪律（事故存档）

- **2026-08-26 20:25 FUSE 僵死事故**：重部署时 pkill 强杀运行中的 AppImage 实例，
  FUSE 挂载进入 D 状态（不可中断），`ps`/`/proc` 遍历/新进程启动链全部被挂死。
  **纪律**：①重部署前必须让实例优雅退出（托盘「退出」= DBus Event id 90，
  含 tray.stop/storage.close/挂载自动卸载）；②绝不在录音进行中杀进程；
  ③清理命令必须用防自匹配写法。

## 维护规则

- 新任务写到对应里程碑下；未启动的里程碑只登记方向，不预列细节。
- 完成记录写在任务条目下（含 **完成时间**），大段过程性记录写入 `docs/tasks-archive/`，不把主文件写成长流水账。
- 里程碑状态变化时同步更新 `PLAN.md`。
