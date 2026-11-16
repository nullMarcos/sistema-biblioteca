#!/usr/bin/env python3
"""C13: escenarios y regresiones reales por Gateway en un Compose desechable.

No usa bases locales, puertos fijos ni datos de otro proyecto. Un fallo produce
salida no cero y conserva diagnóstico/logs; los recursos propios siempre se limpian.
"""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import tempfile
from threading import Barrier
import time
import uuid

from diagnosticar_consistencia import diagnosticar
from verificar_compose import compose, inspeccionar, puerto_libre
from verificar_gateway import verificar as verificar_gateway
from verificar_operaciones import api


class Escenarios:
    def __init__(self, salida, grupo):
        self.salida, self.grupo = salida, grupo
        self.proyecto = "bibg03" + uuid.uuid4().hex[:10]
        self.puerto = puerto_libre()
        self.env = {**os.environ, "GATEWAY_PORT": str(self.puerto), "API_KEY": "clave-g03-desechable",
            "JWT_SECRET": "secreto-g03-desechable-con-longitud-suficiente", "SANCIONES_URL": "http://sanciones:8000",
            "MAX_PRESTAMOS_POR_SOCIO": "2", "SQLITE_BUSY_TIMEOUT": "0.5", "SAGA_LEASE_SECONDS": "30",
            "SAGA_RECUPERACION_MAX_INTENTOS": "5", "SAGA_RECUPERACION_BACKOFF": "1", "SAGA_RECUPERACION_BACKOFF_MAX": "60",
            "CATALOGO_TIMEOUT_SEGS": "0.6", "CATALOGO_MAX_RETRIES": "1", "CATALOGO_INITIAL_BACKOFF_SEGS": "0.1",
            "SANCIONES_TIMEOUT_SEGS": "1.5", "SANCIONES_CB_THRESHOLD": "3", "SANCIONES_CB_RECOVERY_SEGS": "3",
            "SANCIONES_MAX_CONCURRENT": "2", "SAGA_TIMEOUT_SEGS": "8", "GATEWAY_TIMEOUT_SEGS": "10",
            "HABILITAR_FALLOS_DEMO": "false", "RATE_LIMIT": "20" if grupo == "todos" else "1000", "RATE_WINDOW": "3"}
        self.resultado = {"estado": "EN_CURSO", "fecha_utc": datetime.now(timezone.utc).isoformat(), "proyecto": self.proyecto,
            "grupo": grupo, "entorno": {"host": platform.platform(), "python": platform.python_version()},
            "configuracion": {k: v for k, v in self.env.items() if k in {
                "MAX_PRESTAMOS_POR_SOCIO", "SQLITE_BUSY_TIMEOUT", "CATALOGO_TIMEOUT_SEGS", "CATALOGO_MAX_RETRIES",
                "CATALOGO_INITIAL_BACKOFF_SEGS", "SANCIONES_TIMEOUT_SEGS", "SANCIONES_CB_THRESHOLD",
                "SANCIONES_CB_RECOVERY_SEGS", "SANCIONES_MAX_CONCURRENT", "SAGA_TIMEOUT_SEGS", "GATEWAY_TIMEOUT_SEGS"}},
            "peticiones": [], "comprobaciones": []}
        self.token = self.token_socio = None

    def dc(self, *args, captura=False):
        if "--force-recreate" in args:
            self.guardar_logs()
        return compose(self.proyecto, self.env, *args, captura=captura)

    def guardar_logs(self):
        logs = self.dc("logs", "--no-color", captura=True)
        with (self.salida / "servicios.log").open("a") as archivo:
            archivo.write(f"\nCaptura UTC {datetime.now(timezone.utc).isoformat()}\n{logs}")

    def ejecutar(self, servicio, codigo):
        return self.dc("exec", "-T", servicio, "python", "-c", codigo, captura=True)

    def interno(self, servicio, ruta, metodo="GET"):
        codigo = """
import json, os, urllib.request, urllib.error
req = urllib.request.Request('http://SERVICIO:8000' + RUTA, method=METODO,
    headers={'X-API-Key': os.environ.get('API_KEY', '')})
try:
    resp = urllib.request.urlopen(req, timeout=4)
except urllib.error.HTTPError as exc:
    resp = exc
with resp:
    print(json.dumps([resp.code, json.load(resp)]))
""".replace("SERVICIO", servicio).replace("RUTA", repr(ruta)).replace("METODO", repr(metodo))
        return json.loads(self.ejecutar("prestamos", codigo))

    def rpc(self, expresion):
        codigo = """
import json, sys
sys.path.insert(0, '/app/protos')
import grpc, catalogo_pb2 as pb, catalogo_pb2_grpc as rpc
with grpc.insecure_channel('catalogo:50051') as canal:
    stub = rpc.CatalogoStub(canal)
    RESULTADO
""".replace("RESULTADO", expresion)
        return json.loads(self.ejecutar("prestamos", codigo))

    def stock(self, libro):
        return self.rpc(f"r = stub.ConsultarDisponibilidad(pb.ConsultaRequest(libro_id={libro}), timeout=3); "
            "print(json.dumps({'disponibles': r.ejemplares_disponibles, 'total': r.total_ejemplares}))")

    def ok(self, nombre, **evidencia):
        self.resultado["comprobaciones"].append({"nombre": nombre, **evidencia})
        print(f"PASS: {nombre}", flush=True)

    def pedir(self, escenario, ruta, cuerpo=None, clave=None, metodo=None, esperado=200, token=None, headers_extra=None):
        inicio = time.monotonic()
        estado, datos = api(self.puerto, ruta, token or self.token, cuerpo, clave, metodo, headers_extra)
        self.resultado["peticiones"].append({"escenario": escenario, "ruta": ruta, "metodo": metodo or ("POST" if cuerpo is not None else "GET"),
            "http": estado, "duracion_ms": round((time.monotonic() - inicio) * 1000, 3),
            "estado": datos.get("estado") if isinstance(datos, dict) else None,
            "codigo": datos.get("codigo") if isinstance(datos, dict) else None,
            "idempotency_key": clave or (cuerpo or {}).get("idempotency_key")})
        assert estado in ((esperado,) if isinstance(esperado, int) else esperado), (escenario, estado, datos)
        return datos

    def prestar(self, escenario, libro, clave=None, socio=None, esperado=201, **opciones):
        return self.pedir(escenario, "/api/v1/prestamos", cuerpo={"socio_id": socio or self.ana, "libro_id": libro,
            "idempotency_key": clave or escenario, **opciones}, esperado=esperado)

    def devolver(self, prestamo, escenario="devolucion"):
        return self.pedir(escenario, f"/api/v1/prestamos/{prestamo['id']}/devolucion", cuerpo={}, clave=f"devolver-{prestamo['id']}")

    def saga(self, clave):
        return self.pedir("consulta-saga", f"/api/v1/prestamos/saga/idempotency/{clave}")

    def metricas(self):
        estado, datos = self.interno("prestamos", "/internal/resiliencia")
        assert estado == 200
        return datos

    def consistencia(self, etapa):
        rutas = []
        filas = {}
        for servicio in ("catalogo", "prestamos", "sanciones"):
            codigo = f"""
import base64, sqlite3
from contextlib import closing
with closing(sqlite3.connect('file:/app/data/{servicio}.db?mode=ro', uri=True)) as origen:
    with closing(sqlite3.connect(':memory:')) as copia:
        origen.backup(copia)
        print(base64.b64encode(copia.serialize()).decode())
"""
            ruta = self.salida / f"{etapa}-{servicio}.db"
            ruta.write_bytes(base64.b64decode(self.ejecutar(servicio, codigo)))
            rutas.append(ruta)
            with sqlite3.connect(ruta) as db:
                tablas = [t[0] for t in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
                filas[servicio] = {tabla: db.execute(f'SELECT * FROM "{tabla}" ORDER BY rowid').fetchall() for tabla in tablas}
        resultado = diagnosticar(*rutas)
        assert resultado["coherente"], resultado
        self.ok(f"consistencia {etapa}", diagnostico=resultado)
        return filas

    def preparar(self):
        self.token = api(self.puerto, "/auth/login", cuerpo={"username": "admin", "password": "admin123"})[1]["access_token"]
        self.token_socio = api(self.puerto, "/auth/login", cuerpo={"username": "socio1", "password": "socio123"})[1]["access_token"]
        socios = self.pedir("socios-semilla", "/api/v1/socios")
        por_rut = {s["rut"]: s["id"] for s in socios}
        self.ana, self.carlos, self.lucia = (por_rut[rut] for rut in ("11.111.111-1", "22.222.222-2", "33.333.333-3"))
        self.libros = self.rpc("print(json.dumps([{'id': l.id, 'total': l.total_ejemplares} "
            "for l in stub.ListarCatalogo(pb.CatalogoRequest(), timeout=3).libros]))")
        # Desalinear deliberadamente IDs locales en la instalación desechable.
        self.ejecutar("sanciones", "from src.database import engine\nwith engine.begin() as db:\n"
            "    db.exec_driver_sql('PRAGMA defer_foreign_keys=ON')\n"
            "    db.exec_driver_sql('UPDATE socios SET id=id+100')\n"
            "    db.exec_driver_sql('UPDATE bloqueos SET socio_id=socio_id+100')\n"
            "    db.exec_driver_sql('UPDATE multas SET socio_id=socio_id+100')")
        estado, sancion = self.interno("sanciones", "/v1/sanciones/socios/11.111.111-1")
        assert estado == 200 and sancion["socio_id"] != self.ana and not sancion["tiene_sancion"]
        self.consistencia("semillas")

    def operaciones(self):
        libro = self.libros[0]["id"]
        stock = self.stock(libro)
        # Demostración deshabilitada incluso para admin, antes de crear una Saga.
        rechazo = self.prestar("demo-deshabilitada", libro, esperado=403, simular_fallo_paso3=True)
        assert rechazo["codigo"] == "DEMO_DESHABILITADA"
        self.env["HABILITAR_FALLOS_DEMO"] = "true"
        self.dc("up", "-d", "--no-deps", "--force-recreate", "--wait", "prestamos")
        self.pedir("demo-rol-no-autorizado", "/api/v1/prestamos", cuerpo={"socio_id": self.ana, "libro_id": libro,
            "simular_fallo_paso3": True}, esperado=403, token=self.token_socio,
            headers_extra={"X-User-Role": "admin", "X-User-ID": "1"})
        self.ok("inyección requiere habilitación y administrador; Gateway elimina identidad falsificada")

        prestamo = self.prestar("feliz", libro)
        assert prestamo["estado"] == "ACTIVO"
        assert self.stock(libro)["disponibles"] == stock["disponibles"] - 1
        assert self.prestar("replay", libro, clave="feliz") == prestamo
        conflicto = self.prestar("colision", self.libros[1]["id"], clave="feliz", esperado=409)
        assert conflicto["codigo"] == "IDEMPOTENCIA_PAYLOAD_DISTINTO"
        self.ok("Saga real feliz correlaciona por RUT con IDs diferentes; replay y colisión", prestamo_id=prestamo["id"])
        devuelto = self.devolver(prestamo)
        assert devuelto["estado"] == "DEVUELTO"
        assert self.devolver(prestamo) == devuelto
        assert self.pedir("alias-delete", f"/api/v1/prestamos/{prestamo['id']}", clave=f"devolver-{prestamo['id']}", metodo="DELETE") == devuelto
        self.pedir("devuelto-sin-clave", f"/api/v1/prestamos/{prestamo['id']}/devolucion", cuerpo={}, esperado=409)
        assert self.stock(libro) == stock

        for escenario, opciones, codigo in (("fallo-demo", {"simular_fallo_paso3": True}, 500),
                ("sancionado", {"socio": self.carlos}, 409)):
            self.prestar(escenario, libro, esperado=codigo, **opciones)
            saga = self.saga(escenario)
            assert saga["estado"] == "COMPENSADA" and saga["accion_pendiente"] is None
            remota = self.rpc(f"r=stub.ConsultarOperacion(pb.OperacionRequest(operacion_id={saga['reserva_id']!r}), timeout=3); print(json.dumps(r.estado))")
            assert remota == "CANCELADA" and self.stock(libro) == stock
        self.ok("fallo último paso y socio sancionado compensan la reserva; sin anulación local ficticia")

        self.ejecutar("prestamos", "from src.database import engine\nwith engine.begin() as db:\n"
            "    db.exec_driver_sql(\"CREATE TRIGGER fallo_c13 BEFORE INSERT ON prestamos BEGIN SELECT RAISE(ABORT, 'fallo real C13'); END\")")
        try:
            self.prestar("fallo-bd-real", libro, esperado=500)
            assert self.saga("fallo-bd-real")["estado"] == "COMPENSADA"
            assert self.stock(libro) == stock
        finally:
            self.ejecutar("prestamos", "from src.database import engine\nwith engine.begin() as db:\n    db.exec_driver_sql('DROP TRIGGER fallo_c13')")
        self.ok("fallo real SQLite del paso 3 conserva error 500 y compensa")

        unico = next(l["id"] for l in self.libros if l["total"] == 1)
        prestado = self.prestar("stock-ultimo", unico)
        assert self.stock(unico)["disponibles"] == 0
        assert self.prestar("stock-agotado", unico, socio=self.lucia, esperado=409)["codigo"] == "SIN_STOCK"
        assert self.prestar("libro-inexistente", 2147483647, esperado=404)["codigo"] == "CATALOGO_NO_ENCONTRADO"
        self.devolver(prestado)
        self.pedir("id-no-positivo", "/api/v1/prestamos", cuerpo={"socio_id": 0, "libro_id": libro}, esperado=400)
        self.pedir("correo-duplicado", "/api/v1/socios", cuerpo={"rut": "99.999.999-9", "nombre": "Prueba", "correo": "ana.garcia@example.com"}, esperado=409)
        self.pedir("rut-duplicado", "/api/v1/socios", cuerpo={"rut": "11.111.111-1", "nombre": "Prueba", "correo": "otro@example.com"}, esperado=409)
        cupos = [self.prestar(f"cupo-{i}", self.libros[i]["id"]) for i in range(2)]
        assert self.prestar("cupo-agotado", self.libros[2]["id"], esperado=409)["codigo"] == "CUPO_PRESTAMOS_AGOTADO"
        for p in cupos:
            self.devolver(p)
        self.ok("stock válido, inexistente distinto, IDs positivos, conflictos 409 y máximo configurable=2")

        inicio = Barrier(8)

        def simultanea(i):
            inicio.wait(timeout=5)
            return self.prestar("concurrente", libro, clave="concurrente", esperado=(201, 409))

        with ThreadPoolExecutor(max_workers=8) as pool:
            respuestas = list(pool.map(simultanea, range(8)))
        exitosas = [r for r in respuestas if r.get("id")]
        assert exitosas and len({r["id"] for r in exitosas}) == 1
        assert self.stock(libro)["disponibles"] == stock["disponibles"] - 1
        self.devolver(exitosas[0])
        self.ok("misma clave concurrente produce un préstamo y una reserva", solicitudes=8, prestamo_id=exitosas[0]["id"])
        self.consistencia("operaciones")

    def catalogo_fallando(self):
        libro = self.libros[0]["id"]
        stock = self.stock(libro)
        for modo in ("pause", "stop"):
            clave = f"catalogo-{modo}"
            self.dc(modo, "catalogo")
            try:
                self.prestar(clave, libro, esperado=(503, 504))
                assert self.pedir("lectura-catalogo-caido", "/api/v1/socios")
                saga = self.saga(clave)
                assert saga["estado"] == "COMPENSACION_PENDIENTE" and saga["reserva_id"]
            finally:
                if modo == "pause":
                    self.dc("unpause", "catalogo")
                else:
                    self.dc("up", "-d", "--no-deps", "catalogo")
                # Después de una pausa, Docker puede conservar unhealthy hasta el
                # próximo healthcheck. Esperar al RPC real sin ignorar sus errores.
                limite = time.monotonic() + 15
                while True:
                    try:
                        self.stock(libro)
                        break
                    except subprocess.CalledProcessError:
                        if time.monotonic() >= limite:
                            raise
                        time.sleep(0.2)
            recuperacion = json.loads(self.dc("exec", "-T", "prestamos", "python", "-m", "src.saga.recuperacion",
                "--reanudar", "--saga-id", saga["saga_id"], captura=True))
            assert recuperacion["resueltas"] == 1 and recuperacion["pendientes"] == 0, recuperacion
            assert self.saga(clave)["estado"] == "COMPENSADA" and self.stock(libro) == stock
            self.ok(f"Catálogo {modo}: duración acotada, lectura útil y recuperación por identidad", recuperacion=recuperacion)
        circuito = self.metricas()["catalogo"]["circuit_breaker"]
        if circuito["state"] == "open":
            time.sleep(max(0, circuito["recovery_timeout"] - circuito["seconds_since_state_change"]) + 0.1)
        prestamo = self.prestar("catalogo-sonda-recuperada", libro)
        assert prestamo["estado"] == "ACTIVO"
        assert self.metricas()["catalogo"]["circuit_breaker"]["state"] == "closed"
        self.devolver(prestamo)
        self.ok("el circuito de Catálogo en el proceso servidor también recupera por una llamada real", circuito_previo=circuito)
        self.consistencia("catalogo-recuperado")

    def resiliencia(self):
        libro = self.libros[0]["id"]
        stock = self.stock(libro)
        self.interno("sanciones", "/chaos/delay?seconds=4", "POST")
        for i in range(3):
            p = self.prestar(f"timeout-sanciones-{i}", libro)
            assert p["estado"] == "EN_REVISION"
            assert self.devolver(p)["estado"] == "ANULADO"
        metricas = self.metricas()
        assert metricas["sanciones"]["circuit_breaker"]["state"] == "open", metricas
        llamadas = metricas["sanciones"]["network_calls"]
        p = self.prestar("sanciones-circuito-abierto", libro)
        assert p["estado"] == "EN_REVISION"
        assert self.metricas()["sanciones"]["network_calls"] == llamadas
        self.devolver(p)
        assert self.interno("prestamos", "/health")[0] == 503
        assert self.pedir("lectura-con-circuito-abierto", "/api/v1/socios")
        self.interno("sanciones", "/chaos/reset", "POST")
        time.sleep(float(self.env["SANCIONES_CB_RECOVERY_SEGS"]) + 0.1)
        p = self.prestar("sonda-recuperada", libro)
        assert p["estado"] == "ACTIVO"
        assert self.metricas()["sanciones"]["circuit_breaker"]["state"] == "closed"
        self.devolver(p)
        self.ok("Sanciones lenta: timeout real, revisión, circuito abierto sin red y recuperación", metricas_abierto=metricas)

        self.env["MAX_PRESTAMOS_POR_SOCIO"] = "20"
        self.dc("up", "-d", "--no-deps", "--force-recreate", "--wait", "prestamos")
        self.interno("sanciones", "/chaos/delay?seconds=4", "POST")
        inicio = Barrier(9)

        def simultanea(i):
            inicio.wait(timeout=5)
            return self.prestar(f"bulkhead-{i}", self.libros[i]["id"])

        with ThreadPoolExecutor(max_workers=9) as pool:
            pendientes = [pool.submit(simultanea, i) for i in range(9)]
            limite = time.monotonic() + 1.2
            while True:
                activas = self.metricas()["sanciones"]["bulkhead"]["active_calls"]
                if activas == 2 or time.monotonic() >= limite:
                    break
            assert activas == 2, activas
            antes = time.monotonic()
            assert self.pedir("lectura-durante-lentitud", "/api/v1/socios")
            lectura_ms = round((time.monotonic() - antes) * 1000, 3)
            assert not all(f.done() for f in pendientes)
            prestamos = [f.result(timeout=8) for f in pendientes]
        metricas = self.metricas()
        assert all(p["estado"] == "EN_REVISION" for p in prestamos)
        assert metricas["sanciones"]["bulkhead"]["peak_calls"] == 2
        assert metricas["sanciones"]["bulkhead"]["rejected_calls"] >= 1
        assert metricas["sanciones"]["network_calls"] == 2
        assert metricas["sanciones"]["bulkhead"]["active_calls"] == 0
        for p in prestamos:
            assert self.devolver(p)["estado"] == "ANULADO"
        self.interno("sanciones", "/chaos/reset", "POST")
        self.ok("Bulkhead efectivo limita a dos llamadas y mantiene lecturas útiles", metricas=metricas, lectura_durante_lentitud_ms=lectura_ms)

        self.dc("stop", "sanciones")
        try:
            self.dc("up", "-d", "--no-deps", "--force-recreate", "--wait", "prestamos")
            assert self.pedir("lectura-sanciones-caida", "/api/v1/socios")
            p = self.prestar("sanciones-caida", libro)
            assert p["estado"] == "EN_REVISION"
            desconocido = json.loads(self.ejecutar("prestamos", "import json\nfrom src.database import SessionLocal\n"
                "from src.models import SagaTransaccion\nwith SessionLocal() as db:\n"
                "    print(db.query(SagaTransaccion).filter_by(idempotency_key='sanciones-caida').one().sanciones_json)"))
            assert all(desconocido[campo] is None for campo in ("tiene_sancion", "bloqueado", "multas_pendientes", "monto_total_multas"))
            assert self.devolver(p)["estado"] == "ANULADO"
        finally:
            self.dc("up", "-d", "--no-deps", "--wait", "--wait-timeout", "60", "sanciones")
        assert self.stock(libro) == stock
        self.ok("Préstamos arranca sin Sanciones; fallback conserva desconocidos null y revisión cancelable")
        self.consistencia("resiliencia")

    def ejecutar_todo(self, usar_cache=False):
        try:
            self.resultado["docker"] = json.loads(subprocess.check_output(["docker", "version", "--format", "{{json .Server}}"], text=True))
            self.dc("build", *([] if usar_cache else ["--no-cache"]))
            self.dc("up", "-d", "--no-build", "--wait", "--wait-timeout", "120")
            inspeccionar(self.proyecto, self.env, self.puerto)
            if self.grupo == "todos":
                self.resultado["gateway"] = verificar_gateway(self.proyecto, self.env, f"http://127.0.0.1:{self.puerto}")
                self.env["RATE_LIMIT"] = "1000"
                self.dc("up", "-d", "--no-deps", "--force-recreate", "--wait", "gateway")
            self.preparar()
            if self.grupo == "todos":
                self.operaciones()
                self.catalogo_fallando()
            self.resiliencia()
            antes = self.consistencia("antes-recrear")
            self.dc("up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
            assert self.consistencia("despues-recrear") == antes
            self.ok("segundo arranque de los cuatro servicios conserva filas e historial sin duplicación")
            self.resultado["estado"] = "APROBADO"
        except BaseException as exc:
            self.resultado["estado"], self.resultado["error"] = "FALLO", str(exc)
            raise
        finally:
            try:
                self.guardar_logs()
            finally:
                try:
                    self.dc("down", "--volumes", "--remove-orphans", "--rmi", "local")
                    self.resultado["limpieza"] = "COMPLETADA"
                except BaseException:
                    self.resultado["limpieza"] = "FALLO"
                    self.resultado["estado"] = "FALLO"
                    raise
                finally:
                    (self.salida / "resultado.json").write_text(json.dumps(self.resultado, ensure_ascii=False, indent=2) + "\n")
                    print(f"Resultado y diagnóstico: {self.salida}", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grupo", choices=("todos", "resiliencia"), default="todos")
    parser.add_argument("--salida", type=Path, help="Carpeta nueva para JSON, logs y copias SQLite desechables")
    parser.add_argument("--usar-cache", action="store_true", help="Reutilizar capas de build; por defecto se construye sin caché")
    args = parser.parse_args(argv)
    salida = args.salida or Path(tempfile.mkdtemp(prefix="biblioteca-escenarios-"))
    if args.salida:
        salida.mkdir(parents=True, exist_ok=False)
    Escenarios(salida, args.grupo).ejecutar_todo(args.usar_cache)


if __name__ == "__main__":
    main()
