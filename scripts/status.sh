#!/bin/bash
# One-screen status of the Proxima mining stack.
set -u
M=/root/proxima/mine
U=http://65.21.170.230:8001
cd "$M" || exit 1

echo "== screens =="
screen -ls | sed -n '2,7p'

echo "== hashrate (miner view) =="
tail -n 30 mine.log | grep -oE '[0-9_]+ H/s' | tail -2

echo "== our transits (solutions the miner accepted) =="
a0=$(grep -c 'ACCEPTED' gpu0.log 2>/dev/null || true)
a1=$(grep -c 'ACCEPTED' gpu1.log 2>/dev/null || true)
echo "gpu0: $a0   gpu1: $a1   total: $((a0 + a1))"

echo "== network mine chain (no blocks in Proxima: these are transits) =="
curl -s -m 20 "$U/api/v1/monitor" | python3 -c '
import json,sys
d=json.load(sys.stdin)["live"]
f=d["fair_launch"]
print("  transits mined : {:,}".format(f["mined_transactions"]))
print("  remaining      : {:,.0f} PROX".format(f["remaining"]/1e6))
print("  difficulty     : {} bits".format(f["difficulty"]))
print("  miners in last 120 slots: {}".format(f["contest"]["competing_miners"]))
print("  slot           : {}".format(d["current_slot"]))
' 2>/dev/null || echo '  (monitor endpoint unreachable)'

echo "== gpus =="
nvidia-smi --query-gpu=index,utilization.gpu,power.draw,temperature.gpu --format=csv,noheader

echo "== wallet =="
proxi node balance 2>/dev/null | sed -n '/SUMMARY/,$p' | head -8
