#!/bin/bash
# New GPU box: keep the tunnel to the miner and one seeker per GPU running.
# Idempotent; safe to call every minute from the watchdog.
set -u
BASE=/root/proxima
TOKEN=$(cat "$BASE/token" 2>/dev/null || true)
[ -n "$TOKEN" ] || { echo "missing $BASE/token" >&2; exit 1; }
screen -wipe >/dev/null 2>&1 || true

up() {
  name=$1; log=$2; shift 2
  screen -ls 2>/dev/null | grep -q "\.$name\b" && return 0
  screen -dmS "$name" -L -Logfile "$log" "$@"
}

up link "$BASE/link-screen.log" "$BASE/link.sh"

n=$(nvidia-smi -L 2>/dev/null | wc -l)
[ "$n" -gt 0 ] || { echo "no gpus" >&2; exit 1; }
for g in $(seq 0 $((n-1))); do
  up "gpu$g" "$BASE/gpu$g.log" env CUDA_VISIBLE_DEVICES=$g \
     python3 "$BASE/seeker/gpu_seeker.py" run http://127.0.0.1:8100 "$TOKEN"
done
