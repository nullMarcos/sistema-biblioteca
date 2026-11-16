import json
import os
from pathlib import Path
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["API_KEY"] = "clave-bff-solo-unitaria"

from main import crear_app
from src.clients import Recursos
from src.clients.base import ClienteLimitado
from src.clients.http import ClientePrestamos, ClienteSanciones
from src.config import Configuracion
from src.errores import FalloDependencia
from src.resiliencia import Circuito

IDENTIDAD = {"X-API-Key": os.environ["API_KEY"], "X-User-Id": "cuenta-distinta-del-socio", "X-User-Role": "admin"}


def prestamo(identificador=1, libro=1, estado="ACTIVO", socio=1):
    return {"id": identificador, "socio_id": socio, "libro_id": libro, "ejemplar_id": identificador,
        "fecha_prestamo": "2026-11-16T12:00:00Z", "fecha_limite": "2026-11-30", "fecha_devolucion": None,
        "estado": estado, "idempotency_key": "clave", "_links": {"self": {"href": "/v1/prestamos/1"}}}


def sanciones():
    return {"socio_id": 101, "rut": "11.111.111-1", "nombre": "Socio de prueba", "tiene_sancion": False,
        "bloqueado": False, "multas_pendientes": 0, "monto_total_multas": 0.0, "bloqueos_activos": [], "multas_activas": []}


class CatalogoPrueba(ClienteLimitado):
    def __init__(self, fuentes):
        super().__init__("catalogo", 8)
        self.fuentes = fuentes

    async def libro(self, libro_id, request_id, deadline):
        self.fuentes.libros.append(libro_id)
        if libro_id in self.fuentes.fallos_libros:
            raise FalloDependencia("catalogo", self.fuentes.fallos_libros[libro_id])
        return {"titulo": f"Libro {libro_id}", "autor": f"Autor {libro_id}"}


class RecursosPrueba(Recursos):
    def __init__(self, config, fuentes):
        super().__init__(config)
        self.fuentes = fuentes

    async def __aenter__(self):
        self.prestamos = ClientePrestamos(self.config, transport=httpx.MockTransport(self.fuentes.http_prestamos))
        self.stack.push_async_callback(self.prestamos.aclose)
        self.sanciones = ClienteSanciones(self.config, circuito=Circuito(self.config.sanciones_cb_threshold,
            self.config.sanciones_cb_recovery), transport=httpx.MockTransport(self.fuentes.http_sanciones))
        self.stack.push_async_callback(self.sanciones.aclose)
        self.catalogo = CatalogoPrueba(self.fuentes)
        self.fuentes.recursos = self
        return self


class Fuentes:
    def __init__(self):
        self.socio = {"id": 1, "rut": "11.111.111-1", "nombre": "Socio de prueba", "correo": "privado@example.com", "prestamos": []}
        self.sanciones = sanciones()
        self.libros, self.fallos_libros, self.peticiones = [], {}, []
        self.error_prestamos = self.error_sanciones = None
        self.fallo_transporte = None
        self.creado = None

    async def http_prestamos(self, request):
        self.peticiones.append(request)
        if self.fallo_transporte:
            raise self.fallo_transporte
        if self.error_prestamos:
            return httpx.Response(self.error_prestamos, json={"codigo": "ERROR_REMOTO", "mensaje": "Operación remota rechazada"}, headers={"Retry-After": "2"})
        if request.method == "GET":
            return httpx.Response(200, json=self.socio)
        datos = json.loads(request.content)
        return httpx.Response(201, json=self.creado or {**prestamo(socio=datos["socio_id"], libro=datos["libro_id"]), "idempotency_key": datos["idempotency_key"]})

    async def http_sanciones(self, request):
        self.peticiones.append(request)
        if self.error_sanciones:
            return httpx.Response(self.error_sanciones, json={"codigo": "ERROR_SANCIONES", "mensaje": "No se pudo verificar"})
        return httpx.Response(200, json=self.sanciones)


@pytest.fixture
def fuentes():
    return Fuentes()


@pytest.fixture
def config():
    return Configuracion(api_key=IDENTIDAD["X-API-Key"])


@pytest.fixture
def client(config, fuentes):
    app = crear_app(config, lambda c: RecursosPrueba(c, fuentes))
    with TestClient(app, headers=IDENTIDAD, raise_server_exceptions=False) as client:
        yield client
