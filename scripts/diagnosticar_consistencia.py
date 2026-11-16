"""Diagnóstico offline y de solo lectura de copias estables de los almacenes.

No migra, siembra ni repara datos. Código de salida: 0 coherente, 2 discrepancias.
"""

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

OCUPAN = {"ACTIVO", "EN_REVISION", "DEVOLUCION_PENDIENTE"}
TERMINALES = {"COMPLETADA", "COMPENSADA", "FALLIDA"}


def leer_base(ruta, requeridas, opcionales=()):
    uri = Path(ruta).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        tablas = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        faltantes = set(requeridas) - tablas
        if faltantes:
            raise ValueError(f"{ruta}: faltan tablas {sorted(faltantes)}")
        return {tabla: [dict(r) for r in db.execute(f'SELECT * FROM "{tabla}"')]
            if tabla in tablas else [] for tabla in (*requeridas, *opcionales)}


def diagnosticar(catalogo, prestamos, sanciones):
    cat = leer_base(catalogo, ("libros", "ejemplares"), ("reserva_operaciones",))
    pre = leer_base(prestamos, ("socios", "prestamos"), ("saga_transacciones", "devolucion_operaciones"))
    san = leer_base(sanciones, ("socios",))
    diferencias = []

    def marcar(codigo, **ids):
        diferencias.append({"codigo": codigo, **ids})

    ejemplares = {e["id"]: e for e in cat["ejemplares"]}
    libros = {l["id"] for l in cat["libros"]}
    reservas = {r["operacion_id"]: r for r in cat["reserva_operaciones"]}
    sagas = pre["saga_transacciones"]
    ocupantes = [p for p in pre["prestamos"] if p["estado"] in OCUPAN]
    dev_pendientes = {d["prestamo_id"]: d for d in pre["devolucion_operaciones"] if d["estado"] != "COMPLETADA"}

    for e in ejemplares.values():
        if e["libro_id"] not in libros:
            marcar("EJEMPLAR_SIN_LIBRO", ejemplar_id=e["id"], libro_id=e["libro_id"])
        if e["estado"] == "PRESTADO" and not any(
                r["ejemplar_id"] == e["id"] and r["estado"] == "RESERVADA" for r in reservas.values()):
            marcar("PRESTADO_SIN_RESERVA_IDENTIFICADA", ejemplar_id=e["id"], libro_id=e["libro_id"],
                prestamos=[p["id"] for p in ocupantes if p["ejemplar_id"] == e["id"]])

    for p in ocupantes:
        ids = {"prestamo_id": p["id"], "ejemplar_id": p["ejemplar_id"], "libro_id": p["libro_id"]}
        vinculadas = [s for s in sagas if s.get("prestamo_id") == p["id"] and s.get("reserva_id")]
        if len(vinculadas) != 1:
            marcar("PRESTAMO_SIN_IDENTIDAD_UNIVOCA", **ids)
            continue
        saga = vinculadas[0]
        reserva = reservas.get(saga["reserva_id"])
        e = ejemplares.get(p["ejemplar_id"])
        dev = dev_pendientes.get(p["id"])
        liberando = (p["estado"] == "DEVOLUCION_PENDIENTE" and dev
            and dev["reserva_id"] == saga["reserva_id"])
        estados_validos = {"RESERVADA", "LIBERADA"} if liberando else {"RESERVADA"}
        if (not reserva or reserva["estado"] not in estados_validos
                or reserva["ejemplar_id"] != p["ejemplar_id"] or reserva["libro_id"] != p["libro_id"]
                or saga["socio_id"] != p["socio_id"] or saga["libro_id"] != p["libro_id"]):
            marcar("PRESTAMO_RESERVA_INCOMPATIBLE", reserva_id=saga["reserva_id"], **ids)
        if not e or e["libro_id"] != p["libro_id"] or (e["estado"] != "PRESTADO" and not liberando):
            marcar("PRESTAMO_INVENTARIO_INCOMPATIBLE", **ids)
        if sum(otro["ejemplar_id"] == p["ejemplar_id"] for otro in ocupantes) != 1:
            marcar("EJEMPLAR_CON_PRESTAMOS_DUPLICADOS", **ids)

    for r in reservas.values():
        if r["estado"] != "RESERVADA":
            continue
        ids = {"reserva_id": r["operacion_id"], "ejemplar_id": r["ejemplar_id"], "libro_id": r["libro_id"]}
        e = ejemplares.get(r["ejemplar_id"])
        if not e or e["estado"] != "PRESTADO" or e["libro_id"] != r["libro_id"]:
            marcar("RESERVA_INVENTARIO_INCOMPATIBLE", **ids)
        propietarias = [s for s in sagas if s.get("reserva_id") == r["operacion_id"]
            and s["libro_id"] == r["libro_id"] and (s["estado"] not in TERMINALES
                or any(p["id"] == s.get("prestamo_id") for p in ocupantes))]
        if len(propietarias) != 1:
            marcar("RESERVA_SIN_SAGA_PROPIETARIA_UNIVOCA", **ids)

    # Los IDs son locales. Solo se comparan RUT; no se muestran nombres ni correos.
    ruts_pre = {s["rut"]: s["id"] for s in pre["socios"]}
    ruts_san = {s["rut"]: s["id"] for s in san["socios"]}
    for rut in sorted(ruts_pre.keys() - ruts_san.keys()):
        marcar("SOCIO_SIN_CORRELACION_SANCIONES", socio_id=ruts_pre[rut], rut=rut)
    return {"coherente": not diferencias, "conteos": {"libros": len(libros), "ejemplares": len(ejemplares),
        "prestamos_ocupantes": len(ocupantes), "reservas_activas": sum(r["estado"] == "RESERVADA" for r in reservas.values())},
        "socios_correlacionados_por_rut": len(ruts_pre.keys() & ruts_san.keys()), "discrepancias": diferencias}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for servicio in ("catalogo", "prestamos", "sanciones"):
        parser.add_argument(f"--{servicio}", required=True, type=Path, help="Copia SQLite estable de solo lectura")
    args = parser.parse_args()
    resultado = diagnosticar(args.catalogo, args.prestamos, args.sanciones)
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
    return 0 if resultado["coherente"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
