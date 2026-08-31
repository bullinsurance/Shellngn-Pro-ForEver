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
```

- El contenedor expone el puerto interno definido por `PORT` (por defecto
  `8080`) y se publica en el host en `APP_PORT` (por defecto también `8080`).
- Todos los datos persistentes (usuarios, credenciales cifradas, sesiones
  guardadas, licencia, llaves de cifrado, certificados TLS) viven en
  `./data`, montado dentro del contenedor en `/home/node/server/data`.
  **Sin este volumen se pierde todo al recrear el contenedor.**

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

Todo lo importante está en `./data`. Basta con respaldar ese directorio
(contenedor detenido o en caliente, es SQLite):

```bash
docker compose stop
tar -czf shellngn-backup-$(date +%Y%m%d).tar.gz data/
docker compose start
```

## Operación

```bash
# Ver logs
docker compose logs -f

# Reiniciar
docker compose restart

# Actualizar a la última imagen
docker compose pull
docker compose up -d

# Detener
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
# shellngn.com
