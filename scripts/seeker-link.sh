#!/bin/bash
# Keep a reverse SSH tunnel from THIS miner box to a GPU box, so the seeker over there
# reaches the job server at 127.0.0.1:8100 without opening any port on either side.
#
#   1. one time, on the GPU box, authorize the key printed by:
#          cat /root/.ssh/seeker.pub
#      (or, if you can already log in to it: ssh-copy-id -i /root/.ssh/seeker.pub root@GPU_BOX)
#   2. on this box:
#          screen -dmS link /root/proxima/seeker-link.sh root@GPU_BOX
#
# usage: seeker-link.sh user@host [remote_port]
set -u

TARGET=${1:-}
if [ -z "$TARGET" ]; then
  awk 'NR==1{next} /^#/{sub(/^# ?/,"");print;next} {exit}' "$0"
  exit 2
fi

RPORT=${2:-8100}
KEY=/root/.ssh/seeker
LOCAL=127.0.0.1:8100
LOG=/root/proxima/seeker-link.log
TOKEN=$(sed -n 's/^        token: //p' /root/proxima/mine/proxi.yaml 2>/dev/null | head -1)

[ -r "$KEY" ] || { echo "error: missing $KEY (ssh-keygen -t ed25519 -N '' -f $KEY)" >&2; exit 1; }
[ -n "$TOKEN" ] || { echo "error: no mine.seeker.token in /root/proxima/mine/proxi.yaml" >&2; exit 1; }

echo "$(date -Is) linking $TARGET:127.0.0.1:$RPORT -> $LOCAL" | tee -a "$LOG"
echo
echo "on the GPU box, run:"
echo "  gpu_seeker.py run http://127.0.0.1:$RPORT $TOKEN"
echo

while true; do
  ssh -N -T \
      -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes \
      -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new \
      -o ServerAliveInterval=20 -o ServerAliveCountMax=3 \
      -R "127.0.0.1:$RPORT:$LOCAL" "$TARGET" 2>>"$LOG"
  rc=$?
  echo "$(date -Is) tunnel closed (exit $rc), retrying in 10s" | tee -a "$LOG"
  sleep 10
done
