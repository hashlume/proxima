#!/bin/bash
# Primary box: keep mine/cons/dash, swap the reference seekers for ProxiChorizo.
set -u
BASE=/root/proxima
MINE_DIR=$BASE/mine
PXBIN=/root/proxichorizo-2.0.2/proxichorizo
[ -x "$PXBIN" ] || { echo "no px binary"; exit 1; }

supervisorctl stop proxima >/dev/null 2>&1 || true
pkill -f 'gpu_seeker.py run' 2>/dev/null || true
screen -wipe >/dev/null 2>&1 || true
[ -f "$BASE/start-gpu_seeker.sh" ] || cp -f "$BASE/start.sh" "$BASE/start-gpu_seeker.sh" 2>/dev/null || true
sleep 2

cat > "$BASE/start.sh" <<'EOF'
#!/bin/bash
# Primary miner: proxi node mine keeps the key and serves jobs on 127.0.0.1:8100;
# ProxiChorizo does the hashing for the local GPUs in one process.
set -u
BASE=/root/proxima
MINE_DIR=$BASE/mine
PXBIN=/root/proxichorizo-2.0.2/proxichorizo
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

# wait for the job server before starting the GPU process
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

up px "$BASE/px.log" "$PXBIN" --proxi http://127.0.0.1:8100 --token "$TOK" --name O-2439690
up dash "$BASE/dashboard.log" python3 "$BASE/dashboard.py"
EOF
chmod +x "$BASE/start.sh"

supervisorctl start proxima >/dev/null 2>&1 || true
sleep 30
screen -ls | sed -n '2,8p'
echo '--- px ---'
tail -4 "$BASE/px.log"
