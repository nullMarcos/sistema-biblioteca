"""C20: contrato, autorización, agregación e idempotencia reales detrás de Gateway."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import socket
import tempfile
from threading import Barrier
import time
import uuid

import httpx
from jsonschema import Draft202012Validator, FormatChecker
import jwt
import yaml

from test_saga_escenarios import Escenarios
from verificar_compose import inspeccionar, ROOT
from verificar_gateway import verificar as verificar_gateway


class EscenariosBFF(Escenarios):
    def __init__(self, salida, base_sha=None):
        super().__init__(salida, "bff")
        self.proyecto = "bibg04" + uuid.uuid4().hex[:10]
        self.env.update({"RATE_LIMIT": "1000", "SOCIO1_SOCIO_ID": "1", "BFF_PRESTAMOS_URL": "http://prestamos:8000",
            "BFF_SANCIONES_URL": "http://sanciones:8000", "BFF_CATALOGO_TARGET": "catalogo:50051",
            "BFF_SANCIONES_MAX_CONCURRENT": "2", "BFF_SANCIONES_CB_THRESHOLD": "3", "BFF_SANCIONES_CB_RECOVERY_SEGS": "3",
            "BFF_MAX_PETICIONES": "32", "BFF_PRESTAMOS_MAX_CONCURRENT": "16", "BFF_CATALOGO_MAX_CONCURRENT": "8",
            "BFF_CATALOGO_POR_RESUMEN": "4", "BFF_RESUMEN_TIMEOUT_SEGS": "3", "BFF_POST_TIMEOUT_SEGS": "9",
            "BFF_PRESTAMOS_TIMEOUT_SEGS": "0.8", "BFF_SANCIONES_TIMEOUT_SEGS": "1.5", "BFF_CATALOGO_TIMEOUT_SEGS": "0.6"})
        self.resultado.update({"proyecto": self.proyecto, "base_sha": base_sha})
        self.resultado["configuracion"].update({k: v for k, v in self.env.items() if k.startswith("BFF_") or k == "SOCIO1_SOCIO_ID"})
        self.contrato = yaml.safe_load((ROOT / "contracts/bff.openapi.yaml").read_text())

    def bff(self, escenario, *, socio=None, datos=None, clave=None, esperado=200, token=None, headers_extra=None):
        ruta = "/bff/web/solicitar-prestamo" if datos is not None else f"/bff/web/resumen-prestamos-socio/{socio or self.ana}"
        metodo = "POST" if datos is not None else "GET"
        request_id = "g04-" + uuid.uuid4().hex
        headers = {"X-Request-Id": request_id, **({"Authorization": f"Bearer {self.token if token is None else token}"} if token != "" else {})}
        if clave is not None:
            headers["Idempotency-Key"] = clave
        headers.update(headers_extra or {})
        inicio = time.monotonic()
        with httpx.Client(trust_env=False, timeout=12) as client:
            respuesta = client.request(metodo, f"http://127.0.0.1:{self.puerto}{ruta}", headers=headers, json=datos)
        duracion = round((time.monotonic() - inicio) * 1000, 3)
        cuerpo = respuesta.json()
        self.resultado["peticiones"].append({"escenario": escenario, "ruta": ruta, "metodo": metodo,
            "request_id": request_id, "idempotency_key": clave, "http": respuesta.status_code, "duracion_ms": duracion,
            "estado": cuerpo.get("estado"), "parcial": cuerpo.get("parcial"), "codigo": cuerpo.get("codigo")})
        assert respuesta.status_code in ((esperado,) if isinstance(esperado, int) else esperado), (escenario, respuesta.status_code, cuerpo)
        patron = "/bff/web/solicitar-prestamo" if datos is not None else "/bff/web/resumen-prestamos-socio/{socio_id}"
        esquema = self.contrato["paths"][patron][metodo.lower()]["responses"][str(respuesta.status_code)]["content"]["application/json"]["schema"]
        Draft202012Validator({**esquema, "components": self.contrato["components"]}, format_checker=FormatChecker()).validate(cuerpo)
        assert respuesta.headers.get("X-Request-Id") == request_id
        if respuesta.status_code < 400:
            assert cuerpo["request_id"] == request_id
        if clave and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", clave) and respuesta.status_code not in {401, 429}:
            assert respuesta.headers.get("Idempotency-Key") == clave
        assert "_links" not in cuerpo and self.env["API_KEY"] not in respuesta.text and self.env["JWT_SECRET"] not in respuesta.text
        return cuerpo

    def crear(self, clave, libro, socio=None, **kwargs):
        return self.bff(clave, datos={"socio_id": socio or self.ana, "libro_id": libro}, clave=clave, esperado=kwargs.pop("esperado", 201), **kwargs)

    def metricas_bff(self):
        codigo, datos = self.interno("bff", "/internal/metricas")
        assert codigo == 200
        return datos

    @staticmethod
    def desconocidos(resumen):
        assert resumen["parcial"]
        assert all(resumen["sanciones"][campo] is None for campo in ("tiene_sancion", "bloqueado", "multas_pendientes", "monto_total_multas"))

    def autorizacion_y_lecturas(self):
        self.bff("sin-token", token="", esperado=401)
        self.bff("token-invalido", token="invalido", esperado=401)
        claims = {"sub": "2", "role": "socio", "iat": int(time.time()), "exp": int(time.time()) + 60}
        self.bff("sin-vinculo-socio", token=jwt.encode(claims, self.env["JWT_SECRET"], algorithm="HS256"), esperado=401)
        self.bff("token-expirado", token=jwt.encode({**claims, "role": "admin", "iat": int(time.time()) - 120,
            "exp": int(time.time()) - 60}, self.env["JWT_SECRET"], algorithm="HS256"), esperado=401)
        self.bff("socio-ajeno-con-headers-falsos", socio=self.lucia, token=self.token_socio, esperado=403,
            headers_extra={"X-User-Role": "admin", "X-User-Id": "1", "X-Socio-Id": str(self.lucia)})
        self.bff("socio-inexistente", socio=999999, esperado=404)
        self.bff("socio-invalido", socio=-1, esperado=400)
        for ruta in ("/bff/web/%2E%2E/%2E%2E/internal/metricas", "/api/v1/socios/%2E%2E/%2E%2E/internal/resiliencia"):
            self.pedir("diagnostico-no-publicado-ante-traversal", ruta, esperado=404)
        antes = self.metricas_bff()
        vacio = self.bff("socio-sin-prestamos", token=self.token_socio)
        despues = self.metricas_bff()
        assert not vacio["parcial"] and vacio["socio"]["id"] == self.ana and vacio["prestamos_activos"] == []
        assert vacio["sanciones"]["tiene_sancion"] is False and vacio["dependencias"]["catalogo"] == "NO_CONSULTADO"
        assert despues["catalogo"]["llamadas_red"] == antes["catalogo"]["llamadas_red"]
        assert self.bff("socio-sancionado", socio=self.carlos)["sanciones"]["tiene_sancion"] is True
        self.ejecutar("sanciones", "from src.database import engine\nwith engine.begin() as db:\n"
            "    db.exec_driver_sql(\"UPDATE socios SET rut='99.999.999-9' WHERE rut='33.333.333-3'\")")
        try:
            desconocido = self.bff("no-registrado-en-sanciones", socio=self.lucia)
            self.desconocidos(desconocido)
            assert desconocido["sanciones"]["estado"] == "NO_REGISTRADO"
        finally:
            self.ejecutar("sanciones", "from src.database import engine\nwith engine.begin() as db:\n"
                "    db.exec_driver_sql(\"UPDATE socios SET rut='33.333.333-3' WHERE rut='99.999.999-9'\")")
        self.ok("JWT, vínculo explícito, 403 ante spoofing, socio vacío/sancionado y RUT no registrado; contratos reales válidos")

    def operaciones_bff(self):
        self.libro = self.libros[0]["id"]
        stock = self.stock(self.libro)
        antes = self.metricas_bff()
        self.activo = self.crear("bff-feliz", self.libro)
        despues = self.metricas_bff()
        assert self.activo["estado"] == "ACTIVO" and self.stock(self.libro)["disponibles"] == stock["disponibles"] - 1
        assert despues["prestamos"]["llamadas_red"] == antes["prestamos"]["llamadas_red"] + 1
        assert all(despues[s]["llamadas_red"] == antes[s]["llamadas_red"] for s in ("catalogo", "sanciones"))
        replay = self.crear("bff-feliz", self.libro)
        assert {k: v for k, v in replay.items() if k != "request_id"} == {k: v for k, v in self.activo.items() if k != "request_id"}
        assert self.crear("bff-feliz", self.libros[1]["id"], esperado=409)["codigo"] == "IDEMPOTENCIA_PAYLOAD_DISTINTO"
        self.bff("falta-clave", datos={"socio_id": self.ana, "libro_id": self.libro}, esperado=400)
        self.bff("caos-prohibido", datos={"socio_id": self.ana, "libro_id": self.libro, "simular_fallo_paso3": True}, clave="caos", esperado=400)
        self.crear("sancionado-bff", self.libros[1]["id"], socio=self.carlos, esperado=409)
        antes = self.metricas_bff()
        resumen = self.bff("resumen-tres-fuentes")
        despues = self.metricas_bff()
        libro = self.rpc(f"r=stub.ObtenerLibro(pb.LibroRequest(libro_id={self.libro}), timeout=3); print(json.dumps({{'titulo':r.titulo,'autor':r.autor}}))")
        assert not resumen["parcial"] and resumen["prestamos_activos"][0]["id"] == self.activo["id"]
        assert all(resumen["prestamos_activos"][0][campo] == valor for campo, valor in libro.items())
        assert {s: despues[s]["llamadas_red"] - antes[s]["llamadas_red"] for s in ("prestamos", "catalogo", "sanciones")} == {"prestamos": 1, "catalogo": 1, "sanciones": 1}
        ajeno = self.crear("ajeno-bff", self.libros[1]["id"], socio=self.lucia)
        for ruta in (f"/api/v1/socios/{self.lucia}", f"/api/v1/prestamos/{ajeno['id']}", "/api/v1/prestamos/saga/idempotency/ajeno-bff"):
            self.pedir("bypass-rechazado", ruta, token=self.token_socio, esperado=403)
        assert all(s["id"] == self.ana for s in self.pedir("listado-propio", "/api/v1/socios", token=self.token_socio))
        assert all(p["socio_id"] == self.ana for p in self.pedir("prestamos-propios", "/api/v1/prestamos", token=self.token_socio))
        self.pedir("devolucion-ajena", f"/api/v1/prestamos/{ajeno['id']}/devolucion", cuerpo={}, token=self.token_socio, esperado=403)
        self.devolver(ajeno)
        self.ok("GET compone tres fuentes reales (1 solicitud externa, 3 internas); POST delega una escritura, replay/collision y rutas alternativas autorizadas")

    def respuesta_perdida_y_concurrencia(self):
        libro = self.libros[1]["id"]
        stock = self.stock(libro)
        cuerpo = json.dumps({"socio_id": self.ana, "libro_id": libro}).encode()
        cabeceras = (f"POST /bff/web/solicitar-prestamo HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer {self.token}\r\n"
            f"Idempotency-Key: sin-leer-respuesta\r\nX-Request-Id: perdida-g04\r\nContent-Type: application/json\r\nContent-Length: {len(cuerpo)}\r\nConnection: close\r\n\r\n").encode()
        with socket.create_connection(("127.0.0.1", self.puerto), timeout=3) as conexion:
            conexion.sendall(cabeceras + cuerpo)
            limite = time.monotonic() + 10
            while True:
                saga = self.pedir("confirmar-sin-leer-respuesta", "/api/v1/prestamos/saga/idempotency/sin-leer-respuesta", esperado=(200, 404))
                if saga.get("estado") == "COMPLETADA":
                    break
                assert time.monotonic() < limite, saga
                time.sleep(0.05)
            # Se cierra sin leer ningún byte de la respuesta ya confirmada.
        replay = self.crear("sin-leer-respuesta", libro)
        assert replay["id"] == saga["prestamo_id"] and self.stock(libro)["disponibles"] == stock["disponibles"] - 1
        self.devolver(replay)
        inicio = Barrier(6)
        def simultanea(_):
            inicio.wait(timeout=5)
            return self.crear("clave-simultanea-bff", libro, esperado=(201, 409))
        with ThreadPoolExecutor(max_workers=6) as pool:
            respuestas = list(pool.map(simultanea, range(6)))
        confirmadas = [r for r in respuestas if "id" in r]
        assert confirmadas and len({r["id"] for r in confirmadas}) == 1
        final = self.crear("clave-simultanea-bff", libro)
        assert final["id"] == confirmadas[0]["id"] and self.stock(libro)["disponibles"] == stock["disponibles"] - 1
        self.devolver(final)
        self.ok("respuesta confirmada no leída y seis solicitudes simultáneas conservan una única reserva/préstamo por clave")

    def compensacion_real(self):
        libro = self.libros[1]["id"]
        stock = self.stock(libro)
        self.ejecutar("prestamos", "from src.database import engine\nwith engine.begin() as db:\n"
            "    db.exec_driver_sql(\"CREATE TRIGGER fallo_bff BEFORE INSERT ON prestamos BEGIN SELECT RAISE(ABORT, 'fallo real BFF'); END\")")
        try:
            respuesta = self.crear("bff-fallo-sqlite", libro, esperado=500)
            assert respuesta["codigo"] == "FALLO_PASO_COMPENSADO"
            saga = self.saga("bff-fallo-sqlite")
            assert saga["estado"] == "COMPENSADA" and self.stock(libro) == stock
        finally:
            self.ejecutar("prestamos", "from src.database import engine\nwith engine.begin() as db:\n    db.exec_driver_sql('DROP TRIGGER fallo_bff')")
        self.ok("fallo real de persistencia propagado por BFF; compensación ejecutada por Saga y stock restituido")

    def fuentes_caidas(self):
        self.dc("stop", "catalogo")
        try:
            datos = self.bff("catalogo-caido")
            assert datos["parcial"] and datos["prestamos_activos"][0]["id"] == self.activo["id"]
            assert datos["prestamos_activos"][0]["titulo"] is None
        finally:
            self.dc("up", "-d", "--no-deps", "--wait", "catalogo")
        limite = time.monotonic() + 10
        while self.bff("catalogo-recuperando")["parcial"]:
            assert time.monotonic() < limite
            time.sleep(0.2)
        self.dc("pause", "prestamos")
        try:
            assert self.bff("prestamos-lento", esperado=504)["codigo"] == "PRESTAMOS_TIMEOUT"
        finally:
            self.dc("unpause", "prestamos")
        self.ok("Catálogo caído produce parcial sin perder IDs; Préstamos pausado produce 504 explícito")
        self.interno("sanciones", "/chaos/fault?status=500", "POST")
        try:
            for i in range(3):
                self.desconocidos(self.bff(f"sanciones-error-{i}"))
            antes = self.metricas_bff()
            assert antes["sanciones"]["circuito"]["estado"] == "OPEN"
            self.desconocidos(self.bff("sanciones-circuito-abierto"))
            assert self.metricas_bff()["sanciones"]["llamadas_red"] == antes["sanciones"]["llamadas_red"]
        finally:
            self.interno("sanciones", "/chaos/reset", "POST")
        time.sleep(float(self.env["BFF_SANCIONES_CB_RECOVERY_SEGS"]) + 0.1)
        assert not self.bff("sonda-bff-recuperada")["parcial"]
        assert self.metricas_bff()["sanciones"]["circuito"]["estado"] == "CLOSED"
        self.ok("circuito propio BFF abre sin red adicional y recupera por sonda real", metricas_abierto=antes)

    def lentitud_y_carga(self):
        self.interno("sanciones", "/chaos/delay?seconds=4", "POST")
        try:
            self.desconocidos(self.bff("sanciones-lenta-bff"))
            revision = self.crear("revision-bff", self.libros[1]["id"])
            assert revision["estado"] == "EN_REVISION"
            resumen = self.bff("revision-separada-bff")
            assert [p["id"] for p in resumen["prestamos_en_revision"]] == [revision["id"]]
            assert all(p["id"] != revision["id"] for p in resumen["prestamos_activos"])
            assert self.devolver(revision)["estado"] == "ANULADO"
            self.dc("up", "-d", "--no-deps", "--force-recreate", "--wait", "bff")
            inicio = Barrier(8)
            def simultanea(i):
                inicio.wait(timeout=5)
                if i < 6:
                    return self.bff(f"carga-bff-{i}")
                return self.crear(f"carga-prestamos-{i}", self.libros[i]["id"], socio=self.lucia)
            with ThreadPoolExecutor(max_workers=8) as pool:
                tareas = [pool.submit(simultanea, i) for i in range(8)]
                limite = time.monotonic() + 1.3
                while True:
                    observadas = self.metricas_bff()
                    if observadas["sanciones"]["bulkhead"]["activas"] == 2 or time.monotonic() >= limite:
                        break
                assert observadas["sanciones"]["bulkhead"]["activas"] == 2, observadas
                self.pedir("lectura-util-durante-carga", f"/api/v1/socios/{self.ana}")
                assert not all(t.done() for t in tareas)
                respuestas = [t.result(timeout=12) for t in tareas]
            metricas, prestamos = self.metricas_bff(), self.metricas()
            for resumen in respuestas[:6]:
                self.desconocidos(resumen)
            assert all(p["estado"] == "EN_REVISION" for p in respuestas[6:])
            assert metricas["sanciones"]["bulkhead"]["pico"] == 2 and metricas["sanciones"]["bulkhead"]["rechazadas"] >= 1
            assert metricas["sanciones"]["bulkhead"]["activas"] == 0
            assert prestamos["sanciones"]["bulkhead"]["peak_calls"] == 2 and prestamos["sanciones"]["bulkhead"]["active_calls"] == 0
            for prestamo in respuestas[6:]:
                assert self.devolver(prestamo)["estado"] == "ANULADO"
            self.ok("límites independientes BFF=2 y Préstamos=2 (suma configurada=4); revisión, saturación y lectura útil",
                bff=metricas, prestamos=prestamos, muestra_durante_carga=observadas)
        finally:
            self.interno("sanciones", "/chaos/reset", "POST")
        self.dc("stop", "sanciones")
        try:
            self.dc("up", "-d", "--no-deps", "--force-recreate", "--wait", "bff")
            self.desconocidos(self.bff("sanciones-realmente-detenida"))
        finally:
            self.dc("up", "-d", "--no-deps", "--wait", "sanciones")
        self.ok("Sanciones realmente detenida conserva resumen parcial con desconocidos null")

    def ejecutar_todo(self, usar_cache=False):
        try:
            self.dc("build", *([] if usar_cache else ["--no-cache"]))
            self.dc("up", "-d", "--no-build", "--wait", "--wait-timeout", "120")
            inspeccionar(self.proyecto, self.env, self.puerto)
            self.preparar()
            self.autorizacion_y_lecturas()
            self.operaciones_bff()
            self.respuesta_perdida_y_concurrencia()
            self.compensacion_real()
            self.fuentes_caidas()
            self.lentitud_y_carga()
            self.devolver(self.activo)
            final = self.bff("resumen-final-sin-activos")
            assert final["prestamos_activos"] == final["prestamos_en_revision"] == final["prestamos_pendientes"] == []
            self.consistencia("bff-final")
            self.env["RATE_LIMIT"] = "20"
            self.dc("up", "-d", "--no-deps", "--force-recreate", "--wait", "gateway")
            self.resultado["gateway"] = verificar_gateway(self.proyecto, self.env, f"http://127.0.0.1:{self.puerto}")
            self.ok("Gateway: bindings TCP, JWT y 429 real compartido por API/BFF")
            self.guardar_logs()
            logs = (self.salida / "servicios.log").read_text()
            for campo in ("servicio=bff", "dependencia=prestamos", "dependencia=catalogo", "dependencia=sanciones",
                    "request_id=perdida-g04", "duracion_ms=", "resultado="):
                assert campo in logs, f"Falta trazabilidad de logs: {campo}"
            assert not any(secreto in logs for secreto in (self.token, self.token_socio, self.env["API_KEY"], self.env["JWT_SECRET"]))
            self.ok("logs BFF con correlación, resultado y latencia; sin tokens ni credenciales internas")
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
                    self.resultado["estado"], self.resultado["limpieza"] = "FALLO", "FALLO"
                    raise
                finally:
                    (self.salida / "resultado.json").write_text(json.dumps(self.resultado, ensure_ascii=False, indent=2) + "\n")
                    print(f"Resultado BFF: {self.salida}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--salida", type=Path, help="Carpeta nueva para evidencia y logs")
    parser.add_argument("--usar-cache", action="store_true")
    parser.add_argument("--base-sha", help="SHA base del código ensayado, indicado por el operador")
    args = parser.parse_args()
    salida = args.salida or Path(tempfile.mkdtemp(prefix="biblioteca-bff-"))
    if args.salida:
        salida.mkdir(parents=True, exist_ok=False)
    EscenariosBFF(salida, args.base_sha).ejecutar_todo(args.usar_cache)


if __name__ == "__main__":
    main()
