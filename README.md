# Shellngn Pro

Cliente web de SSH/SFTP/RDP/VNC ([shellngn/pro](https://hub.docker.com/r/shellngn/pro)),
desplegado con Docker Compose. Permite administrar servidores remotos
(terminal, transferencia de archivos, escritorio remoto) desde el navegador,
con SSO SAML 2.0, 2FA (TOTP), credenciales cifradas (AES) y control de acceso
por roles/equipos.

## Arquitectura

```
Navegador ──► http://<IP-del-host>:8080 ──► contenedor shellngn-pro (puerto interno 8080)
                                                    │
                                          ./data (bind mount)
                                    (base sqlite, licencia, llaves, TLS, SAML)

Navegador ──► http://<IP-del-host>:5058 ──► contenedor shellngn-panel (red del host)
                                                    │
                              /var/run/docker.sock (maneja shellngn-pro)
                              repo + directorio padre (mismo path que en el host)
                              crontab interno (volumen panel_cron)
```

- El contenedor `shellngn` expone el puerto interno definido por `PORT`
  (por defecto `8080`) y se publica en el host en `APP_PORT` (por defecto
  también `8080`).
- Todos los datos persistentes (usuarios, credenciales cifradas, sesiones
  guardadas, licencia, llaves de cifrado, certificados TLS) viven en
  `./data`, montado dentro del contenedor en `/home/node/server/data`.
  **Sin este volumen se pierde todo al recrear el contenedor.**
- El contenedor `panel` (servicio aparte en el mismo `docker-compose.yml`,
  ver sección "Panel web" más abajo) administra al de `shellngn` desde
  afuera: backups, clones, y recrearlo desde cero.

## Requisitos previos

- Docker y Docker Compose instalados.

## Paso 1: Revisar el archivo `.env`

Ya existe un `.env` (copiado de `.env.example`) con valores por defecto
razonables. Revisa especialmente:

- `APP_PORT`: puerto en el host donde quedará expuesta la app (por defecto
  `8080`).
- `BASE_URL`: solo si vas a servir la app en un subpath detrás de un reverse
  proxy (ej. `/shellngn`).
- `CORS`: restringe a tu dominio en producción si vas a exponerlo
  públicamente (por defecto `*`).

Ver la tabla completa de variables más abajo.

## Paso 2: Levantar el servicio

```bash
docker compose up -d
```

Verifica que quedó saludable:

```bash
docker compose ps
```

## Paso 3: Acceder y cambiar la contraseña por defecto

- Acceso local: http://localhost:8080
- Acceso en LAN: http://<IP-LAN-del-host>:8080

**Credenciales por defecto: `admin` / `admin`.**

⚠️ **Cambia esta contraseña inmediatamente después del primer login**
(Configuración → Usuarios), y activa 2FA (TOTP) para la cuenta admin.

Si quedas bloqueado o necesitas resetear la contraseña sin entrar a la UI,
usa la variable `RESET_USER` (ver tabla de variables), reinicia el
contenedor una vez, y luego **quítala del `.env`**.

## Variables de entorno

| Variable      | Default | Descripción                                                        |
| ------------- | ------- | ------------------------------------------------------------------- |
| `APP_PORT`    | `8080`  | Puerto publicado en el **host** (solo usado por docker-compose)     |
| `PORT`        | `8080`  | Puerto de escucha **dentro** del contenedor                         |
| `HOST`        | `0.0.0.0` | Host de escucha dentro del contenedor                             |
| `CORS`        | `*`     | Origen permitido para CORS                                          |
| `BASE_URL`    | `/`     | Prefijo de URL (para reverse proxy en subpath)                      |
| `TLS_CERT`    | –       | Nombre de archivo del certificado en `./data/tls`                   |
| `TLS_KEY`     | –       | Nombre de archivo de la llave privada en `./data/tls`               |
| `RESET_USER`  | –       | Resetea la contraseña de un usuario: `usuario:nueva_contraseña`     |
| `LOG_LEVEL`   | `info`  | Verbosidad: `error`, `warn`, `info`, `http`, `debug`                |
| `LOG_TO_FILE` | `false` | Habilita logging a archivo con rotación diaria                      |
| `LOG_DIR`     | `./logs`| Directorio de logs dentro del contenedor (requiere `LOG_TO_FILE=true`) |

## HTTPS / exponer públicamente

El contenedor sirve **HTTP** internamente. Para producción, la terminación
TLS debe hacerla algo delante del contenedor. Dos opciones típicas en este
servidor:

### Opción A — Cloudflare Tunnel (mismo patrón que `n8n` en este server)

Este host ya tiene `cloudflared` instalado y corriendo como servicio del
sistema (fuera de Docker) para otros stacks. Para exponer Shellngn con un
subdominio propio:

1. Cloudflare Zero Trust → **Networks → Tunnels** → tu tunnel →
   **Public Hostname** → agregar hostname (ej. `shellngn.deltoro.co`)
   apuntando a `http://localhost:8080`.
2. Listo — no hace falta abrir puertos ni tocar este `docker-compose.yml`.

**Importante:** Shellngn maneja terminales interactivas por WebSocket
(similar a n8n con sus webhooks). Cloudflare Tunnel soporta WebSockets de
forma nativa, así que no se requiere configuración adicional para eso.

### Opción B — Reverse proxy propio (Traefik / Nginx Proxy Manager / Caddy / Nginx)

Cualquiera de estos sirve, mientras se garantice el soporte de WebSocket
(necesario para las sesiones de terminal). Ejemplo con Nginx:

```nginx
location / {
    proxy_pass http://localhost:8080;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection 'upgrade';
    proxy_set_header Host $host;
    proxy_cache_bypass $http_upgrade;
}
```

## Backups

Hay dos scripts en `scripts/`, para dos necesidades distintas:

### Backup filtrado (auditoría, no restauración directa)

`scripts/backup-db.py` hace una copia en caliente de la base y se queda
solo con usuarios, equipos, conexiones y credenciales (descarta logs de
sesión, anuncios y metadata interna). Corre automáticamente según el
horario que tengas configurado en el panel (en esta instancia: mensual,
día 1 a las 3 AM — `docker exec shellngn-panel crontab -l` para
verlo/cambiarlo) y guarda en `backups/`, con rotación automática
(conserva los últimos 5 por defecto).

```bash
python3 scripts/backup-db.py --with-keys --keep 5
```

### Clon completo (para desplegar en otro contenedor/host)

`scripts/export-clone.sh` empaqueta todo lo necesario (`data/` completo +
`docker-compose.yml` + `.env`) para levantar una instancia nueva que
arranca ya con los mismos usuarios, equipos, conexiones y credenciales —
no una instalación en blanco. Detiene el contenedor un momento para
garantizar consistencia y lo vuelve a levantar solo.

```bash
# En el servidor origen:
./scripts/export-clone.sh
# genera clones/shellngn-clone-<fecha>.tar.gz

# Copiar el .tar.gz al servidor/máquina destino, y ahí:
./scripts/import-clone.sh shellngn-clone-<fecha>.tar.gz ~/shellngn-clone [puerto_host]
```

`import-clone.sh` asigna automáticamente un `CONTAINER_NAME` único y el
puerto que le indiques, así se puede correr en paralelo al original en el
mismo host sin choques (probado: instancia clon en :8081 con los mismos
4 usuarios / 1 equipo / 37 conexiones que el original en :8080).

### Panel web

`panel/` tiene un panel liviano (sin dependencias, solo Python estándar)
para hacer todo esto desde el navegador en vez de la terminal:

- Backup manual con un botón.
- Programar la frecuencia automática (cada hora / diaria / semanal /
  mensual / cron personalizado).
- Generar una copia completa y desplegarla como contenedor nuevo,
  eligiendo nombre y puerto.
- Ver qué instancias `shellngn-pro*` están corriendo.
- Recrear esta instancia desde cero (ver sección de abajo).

Corre como **contenedor aparte, definido en el mismo `docker-compose.yml`**
(servicio `panel`) — un solo `docker compose up -d` levanta la app y el
panel juntos:

```bash
docker compose up -d          # levanta shellngn-pro Y el panel
docker compose ps             # los dos deberían salir "Up"
docker compose logs -f panel  # logs del panel (reemplaza a panel.log)
```

Queda escuchando en `http://<IP-del-host>:5058` (mismo puerto que antes,
`PANEL_PORT` en `panel/.env`).

**Cómo puede manejar contenedores y cron desde adentro de un contenedor:**
el servicio `panel` en `docker-compose.yml` monta tres cosas:

1. `/var/run/docker.sock` del host → puede correr `docker`/`docker compose`
   contra el Docker real del host (no uno anidado).
2. El directorio **padre** de este repo (`HOST_APPS_DIR` en `.env`, ej.
   `/home/ubuntu/server`) montado en el **mismo path absoluto** dentro del
   contenedor. Esto es necesario para que las rutas relativas de
   `docker-compose.yml` (`./data`, etc.) se resuelvan igual estando el CLI
   adentro del contenedor que estando en el host, y para que "Desplegar
   copia" pueda seguir creando `../shellngn-<nombre>` como antes.
3. `network_mode: host` — comparte la red del host en vez de tener la suya
   propia, para que `localhost:${APP_PORT}` (que usa `rebuild-inplace.sh`
   para chequear que la instancia responde) apunte al puerto real
   publicado por el servicio `shellngn`, no a una red aislada del panel.

`PROJECT_DIR` y `HOST_APPS_DIR` van en `.env` con los paths absolutos
reales de este repo en tu servidor — revisalos si movés el proyecto de
lugar.

Esto no es más permiso del que ya tenía el panel corriendo como proceso
del host bajo el usuario `ubuntu` (miembro del grupo `docker`) — solo que
ahora es explícito. Sigue aplicando: **no lo expongas a internet sin un
reverse proxy con su propio TLS/autenticación** — quien entre al panel
puede crear/desplegar/recrear contenedores, leer backups con credenciales,
y en la práctica tiene control total del host vía el socket de Docker.

Las credenciales (HTTP Basic Auth) están en `panel/.env`, generadas la
primera vez que corrió — se preservan igual que antes al pasar a Docker
(el archivo no se pisa).

Los horarios de backup/recreación automática (que antes vivían en el
`crontab` del usuario `ubuntu` del host) ahora viven en el **crontab
interno del contenedor del panel**, en un volumen (`panel_cron`) que
sobrevive a que se recree el contenedor. Se administran igual, desde la
misma UI del panel.

`panel/start.sh` / `panel/stop.sh` siguen ahí como alternativa manual (sin
Docker) para debug puntual, pero ya no hace falta usarlos en el uso
normal.

### Recrear esta instancia desde cero (mismo puerto)

`scripts/rebuild-inplace.sh` (también disponible como botón en el panel,
sección "Zona de peligro", y programable ahí mismo para que corra solo)
tira abajo el contenedor actual y lo levanta de nuevo como si fuera un
contenedor recién creado &mdash; mismo puerto, mismo nombre de contenedor
&mdash; pero conservando **solo** usuarios, equipos, conexiones y
credenciales, y reseteando todo lo demás a los valores por defecto de una
instalación nueva.

**Qué se conserva** (tablas de la base sqlite):
`Users`, `Teams`, `Team_Users`, `Devices`, `DevicesTrees`, `Identities`,
`User_Device_Identity` &mdash; las mismas que preserva el backup filtrado
(ver más abajo) &mdash; más `Organizations` (nombre de la
organización y config de SSO) y el par de llaves de cifrado
(`data/keys/{private_key,public_key}.pem`).

`Organizations` no es prescindible aunque el backup de auditoría la
descarte: guarda un valor cifrado con ese mismo par de llaves que el
servidor usa para autoverificarse al arrancar. Si se restaura el par de
llaves viejo pero se deja esa fila con el valor cifrado por un par nuevo,
el servidor no puede descifrarlo y no levanta (`Could not init safe` /
`RSA OAEP decoding error` en los logs) &mdash; por eso viaja junto con las
llaves.

**Qué se recrea desde cero:** licencia, certificados TLS, certificados y
configuración SAML, secreto JWT (esto cierra las sesiones activas),
anuncios y logs de sesión. Si tenías algo de eso configurado, hay que
volverlo a cargar después de recrear.

**Cómo lo hace, paso a paso:**
1. Baja el contenedor y aparta `data/` (sin borrarla) a `data.old-<fecha>/`.
2. Levanta el contenedor **sin** datos previos → arranca en blanco (base,
   llaves y config nuevas), y espera a que responda HTTP 200 &mdash; no
   alcanza con que aparezca el archivo de la base, porque las migraciones
   siguen corriendo un rato después de que el archivo existe.
3. Lo detiene y corre `scripts/restore-filtered-db.py`, que copia sobre esa
   base en blanco solo las tablas de la lista de arriba, tomándolas de la
   base anterior (recalculando también los contadores de autoincrement).
4. Copia el par de llaves de cifrado original sobre el nuevo.
5. Levanta el contenedor de nuevo y espera a que responda HTTP 200.

Es seguro aunque falle a mitad de camino: los datos previos nunca se
borran hasta confirmar que la instancia recreada responde bien con los
datos restaurados. Si cualquier paso falla, imprime los logs del
contenedor que falló (para poder ver la causa) y hace **rollback
automático**: vuelve a levantar el contenedor con los datos de antes tal
cual estaban, sin pérdida. Los datos previos quedan en `data.old-<fecha>/`
después de una corrida exitosa, para poder confirmarlos antes de
borrarlos a mano (no se borran solos).

```bash
./scripts/rebuild-inplace.sh
```

En el panel, este botón pide escribir `RECREAR` para habilitarse — es la
acción más destructiva del panel (aunque reversible por el rollback).

**Frecuencia automática de recreación:** en la misma sección del panel
hay un selector igual al del backup (Desactivada / Cada hora / Diaria /
Semanal / Mensual / Personalizada con cron), que programa
`rebuild-inplace.sh` en el crontab **interno del contenedor del panel**
(no en el del host, ver sección "Panel web") sin afectar otras tareas.
También pide escribir `RECREAR` para guardar el horario. El log de cada
corrida automática queda en `panel/rebuild-scheduled.log`. En esta
instancia está programado semanal, domingos 3 AM
(`docker exec shellngn-panel crontab -l` para verlo/cambiarlo a mano si
hace falta). Es exactamente el mismo script y el mismo flujo de arriba
(con su mismo rollback automático si algo sale mal) — no hay una versión
"automática" distinta que se comporte diferente.

El contenedor del panel se levanta solo si el servidor reinicia gracias a
`restart: unless-stopped` en `docker-compose.yml` (ya no depende de una
entrada `@reboot` en el cron del host).

## Operación

Sin especificar servicio, estos comandos aplican a `shellngn` y `panel`
juntos. Agregá el nombre del servicio al final para apuntar a uno solo
(ej. `docker compose logs -f shellngn`).

```bash
# Ver logs
docker compose logs -f

# Reiniciar
docker compose restart

# Actualizar shellngn-pro a la última imagen
docker compose pull shellngn
docker compose up -d

# Reconstruir el panel después de tocar panel/Dockerfile o docker-entrypoint.sh
docker compose build panel && docker compose up -d panel

# Detener todo
docker compose down
```

## Notas de seguridad

- Cambia la contraseña `admin` por defecto de inmediato.
- Activa 2FA (TOTP) para todas las cuentas administrativas.
- Si expones el servicio a internet, restringe `CORS` a tu dominio real en
  lugar de `*`.
- Configura SSO SAML 2.0 si tu organización ya usa Azure AD / Okta / Google
  Workspace / OneLogin / ADFS, para centralizar autenticación y evitar
  contraseñas locales.
- Restringe visibilidad de servidores por equipo (Team-Based Access) y usa
  roles (Admin / Team Lead / User) según el principio de menor privilegio.
- El contenedor `panel` monta `/var/run/docker.sock` y corre como root
  adentro suyo: quien entre ahí tiene control total del host (puede crear
  contenedores privilegiados, leer cualquier archivo montado, etc.), no
  solo de `shellngn-pro`. Mismo nivel de acceso que tenía como proceso del
  host, pero explícito acá. No lo expongas a internet sin reverse proxy +
  TLS + autenticación propia, además del Basic Auth que ya trae.
# shellngn.com
# Shellngn-Pro-ForEver
