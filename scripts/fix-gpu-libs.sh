#!/bin/bash
# The container bind-mounts /usr/lib/x86_64-linux-gnu/lib{cuda,nvidia-ml}.so.580.178.04 (read-only)
# from the host. A dpkg run that tries to replace them can leave the .so.1 symlinks missing and
# nvidia-smi/CUDA fail with "couldn't find libnvidia-ml.so". This recreates them.
set -u
cd /usr/lib/x86_64-linux-gnu || exit 1
for base in libnvidia-ml libcuda; do
  real=$(ls -1 ${base}.so.580.178.04 2>/dev/null | head -1)
  if [ -n "$real" ]; then
    ln -sf "$real" "${base}.so.1"
    ln -sf "${base}.so.1" "${base}.so"
    echo "linked ${base}.so.1 -> $real"
  else
    echo "missing ${base}.so.580.178.04 (host mount gone?)" >&2
  fi
done
nvidia-smi --query-gpu=index,name,driver_version --format=csv,noheader
