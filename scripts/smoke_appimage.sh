#!/usr/bin/env bash
# V3/M10 AppImage 冒烟：构建产物在 WSL/Linux 的运行验证（openKylin 可直接复跑）。
#
# 验证点：
# 1. 首启播种：AppImage 旁边无 config.json 时自动播种默认配置；
# 2. 便携数据目录：config/db/logs 全部锚定 AppImage 所在目录（VOICEHUB_HOME），
#    不写进 AppImage 只读挂载点；
# 3. 双服务（daemon 8100 / receiver 5051）以 AppImage 形态启动；
# 4. 全链：Alt+2 武装 → xclip 写"转写" → 拦截路由 → 接收端 → 便携 db 落库。
# 用法：bash scripts/smoke_appimage.sh（需先 bash packaging/build_linux.sh 出产物）
set -u

REPO="${REPO:-/mnt/d/funs/VoiceHub}"
SMOKE=/tmp/voicehub-appimage-smoke
TEXT="AppImage冒烟-$$"

cleanup() {
    [ -n "${DAEMON_PID:-}" ] && kill "$DAEMON_PID" 2>/dev/null
    [ -n "${RECV_PID:-}" ] && kill "$RECV_PID" 2>/dev/null
}
trap cleanup EXIT

rm -rf "$SMOKE" && mkdir -p "$SMOKE"
cp "$REPO/dist/VoiceHub-x86_64.AppImage" "$REPO/dist/VoiceHubReceiver-x86_64.AppImage" "$SMOKE/"
chmod +x "$SMOKE"/*.AppImage
export APPIMAGE_EXTRACT_AND_RUN=1  # WSL 无 FUSE，运行期解包执行（真机不需要）

echo "== 1) 首启播种 config 验证 =="
"$SMOKE/VoiceHub-x86_64.AppImage" --help >/dev/null 2>&1
if [ -f "$SMOKE/config.json" ]; then
    echo "  [PASS] 首启播种 config.json 到便携目录"
else
    echo "  [FAIL] 未播种 config.json"; exit 1
fi

echo "== 2) 写入冒烟配置（独立端口 8100/5051/9998，db 相对路径 → 便携目录） =="
python3 - "$SMOKE/config.json" <<'PYEOF'
import json, sys
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
cfg["server"]["port"] = 8100
cfg["voicehub"]["discovery_port"] = 9998
cfg["voicehub"]["receiver_port"] = 5051
cfg["targets"]["laptop"]["endpoint"] = "http://127.0.0.1:5051/paste"
cfg["storage"]["db_path"] = "smoke.db"  # 相对路径：锚定 config 所在目录（便携语义）
json.dump(cfg, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PYEOF

echo "== 3) 启动 receiver AppImage（5051） =="
"$SMOKE/VoiceHubReceiver-x86_64.AppImage" --name laptop --port 5051 --discovery-port 9998 \
    > "$SMOKE/recv.log" 2>&1 &
RECV_PID=$!

echo "== 4) 启动 daemon AppImage（便携 config 默认路径，仪表盘 8100） =="
"$SMOKE/VoiceHub-x86_64.AppImage" > "$SMOKE/daemon.log" 2>&1 &
DAEMON_PID=$!

READY=0
for i in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:8100/api/state" >/dev/null 2>&1 \
       && curl -sf "http://127.0.0.1:5051/health" >/dev/null 2>&1; then
        READY=1; break
    fi
    sleep 1
done
[ "$READY" = "1" ] && echo "  [PASS] 双 AppImage 服务就绪" || { echo "  [FAIL] 服务未就绪"; tail -5 "$SMOKE/daemon.log" "$SMOKE/recv.log"; exit 1; }

echo "== 5) Alt+2 武装（xdotool keydown/keyup 序列） =="
xdotool keydown alt; sleep 0.3; xdotool key 2; sleep 0.3; xdotool keyup alt
sleep 1.5
if grep -q "选定目标 laptop" "$SMOKE/logs/voicehub.log" 2>/dev/null; then
    echo "  [PASS] 武装成功（日志已落便携 logs/）"
    ARM=1
else
    echo "  [FAIL] 未见武装"; ARM=0
fi

echo "== 6) 写入模拟转写 → 拦截路由 =="
printf '%s' "$TEXT" | xclip -selection clipboard -i
sleep 4
if grep -q "路由完成: True -> laptop" "$SMOKE/logs/voicehub.log" 2>/dev/null; then
    echo "  [PASS] 拦截并路由成功"
    ROUTE=1
else
    echo "  [FAIL] 路由未成功"; ROUTE=0
fi

echo "== 7) 便携落库校验 =="
python3 - "$SMOKE/smoke.db" <<'PYEOF'
import sqlite3, sys
try:
    row = sqlite3.connect(sys.argv[1]).execute(
        "select target_device, processed_text, is_routed_successfully "
        "from transcript_logs order by created_at desc limit 1").fetchone()
    if row:
        print(f"  [PASS] 便携 DB 记录: target={row[0]} ok={row[2]} text={row[1][:24]}")
    else:
        print("  [FAIL] DB 无记录")
except Exception as e:
    print(f"  [FAIL] DB 异常: {e}")
PYEOF

echo "== 8) 便携目录终态 =="
ls -la "$SMOKE" | grep -vE "^total|\.$" | awk '{print "  " $NF, "("$5"B)"}'

echo "== 汇总: 武装=$ARM 路由=$ROUTE （日志: $SMOKE/logs/voicehub.log） =="
