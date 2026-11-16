from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time

import pytest
from sqlalchemy.orm import sessionmaker

from src import models
from src.schemas import PrestamoInput
from src.saga.admission import admitir
from src.saga import recuperacion
from src.saga.exceptions import SagaError
from test_persistencia_saga import CatalogoConEstado
from test_reglas_entrada import socio_prueba, SancionesFalsas
from src.saga import PrestamoSagaOrchestrator


def interrumpida(db):
    registro, _ = admitir(db, PrestamoInput(socio_id=socio_prueba(db), libro_id=1,
        idempotency_key="interrumpida"), owner="proceso-terminado")
    registro.lease_until = time.time() - 1
    identificador, reserva = registro.id, registro.reserva_id
    db.commit()
    return identificador, reserva


def test_recuperacion_doble_no_cancela_dos_veces_y_se_puede_repetir(db):
    identificador, reserva = interrumpida(db)
    factory = sessionmaker(bind=db.get_bind())
    db.rollback()
    en_red, responder = Event(), Event()

    class Lento(CatalogoConEstado):
        def cancelar_reserva(self, *args):
            en_red.set()
            assert responder.wait(5)
            return super().cancelar_reserva(*args)

    catalogo = Lento()
    catalogo.activas[reserva] = True
    with ThreadPoolExecutor(max_workers=2) as pool:
        primera = pool.submit(recuperacion.recuperar, factory, grpc_client=catalogo)
        try:
            assert en_red.wait(3)
            segunda = pool.submit(recuperacion.recuperar, factory, grpc_client=catalogo).result(timeout=3)
            assert segunda["procesadas"] == 0
        finally:
            responder.set()
        assert primera.result(timeout=5)["resueltas"] == 1
    assert catalogo.acciones == ["cancelar"]
    assert not catalogo.activas[reserva]
    assert recuperacion.recuperar(factory, grpc_client=catalogo)["procesadas"] == 0
    assert db.get(models.SagaTransaccion, identificador, populate_existing=True).estado == "COMPENSADA"


def test_lease_activo_excluye_recuperador_y_worker_viejo_no_confirma(db):
    socio = socio_prueba(db)
    factory = sessionmaker(bind=db.get_bind())
    db.rollback()
    en_red, responder = Event(), Event()

    class Lento(CatalogoConEstado):
        def reservar_ejemplar(self, *args):
            resultado = super().reservar_ejemplar(*args)
            en_red.set()
            assert responder.wait(5)
            return resultado

    catalogo = Lento()

    def pedir():
        with factory() as sesion:
            return PrestamoSagaOrchestrator(sesion, catalogo, SancionesFalsas()).ejecutar(
                PrestamoInput(socio_id=socio, libro_id=1, idempotency_key="activa"))

    with ThreadPoolExecutor(max_workers=1) as pool:
        solicitud = pool.submit(pedir)
        try:
            assert en_red.wait(3)
            assert recuperacion.recuperar(factory, grpc_client=catalogo)["procesadas"] == 0
            assert catalogo.acciones == ["reservar"]
            # Simular un worker suspendido que excedió su lease.
            registro = db.query(models.SagaTransaccion).one()
            registro.lease_until = time.time() - 1
            db.commit()
            assert recuperacion.recuperar(factory, grpc_client=catalogo)["resueltas"] == 1
        finally:
            responder.set()
        with pytest.raises(SagaError) as error:
            solicitud.result(timeout=5)
        assert error.value.codigo == "OPERACION_PENDIENTE"
    db.expire_all()
    assert db.query(models.Prestamo).count() == 0
    assert db.query(models.SagaTransaccion).one().estado == "COMPENSADA"
    assert not any(catalogo.activas.values())


def test_intentos_backoff_y_reanudacion_no_descartan_pendientes(db, monkeypatch):
    identificador, reserva = interrumpida(db)
    factory = sessionmaker(bind=db.get_bind())
    db.rollback()
    monkeypatch.setattr(recuperacion, "MAX_INTENTOS", 1)
    catalogo = CatalogoConEstado()
    catalogo.activas[reserva] = True
    catalogo.fallar_cancelacion = True
    resultado = recuperacion.recuperar(factory, grpc_client=catalogo)
    assert resultado["pendientes"] == 1
    actual = db.get(models.SagaTransaccion, identificador, populate_existing=True)
    assert actual.recuperacion_intentos == 1
    assert actual.proximo_intento > time.time()
    assert actual.cupo_reservado and actual.estado == "COMPENSACION_PENDIENTE"
    saga_id = actual.saga_id
    db.rollback()
    assert recuperacion.recuperar(factory, grpc_client=catalogo)["procesadas"] == 0
    catalogo.fallar_cancelacion = False
    assert recuperacion.recuperar(factory, grpc_client=catalogo, reanudar=True, saga_id=saga_id)["resueltas"] == 1
    assert not catalogo.activas[reserva]
