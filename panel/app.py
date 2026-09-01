#!/usr/bin/env python3
"""Panel web para administrar backups y copias de Shellngn Pro.

Sin dependencias externas (solo librería estándar de Python). Permite,
desde el navegador:
  - Disparar un backup filtrado (usuarios/equipos/conexiones) manualmente.
  - Ver/editar la frecuencia con la que corre automáticamente (cron).
  - Generar un paquete de clon completo y desplegarlo como contenedor nuevo
    en el puerto que elijas.

Protegido con HTTP Basic Auth (credenciales en panel/.env, autogeneradas
la primera vez que corre). Pensado para uso en LAN / detrás de un reverse
proxy con su propio TLS -- no lo expongas directo a internet.

Uso:
    python3 app.py
"""
from __future__ import annotations

import base64
import html
import json
import re
import secrets
import subprocess
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PANEL_DIR = Path(__file__).resolve().parent
BASE_DIR = PANEL_DIR.parent
BACKUPS_DIR = BASE_DIR / "backups"
CLONES_DIR = BASE_DIR / "clones"
SCRIPTS_DIR = BASE_DIR / "scripts"
ENV_FILE = PANEL_DIR / ".env"

CRON_MARKER = "# shellngn-panel:backup"
REBUILD_MARKER = "# shellngn-panel:rebuild"

FREQUENCY_PRESETS = {
    "hourly": "0 * * * *",
    "daily": "0 3 * * *",
    "weekly": "0 3 * * 0",
    "monthly": "0 3 1 * *",
}

CRON_RE = re.compile(
    r"^(\*|[0-9,\-/]+)\s+(\*|[0-9,\-/]+)\s+(\*|[0-9,\-/]+)\s+(\*|[0-9,\-/]+)\s+(\*|[0-9,\-/]+)$"
)
NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")


# --------------------------------------------------------------------------
# Configuración / credenciales
# --------------------------------------------------------------------------

def load_or_init_env() -> dict:
    if ENV_FILE.exists():
        cfg = {}
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
        if "PANEL_USER" in cfg and "PANEL_PASSWORD" in cfg:
            cfg.setdefault("PANEL_PORT", "5058")
            return cfg

    password = secrets.token_hex(12)
    cfg = {"PANEL_USER": "admin", "PANEL_PASSWORD": password, "PANEL_PORT": "5058"}
    ENV_FILE.write_text(
        "# Credenciales del panel web de Shellngn (Basic Auth).\n"
        "# Generadas automáticamente. Cambialas si querés.\n"
        f"PANEL_USER={cfg['PANEL_USER']}\n"
        f"PANEL_PASSWORD={cfg['PANEL_PASSWORD']}\n"
        f"PANEL_PORT={cfg['PANEL_PORT']}\n"
    )
    ENV_FILE.chmod(0o600)
    print(f"[panel] Credenciales generadas en {ENV_FILE}")
    print(f"[panel] Usuario: {cfg['PANEL_USER']}  Password: {cfg['PANEL_PASSWORD']}")
    return cfg


CONFIG = load_or_init_env()


# --------------------------------------------------------------------------
# Helpers de sistema
# --------------------------------------------------------------------------

def run_cmd(args: list[str], timeout: int = 300) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            args, cwd=BASE_DIR, capture_output=True, text=True, timeout=timeout
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0, out.strip()
    except subprocess.TimeoutExpired:
        return False, f"Timeout tras {timeout}s ejecutando {' '.join(args)}"
    except Exception as exc:  # noqa: BLE001
        return False, f"Error ejecutando {' '.join(args)}: {exc}"


def list_files(directory: Path, pattern: str) -> list[dict]:
    if not directory.exists():
        return []
    items = []
    for f in sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True):
        st = f.stat()
        items.append({
            "name": f.name,
            "size_kb": round(st.st_size / 1024, 1),
            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return items


def list_running_instances() -> list[dict]:
    ok, out = run_cmd([
        "docker", "ps", "--filter", "name=shellngn-pro",
        "--format", "{{.Names}}|{{.Status}}|{{.Ports}}",
    ])
    if not ok or not out:
        return []
    result = []
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) == 3:
            result.append({"name": parts[0], "status": parts[1], "ports": parts[2]})
    return result


def get_cron_line() -> str | None:
    ok, out = run_cmd(["crontab", "-l"])
    if not ok:
        return None
    for line in out.splitlines():
        if "backup-db.py" in line:
            return line.strip()
    return None


def set_cron_line(cron_expr: str | None) -> tuple[bool, str]:
    ok, out = run_cmd(["crontab", "-l"])
    existing = out.splitlines() if ok else []
    kept = [line for line in existing if "backup-db.py" not in line]

    if cron_expr:
        new_line = (
            f"{cron_expr} /usr/bin/python3 {SCRIPTS_DIR}/backup-db.py "
            f"--with-keys --keep 5 >> {BACKUPS_DIR}/backup.log 2>&1 {CRON_MARKER}"
        )
        kept.append(new_line)

    payload = "\n".join(kept) + ("\n" if kept else "")
    try:
        proc = subprocess.run(
            ["crontab", "-"], input=payload, text=True, capture_output=True, timeout=10
        )
        if proc.returncode != 0:
            return False, proc.stderr.strip()
        return True, "Horario actualizado."
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def get_rebuild_cron_line() -> str | None:
    ok, out = run_cmd(["crontab", "-l"])
    if not ok:
        return None
    for line in out.splitlines():
        if "rebuild-inplace.sh" in line:
            return line.strip()
    return None


def set_rebuild_cron_line(cron_expr: str | None) -> tuple[bool, str]:
    ok, out = run_cmd(["crontab", "-l"])
    existing = out.splitlines() if ok else []
    kept = [line for line in existing if "rebuild-inplace.sh" not in line]

    if cron_expr:
        log_path = PANEL_DIR / "rebuild-scheduled.log"
        new_line = (
            f"{cron_expr} bash {SCRIPTS_DIR}/rebuild-inplace.sh "
            f">> {log_path} 2>&1 {REBUILD_MARKER}"
        )
        kept.append(new_line)

    payload = "\n".join(kept) + ("\n" if kept else "")
    try:
        proc = subprocess.run(
            ["crontab", "-"], input=payload, text=True, capture_output=True, timeout=10
        )
        if proc.returncode != 0:
            return False, proc.stderr.strip()
        return True, ("Horario de recreación actualizado." if cron_expr else "Recreación automática desactivada.")
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


# --------------------------------------------------------------------------
# Acciones
# --------------------------------------------------------------------------

def action_backup_now() -> tuple[bool, str]:
    return run_cmd([sys.executable, str(SCRIPTS_DIR / "backup-db.py"), "--with-keys", "--keep", "5"])


def action_export_clone() -> tuple[bool, str]:
    return run_cmd(["bash", str(SCRIPTS_DIR / "export-clone.sh")])


def action_rebuild_inplace() -> tuple[bool, str]:
    # Dos ciclos de arranque (instancia en blanco + instancia con los datos
    # restaurados), cada uno con su espera de verificación por HTTP.
    return run_cmd(["bash", str(SCRIPTS_DIR / "rebuild-inplace.sh")], timeout=240)


def action_deploy_clone(clone_file: str, name: str, port: str) -> tuple[bool, str]:
    if not NAME_RE.match(name):
        return False, "Nombre inválido: solo letras, números, guiones y guion bajo (máx 32)."
    try:
        port_n = int(port)
        if not (1024 <= port_n <= 65535):
            raise ValueError
    except ValueError:
        return False, "Puerto inválido: debe ser un número entre 1024 y 65535."

    clone_path = (CLONES_DIR / clone_file).resolve()
    if clone_path.parent != CLONES_DIR.resolve() or not clone_path.exists():
        return False, "Archivo de clon inválido."

    dest_dir = BASE_DIR.parent / f"shellngn-{name}"
    if dest_dir.exists() and any(dest_dir.iterdir()):
        return False, f"El directorio {dest_dir} ya existe y no está vacío."

    return run_cmd([
        "bash", str(SCRIPTS_DIR / "import-clone.sh"),
        str(clone_path), str(dest_dir), str(port_n),
    ], timeout=120)


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

def render_page(message: str | None = None, ok: bool = True) -> str:
    backups = list_files(BACKUPS_DIR, "shellngn-userdata-*.sqlite.gz")
    clones = list_files(CLONES_DIR, "shellngn-clone-*.tar.gz")
    running = list_running_instances()
    cron_line = get_cron_line()
    rebuild_cron_line = get_rebuild_cron_line()

    def resolve_freq(line: str | None) -> tuple[str, str]:
        current = "custom" if line else "off"
        expr_shown = ""
        if line:
            expr_shown = " ".join(line.split()[:5])
            for key, expr in FREQUENCY_PRESETS.items():
                if expr_shown == expr:
                    current = key
                    break
        return current, expr_shown

    current_freq, cron_expr_shown = resolve_freq(cron_line)
    current_rebuild_freq, rebuild_cron_expr_shown = resolve_freq(rebuild_cron_line)

    banner = ""
    if message is not None:
        cls = "ok" if ok else "err"
        banner = f'<div class="banner {cls}"><pre>{html.escape(message)}</pre></div>'

    def freq_option(value: str, label: str) -> str:
        sel = " selected" if value == current_freq else ""
        return f'<option value="{value}"{sel}>{label}</option>'

    def rebuild_freq_option(value: str, label: str) -> str:
        sel = " selected" if value == current_rebuild_freq else ""
        return f'<option value="{value}"{sel}>{label}</option>'

    backups_rows = "".join(
        f"<tr><td>{html.escape(b['name'])}</td><td>{b['size_kb']} KB</td><td>{b['mtime']}</td></tr>"
        for b in backups
    ) or '<tr><td colspan="3">Sin backups todavía.</td></tr>'

    clone_options = "".join(
        f'<option value="{html.escape(c["name"])}">{html.escape(c["name"])} ({c["size_kb"]} KB)</option>'
        for c in clones
    ) or '<option value="">(generá una copia primero)</option>'

    clones_rows = "".join(
        f"<tr><td>{html.escape(c['name'])}</td><td>{c['size_kb']} KB</td><td>{c['mtime']}</td></tr>"
        for c in clones
    ) or '<tr><td colspan="3">Sin copias todavía.</td></tr>'

    running_rows = "".join(
        f"<tr><td>{html.escape(r['name'])}</td><td>{html.escape(r['status'])}</td><td>{html.escape(r['ports'])}</td></tr>"
        for r in running
    ) or '<tr><td colspan="3">No se detectan contenedores shellngn-pro* corriendo.</td></tr>'

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shellngn - Panel de backups</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; background:#0f1420; color:#e6e9f0; margin:0; padding:2rem; }}
  h1 {{ font-size:1.4rem; margin-bottom:0.25rem; }}
  h2 {{ font-size:1.05rem; margin-top:2rem; color:#9fb3ff; }}
  .sub {{ color:#8b93a7; margin-bottom:1.5rem; }}
  section {{ background:#161c2c; border:1px solid #262e45; border-radius:10px; padding:1.25rem 1.5rem; margin-bottom:1.25rem; }}
  table {{ width:100%; border-collapse:collapse; margin-top:0.5rem; font-size:0.9rem; }}
  th, td {{ text-align:left; padding:0.4rem 0.5rem; border-bottom:1px solid #262e45; }}
  th {{ color:#8b93a7; font-weight:600; }}
  button {{ background:#4f6bff; color:white; border:none; padding:0.5rem 1rem; border-radius:6px; cursor:pointer; font-size:0.9rem; }}
  button:hover {{ background:#3f57e0; }}
  input, select {{ background:#0f1420; border:1px solid #333c56; color:#e6e9f0; padding:0.4rem 0.6rem; border-radius:6px; margin:0.25rem 0.5rem 0.25rem 0; }}
  form.inline {{ display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap; margin-top:0.75rem; }}
  .banner {{ padding:0.75rem 1rem; border-radius:8px; margin-bottom:1.25rem; }}
  .banner.ok {{ background:#123324; border:1px solid #1e6b45; }}
  .banner.err {{ background:#3a1620; border:1px solid #8f2a3f; }}
  .banner pre {{ white-space:pre-wrap; margin:0; font-size:0.85rem; }}
  code {{ background:#0f1420; padding:0.1rem 0.35rem; border-radius:4px; }}
  section.danger {{ border-color:#8f2a3f; }}
  section.danger h2 {{ color:#ff8a9b; }}
  button.danger {{ background:#c23b52; }}
  button.danger:hover {{ background:#a52d42; }}
</style>
</head>
<body>
<h1>Shellngn Pro &mdash; Panel de backups y copias</h1>
<div class="sub">{BASE_DIR}</div>
{banner}

<section>
  <h2>Instancias corriendo</h2>
  <table><thead><tr><th>Contenedor</th><th>Estado</th><th>Puertos</th></tr></thead>
  <tbody>{running_rows}</tbody></table>
</section>

<section>
  <h2>Backup semanal filtrado (usuarios / equipos / conexiones)</h2>
  <form method="post" action="/action/backup">
    <button type="submit">Crear backup ahora</button>
  </form>

  <form class="inline" method="post" action="/action/schedule">
    <label>Frecuencia automática:</label>
    <select name="frequency">
      {freq_option("off", "Desactivada")}
      {freq_option("hourly", "Cada hora")}
      {freq_option("daily", "Diaria (3 AM)")}
      {freq_option("weekly", "Semanal (domingo 3 AM)")}
      {freq_option("monthly", "Mensual (día 1, 3 AM)")}
      {freq_option("custom", "Personalizada (cron)")}
    </select>
    <input type="text" name="custom_cron" placeholder="min hora dia mes diasem" value="{html.escape(cron_expr_shown) if current_freq == 'custom' else ''}">
    <button type="submit">Guardar horario</button>
  </form>
  <p class="sub">Cron actual: <code>{html.escape(cron_expr_shown) if cron_expr_shown else 'sin programar'}</code></p>

  <table><thead><tr><th>Archivo</th><th>Tamaño</th><th>Fecha</th></tr></thead>
  <tbody>{backups_rows}</tbody></table>
</section>

<section>
  <h2>Copia completa (para desplegar en otro contenedor)</h2>
  <form method="post" action="/action/clone">
    <button type="submit">Generar copia ahora</button>
  </form>

  <form class="inline" method="post" action="/action/deploy">
    <label>Copia:</label>
    <select name="clone_file">{clone_options}</select>
    <label>Nombre:</label>
    <input type="text" name="name" placeholder="ej: staging" pattern="[a-zA-Z0-9_-]{{1,32}}" required>
    <label>Puerto:</label>
    <input type="number" name="port" min="1024" max="65535" value="5057" required>
    <button type="submit">Desplegar copia</button>
  </form>
  <p class="sub">El despliegue crea un contenedor nuevo en <code>../shellngn-&lt;nombre&gt;</code> con esos mismos usuarios/equipos/conexiones.</p>

  <table><thead><tr><th>Archivo</th><th>Tamaño</th><th>Fecha</th></tr></thead>
  <tbody>{clones_rows}</tbody></table>
</section>

<section class="danger">
  <h2>Zona de peligro: recrear esta instancia desde cero</h2>
  <p class="sub">
    <strong>Elimina el contenedor y los datos actuales</strong> y lo
    recrea como si fuera un contenedor nuevo &mdash; mismo puerto, mismo
    nombre de contenedor &mdash; conservando <strong>solo</strong>
    usuarios/equipos/conexiones/credenciales. Todo lo demás (licencia,
    TLS, SAML, sesiones activas, anuncios, logs) se recrea desde cero. Si
    la recreación falla, hace rollback automático a los datos anteriores
    (no se pierde nada aunque falle a mitad de camino).
  </p>
  <form method="post" action="/action/rebuild"
        onsubmit="return confirm('Esto va a tirar abajo el contenedor actual y recrearlo dejando solo usuarios/equipos/conexiones/credenciales; el resto (licencia, TLS, SAML, sesiones) se recrea desde cero. ¿Confirmás?');">
    <label>Escribí <code>RECREAR</code> para habilitar el botón:</label>
    <input type="text" name="confirm_text" id="confirm_text" autocomplete="off"
           oninput="document.getElementById('rebuild_btn').disabled = (this.value !== 'RECREAR');">
    <button type="submit" id="rebuild_btn" class="danger" disabled>Recrear desde cero</button>
  </form>

  <hr style="border-color:#333c56; margin:1.25rem 0;">

  <h2 style="font-size:0.95rem;">Frecuencia automática de recreación</h2>
  <form class="inline" method="post" action="/action/rebuild-schedule"
        onsubmit="return confirm('Vas a cambiar la recreación automática de esta instancia. ¿Confirmás?');">
    <label>Frecuencia automática:</label>
    <select name="frequency">
      {rebuild_freq_option("off", "Desactivada")}
      {rebuild_freq_option("hourly", "Cada hora")}
      {rebuild_freq_option("daily", "Diaria (3 AM)")}
      {rebuild_freq_option("weekly", "Semanal (domingo 3 AM)")}
      {rebuild_freq_option("monthly", "Mensual (día 1, 3 AM)")}
      {rebuild_freq_option("custom", "Personalizada (cron)")}
    </select>
    <input type="text" name="custom_cron" placeholder="min hora dia mes diasem" value="{html.escape(rebuild_cron_expr_shown) if current_rebuild_freq == 'custom' else ''}">
    <label>Escribí <code>RECREAR</code> para guardar:</label>
    <input type="text" name="confirm_text" id="confirm_text_sched" autocomplete="off"
           oninput="document.getElementById('rebuild_sched_btn').disabled = (this.value !== 'RECREAR');">
    <button type="submit" id="rebuild_sched_btn" class="danger" disabled>Guardar horario</button>
  </form>
  <p class="sub">Cron actual: <code>{html.escape(rebuild_cron_expr_shown) if rebuild_cron_expr_shown else 'sin programar'}</code></p>
</section>

</body>
</html>"""


# --------------------------------------------------------------------------
# Servidor HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "ShellngnPanel/1.0"

    def log_message(self, fmt, *args):  # noqa: A003
        sys.stderr.write(f"[panel] {self.address_string()} {fmt % args}\n")

    def _check_auth(self) -> bool:
        header = self.headers.get("Authorization")
        if not header or not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
            user, _, pw = decoded.partition(":")
        except Exception:  # noqa: BLE001
            return False
        return secrets.compare_digest(user, CONFIG["PANEL_USER"]) and secrets.compare_digest(
            pw, CONFIG["PANEL_PASSWORD"]
        )

    def _require_auth(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Shellngn Panel"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Autenticacion requerida")

    def _send_html(self, body: str, status: int = 200):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_form(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        parsed = parse_qs(raw)
        return {k: v[0] for k, v in parsed.items()}

    def do_GET(self):  # noqa: N802
        if not self._check_auth():
            return self._require_auth()
        path = urlparse(self.path).path
        if path == "/":
            self._send_html(render_page())
        else:
            self._send_html("<h1>404</h1>", status=404)

    def do_POST(self):  # noqa: N802
        if not self._check_auth():
            return self._require_auth()
        path = urlparse(self.path).path
        form = self._read_form()

        if path == "/action/backup":
            ok, out = action_backup_now()
        elif path == "/action/clone":
            ok, out = action_export_clone()
        elif path == "/action/deploy":
            ok, out = action_deploy_clone(
                form.get("clone_file", ""), form.get("name", ""), form.get("port", "")
            )
        elif path == "/action/rebuild":
            if form.get("confirm_text", "") != "RECREAR":
                ok, out = False, "Confirmación incorrecta: no se ejecutó nada."
            else:
                ok, out = action_rebuild_inplace()
        elif path == "/action/rebuild-schedule":
            freq = form.get("frequency", "off")
            if freq == "off":
                ok, out = set_rebuild_cron_line(None)
            elif form.get("confirm_text", "") != "RECREAR":
                ok, out = False, "Confirmación incorrecta: no se programó nada."
            elif freq == "custom":
                expr = form.get("custom_cron", "").strip()
                if not CRON_RE.match(expr):
                    ok, out = False, "Expresión cron inválida. Formato: min hora dia mes diasem"
                else:
                    ok, out = set_rebuild_cron_line(expr)
            elif freq in FREQUENCY_PRESETS:
                ok, out = set_rebuild_cron_line(FREQUENCY_PRESETS[freq])
            else:
                ok, out = False, "Frecuencia desconocida."
        elif path == "/action/schedule":
            freq = form.get("frequency", "off")
            if freq == "off":
                ok, out = set_cron_line(None)
            elif freq == "custom":
                expr = form.get("custom_cron", "").strip()
                if not CRON_RE.match(expr):
                    ok, out = False, "Expresión cron inválida. Formato: min hora dia mes diasem"
                else:
                    ok, out = set_cron_line(expr)
            elif freq in FREQUENCY_PRESETS:
                ok, out = set_cron_line(FREQUENCY_PRESETS[freq])
            else:
                ok, out = False, "Frecuencia desconocida."
        else:
            return self._send_html("<h1>404</h1>", status=404)

        self._send_html(render_page(message=out or "OK", ok=ok))


def main():
    port = int(CONFIG.get("PANEL_PORT", "5058"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[panel] Escuchando en http://0.0.0.0:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
