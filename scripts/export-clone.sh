#!/usr/bin/env bash
# Empaqueta todo lo necesario para levantar un Shellngn Pro nuevo (en otro
# contenedor/host) ya con los usuarios, equipos, conexiones y credenciales
# de esta instancia. No es el backup filtrado semanal (ese solo sirve para
# auditar) — este paquete es un clon completo y funcional.
#
# Uso: ./export-clone.sh [directorio_salida]
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-$BASE_DIR/clones}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BUNDLE="$OUT_DIR/shellngn-clone-${STAMP}.tar.gz"

mkdir -p "$OUT_DIR"

echo "[$(date -Iseconds)] Deteniendo el contenedor para garantizar un clon consistente..."
cd "$BASE_DIR"
# Con nombre de servicio: sin esto, "docker compose stop" para TAMBIÉN al
# panel (docker-compose.override.yml, en el mismo proyecto) -- si este
# script corre disparado desde el panel, se mataría a sí mismo a mitad de
# camino y nunca llegaría a "docker compose start" de más abajo.
docker compose stop shellngn

echo "[$(date -Iseconds)] Empaquetando data/, docker-compose.yml y .env..."
tar -czf "$BUNDLE" \
    -C "$BASE_DIR" \
    data \
    docker-compose.yml \
    .env.example \
    .env

echo "[$(date -Iseconds)] Reiniciando el contenedor original..."
docker compose start shellngn

chmod 600 "$BUNDLE"
echo "[$(date -Iseconds)] Listo: $BUNDLE"
echo
echo "Copialo al servidor/máquina destino (scp/rsync) y ahí corré:"
echo "  ./import-clone.sh $(basename "$BUNDLE") <directorio_destino>"
