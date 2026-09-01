#!/usr/bin/env bash
# Instalador de Shellngn Pro + panel de administración.
#
# Levanta ambos servicios (docker-compose.yml + docker-compose.override.yml
# se combinan solos), espera a que las credenciales del panel se generen
# solas, y al final imprime las URLs de los dos + la contraseña del panel.
# El primer login de Shellngn en sí se completa a mano en el navegador
# (ver por qué en el comentario cerca de "RESET_USER" más abajo).
#
# Uso (repo ya clonado):
#   ./install.sh
#
# Este repo es privado -- no hay una URL pública de "curl | sh". El
# equivalente para un repo privado es cloná y corré el instalador local:
#   git clone https://github.com/bullinsurance/Shellngn-Pro-ForEver.git
#   cd Shellngn-Pro-ForEver && ./install.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$SCRIPT_DIR"

log() { echo "[$(date -Iseconds)] $*"; }

set_env_var() {
    local key="$1" value="$2"
    if grep -q "^${key}=" .env 2>/dev/null; then
        # Escapa & y # -- lo único que rompería el reemplazo de sed acá.
        local escaped="${value//&/\\&}"
        sed -i "s#^${key}=.*#${key}=${escaped}#" .env
    else
        echo "${key}=${value}" >> .env
    fi
}

get_env_var() {
    grep -m1 "^${1}=" "$2" 2>/dev/null | cut -d= -f2- || true
}

echo "== Shellngn Pro + panel: instalador =="

log "Verificando requisitos (docker, docker compose)..."
command -v docker >/dev/null 2>&1 || { echo "ERROR: falta Docker." >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "ERROR: falta Docker Compose (plugin 'compose')." >&2; exit 1; }

if [ ! -f .env ]; then
    log "Creando .env desde .env.example..."
    cp .env.example .env
fi

log "Fijando PROJECT_DIR / HOST_APPS_DIR para esta máquina..."
set_env_var PROJECT_DIR "$SCRIPT_DIR"
set_env_var HOST_APPS_DIR "$PARENT_DIR"

APP_PORT="$(get_env_var APP_PORT .env)"
APP_PORT="${APP_PORT:-8080}"

INSTANCIA_NUEVA=0
if [ ! -d data ] || [ ! -f data/pro-prod.sqlite ]; then
    INSTANCIA_NUEVA=1
fi

log "Levantando shellngn + panel (docker compose up -d --build)..."
docker compose up -d --build

log "Esperando a que Shellngn responda en el puerto ${APP_PORT}..."
ok=0
for i in $(seq 1 30); do
    sleep 2
    code="$(curl -sS -o /dev/null -w '%{http_code}' "http://localhost:${APP_PORT}/" 2>/dev/null || true)"
    if [ "$code" = "200" ]; then
        ok=1
        break
    fi
done
if [ "$ok" != "1" ]; then
    echo "ERROR: Shellngn no respondió a tiempo (último código: ${code:-sin respuesta}). Revisá 'docker compose logs shellngn'." >&2
    exit 1
fi

# NOTA: probamos usar RESET_USER acá para fijar una contraseña real del
# primer usuario automáticamente, pero en pruebas falla siempre ("Reset
# user not found") sin importar el identificador usado (admin, el email
# semilla, el id) -- parece no funcionar en esta imagen. Preferimos no
# prometer una contraseña que en los hechos no se cambió: el primer login
# se completa a mano en el navegador (ver resumen al final).

# panel/.env lo escribe el contenedor del panel, que corre como root
# (necesario para leer/escribir el crontab del sistema y el socket de
# Docker sin pelear con permisos) -- por eso el archivo queda root:root
# en el host y "ubuntu" no puede leerlo directo. Lo leemos con
# "docker compose exec", que sí corre adentro como ese mismo root.
log "Esperando a que el panel genere sus credenciales (panel/.env)..."
PANEL_ENV_CONTENT=""
for i in $(seq 1 30); do
    PANEL_ENV_CONTENT="$(docker compose exec -T panel cat "${SCRIPT_DIR}/panel/.env" 2>/dev/null || true)"
    if echo "$PANEL_ENV_CONTENT" | grep -q '^PANEL_PASSWORD='; then
        break
    fi
    sleep 1
done

PANEL_USER="$(echo "$PANEL_ENV_CONTENT" | grep -m1 '^PANEL_USER=' | cut -d= -f2- || true)"
PANEL_PASSWORD="$(echo "$PANEL_ENV_CONTENT" | grep -m1 '^PANEL_PASSWORD=' | cut -d= -f2- || true)"
PANEL_PORT="$(echo "$PANEL_ENV_CONTENT" | grep -m1 '^PANEL_PORT=' | cut -d= -f2- || true)"
PANEL_PORT="${PANEL_PORT:-5058}"

HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
HOST_IP="${HOST_IP:-localhost}"

echo
echo "========================================================================"
echo " Shellngn Pro -- instalación completa"
echo "========================================================================"
echo " Shellngn:   http://${HOST_IP}:${APP_PORT}"
if [ "$INSTANCIA_NUEVA" = "1" ]; then
    echo "   Instancia nueva: no hay contraseña que darte todavía. Entrá a esa"
    echo "   URL y completá el primer login/creación de usuario ahí -- la"
    echo "   variable RESET_USER (pensada para fijarla sola) no funciona de"
    echo "   forma confiable en esta imagen, así que no se usó."
else
    echo "   (instancia existente -- usá las credenciales que ya tenías)"
fi
echo
echo " Panel de administración: http://${HOST_IP}:${PANEL_PORT}"
if [ -n "${PANEL_USER:-}" ] && [ -n "${PANEL_PASSWORD:-}" ]; then
    echo "   usuario:  ${PANEL_USER}"
    echo "   password: ${PANEL_PASSWORD}"
else
    echo "   (no se pudieron leer todavía -- mirá panel/.env en unos segundos)"
fi
echo
echo " Guardá la contraseña del panel ahora: no se vuelve a imprimir así. Si"
echo " la perdés, está en panel/.env."
echo "========================================================================"
