#!/data/data/com.termux/files/usr/bin/bash
# VoiceHub 移动端员工机（M14 路线 c）Termux 一键部署。
# 用法（Termux 内）：
#   bash termux_setup.sh [员工名]     # 默认 员工一号
# 前置：先安装 Termux:API 这个 App（F-Droid/GitHub releases），脚本会装它的命令行半边。
set -e

NAME="${1:-员工一号}"

echo "== 1/3 安装依赖（git + python + termux-api 命令行）=="
pkg update -y >/dev/null 2>&1 || true
pkg install -y git python termux-api

echo "== 2/3 拉取 VoiceHub 仓库 =="
if [ -d ~/VoiceHub/.git ]; then
    git -C ~/VoiceHub pull --ff-only
else
    git clone https://github.com/xiZAIzai/VoiceHub.git ~/VoiceHub
fi

echo "== 3/3 申请休眠豁免并启动接收端 =="
termux-wake-lock 2>/dev/null || echo "（termux-wake-lock 不可用，息屏可能断线）"
echo "启动后：手机和主控机连同一个 WiFi，主控机端按 Alt+N 选它即可分发。"
echo "粘贴方式（无 root）：文本自动进剪贴板 + 通知提醒，到目标 App 里长按粘贴。"
echo
cd ~/VoiceHub && exec python -m voicehub.tablet_server --name "$NAME"
