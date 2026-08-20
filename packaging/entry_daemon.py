"""PyInstaller 打包入口：daemon 主程序启动器。

为什么需要：voicehub/main.py 使用包内相对导入，直接作为 PyInstaller 入口会因
脱离包上下文报 "attempted relative import"。本文件以绝对导入方式拉起
voicehub.main，保持包结构完整。
"""
from voicehub.main import main

if __name__ == "__main__":
    raise SystemExit(main())
