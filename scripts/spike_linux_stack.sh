#!/usr/bin/env bash
# V3/M7 输入栈 spike 脚本（WSL2 Ubuntu 22.04 与 openKylin 真机通用）。
#
# 目的：验证 Linux 侧剪贴板读写/粘贴注入/桥接的可行性，为 ADR-7 选型提供事实依据。
# 用法：bash scripts/spike_linux_stack.sh
# 每项输出 PASS/FAIL/SKIP，最终汇总。无副作用（只动剪贴板内容）。
set -u

PASS=0; FAIL=0; SKIP=0
ok()   { echo "  [PASS] $1"; PASS=$((PASS+1)); }
bad()  { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
skip() { echo "  [SKIP] $1"; SKIP=$((SKIP+1)); }

echo "== 0) 环境信息 =="
echo "  OS: $(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME" || uname -a)"
echo "  DISPLAY=${DISPLAY:-<空>} WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-<空>}"

echo "== 1) 工具存在性 =="
for t in xclip xsel xdotool python3; do
    if command -v "$t" >/dev/null 2>&1; then
        echo "  $t -> $(command -v "$t")"
    else
        echo "  $t -> 未安装"
    fi
done

echo "== 2) xclip 写→读自洽（含中文/UTF-8） =="
if command -v xclip >/dev/null 2>&1; then
    printf '中文测试-english-123' | timeout 5 xclip -selection clipboard -i 2>/dev/null
    sleep 0.5
    got=$(timeout 5 xclip -selection clipboard -o 2>/dev/null)
    if [ "$got" = "中文测试-english-123" ]; then ok "xclip 写读一致: $got"; else bad "xclip 写读不一致: [$got]"; fi
else
    skip "xclip 未安装"
fi

echo "== 3) 桥接: Linux 写 → Windows 读（仅 WSL 互操作环境有意义） =="
if command -v xclip >/dev/null 2>&1 && command -v powershell.exe >/dev/null 2>&1; then
    printf 'bridge-from-linux' | timeout 5 xclip -selection clipboard -i 2>/dev/null
    sleep 0.5
    got=$(timeout 20 powershell.exe -NoProfile -Command Get-Clipboard 2>/dev/null | tr -d '\r\n')
    if [ "$got" = "bridge-from-linux" ]; then ok "Windows 读到: $got"; else bad "Windows 侧读到: [$got]"; fi
elif ! command -v powershell.exe >/dev/null 2>&1; then
    skip "非 WSL 环境（无 powershell.exe 互操作），openKylin 真机预期 SKIP"
fi

echo "== 4) 桥接: Windows 写 → Linux 读（仅 WSL 互操作环境有意义） =="
if command -v xclip >/dev/null 2>&1 && command -v powershell.exe >/dev/null 2>&1; then
    timeout 20 powershell.exe -NoProfile -Command 'Set-Clipboard -Value "from-windows"' >/dev/null 2>&1
    sleep 1
    got=$(timeout 5 xclip -selection clipboard -o 2>/dev/null)
    if [ "$got" = "from-windows" ]; then ok "Linux 读到: $got"; else bad "Linux 侧读到: [$got]"; fi
fi

echo "== 5) xdotool 注入 =="
if command -v xdotool >/dev/null 2>&1; then
    if timeout 5 xdotool getdisplaygeometry >/dev/null 2>&1; then
        ok "getdisplaygeometry: $(timeout 5 xdotool getdisplaygeometry 2>/dev/null)"
    else
        bad "getdisplaygeometry 失败（无 X 会话?）"
    fi
    if timeout 5 xdotool key ctrl+v 2>/dev/null; then ok "key ctrl+v 未报错"; else bad "key ctrl+v 报错"; fi
else
    skip "xdotool 未安装"
fi

echo "== 6) 空选区时 xclip -o 的退出码（轮询监听需容错） =="
if command -v xclip >/dev/null 2>&1; then
    timeout 5 xclip -selection clipboard -o >/dev/null 2>&1
    echo "  当前选区读取 exit=$?（1=空选区属正常，代码须按 None 处理）"
    ok "已确认退出码语义"
fi

echo "== 汇总: PASS=$PASS FAIL=$FAIL SKIP=$SKIP =="
[ "$FAIL" -eq 0 ]
