# V2 已完成任务归档

> 归档时间：2026-08-20（触发条件：M6 里程碑六项全部完成）
> V2 结论：daemon / receiver 打成双击即用 exe（零 Python 环境依赖）+ 配置界面 +
> pywebview 原生窗口（关窗驻留托盘）+ 开机自启 + 开源工程化（MIT / CI 自动构建发布）。
> 94 单测全绿；真机联调修复三个打包/桌面级事故（白屏、僵尸进程、关窗语义，见 M6-④）。
> 用户侧待验项移交 TASKS.md「V2 收尾」。

## M6-① daemon exe 打包（2026-08-20）

- [x] PyInstaller 打包台式机 daemon 为 exe（托盘 + 图标 + 自动开窗，双击即用）。
- 产物 `dist/VoiceHub/`（one-dir 43MB）；构建 `packaging/build_daemon.bat`（spec 同目录）。
- 配套：新增 `voicehub/paths.py`（frozen 时 config/db/logs 锚定 exe 目录，源码运行行为
  不变）；db 相对路径解析到 config 同目录；frozen 日志落 `logs/voicehub.log`；
  `scripts/make_icon.py` 生成 `assets/voicehub.ico`。测试 +6（paths 5 + db 解析回归）。
- 冒烟：CWD=System32 最坏场景全过（API/热键/剪贴板/托盘/锚定）。
- 坑（三条）：**conda 系 Python** 的 `sqlite3.dll`/`ffi.dll` 在 `Library\bin\` 不被
  PyInstaller hook 收编，需在 spec 手动收编（conda 的 `_ctypes.pyd` 链接 `ffi.dll`，
  非 python.org 的 `libffi-8.dll`，PE 字符串扫描确认）；**bat 必须纯 ASCII**（cmd 按
  GBK 解析 UTF-8 中文注释会炸）；冒烟配置放 dist 外（`--noconfirm` 会清空 dist）。

## M6-② receiver exe 打包（2026-08-20）

- [x] 接收端 receiver 打包（笔记本双击即用；平板维持 Termux 脚本）。
- 产物 `dist/VoiceHubReceiver/`（one-dir 31MB，console 保留日志可见、关窗即停）；
  默认 `--name laptop`，不同名建快捷方式加参数。
- 冒烟：`/health` 200、`/paste` HTTP 层通（UTF-8）、UDP 心跳协议包正确。
- 发现：**助手会话 shell 的进程树打不开 Win32 剪贴板**（`OpenClipboard` 恒错误 5，
  源码 python 与 exe 同挂，非沙箱亦然），用户自启进程不受影响。→ 剪贴板/键盘模拟类
  冒烟转用户侧执行，助手只做 HTTP 侧验证。

## M6-③ 配置界面（2026-08-20）

- [x] config 读写 API + 仪表盘「设置」页（目标/热键/去抖/超时可视化编辑）。
- 新增 `voicehub/settings.py`（ConfigService）：复用 `Config._apply` 做类型校验 +
  热键唯一/type 合法性；`.tmp` + `os.replace` 原子写回；热应用字段
  （`stability_ms`/`pending_timeout_sec`）即时生效到 monitor+sticky，其余变更返回
  `need_restart` 由前端提示。
- API `GET/PUT /api/config`（Request 直接收 body，沿用 receiver 422 教训）；
  `ClipboardMonitor.apply_params()` 热应用入口。测试 +9（service 6 + HTTP 层 3）。
- 冒烟（exe 内）：热字段 200 即时生效 / 非法值 400 / 热键冲突 400 / 目标变更提示重启。

## M6-④ pywebview 原生窗口（2026-08-20，含三次真机修复）

- [x] 原生窗口包裹仪表盘：关窗驻留托盘、托盘唤回、退出走托盘。
- 新增 `voicehub/app_window.py`（WindowController 纯状态逻辑 + start_app_window，
  pywebview 缺失/失败回退托盘+浏览器）；win_backend 主线程改跑 `webview.start()`；
  托盘菜单「打开主窗口 / 开机自启 / 退出」。依赖 pywebview 6.2.1（Win11 自带 WebView2）。
- 打包备注：需 `collect_submodules("webview") + clr`（pythonnet 动态导入）。
- **真机联调三次修复（用户实测暴露，助手冒烟盲区）**：
  1. **白屏「无法访问」**：windowed exe 双击启动无控制台句柄，`sys.stdout` 为 None，
     uvicorn `ColourizedFormatter` 调 `isatty()` 崩 → dashboard 线程静默死（无任何日志）。
     修复：`run_web` 改 `log_config=None` + frozen 下给 None 流挂 devnull 兜底 + 线程
     异常落日志 + 开窗前 `wait_for_port`。**教训：windowed exe 冒烟必须用无句柄方式
     （`start /D`）跑一轮，带管道的终端启动测不出这类问题。**
  2. **僵尸进程**：托盘退出后 pywebview/pythonnet/pystray 留非 daemon 线程，进程退不出
     且占端口（二次白屏放大器）。修复：退出清理后枚举残留线程记日志，必要时 `os._exit(0)`。
  3. **点 X 误退整机**：pywebview 6.x `Event.set()` 语义与文档直觉**相反**——handler
     返回 **False 才取消关闭**，返回 True 是放行销毁（读 `webview/event.py` 定案）；
     另 FormClosing 内 `Cancel=True` 时同事件内的 `Hide()` 会被吞，需 50ms 延迟隐藏。
     **教训：第三方库事件语义以源码为准，UI 否决类行为必须真机点验。**

## M6-⑤ 托盘开机自启（2026-08-20）

- [x] 托盘「开机自启」勾选项 + HKCU Run 键注册（自 V1/M5 并入）。
- 重写自启段：`_autostart_command()` 区分 frozen（直接启 exe）/源码（`python -m
  voicehub.main`）——**原实现 frozen 下带 `-m` 会 argparse 报错，且是从未被调用的
  死代码**；`is_autostart_enabled/install/remove`；托盘 checked 菜单切换。
- 验证：命令构造单测 2 项 + 注册表真机回环（装→查→删→查，终态还原）。

## M6-⑥ 开源工程化（2026-08-20）

- [x] MIT License + GitHub Actions CI + README 打包版说明。
- `.github/workflows/build.yml`：push/PR 跑 pytest 并构建双 exe 上传 artifact；
  `v*` tag 自动发 Release（zip 附 config.json）。
- README：Releases 下载双击即用（零环境）、构建命令、新模块目录、License 段。
