#!/bin/bash
# Primary box: stop the solo miner and mine on the suprnova pool instead.
# usage: switch-primary-to-pool.sh <pool_token>
set -u
TOKEN=${1:?pool token}
BASE=/root/proxima
PXBIN=/root/proxichorizo-2.0.2/proxichorizo
POOL=stratum+tcp://proxi.suprnova.cc:3355
NAME=$(hostname)

printf '%s\n' "$TOKEN" > "$BASE/pool.token"; chmod 600 "$BASE/pool.token"
[ -x "$PXBIN" ] || { echo "no px binary"; exit 1; }

echo '== stop the solo miner and consolidator =='
supervisorctl stop proxima >/dev/null 2>&1 || true
screen -S mine -X quit 2>/dev/null || true
screen -S cons -X quit 2>/dev/null || true
screen -S px -X quit 2>/dev/null || true
screen -wipe >/dev/null 2>&1 || true
sleep 3

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
if ! screen -ls 2>/dev/null | grep -q '\.dash\b'; then
  screen -dmS dash -L -Logfile \$BASE/dashboard.log python3 \$BASE/dashboard.py
fi
EOF
chmod +x "$BASE/start.sh"
"$BASE/start.sh"
sleep 20
screen -ls | sed -n '2,6p'
echo '--- px ---'
tail -8 "$BASE/px.log"
supervisorctl start proxima >/dev/null 2>&1 || true
