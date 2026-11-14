# New GPU box → mining in 3 commands (seeker only, no wallet)

Miner (already running): **http://n1.de.clorecloud.net:1855**
Token: **05c83b2b3cd40281e89e8ddd469c9d01**

No key, no wallet, no tunnel. The box only hashes for the miner that holds the key.

---

## 0. Check the card's compute capability

```bash
nvidia-smi --query-gpu=index,name,compute_cap --format=csv,noheader
```

Pick the prebuilt library by the `compute_cap` column:

| compute_cap | card | file to copy |
|---|---|---|
| 7.5 | CMP 50HX / 40HX / 30HX, T4 | `seeker/libb2s-sm75.so` |
| 8.6 | CMP 90HX, RTX 30xx, A10 | `seeker/libb2s.so` |
| 8.9 | RTX 40xx, L4/L40 | build (step 4) |
| 12.0 | RTX 50xx | build (step 4) |

A library only runs on the arch it was built for. Mixed boxes need a rebuild (step 4).

## 1. Copy the two files from your Windows machine

```powershell
scp -P <SSH_PORT> "D:\pearl\proxima-kit\seeker\gpu_seeker.py"      root@<HOST>:/root/seeker/
scp -P <SSH_PORT> "D:\pearl\proxima-kit\seeker\libb2s-sm75.so"     root@<HOST>:/root/seeker/libb2s.so
#   ^ use libb2s.so (the other file) for compute_cap 8.6
```

The library must be named exactly `libb2s.so` and sit next to `gpu_seeker.py`.

## 2. Start one seeker per GPU

```bash
ssh -p <SSH_PORT> root@<HOST>
mkdir -p /root/seeker && cd /root/seeker
TOK=05c83b2b3cd40281e89e8ddd469c9d01
N=$(nvidia-smi -L | wc -l)
for g in $(seq 0 $((N-1))); do
  CUDA_VISIBLE_DEVICES=$g screen -dmS gpu$g -L -Logfile /root/gpu$g.log \
    python3 /root/seeker/gpu_seeker.py run http://n1.de.clorecloud.net:1855 "$TOK"
done
sleep 12; tail -3 /root/gpu0.log; screen -ls
```

Expected: `job <id>: slot <n>, K <44..47>, message 441 bytes` for every card.

## 3. Verify it counts

```bash
grep -c '^job' /root/gpu0.log          # grows every ~10 s
tail -5 /root/gpu1.log                 # a card that is working logs jobs
```

On the miner side the total hashrate should rise by the box's contribution.

---

## 4. No matching prebuilt library (8.9 / 12.0 / other)

```bash
apt-get update && apt-get install -y build-essential git screen python3
command -v nvcc || DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-cuda-toolkit gcc-12 g++-12
export PATH=/usr/lib/nvidia-cuda-toolkit/bin:$PATH

git clone --depth 1 --branch v1.6.3p1 https://github.com/lunfardo314/proxima.git /root/proxima-repo
cd /root/proxima-repo/proxi/node_cmd/mine/gpu_seeker

# block instead of spin (matters with many seekers on few cores)
sed -i '/^extern "C" int gpu_setup/a\  cudaSetDeviceFlags(cudaDeviceScheduleBlockingSync);' blake2b_search.cu

# sm_75 = 7.5, sm_86 = 8.6, sm_89 = 8.9, sm_120 = 12.0, sm_80/90 = A100/H100
nvcc -O3 -arch=sm_89 -ccbin g++-12 -Xcompiler -fPIC -shared -o libb2s.so blake2b_search.cu

python3 gpu_seeker.py selftest          # MUST print "selftest passed"
python3 gpu_seeker.py bench 8           # GH/s for this card
cp gpu_seeker.py libb2s.so /root/seeker/
```

Then repeat step 2.

---

## 5. Survive a reboot (optional, 2 files)

`/root/seeker/start.sh`:

```bash
#!/bin/bash
set -u
TOK=05c83b2b3cd40281e89e8ddd469c9d01
cd /root/seeker || exit 1
screen -wipe >/dev/null 2>&1 || true
for g in $(seq 0 $(( $(nvidia-smi -L | wc -l) - 1 ))); do
  screen -ls 2>/dev/null | grep -q "\.gpu$g\b" && continue
  CUDA_VISIBLE_DEVICES=$g screen -dmS "gpu$g" -L -Logfile "/root/gpu$g.log" \
    python3 /root/seeker/gpu_seeker.py run http://n1.de.clorecloud.net:1855 "$TOK"
done
```

`/root/seeker/watchdog.sh`:

```bash
#!/bin/bash
while true; do /root/seeker/start.sh >> /root/seeker/watchdog.log 2>&1; sleep 60; done
```

`/etc/supervisor/conf.d/proxima.conf`:

```ini
[program:proxima]
command=/bin/bash /root/seeker/watchdog.sh
autostart=true
autorestart=true
startsecs=5
stopasgroup=true
killasgroup=true
stdout_logfile=/root/seeker/supervisor.log
stderr_logfile=/root/seeker/supervisor.err.log
```

```bash
chmod +x /root/seeker/*.sh
supervisorctl reread && supervisorctl update && supervisorctl status | grep proxima
```

---

## Notes

* **CUDA only.** AMD/Intel cards need an OpenCL kernel that does not exist yet.
* Keep the token private; it is the only thing standing between the public port and abuse.
* If the miner restarts, seekers reconnect by themselves — but a seeker started *before*
  the miner binds `:8100` can wedge (patched client in the kit retries; don't use the
  upstream copy).
* Watch GPU temps (`nvidia-smi --query-gpu=index,temperature.gpu,power.draw --format=csv`).
  Under this kernel cards draw far less than their rating (~178 W on a 50HX).
