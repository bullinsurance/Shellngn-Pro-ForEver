#!/usr/bin/env bash
# Despliega un Shellngn Pro nuevo (contenedor nuevo, en este host u otro)
# a partir de un paquete generado por export-clone.sh. El contenedor nuevo
# arranca ya con los usuarios, equipos, conexiones y credenciales del
# original — no es una instalación en blanco.
#
# Uso: ./import-clone.sh <paquete.tar.gz> <directorio_destino> [puerto_host]
#
# Ejemplos:
#   ./import-clone.sh shellngn-clone-20260831-150000.tar.gz ~/shellngn-clone
#   ./import-clone.sh shellngn-clone-20260831-150000.tar.gz ~/shellngn-clone 8081
set -euo pipefail

BUNDLE="${1:?Uso: import-clone.sh <paquete.tar.gz> <directorio_destino> [puerto_host]}"
DEST_DIR="${2:?Uso: import-clone.sh <paquete.tar.gz> <directorio_destino> [puerto_host]}"
APP_PORT="${3:-8080}"

if [ ! -f "$BUNDLE" ]; then
    echo "ERROR: no se encontró el paquete $BUNDLE" >&2
    exit 1
fi

if [ -e "$DEST_DIR" ] && [ -n "$(ls -A "$DEST_DIR" 2>/dev/null)" ]; then
    echo "ERROR: $DEST_DIR ya existe y no está vacío. Elegí otro directorio destino para no pisar nada." >&2
    exit 1
fi

mkdir -p "$DEST_DIR"
echo "[$(date -Iseconds)] Extrayendo $BUNDLE en $DEST_DIR..."
tar -xzf "$BUNDLE" -C "$DEST_DIR"

# Permisos restrictivos: la data incluye credenciales cifradas, licencia y
# la llave privada usada para desencriptarlas.
chmod 600 "$DEST_DIR/data/license.key" 2>/dev/null || true
chmod 600 "$DEST_DIR/data/keys/private_key.pem" 2>/dev/null || true
chmod 700 "$DEST_DIR/data/keys" 2>/dev/null || true

# Ajusta el puerto publicado en el host y el nombre del contenedor para no
# chocar con la instancia original si se despliega en la misma máquina.
CONTAINER_NAME="shellngn-pro-$(basename "$DEST_DIR" | tr -c 'a-zA-Z0-9_.-' '-')"

if grep -q '^APP_PORT=' "$DEST_DIR/.env"; then
    sed -i "s/^APP_PORT=.*/APP_PORT=${APP_PORT}/" "$DEST_DIR/.env"
else
    echo "APP_PORT=${APP_PORT}" >> "$DEST_DIR/.env"
fi

if grep -q '^CONTAINER_NAME=' "$DEST_DIR/.env"; then
    sed -i "s/^CONTAINER_NAME=.*/CONTAINER_NAME=${CONTAINER_NAME}/" "$DEST_DIR/.env"
else
    echo "CONTAINER_NAME=${CONTAINER_NAME}" >> "$DEST_DIR/.env"
fi

echo "[$(date -Iseconds)] Levantando el contenedor nuevo en el puerto ${APP_PORT}..."
cd "$DEST_DIR"
docker compose up -d

sleep 5
docker compose ps

echo
echo "Listo. Deberías poder entrar con los mismos usuarios/contraseñas del original en:"
echo "  http://localhost:${APP_PORT}"
echo
echo "Revisá $DEST_DIR/.env (CORS, BASE_URL, TLS, etc.) si el destino final necesita otros valores."
