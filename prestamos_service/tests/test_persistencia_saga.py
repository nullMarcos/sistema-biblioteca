from datetime import date
import time

import pytest
from sqlalchemy import text

from src import models
from src.schemas import PrestamoInput
from src.saga import PrestamoSagaOrchestrator, SagaContext, SagaState, SagaStep
from src import grpc_client
from src.saga.exceptions import SagaError
from test_reglas_entrada import socio_prueba, SancionesFalsas


class CatalogoConEstado:
    def __init__(self):
        self.activas = {}
        self.acciones = []
        self.fallar_cancelacion = False

    def reservar_ejemplar(self, libro, operacion):
        self.activas[operacion] = True
        self.acciones.append("reservar")
        return 1

    def liberar_ejemplar(self, ejemplar, operacion_id, reserva_id):
        return self.cancelar_reserva(1, reserva_id, operacion_id)

    def cancelar_reserva(self, libro, reserva_id, operacion_id):
        self.acciones.append("cancelar")
        if self.fallar_cancelacion:
            raise ConnectionError("Caída probada de Catálogo")
        self.activas[reserva_id] = False
        return True


def ejecutar(db, catalogo, clave="durable", fallo=False):
    return PrestamoSagaOrchestrator(db, catalogo, SancionesFalsas()).ejecutar(
        PrestamoInput(socio_id=socio_prueba(db), libro_id=1, idempotency_key=clave, simular_fallo_paso3=fallo))


def trigger(db, sql):
    db.execute(text(sql))
    db.commit()


def test_fallo_guardando_reserva_se_compensa_por_identidad(db):
    trigger(db, """CREATE TRIGGER fallo_progreso BEFORE UPDATE ON saga_transacciones
        WHEN NEW.estado='EJEMPLAR_RESERVADO' BEGIN SELECT RAISE(ABORT, 'fallo real de progreso'); END""")
    catalogo = CatalogoConEstado()
    with pytest.raises(Exception):
        ejecutar(db, catalogo)
    db.rollback()
    assert not any(catalogo.activas.values())
    registro = db.query(models.SagaTransaccion).one()
    assert registro.estado == "COMPENSADA"
    assert not registro.cupo_reservado
    assert db.query(models.Prestamo).count() == 0


@pytest.mark.parametrize("antes_de_insertar", [True, False])
def test_fallo_real_de_ultimo_commit_no_confirma_prestamo_separado(db, antes_de_insertar):
    sql = ("CREATE TRIGGER fallo_ultimo BEFORE INSERT ON prestamos BEGIN SELECT RAISE(ABORT, 'fallo insertar'); END"
        if antes_de_insertar else """CREATE TRIGGER fallo_ultimo BEFORE UPDATE ON saga_transacciones
            WHEN NEW.estado='COMPLETADA' BEGIN SELECT RAISE(ABORT, 'fallo confirmar saga'); END""")
    trigger(db, sql)
    catalogo = CatalogoConEstado()
    with pytest.raises(Exception):
        ejecutar(db, catalogo)
    db.rollback()
    assert db.query(models.Prestamo).count() == 0
    assert not any(catalogo.activas.values())
    assert db.query(models.SagaTransaccion).one().estado == "COMPENSADA"


def test_fallo_anulacion_no_libera_ejemplar_de_prestamo_activo(db):
    socio = socio_prueba(db)
    prestamo = models.Prestamo(socio_id=socio, libro_id=1, ejemplar_id=1, fecha_limite=date.today(), estado="ACTIVO")
    db.add(prestamo)
    db.flush()
    registro = models.SagaTransaccion(saga_id="saga-parcial", idempotency_key="parcial", socio_id=socio,
        libro_id=1, ejemplar_id=1, prestamo_id=prestamo.id, estado="COMPENSANDO", paso_actual="ANULAR_PRESTAMO",
        cupo_reservado=True)
    orquestador = PrestamoSagaOrchestrator(db, CatalogoConEstado(), SancionesFalsas())
    registro.reserva_id = "saga-parcial"
    registro.compensacion_id = "saga-parcial:compensar"
    registro.lease_owner = getattr(orquestador, "owner", "anterior")
    registro.lease_until = time.time() + 30
    db.add(registro)
    db.commit()
    orquestador.grpc_client.activas["saga-parcial"] = True
    trigger(db, """CREATE TRIGGER fallo_anular BEFORE UPDATE ON prestamos
        WHEN NEW.estado='ANULADO' BEGIN SELECT RAISE(ABORT, 'fallo real de anulación'); END""")
    contexto = SagaContext(socio_id=socio, libro_id=1, saga_id="saga-parcial", ejemplar_id=1, prestamo_id=prestamo.id,
        pasos_completados=[SagaStep.PASO_1_RESERVA_CATALOGO.value, SagaStep.PASO_2_VERIFICAR_SANCIONES.value,
            SagaStep.PASO_3_REGISTRAR_PRESTAMO.value])
    with pytest.raises(Exception):
        orquestador.compensar(contexto, registro, motivo="Prueba de anulación fallida")
    db.rollback()
    assert orquestador.grpc_client.activas["saga-parcial"]
    assert "cancelar" not in orquestador.grpc_client.acciones
    assert db.query(models.Prestamo).one().estado == "ACTIVO"
    assert db.query(models.SagaTransaccion).one().estado == "COMPENSACION_PENDIENTE"
    trigger(db, "DROP TRIGGER fallo_anular")
    from sqlalchemy.orm import sessionmaker
    from src.saga.recuperacion import recuperar
    assert recuperar(sessionmaker(bind=db.get_bind()), grpc_client=orquestador.grpc_client,
        reanudar=True, saga_id="saga-parcial")["resueltas"] == 1
    db.expire_all()
    assert db.query(models.Prestamo).one().estado == "ANULADO"
    assert not orquestador.grpc_client.activas["saga-parcial"]


def test_compensacion_fallida_retiene_cupo_y_pendiente(db):
    catalogo = CatalogoConEstado()
    catalogo.fallar_cancelacion = True
    with pytest.raises(Exception) as error:
        ejecutar(db, catalogo, fallo=True)
    db.rollback()
    registro = db.query(models.SagaTransaccion).one()
    assert registro.estado == "COMPENSACION_PENDIENTE"
    assert registro.cupo_reservado
    assert any(catalogo.activas.values())
    assert getattr(error.value, "http_status", None) == 503


def test_bd_caida_no_inventa_compensacion_exitosa(db):
    trigger(db, """CREATE TRIGGER fallo_total BEFORE UPDATE ON saga_transacciones
        BEGIN SELECT RAISE(ABORT, 'persistencia no disponible'); END""")
    catalogo = CatalogoConEstado()
    with pytest.raises(Exception) as error:
        ejecutar(db, catalogo)
    db.rollback()
    assert db.query(models.SagaTransaccion).one().estado == "INICIADA"
    assert any(catalogo.activas.values())
    assert getattr(error.value, "http_status", None) == 503


def test_error_real_en_commit_diferido_revierte_prestamo_y_saga(db):
    db.execute(text("PRAGMA foreign_keys=ON"))
    trigger(db, "CREATE TABLE destino (id INTEGER PRIMARY KEY)")
    trigger(db, """CREATE TABLE diferido (id INTEGER REFERENCES destino(id) DEFERRABLE INITIALLY DEFERRED)""")
    trigger(db, """CREATE TRIGGER fallo_commit AFTER INSERT ON prestamos
        BEGIN INSERT INTO diferido VALUES (999); END""")
    catalogo = CatalogoConEstado()
    with pytest.raises(SagaError):
        ejecutar(db, catalogo)
    assert db.query(models.Prestamo).count() == 0
    assert db.query(models.SagaTransaccion).one().estado == "COMPENSADA"
    assert not any(catalogo.activas.values())


@pytest.mark.parametrize("fallo", ["SIN_STOCK", "SANCION", "PASO_3"])
def test_resultado_terminal_de_error_se_repite_sin_reservar(db, fallo):
    socio = socio_prueba(db)
    catalogo = CatalogoConEstado()

    class SinStock(CatalogoConEstado):
        def reservar_ejemplar(self, *args):
            self.acciones.append("reservar")
            raise grpc_client.SinStockError()

    class Sancionado(SancionesFalsas):
        def verificar_sanciones(self, rut):
            return {"tiene_sancion": True}

    if fallo == "SIN_STOCK":
        catalogo = SinStock()
    orquestador = PrestamoSagaOrchestrator(db, catalogo, Sancionado() if fallo == "SANCION" else SancionesFalsas())
    payload = PrestamoInput(socio_id=socio, libro_id=1, idempotency_key="terminal", simular_fallo_paso3=fallo == "PASO_3")
    with pytest.raises(Exception) as original:
        orquestador.ejecutar(payload)
    acciones = list(catalogo.acciones)
    with pytest.raises(SagaError) as repetido:
        orquestador.ejecutar(payload)
    assert repetido.value.codigo == original.value.codigo
    assert catalogo.acciones == acciones
    assert not db.query(models.SagaTransaccion).one().cupo_reservado
