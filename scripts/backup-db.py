#!/usr/bin/env python3
"""Backup semanal de usuarios, equipos y conexiones de Shellngn Pro.

Copia la sqlite en caliente (Online Backup API, segura con el contenedor
corriendo), y descarta todo lo que no sea usuarios/equipos/conexiones
(logs de sesión, anuncios, metadata de Sequelize, datos de la organización).

Uso:
    python3 backup-db.py [--with-keys] [--keep N]

--with-keys  también copia data/keys/private_key.pem (necesaria para que
             las contraseñas guardadas en Identities sigan siendo
             desencriptables por la app tras un restore). Sin esta opción
             el backup solo sirve para consultar/auditar datos, no para
             restaurar conexiones funcionales con sus credenciales.
--keep N     cuántos backups conservar (default 5). Los más viejos se
             borran automáticamente.
"""
import argparse
import gzip
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "pro-prod.sqlite"
KEYS_PATH = BASE_DIR / "data" / "keys" / "private_key.pem"
BACKUP_DIR = BASE_DIR / "backups"

# Tablas relacionadas a usuarios, equipos y conexiones (y sus credenciales).
# Todo lo demás (SessionLogs, Announcements, Organizations, SequelizeMeta,
# sqlite_sequence) se descarta del backup.
TABLES_TO_KEEP = {
    "Users",
    "Teams",
    "Team_Users",
    "Devices",
    "DevicesTrees",
    "Identities",
    "User_Device_Identity",
}


def log(msg: str) -> None:
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}")


def backup_database(tmp_path: Path) -> None:
    src = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp_path)
    src.backup(dst)
    src.close()

    cur = dst.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    all_tables = {r[0] for r in cur.fetchall()}
    # sqlite_sequence es una tabla interna de SQLite; no se puede dropear
    # con DROP TABLE, se limpia con DELETE en su lugar.
    to_drop = all_tables - TABLES_TO_KEEP - {"sqlite_sequence"}

    for table in sorted(to_drop):
        cur.execute(f'DROP TABLE IF EXISTS "{table}";')
    if "sqlite_sequence" in all_tables:
        cur.execute(
            "DELETE FROM sqlite_sequence WHERE name NOT IN ({});".format(
                ",".join("?" * len(TABLES_TO_KEEP))
            ),
            tuple(TABLES_TO_KEEP),
        )
    dst.commit()
    cur.execute("VACUUM;")
    dst.close()

    log(f"Tablas conservadas: {sorted(all_tables & TABLES_TO_KEEP)}")
    log(f"Tablas descartadas: {sorted(to_drop)}")


def compress(src_path: Path, dst_path: Path) -> None:
    with open(src_path, "rb") as f_in, gzip.open(dst_path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    src_path.unlink()


def prune_old_backups(pattern: str, keep: int) -> None:
    backups = sorted(BACKUP_DIR.glob(pattern))
    for old in backups[:-keep] if keep > 0 else []:
        log(f"Eliminando backup antiguo: {old.name}")
        old.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-keys", action="store_true",
                         help="incluir data/keys/private_key.pem en el backup")
    parser.add_argument("--keep", type=int, default=5,
                         help="cantidad de backups a conservar (default: 5)")
    args = parser.parse_args()

    if not DB_PATH.exists():
        log(f"ERROR: no se encontró la base de datos en {DB_PATH}")
        return 1

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    tmp_db = BACKUP_DIR / f".tmp-{stamp}.sqlite"
    final_db_gz = BACKUP_DIR / f"shellngn-userdata-{stamp}.sqlite.gz"

    log(f"Iniciando backup de {DB_PATH}")
    backup_database(tmp_db)
    compress(tmp_db, final_db_gz)
    final_db_gz.chmod(0o600)
    log(f"Backup de base de datos guardado en {final_db_gz} ({final_db_gz.stat().st_size} bytes)")

    if args.with_keys:
        if KEYS_PATH.exists():
            key_dst = BACKUP_DIR / f"shellngn-private_key-{stamp}.pem"
            shutil.copy2(KEYS_PATH, key_dst)
            key_dst.chmod(0o600)
            log(f"Llave de cifrado copiada en {key_dst}")
        else:
            log(f"AVISO: no se encontró {KEYS_PATH}, se omite --with-keys")

    prune_old_backups("shellngn-userdata-*.sqlite.gz", args.keep)
    if args.with_keys:
        prune_old_backups("shellngn-private_key-*.pem", args.keep)

    log("Backup completado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
