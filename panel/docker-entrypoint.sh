#!/bin/sh
# Arranca cron (para los horarios de backup / recreación automática que
# administra el panel -- antes vivían en el crontab del host, ahora viven
# en el crontab de este contenedor, ver README) y despues el panel mismo.
set -e

: "${PROJECT_DIR:?PROJECT_DIR debe estar seteado (ver docker-compose.yml / .env)}"

crond -b -l 8

exec python3 "${PROJECT_DIR}/panel/app.py"
