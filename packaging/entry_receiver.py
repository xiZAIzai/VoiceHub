"""PyInstaller 打包入口：接收端（笔记本）启动器。

保持包上下文（voicehub.receiver 使用相对导入），双击 exe 默认 --name laptop，
名称需与 daemon 端 config.json 的 target key 一致。
"""
from voicehub.receiver import main

if __name__ == "__main__":
    raise SystemExit(main())
