#!/usr/bin/env bash
# Arranca el panel web en segundo plano (nohup) y guarda su PID.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="$DIR/panel.pid"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "El panel ya está corriendo (PID $(cat "$PIDFILE"))."
    exit 0
fi

cd "$DIR"
nohup python3 app.py >> panel.log 2>&1 &
echo $! > "$PIDFILE"
sleep 1
echo "Panel iniciado (PID $(cat "$PIDFILE")). Log: $DIR/panel.log"
