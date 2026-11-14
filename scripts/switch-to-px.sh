#!/bin/bash
# Replace the reference seeker with ProxiChorizo on this box.
# usage: switch-to-px.sh <miner_url> <name>
set -u
URL=${1:-http://n1.de.clorecloud.net:1855}
NAME=${2:-$(hostname)}
BASE=/root/proxima
PXBIN=/root/proxichorizo-2.0.2/proxichorizo
TOKEN=$(cat "$BASE/token" 2>/dev/null || true)
[ -n "$TOKEN" ] || { echo "no token"; exit 1; }
[ -x "$PXBIN" ] || { echo "no binary at $PXBIN"; exit 1; }

# stop the reference seekers and the watchdog that would resurrect them
supervisorctl stop proxima >/dev/null 2>&1 || true
pkill -f 'gpu_seeker.py run' 2>/dev/null || true
screen -wipe >/dev/null 2>&1 || true
sleep 2

# keep the old launcher for a rollback
[ -f "$BASE/start-gpu_seeker.sh" ] || cp -f "$BASE/start.sh" "$BASE/start-gpu_seeker.sh" 2>/dev/null || true

cat > "$BASE/start.sh" <<EOF
#!/bin/bash
set -u
BASE=$BASE
TOKEN=\$(cat \$BASE/token)
URL=$URL
NAME=$NAME
PXBIN=$PXBIN
screen -wipe >/dev/null 2>&1 || true
if ! screen -ls 2>/dev/null | grep -q '\.px\b'; then
  screen -dmS px -L -Logfile \$BASE/px.log \$PXBIN --proxi \$URL --token "\$TOKEN" --name "\$NAME"
fi
EOF
chmod +x "$BASE/start.sh"

supervisorctl start proxima >/dev/null 2>&1 || true
sleep 25
screen -ls | sed -n '2,6p'
echo '--- px log ---'
tail -6 "$BASE/px.log"
