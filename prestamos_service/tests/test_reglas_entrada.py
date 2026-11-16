from datetime import date
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker

from src import models
from src.schemas import PrestamoInput, SocioInput
from src.saga import PrestamoSagaOrchestrator
from src.routers.socios import crear_socio
from src.saga import admission
from src.saga.exceptions import SagaError
from src.identity import Identidad


class CatalogoFalso:
    def __init__(self):
        self.reservas = []

    def reservar_ejemplar(self, libro, operacion):
        self.reservas.append((libro, operacion))
        return len(self.reservas)

    def liberar_ejemplar(self, *args, **kwargs):
        return True

    def cancelar_reserva(self, *args, **kwargs):
        return True


class SancionesFalsas:
    def verificar_sanciones(self, rut):
        return {"tiene_sancion": False, "degradado": False}


def socio_prueba(db):
    socio = models.Socio(rut="11.111.111-1", nombre="Ana", correo="ana@example.com")
    db.add(socio)
    db.commit()
    return socio.id


@pytest.mark.parametrize("campo,valor", [("socio_id", 0), ("socio_id", -1), ("libro_id", 0), ("libro_id", -1),
    ("idempotency_key", "x" * 65), ("idempotency_key", "clave con espacios"), ("idempotency_key", "")])
def test_validacion_precede_efectos_remotos(client, db, campo, valor):
    payload = {"socio_id": socio_prueba(db), "libro_id": 1, "idempotency_key": "valida"}
    payload[campo] = valor
    response = client.post("/v1/prestamos", json=payload)
    assert response.status_code == 400
    assert response.json()["codigo"] == "ERROR_VALIDACION"
    assert db.query(models.SagaTransaccion).count() == 0


def test_replay_con_otro_payload_no_entrega_resultado_ajeno(db):
    catalogo = CatalogoFalso()
    orquestador = PrestamoSagaOrchestrator(db, catalogo, SancionesFalsas())
    socio = socio_prueba(db)
    primero, _ = orquestador.ejecutar(PrestamoInput(socio_id=socio, libro_id=1, idempotency_key="misma"))
    with pytest.raises(Exception) as error:
        orquestador.ejecutar(PrestamoInput(socio_id=socio, libro_id=2, idempotency_key="misma"))
    assert getattr(error.value, "codigo", "") == "IDEMPOTENCIA_PAYLOAD_DISTINTO"
    assert len(catalogo.reservas) == 1


def test_segundo_prestamo_socio_libro_no_reserva(db):
    socio = socio_prueba(db)
    db.add(models.Prestamo(socio_id=socio, libro_id=1, ejemplar_id=10, fecha_limite=date.today(), estado="EN_REVISION"))
    db.commit()
    catalogo = CatalogoFalso()
    with pytest.raises(Exception) as error:
        PrestamoSagaOrchestrator(db, catalogo, SancionesFalsas()).ejecutar(
            PrestamoInput(socio_id=socio, libro_id=1, idempotency_key="otra"))
    assert getattr(error.value, "codigo", "") == "PRESTAMO_DUPLICADO"
    assert catalogo.reservas == []


@pytest.mark.parametrize("campo", ["rut", "correo"])
def test_duplicados_de_socio_son_409(db, campo):
    socio_prueba(db)
    payload = {"rut": "22.222.222-2", "nombre": "Otra", "correo": "otra@example.com"}
    payload[campo] = "11.111.111-1" if campo == "rut" else "ana@example.com"
    with pytest.raises(HTTPException) as error:
        crear_socio(SocioInput(**payload), db, Identidad(None, "interno"))
    assert error.value.status_code == 409


@pytest.mark.parametrize("misma_clave,mismo_libro", [(True, True), (False, True), (False, False)])
def test_admision_concurrente_reserva_cupo_una_sola_vez(db, monkeypatch, misma_clave, mismo_libro):
    socio = socio_prueba(db)
    engine = db.get_bind()
    db.rollback()
    monkeypatch.setattr(admission, "MAX_PRESTAMOS_POR_SOCIO", 2)
    inicio = Barrier(10)
    catalogo = CatalogoFalso()

    def solicitar(i):
        with sessionmaker(bind=engine)() as sesion:
            inicio.wait(timeout=5)
            try:
                prestamo, _ = PrestamoSagaOrchestrator(sesion, catalogo, SancionesFalsas()).ejecutar(
                    PrestamoInput(socio_id=socio, libro_id=1 if mismo_libro else i + 1,
                        idempotency_key="igual" if misma_clave else f"clave-{i}"))
                return ("OK", prestamo.id)
            except SagaError as exc:
                return (exc.codigo, None)

    with ThreadPoolExecutor(max_workers=10) as pool:
        resultados = list(pool.map(solicitar, range(10)))
    exitos = {identificador for codigo, identificador in resultados if codigo == "OK"}
    assert len(exitos) == (1 if mismo_libro else 2)
    assert len(catalogo.reservas) == len(exitos)
    assert all(codigo in {"OK", "OPERACION_PENDIENTE", "PRESTAMO_DUPLICADO", "CUPO_PRESTAMOS_AGOTADO"}
        for codigo, _ in resultados)
    assert db.query(models.Prestamo).count() == len(exitos)


def test_pendiente_ocupa_cupo_sin_bloquear_red_con_transaccion(db, monkeypatch):
    socio = socio_prueba(db)
    engine = db.get_bind()
    db.rollback()
    monkeypatch.setattr(admission, "MAX_PRESTAMOS_POR_SOCIO", 1)
    en_red, responder = Event(), Event()
    catalogo = CatalogoFalso()

    def solicitar():
        with sessionmaker(bind=engine)() as sesion:
            class RedLenta(CatalogoFalso):
                def reservar_ejemplar(self, *args):
                    assert not sesion.connection().connection.driver_connection.in_transaction
                    en_red.set()
                    assert responder.wait(5)
                    return 1
            return PrestamoSagaOrchestrator(sesion, RedLenta(), SancionesFalsas()).ejecutar(
                PrestamoInput(socio_id=socio, libro_id=1, idempotency_key="lenta"))[0].id

    with ThreadPoolExecutor(max_workers=1) as pool:
        llamada = pool.submit(solicitar)
        try:
            assert en_red.wait(3)
            with sessionmaker(bind=engine)() as sesion:
                with pytest.raises(SagaError) as error:
                    PrestamoSagaOrchestrator(sesion, catalogo, SancionesFalsas()).ejecutar(
                        PrestamoInput(socio_id=socio, libro_id=2, idempotency_key="otra"))
                assert error.value.codigo == "CUPO_PRESTAMOS_AGOTADO"
        finally:
            responder.set()
        assert llamada.result(timeout=5) > 0
    assert catalogo.reservas == []


def test_actor_y_resultado_original_en_replay(db):
    socio = socio_prueba(db)
    catalogo = CatalogoFalso()
    orquestador = PrestamoSagaOrchestrator(db, catalogo, SancionesFalsas())
    payload = PrestamoInput(socio_id=socio, libro_id=1, idempotency_key="actor")
    primero, _ = orquestador.ejecutar(payload, actor_id="usuario-1")
    with pytest.raises(SagaError) as error:
        orquestador.ejecutar(payload, actor_id="usuario-2")
    assert error.value.codigo == "IDEMPOTENCIA_PAYLOAD_DISTINTO"
    prestamo = db.get(models.Prestamo, primero.id)
    prestamo.estado = "DEVUELTO"
    db.commit()
    replay, _ = orquestador.ejecutar(payload, actor_id="usuario-1")
    assert replay.estado == "ACTIVO"
    assert len(catalogo.reservas) == 1


@pytest.mark.parametrize("campo", ["rut", "correo"])
def test_conflicto_en_commit_de_socio_es_409(db, campo):
    engine = db.get_bind()
    db.rollback()
    barrera = Barrier(2)

    class SesionConcurrente(Session):
        pass

    event.listen(SesionConcurrente, "before_flush", lambda *args: barrera.wait(timeout=5))

    def insertar(i):
        payload = {"rut": f"{i + 1}{i + 1}.111.111-1", "nombre": "Prueba", "correo": f"socio{i}@example.com"}
        payload[campo] = "11.111.111-1" if campo == "rut" else "igual@example.com"
        with SesionConcurrente(bind=engine) as sesion:
            try:
                crear_socio(SocioInput(**payload), sesion, Identidad(None, "interno"))
                return 201
            except HTTPException as exc:
                assert exc.detail["codigo"] == ("RUT_DUPLICADO" if campo == "rut" else "CORREO_DUPLICADO")
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(insertar, range(2))) == [201, 409]
