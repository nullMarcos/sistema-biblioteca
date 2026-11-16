"""C04–C09: préstamo, compensación y devolución reales en Compose desechable."""

import json
import os
import urllib.error
import urllib.request
import uuid

from verificar_compose import compose, inspeccionar, puerto_libre


def api(puerto, ruta, token=None, cuerpo=None, clave=None, metodo=None, headers_extra=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if clave:
        headers["Idempotency-Key"] = clave
    headers.update(headers_extra or {})
    request = urllib.request.Request(f"http://127.0.0.1:{puerto}{ruta}", headers=headers, method=metodo,
        data=json.dumps(cuerpo).encode() if cuerpo is not None else None)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        respuesta = opener.open(request, timeout=12)
    except urllib.error.HTTPError as exc:
        respuesta = exc
    with respuesta:
        return respuesta.code, json.load(respuesta)


def ejecutar(proyecto, entorno, codigo):
    return compose(proyecto, entorno, "exec", "-T", "prestamos", "python", "-c", codigo, captura=True)


def inventario(proyecto, entorno, libro_id=None):
    codigo = """
import json, sys
sys.path.insert(0, '/app/protos')
import grpc, catalogo_pb2 as pb, catalogo_pb2_grpc as rpc
with grpc.insecure_channel('catalogo:50051') as canal:
    libros = rpc.CatalogoStub(canal).ListarCatalogo(pb.CatalogoRequest(), timeout=3).libros
    libro = next(l for l in libros if l.id == LIBRO) if LIBRO else next(l for l in libros if l.ejemplares_disponibles > 0)
    print(json.dumps({'id': libro.id, 'disponibles': libro.ejemplares_disponibles}))
""".replace("LIBRO", repr(libro_id))
    return json.loads(ejecutar(proyecto, entorno, codigo))


def main():
    proyecto = "bibg02" + uuid.uuid4().hex[:10]
    puerto = puerto_libre()
    entorno = {**os.environ, "GATEWAY_PORT": str(puerto), "API_KEY": "clave-g02-desechable",
        "JWT_SECRET": "secreto-g02-desechable-con-longitud-suficiente", "SANCIONES_URL": "http://sanciones:8000",
        "MAX_PRESTAMOS_POR_SOCIO": "5", "SAGA_LEASE_SECONDS": "30", "SAGA_RECUPERACION_MAX_INTENTOS": "5",
        "SAGA_RECUPERACION_BACKOFF": "1", "SAGA_RECUPERACION_BACKOFF_MAX": "60"}
    try:
        compose(proyecto, entorno, "up", "-d", "--build", "--wait", "--wait-timeout", "120")
        inspeccionar(proyecto, entorno, puerto)
        codigo, login = api(puerto, "/auth/login", cuerpo={"username": "admin", "password": "admin123"})
        assert codigo == 200, login
        token = login["access_token"]
        codigo, socios = api(puerto, "/api/v1/socios?rut=33.333.333-3", token)
        assert codigo == 200 and len(socios) == 1, socios
        libro = inventario(proyecto, entorno)
        payload = {"socio_id": socios[0]["id"], "libro_id": libro["id"], "idempotency_key": "alta-compose"}
        codigo, creado = api(puerto, "/api/v1/prestamos", token, payload)
        assert codigo == 201 and creado["estado"] == "ACTIVO", creado
        assert inventario(proyecto, entorno, libro["id"])["disponibles"] == libro["disponibles"] - 1
        codigo, replay = api(puerto, "/api/v1/prestamos", token, payload)
        assert codigo == 201 and replay == creado, replay
        codigo, conflicto = api(puerto, "/api/v1/prestamos", token, {**payload, "libro_id": libro["id"] + 1000000})
        assert codigo == 409 and conflicto["codigo"] == "IDEMPOTENCIA_PAYLOAD_DISTINTO", conflicto
        print("PASS: Saga real Catálogo/Sanciones/Préstamos por Gateway, replay y colisión de payload.", flush=True)

        ruta = f"/api/v1/prestamos/{creado['id']}/devolucion"
        codigo, devuelto = api(puerto, ruta, token, cuerpo={}, clave="devolver-compose")
        assert codigo == 200 and devuelto["estado"] == "DEVUELTO", devuelto
        assert api(puerto, ruta, token, cuerpo={}, clave="devolver-compose") == (200, devuelto)
        assert api(puerto, f"/api/v1/prestamos/{creado['id']}", token, clave="devolver-compose", metodo="DELETE") == (200, devuelto)
        assert inventario(proyecto, entorno, libro["id"])["disponibles"] == libro["disponibles"]
        print("PASS: POST devolución y alias DELETE comparten identidad y restituyen una sola copia.", flush=True)

        ejecutar(proyecto, entorno, "from src.database import engine\nwith engine.begin() as db:\n"
            "    db.exec_driver_sql(\"CREATE TRIGGER fallo_g02 BEFORE INSERT ON prestamos BEGIN SELECT RAISE(ABORT, 'fallo real G02'); END\")")
        try:
            codigo, fallida = api(puerto, "/api/v1/prestamos", token, {**payload, "idempotency_key": "fallo-bd-compose"})
            assert codigo == 500 and fallida["codigo"] == "FALLO_PASO_COMPENSADO", fallida
            codigo, saga = api(puerto, "/api/v1/prestamos/saga/idempotency/fallo-bd-compose", token)
            assert codigo == 200 and saga["estado"] == "COMPENSADA" and saga["accion_pendiente"] is None, saga
            assert inventario(proyecto, entorno, libro["id"])["disponibles"] == libro["disponibles"]
        finally:
            ejecutar(proyecto, entorno, "from src.database import engine\nwith engine.begin() as db:\n    db.exec_driver_sql('DROP TRIGGER fallo_g02')")
        print("PASS: error real de SQLite devuelve 500 y compensa la reserva sin dejar otro préstamo activo.", flush=True)

        compose(proyecto, entorno, "up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "60", "prestamos")
        assert api(puerto, ruta, token, cuerpo={}, clave="devolver-compose") == (200, devuelto)
        resultado = json.loads(compose(proyecto, entorno, "exec", "-T", "prestamos", "python", "-m", "src.saga.recuperacion", captura=True))
        assert resultado["procesadas"] == resultado["pendientes"] == 0, resultado
        ejecutar(proyecto, entorno, "from src.database import engine\nwith engine.connect() as db:\n"
            "    assert db.exec_driver_sql('SELECT count(*) FROM schema_migrations').scalar() == 3")
        print("PASS: recreación conserva datos/replay, migraciones únicas y runner interno sin operaciones pendientes.", flush=True)
    finally:
        compose(proyecto, entorno, "down", "--volumes", "--remove-orphans", "--rmi", "local")
        print(f"Limpieza terminada: {proyecto}", flush=True)


if __name__ == "__main__":
    main()
