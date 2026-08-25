#!/usr/bin/env bash
# V3/M10：Linux 一键打包（PyInstaller one-dir → AppImage）。
#
# 产物：dist/VoiceHub-x86_64.AppImage（主控）
#       dist/VoiceHubReceiver-x86_64.AppImage（接收端）
# 用法（WSL/Linux，任意目录）：bash packaging/build_linux.sh
# 环境要求：
# - Python 3.10+ venv（自动探测 .venv → ~/.venvs/voicehub → PATH python3）；
#   pyinstaller/pillow 缺失时自动 pip 安装。
# - 无需 root、无需 FUSE：appimagetool 以 APPIMAGE_EXTRACT_AND_RUN 运行。
# - 运行期系统依赖（不打进包，AppRun 会检测提示）：xclip、xdotool。
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# ---------- venv 探测 ----------
PY=""
for cand in ".venv/bin/python" "$HOME/.venvs/voicehub/bin/python"; do
    [ -x "$cand" ] && PY="$cand" && break
done
[ -z "$PY" ] && PY="$(command -v python3)"
echo "== Python: $PY ($("$PY" --version 2>&1)) =="

# ---------- 构建依赖 ----------
"$PY" -m PyInstaller --version >/dev/null 2>&1 || "$PY" -m pip install -q pyinstaller
"$PY" -c "import PIL" >/dev/null 2>&1 || "$PY" -m pip install -q pillow

# ---------- 图标（ico + png）与测试 ----------
"$PY" scripts/make_icon.py
"$PY" -m pytest -q

# ---------- PyInstaller 双产物 ----------
"$PY" -m PyInstaller packaging/voicehub-daemon-linux.spec \
    --noconfirm --distpath dist --workpath build
"$PY" -m PyInstaller packaging/voicehub-receiver-linux.spec \
    --noconfirm --distpath dist --workpath build

# ---------- appimagetool（缓存 build/，无需 FUSE） ----------
APPIMAGETOOL=build/appimagetool-x86_64.AppImage
if [ ! -x "$APPIMAGETOOL" ]; then
    mkdir -p build
    echo "== 下载 appimagetool =="
    curl -fL --retry 3 -o "$APPIMAGETOOL" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x "$APPIMAGETOOL"
fi

# ---------- AppDir 组装 ----------
# 参数：$1=PyInstaller 产物目录名（=可执行名）  $2=desktop 文件名  $3=注释  $4=播种config(yes/no)
make_appdir() {
    local BIN="$1" DESKTOP_FILE="$2" COMMENT="$3" SEED_CONFIG="$4"
    local APPDIR="build/${BIN}.AppDir"
    rm -rf "$APPDIR"
    mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/voicehub"

    cp -a "dist/${BIN}/." "$APPDIR/usr/bin/"
    cp assets/voicehub.png "$APPDIR/voicehub.png"

    # 首次运行播种默认配置（AppImage 内部只读，config 必须住在数据目录）
    local seed_block=""
    if [ "$SEED_CONFIG" = "yes" ]; then
        cp config.json "$APPDIR/usr/share/voicehub/config.json"
        seed_block='if [ ! -f "$_vh_home/config.json" ]; then
    cp "$APPDIR/usr/share/voicehub/config.json" "$_vh_home/config.json"
fi'
    fi

    cat > "$APPDIR/${DESKTOP_FILE}" <<DESKEOF
[Desktop Entry]
Type=Application
Name=${BIN}
Comment=${COMMENT}
Exec=${BIN}
Icon=voicehub
Terminal=false
Categories=Utility;
DESKEOF

    cat > "$APPDIR/AppRun" <<RUNEOF
#!/usr/bin/env bash
# ${BIN} AppImage 启动器：AppImage 内部挂载点只读，
# 数据目录（config/db/logs）优先用 AppImage 旁边目录（便携式，与 Windows exe 同体验），
# 不可写时回退 ~/.config/voicehub。首次运行播种默认配置。
for _t in xclip xdotool; do
    command -v "\$_t" >/dev/null 2>&1 \\
        || echo "[${BIN}] 提示：缺少系统依赖 \$_t（安装：sudo apt install \$_t）" >&2
done
_self="\${APPIMAGE:-\$PWD}"
_vh_home="\$(dirname "\$_self")"
if [ ! -w "\$_vh_home" ]; then
    _vh_home="\${XDG_CONFIG_HOME:-\$HOME/.config}/voicehub"
    mkdir -p "\$_vh_home"
fi
export VOICEHUB_HOME="\$_vh_home"
${seed_block}
exec "\$APPDIR/usr/bin/${BIN}" "\$@"
RUNEOF
    chmod +x "$APPDIR/AppRun"
}

echo "== 组装 AppDir =="
make_appdir VoiceHub voicehub.desktop "单点语音输入，多端分发（主控）" yes
make_appdir VoiceHubReceiver voicehubreceiver.desktop "VoiceHub 接收端（收文+自动粘贴）" no

# ---------- 生成 AppImage ----------
echo "== 生成 AppImage =="
rm -f dist/VoiceHub-x86_64.AppImage dist/VoiceHubReceiver-x86_64.AppImage
APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGETOOL" build/VoiceHub.AppDir dist/VoiceHub-x86_64.AppImage
APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGETOOL" build/VoiceHubReceiver.AppDir dist/VoiceHubReceiver-x86_64.AppImage

ls -lh dist/*.AppImage
echo "== 完成：dist/VoiceHub-x86_64.AppImage + dist/VoiceHubReceiver-x86_64.AppImage =="
