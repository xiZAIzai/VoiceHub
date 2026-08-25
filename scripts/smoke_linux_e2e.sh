#!/usr/bin/env bash
# V3/M8 Linux 主控端到端冒烟（WSL2 与 openKylin 真机通用）。
#
# 链路：daemon(Linux 后端) 启动 → xdotool 合成 Alt+2 武装（pynput）→
#       xclip 写入"转写"文本 → 轮询拦截 → 路由 HTTP 推送 → 接收端收文。
# 隔离性：
# - config/db/log 全部落 /tmp，不触碰仓库真实数据；
# - 使用独立端口（仪表盘 8100 / 接收端 5051 / 心跳 9998），避免与
#   Windows 侧真身 daemon 撞车（WSL mirrored 网络下端口与 Windows 共享）；
# - laptop 的 endpoint 写死直连，冒烟不依赖 UDP 发现阶段。
# 用法：bash scripts/smoke_linux_e2e.sh
#       可用环境变量覆盖：VENV_PY（venv python 路径）、REPO（仓库路径）
set -u

VENV_PY="${VENV_PY:-$HOME/.venvs/voicehub/bin/python}"
REPO="${REPO:-/mnt/d/funs/VoiceHub}"
CFG=/tmp/voicehub-smoke.json
DB=/tmp/voicehub-smoke.db
DAEMON_LOG=/tmp/voicehub-smoke-daemon.log
RECV_LOG=/tmp/voicehub-smoke-recv.log
TEXT="V3冒烟文本-$$"

cleanup() {
    [ -n "${DAEMON_PID:-}" ] && kill "$DAEMON_PID" 2>/dev/null
    [ -n "${RECV_PID:-}" ] && kill "$RECV_PID" 2>/dev/null
}
trap cleanup EXIT

rm -f "$DB" "$DAEMON_LOG" "$RECV_LOG"

echo "== 0) 生成隔离配置（独立端口 + db 落 /tmp + endpoint 直连） =="
python3 - "$REPO" "$CFG" "$DB" <<'PYEOF'
import json, sys
repo, cfg_path, db_path = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = json.load(open(f"{repo}/config.json", encoding="utf-8"))
cfg["server"]["port"] = 8100
cfg["voicehub"]["discovery_port"] = 9998
cfg["voicehub"]["receiver_port"] = 5051
cfg["targets"]["laptop"]["endpoint"] = "http://127.0.0.1:5051/paste"
cfg["storage"]["db_path"] = db_path
json.dump(cfg, open(cfg_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PYEOF
echo "  配置已写入 $CFG（db -> $DB）"

echo "== 1) 启动接收端（name=laptop，端口 5051） =="
cd "$REPO" || exit 1
"$VENV_PY" -m voicehub.receiver --name laptop --port 5051 --discovery-port 9998 \
    > "$RECV_LOG" 2>&1 &
RECV_PID=$!

echo "== 2) 启动 Linux 主控 daemon（隔离 config，仪表盘 8100） =="
"$VENV_PY" -m voicehub.main --config "$CFG" > "$DAEMON_LOG" 2>&1 &
DAEMON_PID=$!

echo "== 3) 等待服务就绪 =="
READY=0
for i in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:8100/api/state" >/dev/null 2>&1 \
       && curl -sf "http://127.0.0.1:5051/health" >/dev/null 2>&1; then
        READY=1; break
    fi
    sleep 1
done
[ "$READY" = "1" ] && echo "  [PASS] 双服务就绪" || { echo "  [FAIL] 服务未就绪"; exit 1; }
grep -q "目标 3 个" "$DAEMON_LOG" && echo "  [PASS] 配置加载正常（3 目标）" \
    || echo "  [FAIL] 目标数异常: $(grep -o '目标.*个' "$DAEMON_LOG" | head -1)"

echo "== 4) 热键武装（xdotool 合成 Alt+2） =="
grep -q "已注册 Linux 热键: \['<alt>+1', '<alt>+2', '<alt>+3'\]" "$DAEMON_LOG" \
    && echo "  [PASS] 3 组热键注册" \
    || echo "  [WARN] 热键注册行: $(grep -o '已注册 Linux 热键.*' "$DAEMON_LOG")"
# 注意：`xdotool key alt+2` 的合成方式 pynput 收不到（WSLg 实测），
# 必须显式 keydown/keyup 序列（等价真人按下修饰键再按数字）。
xdotool keydown alt; sleep 0.3; xdotool key 2; sleep 0.3; xdotool keyup alt
sleep 1.5
if grep -q "选定目标 laptop" "$DAEMON_LOG"; then
    echo "  [PASS] Alt+2 武装成功"
    ARM=1
else
    echo "  [FAIL] Alt+2 未见武装"; ARM=0
fi

echo "== 5) 写入模拟转写 → 等待拦截路由 =="
printf '%s' "$TEXT" | xclip -selection clipboard -i
sleep 4
if grep -q "路由完成: True -> laptop" "$DAEMON_LOG"; then
    echo "  [PASS] 拦截并路由成功"
    ROUTE=1
elif grep -q "路由完成" "$DAEMON_LOG"; then
    echo "  [FAIL] 路由未成功: $(grep -o '路由完成.*' "$DAEMON_LOG" | tail -1)"; ROUTE=0
else
    echo "  [FAIL] 未见路由完成"; ROUTE=0
fi

echo "== 6) 接收端直连校验（独立证明粘贴后端可用） =="
RECV_DIRECT=$(curl -s -X POST "http://127.0.0.1:5051/paste" \
    -H "Content-Type: application/json" -d '{"text":"receiver直连校验"}')
case "$RECV_DIRECT" in
    *'"ok":true'*) echo "  [PASS] 接收端直连: $RECV_DIRECT" ;;
    *) echo "  [FAIL] 接收端直连异常: $RECV_DIRECT" ;;
esac

echo "== 7) 落库校验 =="
"$VENV_PY" - "$DB" <<'PYEOF'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
row = conn.execute(
    "select target_device, processed_text, is_routed_successfully "
    "from transcript_logs order by created_at desc limit 1").fetchone()
if row:
    print(f"  [PASS] DB 最新记录: target={row[0]} ok={row[2]} text={row[1][:24]}")
else:
    print("  [FAIL] DB 无记录")
PYEOF

echo "== 汇总: 武装=$ARM 路由=$ROUTE （日志: $DAEMON_LOG / $RECV_LOG） =="
