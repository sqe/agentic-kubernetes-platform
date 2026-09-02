#!/usr/bin/env bash
set -euo pipefail

model=${MLX_MODEL:-mlx-community/Qwen3.8-27B-4bit}
port=${MLX_PORT:-8081}
server=${MLX_SERVER:-mlx_lm.server}

if ! command -v "$server" >/dev/null 2>&1; then
  cat >&2 <<'EOF'
mlx-lm is required. Install it in a dedicated Python 3.12 environment:
  python3.12 -m venv .venv-mlx
  .venv-mlx/bin/pip install -U mlx-lm
  MLX_SERVER=.venv-mlx/bin/mlx_lm.server bash scripts/run-mlx-gateway.sh
EOF
  exit 1
fi

available_kib=$(df -Pk "$HOME" | awk 'NR==2 {print $4}')
if (( available_kib < 20 * 1024 * 1024 )); then
  printf 'At least 20 GiB free disk is required for %s; only %d GiB is available.\n' \
    "$model" "$((available_kib / 1024 / 1024))" >&2
  exit 1
fi

exec "$server" \
  --model "$model" \
  --host 0.0.0.0 \
  --port "$port" \
  --max-tokens 256 \
  --prompt-cache-size 4 \
  --prompt-cache-bytes 4GB
