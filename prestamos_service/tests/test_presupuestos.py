from concurrent import futures
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time

import grpc
import pytest

from src import grpc_client, models, presupuestos
from src.schemas import PrestamoInput
from src.saga import PrestamoSagaOrchestrator
from src.saga.exceptions import OperacionPendienteError
from src.saga import recuperacion
from sqlalchemy.orm import sessionmaker
from src.routers import prestamos
from src.sanciones_client import SancionesResilientClient
from test_persistencia_saga import CatalogoConEstado
from test_reglas_entrada import socio_prueba, SancionesFalsas
from test_devolucion import prestar
from test_resiliencia_efectiva import VALIDA, servidor_http

import catalogo_pb2 as pb
import catalogo_pb2_grpc as rpc


def test_grpc_lento_respeta_intentos_y_presupuesto_total(monkeypatch):
    class Lento(rpc.CatalogoServicer):
        def __init__(self):
            self.intentos = []

        def ReservarEjemplar(self, request, context):
            self.intentos.append(request.operacion_id)
            while context.is_active():
                time.sleep(0.005)
            return pb.ReservaResponse()

    lento = Lento()
    servidor = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    rpc.add_CatalogoServicer_to_server(lento, servidor)
    puerto = servidor.add_insecure_port("127.0.0.1:0")
    servidor.start()
    canal = grpc.insecure_channel(f"127.0.0.1:{puerto}", options=(("grpc.enable_retries", 0),))
    try:
        grpc.channel_ready_future(canal).result(timeout=2)
        monkeypatch.setattr(grpc_client, "stub", rpc.CatalogoStub(canal))
        monkeypatch.setattr(grpc_client, "TIME_OUT_SEGS", 0.08)
        monkeypatch.setattr(grpc_client, "MAX_RETRIES", 1)
        monkeypatch.setattr(grpc_client, "INITIAL_BACKOFF_SEGS", 0.02)
        inicio = time.monotonic()
        with pytest.raises(grpc_client.CatalogoTimeoutError):
            grpc_client._reservar_raw(1, "misma-identidad")
        duracion = time.monotonic() - inicio
        assert lento.intentos == ["misma-identidad", "misma-identidad"]
        assert 0.14 <= duracion < 0.4, duracion
    finally:
        canal.close()
        servidor.stop(grace=0).wait(timeout=2)


def test_deadline_saga_corta_nuevos_efectos_y_conserva_intencion_recuperable(db, monkeypatch):
    catalogo = CatalogoConEstado()

    class SancionesLentas(SancionesFalsas):
        def verificar_sanciones(self, rut):
            time.sleep(0.05)
            return super().verificar_sanciones(rut)

    monkeypatch.setattr(presupuestos, "SAGA_TIMEOUT", 0.03)
    socio = socio_prueba(db)
    with pytest.raises(OperacionPendienteError) as error:
        PrestamoSagaOrchestrator(db, catalogo, SancionesLentas()).ejecutar(
            PrestamoInput(socio_id=socio, libro_id=1, idempotency_key="deadline"))
    assert error.value.http_status == 504
    assert db.query(models.Prestamo).count() == 0
    saga = db.query(models.SagaTransaccion).one()
    assert saga.reserva_id in catalogo.activas and saga.cupo_reservado
    assert saga.estado == "EJEMPLAR_RESERVADO"
    assert catalogo.acciones == ["reservar"]


def test_lectura_util_sigue_respondiendo_durante_sanciones_lentas(client, db, monkeypatch):
    iniciada, soltar = Event(), Event()
    sanciones = SancionesResilientClient()

    def consultar(*args):
        iniciada.set()
        assert soltar.wait(2)
        return VALIDA

    monkeypatch.setattr(sanciones, "_http_get", consultar)
    monkeypatch.setattr(prestamos, "PrestamoSagaOrchestrator", lambda db: PrestamoSagaOrchestrator(db, CatalogoConEstado(), sanciones))
    socio = socio_prueba(db)
    with ThreadPoolExecutor(max_workers=1) as pool:
        futura = pool.submit(client.post, "/v1/prestamos", json={"socio_id": socio, "libro_id": 1})
        try:
            assert iniciada.wait(1)
            inicio = time.monotonic()
            respuesta = client.get(f"/v1/socios/{socio}")
            assert respuesta.status_code == 200, respuesta.text
            assert time.monotonic() - inicio < 0.3
            assert not futura.done()
        finally:
            soltar.set()
        assert futura.result(timeout=2).status_code == 201


def test_timeout_real_libera_transporte_antes_del_cupo(servidor_http):
    servidor_http.demora = 0.4
    cliente = SancionesResilientClient(base_url=servidor_http.url, timeout=0.1, max_concurrent=1)
    resultado = cliente.verificar_sanciones("11.111.111-1")
    assert resultado["motivo_degradacion"] == "TIMEOUT"
    assert cliente.get_metrics()["bulkhead"]["active_calls"] == 0
    servidor_http.demora = 0
    assert cliente.verificar_sanciones("11.111.111-1")["estado"] == "APROBADO"
    assert cliente.get_metrics()["network_calls"] == 2


@pytest.mark.parametrize("metodo", ["post", "delete"])
def test_deadline_total_devolucion_conserva_504_e_identidad_recuperable(client, db, monkeypatch, metodo):
    identificador, catalogo = prestar(db)

    def liberar_lentamente(*args):
        catalogo.liberar_ejemplar(*args)
        time.sleep(0.05)
        return True

    monkeypatch.setattr(presupuestos, "SAGA_TIMEOUT", 0.03)
    monkeypatch.setattr(grpc_client, "liberar_ejemplar", liberar_lentamente)
    ruta = f"/v1/prestamos/{identificador}" + ("/devolucion" if metodo == "post" else "")
    respuesta = getattr(client, metodo)(ruta, headers={"Idempotency-Key": "deadline-devolver"})
    assert respuesta.status_code == 504, respuesta.text
    assert respuesta.json()["codigo"] == "OPERACION_PENDIENTE"
    operacion = db.query(models.DevolucionOperacion).one()
    assert operacion.estado == "PENDIENTE"
    assert db.get(models.Prestamo, identificador).estado == "DEVOLUCION_PENDIENTE"
    assert not any(catalogo.activas.values())
    operacion_id = operacion.operacion_id
    # La respuesta expiró sin poder guardar progreso: el lease original sigue vigente.
    operacion.lease_until = 0
    db.commit()
    monkeypatch.setattr(presupuestos, "SAGA_TIMEOUT", 8)
    assert recuperacion.recuperar(sessionmaker(bind=db.get_bind()), grpc_client=catalogo,
        reanudar=True, devolucion_id=operacion_id)["resueltas"] == 1
    db.expire_all()
    assert db.get(models.Prestamo, identificador).estado == "DEVUELTO"
    assert catalogo.acciones.count("reservar") == 1
