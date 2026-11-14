#!/bin/bash
# Fresh seeker box: install ProxiChorizo and run it against the miner.
# usage: install-px-box.sh <token> [miner_url] [name]
set -u
TOKEN=${1:?token}
URL=${2:-http://n1.de.clorecloud.net:1855}
NAME=${3:-$(hostname)}
BASE=/root/proxima
PXBIN=/root/proxichorizo-2.0.2/proxichorizo

mkdir -p "$BASE"
printf '%s\n' "$TOKEN" > "$BASE/token"; chmod 600 "$BASE/token"
[ -x "$PXBIN" ] || { echo "missing $PXBIN (extract the tarball first)"; exit 1; }

nvidia-smi --query-gpu=index,name,compute_cap --format=csv,noheader | sed 's/^/  /'

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

cat > "$BASE/watchdog.sh" <<EOF
#!/bin/bash
while true; do $BASE/start.sh >> $BASE/watchdog.log 2>&1; sleep 60; done
EOF
chmod +x "$BASE/watchdog.sh"

mkdir -p /etc/supervisor/conf.d
cat > /etc/supervisor/conf.d/proxima.conf <<EOF
[program:proxima]
command=/bin/bash $BASE/watchdog.sh
directory=$BASE
autostart=true
autorestart=true
startsecs=5
stopasgroup=true
killasgroup=true
stdout_logfile=$BASE/supervisor.log
stderr_logfile=$BASE/supervisor.err.log
EOF

command -v screen >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq screen >/dev/null 2>&1
"$BASE/start.sh"
sleep 20
screen -ls | sed -n '2,5p'
tail -4 "$BASE/px.log"
supervisorctl reread >/dev/null 2>&1; supervisorctl update >/dev/null 2>&1
sleep 5
supervisorctl status 2>/dev/null | grep proxima || echo "supervisor not registered"
