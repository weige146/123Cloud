#!/bin/bash
# 开发用：带超时跑单个测试（macOS 没有 timeout 命令）
# 用法：sh tests/rt.sh <test-name-without-ext> [超时秒，默认 60]
cd "$(dirname "$0")"
t="${1%.test.mjs}"
limit="${2:-60}"
log="/tmp/c123rt-$t.log"
node "$t.test.mjs" >"$log" 2>&1 &
pid=$!
waited=0
while kill -0 $pid 2>/dev/null; do
  if [ "$waited" -ge "$limit" ]; then
    kill -9 $pid 2>/dev/null
    echo "TIMEOUT($limit s) $t"; tail -6 "$log"; exit 2
  fi
  sleep 1; waited=$((waited+1))
done
wait $pid; rc=$?
if [ $rc -eq 0 ]; then echo "PASS $t (${waited}s)"; else echo "FAIL $t"; tail -14 "$log"; fi
exit $rc
