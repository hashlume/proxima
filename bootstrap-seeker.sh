#!/bin/bash
# One-command seeker setup on a fresh NVIDIA box.
#   ./bootstrap-seeker.sh <token> [miner_url]
#
#   token      the miner's mine.seeker.token
#   miner_url  default http://n1.de.clorecloud.net:1855
#
# No wallet, no key, no tunnel: the box hashes for the miner that holds the key.
# Uses the prebuilt library matching the card when possible, otherwise builds it.
set -u

TOKEN=${1:-}
URL=${2:-http://n1.de.clorecloud.net:1855}
HERE=$(cd "$(dirname "$0")" && pwd)
DEST=/root/proxima
SEEK=$DEST/seeker

if [ -z "$TOKEN" ]; then
  sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
fi
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }

say() { echo "[$(date +%H:%M:%S)] $*"; }

say "cards:"
nvidia-smi --query-gpu=index,name,compute_cap,memory.total --format=csv,noheader | sed 's/^/    /' \
  || { echo "nvidia-smi failed"; exit 1; }
cap=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d ' ')

mkdir -p "$SEEK"
if [ "$HERE" = "$DEST" ]; then
  : # cloned straight into the install dir: the file is already where it must be
else
  install -m 755 "$HERE/seeker/gpu_seeker.py" "$SEEK/gpu_seeker.py"
fi

lib=""
case "$cap" in
  7.5) lib=libb2s-sm75.so ;;
  8.6) lib=libb2s.so ;;
esac

if [ -n "$lib" ] && [ -f "$HERE/seeker/$lib" ]; then
  say "compute_cap $cap -> using prebuilt $lib"
  if [ "$HERE" = "$DEST" ] && [ "$lib" = "libb2s.so" ]; then
    : # already named right for the client
  else
    cp -f "$HERE/seeker/$lib" "$SEEK/libb2s.so"
    [ "$HERE" = "$DEST" ] || chmod 755 "$SEEK/libb2s.so"
  fi
else
  arch="sm_$(echo "$cap" | tr -d '.')"
  say "compute_cap $cap -> no prebuilt library, building $arch"
  command -v git >/dev/null 2>&1 || { apt-get update -qq; apt-get install -y -qq git; }
  if ! command -v nvcc >/dev/null 2>&1; then
    say "installing CUDA toolkit (nvcc), ~2.5 GB"
    DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-cuda-toolkit >/tmp/cuda-install.log 2>&1 || true
    nvidia-smi >/dev/null 2>&1 || {
      say "GPU access broken by the install; repairing driver symlinks"
      cd /usr/lib/x86_64-linux-gnu
      for base in libnvidia-ml libcuda; do
        real=$(ls -1 ${base}.so.[0-9]* 2>/dev/null | grep -E '\.so\.[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1)
        [ -n "$real" ] && ln -sf "$real" "${base}.so.1" && ln -sf "${base}.so.1" "${base}.so"
      done
    }
  fi
  export PATH="/usr/lib/nvidia-cuda-toolkit/bin:$PATH"
  [ -d /root/proxima-repo/.git ] || \
    git clone --depth 1 --branch v1.6.3p1 https://github.com/lunfardo314/proxima.git /root/proxima-repo
  cd /root/proxima-repo/proxi/node_cmd/mine/gpu_seeker || exit 1
  grep -q cudaDeviceScheduleBlockingSync blake2b_search.cu || \
    sed -i '/^extern "C" int gpu_setup/a\  cudaSetDeviceFlags(cudaDeviceScheduleBlockingSync);' blake2b_search.cu
  CCBIN=""
  build() { nvcc -O3 -arch="$arch" ${CCBIN:+-ccbin $CCBIN} -Xcompiler -fPIC -shared -o libb2s.so blake2b_search.cu; }
  if ! build 2>/tmp/build.err; then
    if grep -q 'unsupported GNU version' /tmp/build.err; then
      say "host gcc too new for this nvcc; installing g++-12"
      DEBIAN_FRONTEND=noninteractive apt-get install -y -qq gcc-12 g++-12 >/dev/null 2>&1 || true
      CCBIN=g++-12
      build || { tail -10 /tmp/build.err; exit 1; }
    else
      tail -10 /tmp/build.err; exit 1
    fi
  fi
  install -m 755 libb2s.so "$SEEK/libb2s.so"
  install -m 755 gpu_seeker.py "$SEEK/gpu_seeker.py"
fi

say "self-test on gpu0"
if ! ( cd "$SEEK" && CUDA_VISIBLE_DEVICES=0 python3 gpu_seeker.py selftest >/tmp/selftest.log 2>&1 ); then
  tail -4 /tmp/selftest.log | sed 's/^/    /'
  echo "SELFTEST FAILED - not starting"; exit 1
fi
tail -1 /tmp/selftest.log | sed 's/^/    /'

# a re-run on an existing box must pick up the freshly installed library
pkill -f 'gpu_seeker.py run' 2>/dev/null || true
screen -wipe >/dev/null 2>&1 || true

printf '%s\n' "$TOKEN" > "$DEST/token"
chmod 600 "$DEST/token"

cat > "$DEST/start.sh" <<EOF
#!/bin/bash
set -u
TOK=\$(cat $DEST/token)
URL=$URL
screen -wipe >/dev/null 2>&1 || true
for g in \$(seq 0 \$(( \$(nvidia-smi -L | wc -l) - 1 ))); do
  screen -ls 2>/dev/null | grep -q "\.gpu\$g\b" && continue
  CUDA_VISIBLE_DEVICES=\$g screen -dmS "gpu\$g" -L -Logfile "$DEST/gpu\$g.log" \\
    python3 "$SEEK/gpu_seeker.py" run "\$URL" "\$TOK"
done
EOF
chmod +x "$DEST/start.sh"

cat > "$DEST/watchdog.sh" <<EOF
#!/bin/bash
while true; do $DEST/start.sh >> $DEST/watchdog.log 2>&1; sleep 60; done
EOF
chmod +x "$DEST/watchdog.sh"

"$DEST/start.sh"
sleep 8
say "started:"
screen -ls | sed -n '2,10p'

# survive a reboot / dead seeker without anyone logged in
if command -v supervisorctl >/dev/null 2>&1; then
  cat > /etc/supervisor/conf.d/proxima.conf <<EOF
[program:proxima]
command=/bin/bash $DEST/watchdog.sh
directory=$DEST
autostart=true
autorestart=true
startsecs=5
stopasgroup=true
killasgroup=true
stdout_logfile=$DEST/supervisor.log
stderr_logfile=$DEST/supervisor.err.log
EOF
  supervisorctl reread >/dev/null 2>&1 || true
  supervisorctl update >/dev/null 2>&1 || true
  sleep 3
  say "supervisor: $(supervisorctl status 2>/dev/null | grep proxima || echo 'not registered')"
else
  say "no supervisorctl: screens run until the container restarts (see NEW-GPU-QUICKSTART.md §5)"
fi

say "check: tail -3 $DEST/gpu0.log"
say "miner: $URL"
