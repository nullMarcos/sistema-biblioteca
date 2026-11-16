from concurrent.futures import ThreadPoolExecutor
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Event, Lock, Thread
import time

import pytest

from src.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from src.sanciones_client import SancionesResilientClient
from src.schemas import PrestamoInput
from src.saga import PrestamoSagaOrchestrator
from test_persistencia_saga import CatalogoConEstado
from test_reglas_entrada import socio_prueba

RUT = "11.111.111-1"
VALIDA = {"socio_id": 101, "rut": RUT, "nombre": "Ana", "tiene_sancion": False,
    "bloqueado": False, "multas_pendientes": 0, "monto_total_multas": 0,
    "bloqueos_activos": [], "multas_activas": []}


@pytest.mark.parametrize("cambio", [{"tiene_sancion": None}, {"tiene_sancion": "false"}, {"bloqueado": "false"},
    {"rut": "99.999.999-9"}, {"multas_pendientes": -1}, {"monto_total_multas": float("nan")},
    {"bloqueos_activos": [{}]}, {"tiene_sancion": "OMITIR"}])
def test_respuesta_invalida_del_consumidor_real_nunca_habilita(cambio, monkeypatch):
    cliente = SancionesResilientClient()
    data = {**VALIDA, **cambio}
    if data["tiene_sancion"] == "OMITIR":
        del data["tiene_sancion"]
    monkeypatch.setattr(cliente, "_http_get", lambda *args: data)
    resultado = cliente.verificar_sanciones(RUT)
    assert resultado["degradado"] is True, resultado
    assert resultado["tiene_sancion"] is None
    assert resultado["bloqueado"] is None
    assert resultado["multas_pendientes"] is None
    assert resultado["monto_total_multas"] is None


def test_half_open_admite_solo_una_sonda_concurrente():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01, expected_exceptions=(ConnectionError,))
    with pytest.raises(ConnectionError):
        cb.call(lambda: (_ for _ in ()).throw(ConnectionError()))
    time.sleep(0.02)
    entrando, soltar = Event(), Event()
    ejecutadas = []

    def sonda():
        ejecutadas.append(1)
        entrando.set()
        assert soltar.wait(2)

    def intentar():
        try:
            cb.call(sonda)
        except CircuitBreakerOpenError:
            return "rechazo"

    with ThreadPoolExecutor(max_workers=10) as pool:
        primera = pool.submit(intentar)
        assert entrando.wait(1)
        otras = [pool.submit(intentar) for _ in range(9)]
        try:
            time.sleep(0.05)
            assert len(ejecutadas) == 1
        finally:
            soltar.set()
        primera.result(timeout=2)
        assert [f.result(timeout=2) for f in otras] == ["rechazo"] * 9
    assert cb.get_state() == "closed"


def test_exito_antiguo_no_cierra_una_generacion_half_open_nueva():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01, expected_exceptions=(ConnectionError,))
    vieja_iniciada, liberar_vieja, sonda_iniciada, liberar_sonda = Event(), Event(), Event(), Event()

    def vieja():
        vieja_iniciada.set()
        assert liberar_vieja.wait(2)

    def sonda():
        sonda_iniciada.set()
        assert liberar_sonda.wait(2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        vieja_futuro = pool.submit(cb.call, vieja)
        assert vieja_iniciada.wait(1)
        with pytest.raises(ConnectionError):
            cb.call(lambda: (_ for _ in ()).throw(ConnectionError()))
        time.sleep(0.02)
        sonda_futuro = pool.submit(cb.call, sonda)
        try:
            assert sonda_iniciada.wait(1)
            liberar_vieja.set()
            vieja_futuro.result(timeout=1)
            assert cb.get_state() == "half_open"
        finally:
            liberar_vieja.set()
            liberar_sonda.set()
        sonda_futuro.result(timeout=1)
    assert cb.get_state() == "closed"


@pytest.fixture
def servidor_http():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.server.rutas.append(self.path)
            time.sleep(self.server.demora)
            cuerpo = self.server.cuerpo
            self.send_response(self.server.estado)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            try:
                if self.server.goteo:
                    for byte in cuerpo:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(self.server.goteo)
                else:
                    self.wfile.write(cuerpo)
            except (BrokenPipeError, ConnectionResetError):
                pass

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    servidor.daemon_threads = True
    servidor.cuerpo = json.dumps(VALIDA).encode()
    servidor.estado, servidor.demora, servidor.goteo = 200, 0, 0
    servidor.rutas = []
    servidor.url = f"http://127.0.0.1:{servidor.server_port}"
    hilo = Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    try:
        yield servidor
    finally:
        servidor.shutdown()
        servidor.server_close()
        hilo.join(timeout=2)


def test_json_roto_en_red_real_produce_revision_y_libera_bulkhead(servidor_http):
    servidor_http.cuerpo = b"esto no es JSON"
    cliente = SancionesResilientClient(base_url=servidor_http.url)
    resultado = cliente.verificar_sanciones(RUT)
    assert resultado["degradado"] and resultado["tiene_sancion"] is None
    assert cliente.get_metrics()["bulkhead"]["active_calls"] == 0


def test_timeout_total_no_se_extiende_por_respuesta_a_goteo(servidor_http):
    servidor_http.goteo = 0.03
    cliente = SancionesResilientClient(base_url=servidor_http.url, timeout=0.12)
    inicio = time.monotonic()
    resultado = cliente.verificar_sanciones(RUT)
    duracion = time.monotonic() - inicio
    assert resultado["degradado"] and resultado["motivo_degradacion"] == "TIMEOUT", resultado
    assert 0.09 <= duracion < 0.4, duracion
    assert cliente.get_metrics()["bulkhead"]["active_calls"] == 0


def test_socio_remoto_desconocido_no_equivale_a_habilitado_ni_caida(servidor_http):
    servidor_http.estado = 404
    cliente = SancionesResilientClient(base_url=servidor_http.url)
    for _ in range(4):
        resultado = cliente.verificar_sanciones(RUT)
        assert resultado["degradado"] and resultado["tiene_sancion"] is None
    assert cliente.get_metrics()["circuit_breaker"]["state"] == "closed"


def test_cliente_real_limita_red_y_no_cuenta_saturacion_como_caida(monkeypatch):
    cliente = SancionesResilientClient(max_concurrent=2)
    soltar, llenos, lock = Event(), Event(), Lock()
    efectivas = []

    def red(*args):
        with lock:
            efectivas.append(1)
            if len(efectivas) == 2:
                llenos.set()
        assert soltar.wait(2)
        return copy.deepcopy(VALIDA)

    monkeypatch.setattr(cliente, "_http_get", red)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pendientes = [pool.submit(cliente.verificar_sanciones, RUT) for _ in range(2)]
        try:
            assert llenos.wait(1)
            inicio = time.monotonic()
            for _ in range(10):
                resultado = cliente.verificar_sanciones(RUT)
                assert resultado["motivo_degradacion"] == "BULKHEAD_SATURADO"
            assert time.monotonic() - inicio < 0.2
            assert cliente.get_metrics()["bulkhead"]["active_calls"] == 2
            assert cliente.get_metrics()["circuit_breaker"]["failure_count"] == 0
        finally:
            soltar.set()
        assert all(not f.result(timeout=1)["degradado"] for f in pendientes)
    assert len(efectivas) == 2
    assert cliente.get_metrics()["bulkhead"]["active_calls"] == 0


def test_saga_con_consumidor_real_invalido_persiste_revision(db, monkeypatch):
    cliente = SancionesResilientClient()
    monkeypatch.setattr(cliente, "_http_get", lambda *args: {})
    prestamo, _ = PrestamoSagaOrchestrator(db, CatalogoConEstado(), cliente).ejecutar(
        PrestamoInput(socio_id=socio_prueba(db), libro_id=1, idempotency_key="respuesta-invalida"))
    assert prestamo.estado == "EN_REVISION"


def test_circuito_real_abre_no_llama_red_y_recupera(servidor_http):
    servidor_http.estado = 500
    cliente = SancionesResilientClient(base_url=servidor_http.url, failure_threshold=2, recovery_timeout=0.03)
    for _ in range(2):
        assert cliente.verificar_sanciones(RUT)["degradado"]
    assert cliente.get_metrics()["circuit_breaker"]["state"] == "open"
    llamadas = len(servidor_http.rutas)
    assert cliente.verificar_sanciones(RUT)["motivo_degradacion"] == "CIRCUIT_BREAKER_OPEN"
    assert len(servidor_http.rutas) == llamadas
    servidor_http.estado = 200
    time.sleep(0.04)
    assert cliente.verificar_sanciones(RUT)["estado"] == "APROBADO"
    assert cliente.get_metrics()["circuit_breaker"]["state"] == "closed"


def test_fallo_antiguo_no_reabre_un_circuito_ya_recuperado():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01, expected_exceptions=(ConnectionError,))
    iniciada, soltar = Event(), Event()

    def vieja():
        iniciada.set()
        assert soltar.wait(2)
        raise ConnectionError("Respuesta tardía de una generación anterior")

    with ThreadPoolExecutor(max_workers=1) as pool:
        antigua = pool.submit(cb.call, vieja)
        try:
            assert iniciada.wait(1)
            with pytest.raises(ConnectionError):
                cb.call(lambda: (_ for _ in ()).throw(ConnectionError()))
            time.sleep(0.02)
            cb.call(lambda: True)
            soltar.set()
            with pytest.raises(ConnectionError):
                antigua.result(timeout=1)
            assert cb.get_state() == "closed"
        finally:
            soltar.set()
