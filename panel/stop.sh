#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="$DIR/panel.pid"

if [ ! -f "$PIDFILE" ] || ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "El panel no está corriendo."
    rm -f "$PIDFILE"
    exit 0
fi

kill "$(cat "$PIDFILE")"
rm -f "$PIDFILE"
echo "Panel detenido."
