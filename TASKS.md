# VoiceHub 任务清单

> 最后更新：2026-08-20（V2 / M6 六项完成并归档；剩余用户侧验证与平板部署）
> 已完成任务归档：[V1](./docs/tasks-archive/v1-completed.md) · [V2](./docs/tasks-archive/v2-completed.md)

## V2 收尾（用户侧验证，随用随验）

- [ ] 窗口交互三步：点 X 驻留托盘 → 托盘「打开主窗口」唤回 → 托盘「退出」彻底关闭
  （exe 当前版本为 pywebview 关窗语义修复后的重建版）。
- [ ] commit + push 后观察 GitHub Actions 首跑；满意后打 `v0.2.0` 发首个 Release。
- [ ] 笔记本部署 VoiceHubReceiver exe，Alt+2 跑一轮真链路（发一条语音到笔记本粘贴）。
- [ ] 托盘勾选「开机自启」后重启电脑，验证托盘自动出现（此前已注册过一次）。

## V1 收尾（剩余项）

- [ ] 平板 Termux 部署 + Root 粘贴 + 后台保活（需 Termux 真机）。

## 维护规则

- 新任务写到对应里程碑下；未启动的里程碑只登记方向，不预列细节。
- 完成记录写在任务条目下（含 **完成时间**），大段过程性记录写入 `docs/tasks-archive/`，不把主文件写成长流水账。
- 里程碑状态变化时同步更新 `PLAN.md`。
