# Proxima mining kit — local copy

Everything needed to stand up a Proxima miner or a GPU seeker box, copied from the two
running VPSes. Same files that are deployed, not a reconstruction.

## Fast path — new GPU box

```bash
git clone <this repo> proxima-kit && cd proxima-kit
./bootstrap-seeker.sh <TOKEN>
```

That checks the cards, picks the prebuilt library for the arch (or builds one), runs the
self-test, writes `/root/proxima/{seeker,start.sh,watchdog.sh,token}` and starts one seeker
per GPU against `http://n1.de.clorecloud.net:1855`. Options:
`./bootstrap-seeker.sh <TOKEN> <MINER_URL>`.

The token is not in this repo. Get it from the miner:
`sed -n 's/^        token: //p' /root/proxima/mine/proxi.yaml`.

Deeper detail for each scenario: `NEW-GPU-QUICKSTART.md`, `PUBLIC-MINER.md`,
`SETUP-90HX.md`, and `../README-proxima-mining.md`.

| Path | Purpose |
|---|---|
| `seeker/gpu_seeker.py` | reference seeker client (protocol v2, HTTP job API), poll() patched to survive a miner restart |
| `seeker/libb2s.so` | **prebuilt sm_86 library** (RTX 30xx / CMP 90HX / Ampere), blocking-sync patch |
| `seeker/libb2s-sm75.so` | **prebuilt sm_75 library** (CMP 50HX / Turing), blocking-sync patch |
| `seeker/blake2b_search.cu` | patched CUDA kernel source (rebuild only for another arch) |
| `scripts/start.sh` | miner box (older layout): `mine`, `cons`, `gpu0/1`, `dash` |
| `scripts/start-primary.sh` | miner box (current): waits for `:8100`, then `mine`, `cons`, one seeker per GPU, `dash` |
| `scripts/start.sh` | miner box: `mine`, `cons`, `gpu0/1`, `dash` — idempotent |
| `scripts/start-seeker.sh` | seeker box: tunnel + one seeker per GPU, auto-detects the card count |
| `scripts/watchdog.sh` | calls `start.sh` every 60 s (run by supervisord) |
| `scripts/link.sh` | seeker box: keeps `ssh -L 8100` to the miner alive |
| `scripts/seeker-link.sh` | miner box: reverse tunnel to a seeker box (`ssh -R`), for hosts the miner can reach |
| `scripts/newbox-seeker.sh` | one-shot installer for a fresh seeker box (builds from source if no prebuilt lib) |
| `scripts/dashboard.py` | live status page on `:8080` |
| `scripts/status.sh` | terminal status summary (miner box) |
| `scripts/fix-gpu-libs.sh` | repairs `libnvidia-ml.so` after a broken CUDA install |
| `token` | the miner's `mine.seeker.token` — **secret** |

`libb2s.so` is only valid for **sm_86** (RTX 30xx, CMP 90HX, Ampere); `libb2s-sm75.so` is
for **sm_75** (CMP 50HX, Turing). For a 4090 build `-arch=sm_89`, for a 5090 `-arch=sm_120`
(needs CUDA ≥ 12.8). All cards in a box must share one arch for the single library.

Rebuild (only when the arch differs, or after a Proxima upgrade):

```bash
nvcc -O3 -arch=sm_86 -ccbin g++-12 -Xcompiler -fPIC -shared -o libb2s.so blake2b_search.cu
python3 gpu_seeker.py selftest     # must print "selftest passed"
python3 gpu_seeker.py bench 8      # ~4.05 GH/s per 3090, ~2.6 GH/s per 90HX
```

## Deploy a new seeker box (no key, ~5 minutes)

```bash
# on the new box
mkdir -p /root/proxima/seeker
# copy seeker/ and scripts/ there (scp from this machine), then:
cp scripts/start-seeker.sh /root/proxima/start.sh
cp scripts/watchdog.sh     /root/proxima/watchdog.sh
cp scripts/link.sh         /root/proxima/link.sh
cp token                   /root/proxima/token && chmod 600 /root/proxima/token
chmod +x /root/proxima/*.sh
# the box needs its own key that the miner authorizes:
ls /root/.ssh/miner || ssh-keygen -t ed25519 -N '' -f /root/.ssh/miner
cat /root/.ssh/miner.pub      # add this line to the miner's /root/.ssh/authorized_keys, then
/root/proxima/start.sh
# persistence
cp scripts/proxima-supervisor.conf /etc/supervisor/conf.d/proxima.conf
supervisorctl reread && supervisorctl update
```

`link.sh` assumes the miner is `n1.de.clorecloud.net:1856` and the tunnel port is 8100;
both are arguments: `link.sh [miner_host] [miner_ssh_port] [local_port]`.

Supervisor file (create it if missing):

```ini
[program:proxima]
command=/bin/bash /root/proxima/watchdog.sh
directory=/root/proxima
autostart=true
autorestart=true
startsecs=5
stopasgroup=true
killasgroup=true
stdout_logfile=/root/proxima/supervisor.out.log
stderr_logfile=/root/proxima/supervisor.err.log
```

## Deploy a new miner box (holds the key)

Same file layout, but `start.sh` (miner variant) and the `proxi` release:

```bash
mkdir -p /root/proxima/mine && cd /root/proxima/mine
# release: https://github.com/lunfardo314/proxima/releases
proxi config wallet -v -f < /dev/null       # or reuse a backed-up proxima.key
# edit proxi.yaml: api.node_url, mine.seeker.listen 127.0.0.1:8100, mine.seeker.token
/root/proxima/start.sh
```

See `../README-proxima-mining.md` for the full walkthrough, including the `proxi.yaml`
keys and the failure modes.

## Numbers measured

| Card | Count | each | total |
|---|---|---|---|
| RTX 3090 | 2 | 4.05 GH/s | 8.1 GH/s |
| CMP 90HX (sm_86) | 6 | 2.60 GH/s | 15.6 GH/s |
| CMP 50HX (sm_75) | 6 | 2.78 GH/s | 16.7 GH/s |

A 6× CMP 50HX box measured 14.6 GH/s at the miner with all six seekers running and two CPU
cores. CMP cards are Turing/Ampere with no display output and PCIe x1 — irrelevant here,
the kernel is compute-bound.

Network difficulty K 44–47 → at ~15 GH/s a transit (~95 PROX) lands roughly every
40 min–4 h, depending on K and the rest of the network.

## Handling

* `token` and `proxima.key` are secrets. Do not commit this folder to git or put it in a
  cloud-sync directory.
* A seeker box never needs `proxima.key` — only the token and its own SSH key.
