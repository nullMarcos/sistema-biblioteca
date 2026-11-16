import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import time

import grpc
import httpx
import pytest

from conftest import sanciones
from src.agregacion import consultar_libros
from src.clients.catalogo import ClienteCatalogo, pb, rpc
from src.clients.http import ClienteSanciones
from src.errores import FalloDependencia
from src.resiliencia import Circuito


@contextmanager
def servidor_http():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            self.server.peticiones.append((self.path, self.client_address, dict(self.headers)))
            contenido = json.dumps(sanciones()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(contenido)))
            self.end_headers()
            try:
                if self.server.goteo:
                    for byte in contenido:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.015)
                else:
                    self.wfile.write(contenido)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.peticiones, server.goteo = [], False
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_real_reutiliza_conexion_y_acota_el_cuerpo_completo(config):
    with servidor_http() as server:
        async def caso():
            conf = config.model_copy(update={"sanciones_url": f"http://127.0.0.1:{server.server_port}", "sanciones_timeout": 0.09})
            cliente = ClienteSanciones(conf)
            try:
                for _ in range(2):
                    respuesta = await cliente.consultar("11.111.111-1", "lectura-real", time.monotonic() + 1)
                    assert respuesta.socio_id == 101
                assert server.peticiones[0][1] == server.peticiones[1][1]
                assert server.peticiones[0][2]["X-Request-Id"] == "lectura-real"
                assert "X-API-Key" not in server.peticiones[0][2]
                server.goteo = True
                inicio = time.monotonic()
                with pytest.raises(FalloDependencia) as error:
                    await cliente.consultar("11.111.111-1", "goteo", inicio + 1)
                assert error.value.estado == "TIMEOUT" and time.monotonic() - inicio < 0.5
                assert cliente.limite.activas == 0 and cliente.red == 3
                server.goteo = False
                assert (await cliente.consultar("11.111.111-1", "recuperada", time.monotonic() + 1)).tiene_sancion is False
            finally:
                await cliente.aclose()
            assert cliente.http.is_closed
        asyncio.run(caso())


def test_timeout_no_libera_slot_hasta_cerrar_transporte(config):
    async def caso():
        cerrando, permitir_cierre, cerrado = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class CuerpoLento(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"{"
                await asyncio.Event().wait()

            async def aclose(self):
                cerrando.set()
                await permitir_cierre.wait()
                cerrado.set()

        conf = config.model_copy(update={"sanciones_timeout": 0.03, "sanciones_max_concurrent": 1})
        cliente = ClienteSanciones(conf, transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=CuerpoLento())))
        tarea = asyncio.create_task(cliente.consultar("rut", "primera", time.monotonic() + 1))
        try:
            await asyncio.wait_for(cerrando.wait(), 1)
            assert cliente.limite.activas == 1 and not cerrado.is_set()
            with pytest.raises(FalloDependencia) as error:
                await cliente.consultar("rut", "exceso", time.monotonic() + 1)
            assert error.value.estado == "SATURADO" and cliente.red == 1
            permitir_cierre.set()
            with pytest.raises(FalloDependencia) as error:
                await tarea
            assert error.value.estado == "TIMEOUT" and cerrado.is_set() and cliente.limite.activas == 0
        finally:
            permitir_cierre.set()
            await asyncio.gather(tarea, return_exceptions=True)
            await cliente.aclose()
    asyncio.run(caso())


def test_circuito_generaciones_y_una_sonda_bajo_concurrencia(config):
    async def caso():
        ahora = [0.0]
        antigua_iniciada, terminar_antigua, sonda_iniciada, terminar_sonda = (asyncio.Event() for _ in range(4))

        async def remoto(request):
            operacion = request.headers["X-Request-Id"]
            if operacion == "antigua":
                antigua_iniciada.set()
                await terminar_antigua.wait()
            if operacion == "falla":
                return httpx.Response(503, json={"codigo": "CAIDA", "mensaje": "Caída real"})
            if operacion == "sonda":
                sonda_iniciada.set()
                await terminar_sonda.wait()
            return httpx.Response(200, json=sanciones())

        circuito = Circuito(1, 5, reloj=lambda: ahora[0])
        cliente = ClienteSanciones(config, circuito=circuito, transport=httpx.MockTransport(remoto))
        antigua = asyncio.create_task(cliente.consultar("11.111.111-1", "antigua", time.monotonic() + 3))
        try:
            await asyncio.wait_for(antigua_iniciada.wait(), 1)
            with pytest.raises(FalloDependencia):
                await cliente.consultar("rut", "falla", time.monotonic() + 1)
            assert circuito.estado == "OPEN"
            terminar_antigua.set()
            await antigua
            assert circuito.estado == "OPEN"  # El éxito tardío no cierra una generación nueva.
            llamadas = cliente.red
            with pytest.raises(FalloDependencia) as error:
                await cliente.consultar("rut", "rechazada", time.monotonic() + 1)
            assert error.value.estado == "CIRCUITO_ABIERTO" and cliente.red == llamadas
            ahora[0] = 6
            sonda = asyncio.create_task(cliente.consultar("11.111.111-1", "sonda", time.monotonic() + 3))
            try:
                await asyncio.wait_for(sonda_iniciada.wait(), 1)
                with pytest.raises(FalloDependencia):
                    await cliente.consultar("rut", "segunda-sonda", time.monotonic() + 1)
                assert cliente.red == llamadas + 1 and circuito.estado == "HALF_OPEN"
            finally:
                terminar_sonda.set()
                await sonda
            assert circuito.estado == "CLOSED" and cliente.limite.activas == 0
        finally:
            terminar_antigua.set()
            await asyncio.gather(antigua, return_exceptions=True)
            await cliente.aclose()
    asyncio.run(caso())


@pytest.mark.parametrize("status", [404, 409])
def test_rechazo_de_negocio_no_abre_circuito(config, status):
    async def caso():
        circuito = Circuito(1, 5)
        cliente = ClienteSanciones(config, circuito=circuito, transport=httpx.MockTransport(
            lambda r: httpx.Response(status, json={"codigo": "NEGOCIO", "mensaje": "No registrado o conflicto"})))
        try:
            for _ in range(3):
                with pytest.raises(FalloDependencia):
                    await cliente.consultar("rut", "negocio", time.monotonic() + 1)
            assert circuito.estado == "CLOSED" and circuito.fallos == 0
        finally:
            await cliente.aclose()
    asyncio.run(caso())


def test_schema_invalido_abre_circuito_y_rut_se_codifica(config):
    async def caso():
        paths = []
        def remoto(request):
            paths.append(request.url.raw_path)
            return httpx.Response(200, json={"tiene_sancion": False})
        circuito = Circuito(1, 5)
        cliente = ClienteSanciones(config, circuito=circuito, transport=httpx.MockTransport(remoto))
        try:
            with pytest.raises(FalloDependencia) as error:
                await cliente.consultar("rut/a?b#c", "codificacion", time.monotonic() + 1)
            assert error.value.estado == "RESPUESTA_INVALIDA" and circuito.estado == "OPEN"
            assert paths == [b"/v1/sanciones/socios/rut%2Fa%3Fb%23c"]
        finally:
            await cliente.aclose()
    asyncio.run(caso())


def test_cancelacion_libera_sonda_y_bulkhead_sin_inventar_fallo(config):
    async def caso():
        iniciado = asyncio.Event()
        async def remoto(request):
            iniciado.set()
            await asyncio.Event().wait()
        circuito = Circuito(1, 5)
        circuito._transicion("HALF_OPEN")
        cliente = ClienteSanciones(config, circuito=circuito, transport=httpx.MockTransport(remoto))
        tarea = asyncio.create_task(cliente.consultar("rut", "cancelada", time.monotonic() + 2))
        try:
            await asyncio.wait_for(iniciado.wait(), 1)
            tarea.cancel()
            with pytest.raises(asyncio.CancelledError):
                await tarea
            assert cliente.limite.activas == 0 and not circuito.sonda and circuito.estado == "HALF_OPEN" and circuito.fallos == 0
        finally:
            await cliente.aclose()
    asyncio.run(caso())


def test_grpc_real_valida_identidad_deadline_y_cierre(config):
    peticiones = []
    class Catalogo(rpc.CatalogoServicer):
        def ObtenerLibro(self, request, context):
            peticiones.append((request.libro_id, dict(context.invocation_metadata())))
            if request.libro_id == 404:
                context.abort(grpc.StatusCode.NOT_FOUND, "No existe")
            if request.libro_id == 8:
                time.sleep(0.15)
            return pb.LibroDTO(id=999 if request.libro_id == 7 else request.libro_id, titulo="Título real", autor="Autor real")

    with ThreadPoolExecutor(max_workers=4) as pool:
        server = grpc.server(pool)
        rpc.add_CatalogoServicer_to_server(Catalogo(), server)
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        try:
            async def caso():
                conf = config.model_copy(update={"catalogo_timeout": 0.06})
                async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as canal:
                    cliente = ClienteCatalogo(conf, canal)
                    assert await cliente.libro(1, "grpc-real", time.monotonic() + 1) == {"titulo": "Título real", "autor": "Autor real"}
                    for identificador, estado in ((404, "NO_ENCONTRADO"), (7, "RESPUESTA_INVALIDA"), (8, "TIMEOUT")):
                        with pytest.raises(FalloDependencia) as error:
                            await cliente.libro(identificador, "grpc-real", time.monotonic() + 1)
                        assert error.value.estado == estado
                    assert cliente.red == 4 and cliente.limite.activas == 0
                with pytest.raises(grpc.aio.UsageError):
                    await cliente.libro(1, "cerrado", time.monotonic() + 1)
            asyncio.run(caso())
        finally:
            server.stop(0).wait()
    assert [p[0] for p in peticiones] == [1, 404, 7, 8]
    assert peticiones[0][1]["x-request-id"] == "grpc-real"


def test_enriquecimiento_grande_crea_trabajadores_acotados(config):
    async def caso():
        class Fuente:
            activas = pico = 0
            ids = []
            async def libro(self, libro_id, *args):
                self.activas += 1
                self.pico = max(self.pico, self.activas)
                self.ids.append(libro_id)
                try:
                    await asyncio.sleep(0.001)
                    return {"titulo": str(libro_id), "autor": "Autor"}
                finally:
                    self.activas -= 1
        from types import SimpleNamespace
        fuente = Fuente()
        recursos = SimpleNamespace(config=config.model_copy(update={"catalogo_por_resumen": 3}), catalogo=fuente)
        resultado = await consultar_libros(recursos, set(range(1, 201)), "muchos", time.monotonic() + 1)
        assert len(resultado) == len(fuente.ids) == len(set(fuente.ids)) == 200
        assert fuente.pico == 3 and fuente.activas == 0
    asyncio.run(caso())
