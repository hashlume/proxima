#!/bin/bash
# Keep a local forward from this GPU box to the miner's job server, so the seeker here
# talks to 127.0.0.1:8100 and no port is opened anywhere.
# This box dials out to the miner (the miner cannot reach us on our ssh port).
#
#   usage: link.sh [miner_host] [miner_ssh_port] [local_port]
set -u
MINER=${1:-n1.de.clorecloud.net}
MPORT=${2:-1856}
LPORT=${3:-8100}
KEY=/root/.ssh/miner
LOG=/root/proxima/link.log

[ -r "$KEY" ] || { echo "error: missing $KEY" >&2; exit 1; }

while true; do
  echo "$(date -Is) forward 127.0.0.1:$LPORT -> $MINER:127.0.0.1:8100 (ssh $MPORT)" >> "$LOG"
  ssh -N -T -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes \
      -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=yes \
      -o ServerAliveInterval=20 -o ServerAliveCountMax=3 \
      -p "$MPORT" -L "127.0.0.1:$LPORT:127.0.0.1:8100" \
      "root@$MINER" 2>>"$LOG"
  echo "$(date -Is) link closed (exit $?), retrying in 10s" >> "$LOG"
  sleep 10
done
