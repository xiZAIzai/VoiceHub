# VoiceHub 任务清单

> 最后更新：2026-08-20（V1 主体闭环并归档；V2 桌面化立项）
> 当前焦点：V2 桌面化打包（一键启动 / 配置界面 / 开源化）
> V1 已完成任务归档：[docs/tasks-archive/v1-completed.md](./docs/tasks-archive/v1-completed.md)

## V1 收尾（剩余项）

### M1 环境与 API 校验（转出/搁置）

- [ ] 闪电说完成模型配置（SenseVoice/Whisper + DeepSeek），验证 Alt tap-toggle 转录可用性。
  （2026-08-20 定：转用户侧自行处理）
- [ ] 热键验证：Alt+1/2/3/4 不透传前台应用（浏览器不切标签）。
  （实际使用未观察到异常，随用随验）

### M2 目标端接收服务（平板待部署）

- [ ] 平板 Termux 部署 + Root 粘贴 + 后台保活（需 Termux 真机）。

## V2 桌面化打包与开源分发（⏳ 未启动，路线定案见 PLAN.md ADR-6）

- [ ] PyInstaller 打包台式机 daemon 为 exe（托盘 + 图标 + 自动打开窗口，双击即用）。
- [ ] 接收端 receiver 打包（笔记本双击即用；平板维持 Termux 脚本）。
- [ ] 配置界面：FastAPI 增加 config 读写 API + 仪表盘"设置"页
  （目标设备/热键/去抖/超时参数可视化编辑）。
- [ ] pywebview 原生窗口包裹仪表盘（ADR-3 预留路径；关窗口不退程序，退出走托盘）。
- [ ] 托盘开机自启实测（自 V1/M5 并入）。
- [ ] 开源工程化：README 完善、License、GitHub Actions 自动构建 exe。

## 维护规则

- 新任务写到对应里程碑下；未启动的里程碑只登记方向，不预列细节。
- 完成记录写在任务条目下（含 **完成时间**），大段过程性记录写入 `docs/tasks-archive/`，不把主文件写成长流水账。
- 里程碑状态变化时同步更新 `PLAN.md`。
