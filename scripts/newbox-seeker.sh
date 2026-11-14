#!/bin/bash
# Run this ON the new GPU box. Builds Proxima's CUDA nonce seeker and starts one process
# per GPU, pointed at a miner's job server.
#
# The miner side must be configured with mine.seeker.listen/token and a reverse tunnel
# (/root/proxima/seeker-link.sh on the miner box) so that the job server appears here at
# 127.0.0.1:$PORT. No wallet key is needed on this machine.
#
# usage: newbox-seeker.sh <token> [port] [git_tag]
#   token    the miner's mine.seeker.token
#   port     remote tunnel port, default 8100
#   git_tag  Proxima release tag, default v1.6.3p1
set -u

TOKEN=${1:-}
PORT=${2:-8100}
TAG=${3:-v1.6.3p1}
BASE=/root/proxima
REPO=$BASE/repo
DIR=$REPO/proxi/node_cmd/mine/gpu_seeker

if [ -z "$TOKEN" ]; then
  awk 'NR==1{next} /^#/{sub(/^# ?/,"");print;next} {exit}' "$0"
  exit 2
fi

say() { echo "[$(date +%H:%M:%S)] $*"; }

fix_gpu_libs() {
  # A distro CUDA install can strip the container's read-only host driver symlinks.
  cd /usr/lib/x86_64-linux-gnu || return 0
  for base in libnvidia-ml libcuda; do
    real=$(ls -1 ${base}.so.[0-9]* 2>/dev/null | grep -E '\.so\.[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1)
    if [ -n "$real" ]; then
      ln -sf "$real" "${base}.so.1"
      ln -sf "${base}.so.1" "${base}.so"
    fi
  done
}

# ---- 0. sanity ---------------------------------------------------------------
[ "$(id -u)" = 0 ] || { say "run as root"; exit 1; }
say "card check:"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader | sed 's/^/    /' \
  || { say "nvidia-smi failed"; exit 1; }

# ---- 1. packages -------------------------------------------------------------
need=""
for t in git curl make screen python3 gcc; do
  command -v "$t" >/dev/null 2>&1 || need="$need $([ "$t" = gcc ] && echo build-essential || echo "$t")"
done
if [ -n "$need" ]; then
  say "installing:$need"
  DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1 || true
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $need > /tmp/apt-base.log 2>&1 || true
fi

if ! command -v nvcc >/dev/null 2>&1; then
  say "installing CUDA toolkit (nvcc), pulls ~2.5 GB"
  DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-cuda-toolkit > /tmp/cuda-install.log 2>&1 || true
  if ! nvidia-smi >/dev/null 2>&1; then
    say "GPU access broken by the install; recreating driver symlinks"
    fix_gpu_libs
  fi
fi
command -v nvcc >/dev/null 2>&1 || { say "nvcc still missing; see /tmp/cuda-install.log"; exit 1; }

export PATH="/usr/lib/nvidia-cuda-toolkit/bin:$PATH"
say "nvcc: $(nvcc --version | grep -o 'release [0-9.]*')"

# ---- 2. source ---------------------------------------------------------------
mkdir -p "$BASE"
if [ -d "$REPO/.git" ]; then
  say "repo present at $REPO"
else
  say "cloning $TAG"
  if ! git clone --depth 1 --branch "$TAG" https://github.com/lunfardo314/proxima.git "$REPO" > /tmp/clone.log 2>&1; then
    say "clone failed"; tail -5 /tmp/clone.log; exit 1
  fi
fi
[ -f "$DIR/blake2b_search.cu" ] || { say "missing $DIR/blake2b_search.cu"; exit 1; }

# ---- 3. arch ---------------------------------------------------------------
name=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)
case "$name" in
  *"RTX 50"*) ARCH=sm_120 ;;
  *"RTX 40"*|*L40*|*"L4"*) ARCH=sm_89 ;;
  *"RTX 30"*) ARCH=sm_86 ;;
  *A100*) ARCH=sm_80 ;;
  *H100*) ARCH=sm_90 ;;
  *T4*) ARCH=sm_75 ;;
  *) ARCH=sm_86; say "unknown card '$name', defaulting to $ARCH" ;;
esac
say "card '$name' -> $ARCH"

# ---- 4. build ---------------------------------------------------------------
cd "$DIR" || exit 1
CCBIN=""
build() { nvcc -O3 -arch=$ARCH ${CCBIN:+-ccbin $CCBIN} -Xcompiler -fPIC -shared \
            -o libb2s.so blake2b_search.cu; }

say "building libb2s.so"
if ! build 2>/tmp/build.err; then
  if grep -q 'unsupported GNU version' /tmp/build.err; then
    say "host gcc too new for this nvcc; installing g++-12 and retrying"
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq gcc-12 g++-12 >/dev/null 2>&1 || true
    CCBIN=g++-12
    if ! build; then say "build failed"; exit 1; fi
  else
    tail -10 /tmp/build.err; say "build failed"; exit 1
  fi
fi
[ -f libb2s.so ] || { say "libb2s.so not produced"; exit 1; }
say "built $(du -h libb2s.so | cut -f1) libb2s.so"

# ---- 5. verify -------------------------------------------------------------
say "selftest (must end with 'selftest passed')"
if ! python3 gpu_seeker.py selftest > /tmp/selftest.log 2>&1; then
  tail -4 /tmp/selftest.log | sed 's/^/    /'
  say "SELFTEST FAILED - do not mine with this build"
  exit 1
fi
tail -1 /tmp/selftest.log | sed 's/^/    /'

n=$(nvidia-smi -L | wc -l)
say "bench 6s per GPU"
for i in $(seq 0 $((n-1))); do
  printf '    gpu%s: ' "$i"
  CUDA_VISIBLE_DEVICES=$i python3 gpu_seeker.py bench 6 2>&1 | tail -1
done

# ---- 6. run ----------------------------------------------------------------
say "starting $n seeker(s) -> 127.0.0.1:$PORT"
for i in $(seq 0 $((n-1))); do
  s="gpu$i"
  if screen -ls 2>/dev/null | grep -q "\.$s\b"; then say "$s already running"; continue; fi
  CUDA_VISIBLE_DEVICES=$i screen -dmS "$s" -L -Logfile "$BASE/$s.log" \
    python3 "$DIR/gpu_seeker.py" run "http://127.0.0.1:$PORT" "$TOKEN"
done
sleep 6
screen -ls | sed -n '2,9p'
say "logs: $BASE/gpu*.log — a 'result refused' line means build/tag does not match the miner"
say "on the miner box check: tail -5 /root/proxima/mine/mine.log"
