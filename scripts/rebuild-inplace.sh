#!/usr/bin/env bash
# Reconstruye ESTA instancia desde cero: tira abajo el contenedor y sus
# datos actuales, y lo levanta de nuevo como si fuera un contenedor recién
# creado -- mismo puerto, mismo nombre de contenedor -- pero conservando
# SOLO usuarios/equipos/conexiones/credenciales (las mismas tablas que
# preserva el backup semanal, ver scripts/backup-db.py) y la llave de
# cifrado que las hace desencriptables.
#
# Todo lo demás (sesiones guardadas, anuncios, licencia, certificados TLS,
# certificados SAML, secreto JWT) queda tal como lo genera la instancia
# nueva al arrancar por primera vez -- si tenías licencia, TLS o SSO SAML
# configurados, hay que volver a cargarlos después.
#
# Es seguro incluso si algo falla a mitad de camino: los datos previos
# nunca se borran hasta confirmar que la instancia recreada responde bien
# con los datos restaurados. Si cualquier paso falla, hace rollback
# automático a esos datos y vuelve a levantar el contenedor con ellos.
#
# Uso: ./rebuild-inplace.sh
set -uo pipefail
# (sin -e a propósito: los pasos riesgosos se chequean explícitamente
# para poder hacer rollback en vez de cortar el script a mitad de camino)

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
cd "$BASE_DIR"

APP_PORT="$(grep -m1 '^APP_PORT=' .env 2>/dev/null | cut -d= -f2)"
APP_PORT="${APP_PORT:-8080}"
OLD_DATA="data.old-${STAMP}"

# A partir de rollback() asumimos que "data" puede ya haber sido
# reemplazado por la instancia nueva y que "$OLD_DATA" todavía tiene los
# datos previos intactos.
rollback() {
    echo "[$(date -Iseconds)] ERROR: la recreación falló. Revirtiendo a los datos anteriores..." >&2
    echo "----- logs del contenedor que falló (para diagnóstico) -----" >&2
    docker compose logs shellngn --no-color --tail=200 >&2 2>&1 || true
    echo "----- fin logs -----" >&2
    docker compose down shellngn >/dev/null 2>&1 || true
    rm -rf data
    mv "$OLD_DATA" data
    if docker compose up -d shellngn; then
        echo "[$(date -Iseconds)] Rollback completado: la instancia sigue con los datos anteriores (sin pérdida)." >&2
    else
        echo "[$(date -Iseconds)] ERROR CRÍTICO: el rollback tampoco pudo levantar el contenedor." >&2
        echo "Los datos anteriores están intactos y a salvo en: ${BASE_DIR}/data -- revisar 'docker compose up shellngn' manualmente." >&2
    fi
    exit 1
}

wait_for_http() {
    local max_tries="${1:-20}"
    local tries=0
    local code=""
    while [ "$tries" -lt "$max_tries" ]; do
        sleep 2
        code="$(curl -sS -o /dev/null -w '%{http_code}' "http://localhost:${APP_PORT}/" 2>/dev/null || true)"
        [ "$code" = "200" ] && return 0
        tries=$((tries + 1))
    done
    echo "última respuesta: ${code:-sin respuesta}" >&2
    return 1
}

echo "[$(date -Iseconds)] Paso 1/6: deteniendo el contenedor actual..."
# Con nombre de servicio: docker compose sin argumentos afecta a TODOS los
# servicios del proyecto (incluido "panel", en docker-compose.override.yml)
# -- acá solo debe tocarse "shellngn".
if ! docker compose down shellngn; then
    echo "ERROR deteniendo el contenedor actual. Abortando: no se tocó nada." >&2
    exit 1
fi

# --- Punto de no retorno: a partir de acá los datos actuales se apartan.
echo "[$(date -Iseconds)] Paso 2/6: apartando los datos actuales (no se borran todavía)..."
if ! mv data "$OLD_DATA"; then
    echo "ERROR apartando los datos actuales. Abortando." >&2
    exit 1
fi

echo "[$(date -Iseconds)] Paso 3/6: levantando el contenedor sin datos previos (arranque en blanco)..."
if ! docker compose up -d shellngn; then
    rollback
fi

echo "[$(date -Iseconds)] Paso 4/6: esperando a que la instancia en blanco termine de inicializar (migraciones de base y llaves nuevas)..."
# No alcanza con que exista pro-prod.sqlite: el archivo aparece antes de que
# terminen de correr las migraciones (columnas que se agregan sobre la
# marcha). Recién cuando responde HTTP 200 el arranque terminó del todo.
if ! wait_for_http; then
    echo "La instancia en blanco no respondió a tiempo." >&2
    rollback
fi
if [ ! -f "data/pro-prod.sqlite" ] || [ ! -f "data/keys/private_key.pem" ]; then
    echo "La instancia en blanco respondió pero faltan data/pro-prod.sqlite o data/keys/private_key.pem." >&2
    rollback
fi
# Margen extra por si alguna migración termina justo después de que el
# servidor empieza a responder.
sleep 3

echo "[$(date -Iseconds)] Paso 5/6: deteniendo el contenedor para restaurar usuarios/equipos/conexiones/credenciales..."
if ! docker compose stop shellngn; then
    echo "ERROR deteniendo el contenedor para restaurar los datos." >&2
    rollback
fi

if ! python3 scripts/restore-filtered-db.py \
        --target-db "data/pro-prod.sqlite" \
        --source-db "${OLD_DATA}/pro-prod.sqlite"; then
    echo "ERROR restaurando usuarios/equipos/conexiones/credenciales en la base nueva." >&2
    rollback
fi

echo "[$(date -Iseconds)] Restaurando la llave de cifrado (para que las credenciales sigan siendo desencriptables)..."
if [ -f "${OLD_DATA}/keys/private_key.pem" ] && [ -f "${OLD_DATA}/keys/public_key.pem" ]; then
    # -p preserva los permisos originales tal cual (los de la instancia
    # anterior), en vez de asumir un modo fijo.
    cp -p "${OLD_DATA}/keys/private_key.pem" "data/keys/private_key.pem"
    cp -p "${OLD_DATA}/keys/public_key.pem" "data/keys/public_key.pem"
else
    echo "ERROR: no se encontró el par de llaves anterior en ${OLD_DATA}/keys/. Abortando para no dejar credenciales indescifrables." >&2
    rollback
fi

echo "[$(date -Iseconds)] Paso 6/6: levantando el contenedor con los datos restaurados..."
if ! docker compose up -d shellngn; then
    rollback
fi

echo "[$(date -Iseconds)] Verificando que responda en el puerto ${APP_PORT}..."
if ! wait_for_http; then
    echo "La instancia recreada no respondió." >&2
    rollback
fi

echo "[$(date -Iseconds)] OK: instancia recreada y respondiendo (HTTP 200), con usuarios/equipos/conexiones/credenciales restaurados."
echo "Se reseteó a valores nuevos (como una instalación en blanco): licencia, certificados TLS, certificados/config SAML, secreto JWT (sesiones activas se cierran), anuncios y logs de sesión."
echo "Los datos anteriores quedaron en '${OLD_DATA}/' por si querés confirmarlos antes de borrarlos a mano:"
echo "  rm -rf ${BASE_DIR}/${OLD_DATA}"
exit 0
