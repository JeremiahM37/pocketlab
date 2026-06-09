#!/usr/bin/env bash
# Start/stop a pocketlab instance for the .verify.yaml http + ui checks.
# Self-contained: uses the example config so `verify` needs no external service.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${POCKETLAB_VERIFY_PORT:-8838}"
PIDFILE="/tmp/pocketlab-verify.pid"
LOG="/tmp/pocketlab-verify.log"

start() {
  stop  # ensure a clean slate
  local py="$DIR/.venv/bin/pocketlab"
  [ -x "$py" ] || py="pocketlab"
  cd "$DIR"
  nohup "$py" --config "$DIR/pocketlab.example.yaml" --host 127.0.0.1 --port "$PORT" >"$LOG" 2>&1 &
  echo $! >"$PIDFILE"
  for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
      echo "pocketlab healthy on :$PORT"; return 0
    fi
    sleep 0.3
  done
  echo "pocketlab did not become healthy; log:"; cat "$LOG"; return 1
}

stop() {
  [ -f "$PIDFILE" ] && kill "$(cat "$PIDFILE")" 2>/dev/null
  command -v fuser >/dev/null && fuser -k "${PORT}/tcp" 2>/dev/null
  rm -f "$PIDFILE"
  sleep 0.4
  echo "stopped"
}

case "${1:-start}" in
  start) start ;;
  stop)  stop ;;
  *) echo "usage: $0 {start|stop}"; exit 2 ;;
esac
