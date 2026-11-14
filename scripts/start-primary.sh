#!/bin/bash
# Primary miner: holds the key, runs the miner + consolidator, serves jobs on 0.0.0.0:8100,
# and runs one CUDA seeker per local GPU. Idempotent; called by the watchdog every 60 s.
set -u
MINE_DIR=/root/proxima/mine
SEEKER=/root/proxima/seeker/gpu_seeker.py
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

# Seekers connect once at start; if the miner has not bound :8100 yet they must not race it.
python3 - <<'PY'
import socket, time
for _ in range(30):
    s = socket.socket(); s.settimeout(1)
    try:
        s.connect(("127.0.0.1", 8100)); break
    except OSError:
        time.sleep(1)
    finally:
        s.close()
PY

n=$(nvidia-smi -L 2>/dev/null | wc -l)
for g in $(seq 0 $((n-1))); do
  up "gpu$g" "$MINE_DIR/gpu$g.log" env CUDA_VISIBLE_DEVICES=$g \
     python3 "$SEEKER" run http://127.0.0.1:8100 "$TOK"
done

up dash /root/proxima/dashboard.log python3 /root/proxima/dashboard.py
