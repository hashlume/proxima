# Swapping the RTX 3090s for CMP 90HX

Short version: **nothing needs rebuilding.** The 90HX is GA102, `sm_86`, exactly like the
3090, so `seeker/libb2s.so` (the patched, blocking-sync build in this kit) runs unchanged.

## What to expect

| | RTX 3090 | CMP 90HX |
|---|---|---|
| arch | sm_86 | sm_86 |
| measured here | 4.05 GH/s | 2.60 GH/s |
| power under this kernel | ~230 W | ~160 W |
| memory | 24 GB | 10 GB |
| display out | yes | none |
| PCIe | x16 | x1 is fine — the kernel is compute-bound |

So one 90HX is ~64 % of a 3090 for Proxima, at ~70 % of the power. Six of them (what the
Moscow box has) = 15.6 GH/s. Six on the miner box would take it from 8.1 to ~15.6 GH/s.

## Checklist when the card(s) arrive

```bash
# 1. stop the stack (screens, then the watchdog, or it will restart them)
supervisorctl stop proxima
for s in mine cons gpu0 gpu1 dash; do screen -S "$s" -X quit 2>/dev/null; done
screen -ls

# 2. install the card, then:
nvidia-smi                       # all cards listed, driver fine
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader

# 3. the prebuilt library is already sm_86 — just verify it on each card
cd /root/proxima/seeker            # or /root/proxima/repo/proxi/node_cmd/mine/gpu_seeker
for g in 0 1; do CUDA_VISIBLE_DEVICES=$g python3 gpu_seeker.py selftest | tail -1; done
for g in 0 1; do CUDA_VISIBLE_DEVICES=$g python3 gpu_seeker.py bench 8 | tail -1; done

# 4. start it all again
/root/proxima/start.sh
supervisorctl start proxima
```

## One thing to change: the GPU count

The miner box's `scripts/start.sh` starts exactly `gpu0` and `gpu1`. With a different number
of cards, use the auto-detecting variant (`scripts/start-seeker.sh`, the one the Moscow box
runs) or patch the loop:

```bash
for g in $(seq 0 $(( $(nvidia-smi -L | wc -l) - 1 ))); do
  up "gpu$g" "$MINE_DIR/gpu$g.log" env CUDA_VISIBLE_DEVICES=$g \
     python3 "$SEEKER_DIR/gpu_seeker.py" run http://127.0.0.1:8100 "$TOK"
done
```

Everything else stays: the same key, the same `proxi.yaml`, the same token, the same
tunnel, the same dashboard.

## If the box becomes "miner + 6× 90HX"

That is the best configuration — the key stays where you control it and there is no tunnel
hop. Order:

1. swap cards, `nvidia-smi` clean;
2. `proxi node mine --workers 0` + `proxi node consolidate` still run (unchanged);
3. fix the `for g in` loop above (2 → 6);
4. `supervisctl stop/start proxima`.

Expected: ~15.6 GH/s on the miner box + 15.6 GH/s from Moscow = ~31 GH/s.

## Thermal / power notes

* 90HX has no fan control via `nvidia-smi` on many mining cards; keep airflow and check
  `nvidia-smi --query-gpu=temperature.gpu`.
* Under this kernel they draw ~155–170 W each (well under the 320 W rating) because blake2b
  is compute-bound, not memory-bound.
* Six cards at ~160 W = ~1 kW plus the host; make sure the PSU and the circuit handle it.

## If you ever mix generations

A single `libb2s.so` targets one arch. Either build one binary per arch in its own directory,
or emit a fat binary:

```bash
nvcc -O3 -gencode arch=compute_86,code=sm_86 \
        -gencode arch=compute_89,code=sm_89 \
        -ccbin g++-12 -Xcompiler -fPIC -shared -o libb2s.so blake2b_search.cu
```

Keep the `selftest` habit: it hashes the same bytes on the GPU and the CPU and compares, so
a wrong-arch build is caught in seconds instead of after hours of silent zero results.
