#!/usr/bin/env bash
set -euo pipefail

model=${MLX_VISION_MODEL:-mlx-community/Qwen3-VL-4B-Instruct-4bit}
port=${MLX_VISION_PORT:-8082}
server=${MLX_VISION_SERVER:-.venv-mlx/bin/mlx_vlm.server}

if [[ ! -x "$server" ]]; then
  cat >&2 <<'EOF'
mlx-vlm is required in the Python 3.12 MLX environment:
  .venv-mlx/bin/pip install -U mlx-vlm
  bash scripts/run-mlx-vision.sh
EOF
  exit 1
fi

exec "$server" --model "$model" --host 0.0.0.0 --port "$port"
