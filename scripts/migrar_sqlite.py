"""Aplicar migraciones SQL incrementales y atómicas a una base SQLite explícita.

Uso: python scripts/migrar_sqlite.py /ruta/base.db /ruta/migraciones
Los archivos 0001_nombre.sql, 0002_nombre.sql, etc. no pueden controlar transacciones.
Este runner prepara las migraciones de C04 en adelante; no reemplaza un esquema
existente ni deduce que create_all haya migrado sus tablas.
"""

import argparse
import hashlib
import sqlite3
from pathlib import Path


def autorizar_migracion(accion, *args):
    if accion in (sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def aplicar(base: Path, directorio: Path):
    if not directorio.is_dir():
        raise ValueError(f"Directorio de migraciones inexistente: {directorio}")
    conexion = sqlite3.connect(base, timeout=5, isolation_level=None)
    try:
        conexion.execute("BEGIN IMMEDIATE")
        conexion.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, sha256 TEXT NOT NULL)")
        conexion.set_authorizer(autorizar_migracion)
        for archivo in sorted(directorio.glob("*.sql")):
            contenido = archivo.read_bytes()
            digest = hashlib.sha256(contenido).hexdigest()
            previa = conexion.execute("SELECT sha256 FROM schema_migrations WHERE version = ?", (archivo.name,)).fetchone()
            if previa:
                if previa[0] != digest:
                    raise ValueError(f"Migración ya aplicada fue modificada: {archivo.name}")
                continue
            sentencia = ""
            for caracter in contenido.decode("utf-8"):
                sentencia += caracter
                if caracter == ";" and sqlite3.complete_statement(sentencia):
                    conexion.execute(sentencia)
                    sentencia = ""
            if sentencia.strip():
                conexion.execute(sentencia)
            conexion.execute("INSERT INTO schema_migrations VALUES (?, ?)", (archivo.name, digest))
        conexion.set_authorizer(None)
        conexion.commit()
    except Exception:
        conexion.set_authorizer(None)
        conexion.rollback()
        raise
    finally:
        conexion.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path)
    parser.add_argument("migraciones", type=Path)
    args = parser.parse_args()
    aplicar(args.base, args.migraciones)
