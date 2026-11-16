"""Saga Catálogo → Sanciones → Préstamos con progreso y compensación durables."""

import json
import logging
import uuid
from datetime import date, timedelta
from typing import Any

from src import models, schemas
from src import grpc_client as default_grpc_client
from src import sanciones_client as default_sanciones_client
from src.saga.admission import admitir, resultado_previo
from src.saga.constants import SagaState, SagaStep
from src.saga.context import SagaContext
from src.saga.exceptions import (
    SagaError, SagaStepError, SocioSancionadoError, ReglaPrestamoError,
    OperacionPendienteError, LeasePerdidoError, respuesta_error,
)
from src.saga.storage import escritura_propietaria, soltar, proximo_intento, validar_presupuesto_lease

logger = logging.getLogger("prestamos.saga.orchestrator")


class PrestamoSagaOrchestrator:
    def __init__(self, db, grpc_client: Any = None, sanciones_client: Any = None):
        self.db = db
        self.grpc_client = grpc_client or default_grpc_client
        self.sanciones_client = sanciones_client or default_sanciones_client.get_sanciones_client()
        self.owner = str(uuid.uuid4())
        validar_presupuesto_lease(self.grpc_client, self.sanciones_client)

    def _escritura(self, identificador):
        return escritura_propietaria(self.db, models.SagaTransaccion, identificador, self.owner)

    @staticmethod
    def contexto(registro):
        return SagaContext(socio_id=registro.socio_id, libro_id=registro.libro_id,
            saga_id=registro.saga_id, idempotency_key=registro.idempotency_key,
            ejemplar_id=registro.ejemplar_id, prestamo_id=registro.prestamo_id,
            estado=SagaState(registro.estado))

    def _pendiente(self, identificador, saga_id, exc):
        """Rollback primero; si la BD no permite escribir, conservar la intención anterior."""
        self.db.rollback()
        try:
            with self._escritura(identificador) as registro:
                registro.estado = SagaState.COMPENSACION_PENDIENTE.value
                registro.error_detalle = str(exc)
                registro.cupo_reservado = True
                registro.proximo_intento = proximo_intento(registro.recuperacion_intentos)
                soltar(registro)
        except Exception:
            self.db.rollback()
            logger.exception("saga=%s pendiente sin poder guardar nuevo progreso", saga_id)
        raise OperacionPendienteError(saga_id) from exc

    def ejecutar(self, payload: schemas.PrestamoInput, actor_id=None):
        registro, nueva = admitir(self.db, payload, actor_id, owner=self.owner)
        context = self.contexto(registro)
        identificador, reserva_id = registro.id, registro.reserva_id
        if not nueva:
            return resultado_previo(registro), context
        context.simular_fallo_paso3 = bool(payload.simular_fallo_paso3)
        paso = SagaStep.PASO_1_RESERVA_CATALOGO
        try:
            # La intención RESERVAR_EJEMPLAR y su identidad ya confirmaron en admitir().
            self.db.rollback()
            ejemplar = self.grpc_client.reservar_ejemplar(payload.libro_id, reserva_id)
            context.ejemplar_id = ejemplar
            with self._escritura(identificador) as actual:
                actual.ejemplar_id = ejemplar
                actual.estado = SagaState.EJEMPLAR_RESERVADO.value
                actual.paso_actual = paso.value
                actual.accion_pendiente = SagaStep.PASO_2_VERIFICAR_SANCIONES.value
                rut = self.db.get(models.Socio, payload.socio_id).rut
            context.registrar_paso_exitoso(paso, SagaState.EJEMPLAR_RESERVADO)

            paso = SagaStep.PASO_2_VERIFICAR_SANCIONES
            sanciones = self.sanciones_client.verificar_sanciones(rut)
            if sanciones.get("tiene_sancion") is True:
                raise SocioSancionadoError(payload.socio_id)
            context.degradado = sanciones.get("degradado") is True
            context.motivo_degradacion = sanciones.get("motivo_degradacion")
            with self._escritura(identificador) as actual:
                actual.sanciones_json = json.dumps(sanciones)
                actual.estado = SagaState.SANCIONES_VERIFICADAS.value
                actual.paso_actual = paso.value
                actual.accion_pendiente = SagaStep.PASO_3_REGISTRAR_PRESTAMO.value
            context.registrar_paso_exitoso(paso, SagaState.SANCIONES_VERIFICADAS)

            paso = SagaStep.PASO_3_REGISTRAR_PRESTAMO
            if context.simular_fallo_paso3:
                raise RuntimeError("Falla inducida en Paso 3 para verificar compensación")
            with self._escritura(identificador) as actual:
                nuevo = models.Prestamo(socio_id=payload.socio_id, libro_id=payload.libro_id,
                    ejemplar_id=ejemplar, fecha_limite=date.today() + timedelta(days=14),
                    estado="EN_REVISION" if context.degradado else "ACTIVO")
                self.db.add(nuevo)
                self.db.flush()
                resultado = schemas.PrestamoOut.model_validate(nuevo)
                resultado.idempotency_key = context.idempotency_key
                actual.prestamo_id = nuevo.id
                actual.estado = SagaState.COMPLETADA.value
                actual.paso_actual = paso.value
                actual.accion_pendiente = None
                actual.respuesta_json = resultado.model_dump_json()
                actual.cupo_reservado = False
                soltar(actual)
                # Préstamo, resultado y COMPLETADA confirman juntos al salir.
            context.prestamo_id = resultado.id
            context.registrar_paso_exitoso(paso, SagaState.COMPLETADA)
            logger.info("saga=%s completada prestamo=%s duracion_ms=%s", context.saga_id, resultado.id, context.duracion_ms())
            return resultado, context
        except LeasePerdidoError:
            self.db.rollback()
            raise
        except Exception as exc:
            self.db.rollback()
            logger.exception("saga=%s fallo paso=%s", context.saga_id, paso.value)
            # Verificar un commit de resultado incierto antes de decidir compensar.
            try:
                actual = self.db.get(models.SagaTransaccion, identificador, populate_existing=True)
                if actual.estado == SagaState.COMPLETADA.value:
                    return resultado_previo(actual), self.contexto(actual)
            except Exception as lectura:
                self._pendiente(identificador, context.saga_id, lectura)
            error = exc if isinstance(exc, (SagaError, default_grpc_client.CatalogoError,
                default_grpc_client.CircuitBreakerOpenError)) else SagaStepError(paso.value,
                    "Fallo de persistencia o ejecución; compensación requerida", codigo="FALLO_PASO_COMPENSADO")
            terminal_sin_efecto = paso == SagaStep.PASO_1_RESERVA_CATALOGO and isinstance(exc, (
                default_grpc_client.SinStockError, default_grpc_client.CatalogoNotFoundError,
                default_grpc_client.CatalogoInvalidArgumentError))
            if terminal_sin_efecto:
                try:
                    with self._escritura(identificador) as actual:
                        actual.estado = SagaState.FALLIDA.value
                        actual.accion_pendiente = None
                        actual.respuesta_json = json.dumps(respuesta_error(error))
                        actual.error_detalle = str(exc)
                        actual.cupo_reservado = False
                        soltar(actual)
                except Exception as persistencia:
                    self._pendiente(identificador, context.saga_id, persistencia)
            else:
                self.compensar(context, actual, motivo=str(exc), resultado_error=respuesta_error(error))
            raise error from exc

    def compensar(self, context, registro_saga, motivo="", resultado_error=None):
        """Reconstruir acciones desde BD. Un fallo local impide liberar el ejemplar."""
        identificador, saga_id = registro_saga.id, context.saga_id
        try:
            with self._escritura(identificador) as actual:
                if actual.estado == SagaState.COMPLETADA.value:
                    raise ReglaPrestamoError("Una Saga completada requiere el caso de uso de devolución", "SAGA_COMPLETADA")
                actual.estado = SagaState.COMPENSANDO.value
                actual.error_detalle = motivo
                if resultado_error or not actual.respuesta_json:
                    actual.respuesta_json = json.dumps(resultado_error or {
                        "http": 409, "error": {"codigo": "OPERACION_INTERRUMPIDA", "mensaje": "La operación fue interrumpida y compensada"}})
                actual.accion_pendiente = "ANULAR_PRESTAMO" if actual.prestamo_id else "CANCELAR_RESERVA"
            with self._escritura(identificador) as actual:
                if actual.prestamo_id:
                    prestamo = self.db.get(models.Prestamo, actual.prestamo_id)
                    if prestamo is None or (prestamo.socio_id, prestamo.libro_id, prestamo.ejemplar_id) != (
                            actual.socio_id, actual.libro_id, actual.ejemplar_id):
                        raise RuntimeError("Préstamo parcial inconsistente; requiere conciliación")
                    if prestamo.estado not in {"ACTIVO", "EN_REVISION", "ANULADO"}:
                        raise RuntimeError("Estado de préstamo incompatible con compensación")
                    prestamo.estado = "ANULADO"
                    logger.info("saga=%s paso3 anulado prestamo=%s", saga_id, prestamo.id)
                else:
                    logger.info("saga=%s paso3 no confirmado; sin anulación local", saga_id)
                # Paso 2 fue consulta: no requiere reversión.
                actual.accion_pendiente = "CANCELAR_RESERVA"
                if not actual.reserva_id or not actual.compensacion_id:
                    raise RuntimeError("Reserva anterior sin identidad; requiere conciliación explícita")
                libro, reserva, cancelacion = actual.libro_id, actual.reserva_id, actual.compensacion_id
            # Se cancela por identidad aunque nunca se recibió el ejemplar_id.
            if not self.grpc_client.cancelar_reserva(libro, reserva, cancelacion):
                raise RuntimeError("Catálogo no confirmó la cancelación")
            with self._escritura(identificador) as actual:
                actual.estado = SagaState.COMPENSADA.value
                actual.accion_pendiente = None
                actual.cupo_reservado = False
                soltar(actual)
            context.estado = SagaState.COMPENSADA
            logger.info("saga=%s compensada; paso2 solo lectura; reserva=%s cancelada", saga_id, reserva)
        except LeasePerdidoError:
            self.db.rollback()
            raise
        except Exception as exc:
            self._pendiente(identificador, saga_id, exc)
