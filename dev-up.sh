#!/bin/sh
# Start the helper LLM (mlx_lm.server) and tinystudio serve for a project in their own sessions,
# so they outlive the shell (or tool) that started them.
# Usage: ./dev-up.sh [project-dir] [model-id-to-load]
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
PROJECT="${1:-$ROOT/projects/home}"
UP="${2:-smol-full/1}"
export PATH="$ROOT/.venv/bin:$PATH"
export TINYSTUDIO_LLM_BASE_URL="${TINYSTUDIO_LLM_BASE_URL:-http://127.0.0.1:8080/v1}"
export TINYSTUDIO_LLM_MODEL="${TINYSTUDIO_LLM_MODEL:-mlx-community/Qwen3-4B-Instruct-2507-4bit}"
cd "$PROJECT"
spawn() {  # spawn LOGFILE CMD...  -> new session, detached from this process group
  log="$1"; shift
  "$ROOT/.venv/bin/python" - "$log" "$@" <<'PY'
import subprocess, sys
log = open(sys.argv[1], "a")
subprocess.Popen(sys.argv[2:], start_new_session=True, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, close_fds=True)
PY
}
if ! curl -sf "$TINYSTUDIO_LLM_BASE_URL/models" >/dev/null 2>&1; then
  spawn "$ROOT/.mlx-server.log" "$ROOT/.venv/bin/mlx_lm.server" --model "$TINYSTUDIO_LLM_MODEL" --port 8080
fi
if ! curl -sf http://127.0.0.1:8000/api/overview >/dev/null 2>&1; then
  spawn "$ROOT/.serve.log" "$ROOT/.venv/bin/tinystudio" serve --port 8000 --up "$UP"
fi
for _ in $(seq 1 120); do
  curl -sf http://127.0.0.1:8000/api/overview >/dev/null 2>&1 && break
  sleep 1
done
echo "web interface: http://127.0.0.1:8000/"
echo "helper LLM:    $TINYSTUDIO_LLM_BASE_URL ($TINYSTUDIO_LLM_MODEL)"
