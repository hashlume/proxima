#!/bin/bash
# Idempotent: start whatever part of the mining stack is not running.
#   mine        : proxi node mine --workers 0 (owns the key, serves seeker jobs on 127.0.0.1:8100)
#   cons        : proxi node consolidate
#   gpu0 / gpu1 : CUDA seeker per RTX 3090
#   dash        : status page on 0.0.0.0:8080
set -u
MINE_DIR=/root/proxima/mine
SEEKER_DIR=/root/proxima/repo/proxi/node_cmd/mine/gpu_seeker
cd "$MINE_DIR" || exit 1
TOK=$(sed -n 's/^        token: //p' "$MINE_DIR/proxi.yaml" | head -1)
screen -wipe >/dev/null 2>&1 || true

up() {
  name=$1; log=$2; shift 2
  screen -ls 2>/dev/null | grep -q "\.$name\b" && return 0
  screen -dmS "$name" -L -Logfile "$log" "$@"
}

up mine "$MINE_DIR/mine.log" proxi node mine --workers 0 \
   --stream http://79.137.70.25:8001,http://51.254.47.76:8001
up cons "$MINE_DIR/consolidate.log" proxi node consolidate
for g in 0 1; do
  up "gpu$g" "$MINE_DIR/gpu$g.log" env CUDA_VISIBLE_DEVICES=$g \
     python3 "$SEEKER_DIR/gpu_seeker.py" run http://127.0.0.1:8100 "$TOK"
done
up dash /root/proxima/dashboard.log python3 /root/proxima/dashboard.py
