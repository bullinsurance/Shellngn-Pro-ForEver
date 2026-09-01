#!/usr/bin/env python3
"""Copia, dentro de una base sqlite recién inicializada desde cero, solo las
filas de usuarios/equipos/conexiones/credenciales de una base anterior.

Usada por rebuild-inplace.sh: nunca se ejecuta contra una base en uso (el
contenedor debe estar detenido tanto para la base origen como la destino).

Parte de la misma lista de tablas (TABLES_TO_KEEP) que backup-db.py, para
que "lo que se preserva al recrear la instancia" nunca quede por debajo de
"lo que se preserva en el backup semanal". A esa lista se le suma acá
"Organizations": esa tabla guarda (columna "key") un valor de verificación
cifrado con el par de llaves de la organización -- si se deja la fila
nueva (cifrada con el par nuevo) puesta junto al par de llaves viejo que
también se restaura, el servidor no puede descifrarla al arrancar
("Could not init safe" / RSA OAEP decoding error) y no levanta. No es
descartable como SessionLogs/Announcements: viaja pegada a las llaves.

Todo lo demás (SessionLogs, Announcements, SequelizeMeta, etc.) se deja tal
cual lo creó la instancia nueva al arrancar por primera vez, como si fuera
un contenedor recién creado.

Uso:
    restore-filtered-db.py --target-db <sqlite nuevo> --source-db <sqlite viejo>
"""
import argparse
import importlib.util
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# Tablas que además de TABLES_TO_KEEP hay que restaurar en el rebuild en
# vivo (aunque el backup semanal, solo de auditoría, las descarte) porque
# están ligadas estructuralmente al par de llaves que también se restaura.
EXTRA_TABLES_FOR_LIVE_RESTORE = {"Organizations"}


def load_tables_to_keep() -> set[str]:
    spec = importlib.util.spec_from_file_location("backup_db", SCRIPT_DIR / "backup-db.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TABLES_TO_KEEP | EXTRA_TABLES_FOR_LIVE_RESTORE


def log(msg: str) -> None:
    print(f"[restore-filtered-db] {msg}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-db", required=True, help="sqlite de la instancia recién creada")
    parser.add_argument("--source-db", required=True, help="sqlite de la instancia anterior (contenedor detenido)")
    args = parser.parse_args()

    target_db = Path(args.target_db)
    source_db = Path(args.source_db)
    if not target_db.exists():
        log(f"ERROR: no existe la base de la instancia nueva: {target_db}")
        return 1
    if not source_db.exists():
        log(f"ERROR: no existe la base de la instancia anterior: {source_db}")
        return 1

    tables = load_tables_to_keep()
    log(f"Tablas a preservar: {sorted(tables)}")

    con = sqlite3.connect(target_db)
    try:
        con.execute("PRAGMA foreign_keys=OFF;")
        con.execute("ATTACH DATABASE ? AS src;", (str(source_db),))

        cur = con.execute("SELECT name FROM src.sqlite_master WHERE type='table';")
        source_tables = {r[0] for r in cur.fetchall()}
        missing = tables - source_tables
        if missing:
            log(f"AVISO: la base anterior no tiene estas tablas, se omiten: {sorted(missing)}")
        to_copy = sorted(tables & source_tables)

        con.execute("BEGIN;")
        for table in to_copy:
            con.execute(f'DELETE FROM main."{table}";')
            con.execute(f'INSERT INTO main."{table}" SELECT * FROM src."{table}";')
            log(f"  {table}: restaurada")

        target_has_seq = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence';"
        ).fetchone()
        source_has_seq = "sqlite_sequence" in source_tables
        if target_has_seq and source_has_seq and to_copy:
            placeholders = ",".join("?" * len(to_copy))
            con.execute(
                f"DELETE FROM main.sqlite_sequence WHERE name IN ({placeholders});",
                to_copy,
            )
            con.execute(
                "INSERT INTO main.sqlite_sequence "
                f"SELECT * FROM src.sqlite_sequence WHERE name IN ({placeholders});",
                to_copy,
            )
            log("  sqlite_sequence: contadores de autoincrement realineados")

        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.execute("DETACH DATABASE src;")
        con.execute("VACUUM;")
        con.close()

    log("Restauración completa.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
