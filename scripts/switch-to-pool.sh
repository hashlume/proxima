#!/bin/bash
# Point this box's ProxiChorizo at the suprnova pool.
# usage: switch-to-pool.sh <pool_token> <worker>
set -u
TOKEN=${1:?pool token}
NAME=${2:-$(hostname)}
BASE=/root/proxima
PXBIN=/root/proxichorizo-2.0.2/proxichorizo
POOL=stratum+tcp://proxi.suprnova.cc:3355

printf '%s\n' "$TOKEN" > "$BASE/pool.token"; chmod 600 "$BASE/pool.token"
[ -x "$PXBIN" ] || { echo "no px binary"; exit 1; }

supervisorctl stop proxima >/dev/null 2>&1 || true
screen -S px -X quit 2>/dev/null || true
screen -wipe >/dev/null 2>&1 || true
sleep 2

cat > "$BASE/start.sh" <<EOF
#!/bin/bash
set -u
BASE=$BASE
PTOK=\$(cat \$BASE/pool.token)
NAME=$NAME
PXBIN=$PXBIN
POOL=$POOL
screen -wipe >/dev/null 2>&1 || true
if ! screen -ls 2>/dev/null | grep -q '\.px\b'; then
  screen -dmS px -L -Logfile \$BASE/px.log \$PXBIN --proxi "\$POOL" -u "\$NAME" -p "\$PTOK" --name "\$NAME" --api-bind 127.0.0.1:4078
fi
EOF
chmod +x "$BASE/start.sh"
"$BASE/start.sh"
sleep 15
tail -6 "$BASE/px.log"
supervisorctl start proxima >/dev/null 2>&1 || true
