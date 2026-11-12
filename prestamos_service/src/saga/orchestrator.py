"""
Motor Orquestador de Saga para la transacción distribuida 'Registrar préstamo de un libro'.
Coordina los pasos de reserva en Catálogo, verificación en Sanciones y persistencia en Préstamos,
con soporte para compensaciones semánticas en orden inverso, idempotencia estricta y degradación elegante.
"""

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Tuple, Optional, Any

from sqlalchemy.orm import Session
from sqlalchemy import select

from src import models
from src import schemas
from src import grpc_client as default_grpc_client
from src import sanciones_client as default_sanciones_client
from src.saga.constants import SagaState, SagaStep
from src.saga.context import SagaContext
from src.saga.exceptions import (
    SagaError,
    SagaStepError,
    SagaCompensationError,
    SocioSancionadoError,
    IdempotencyConflictError,
)

logger = logging.getLogger("prestamos.saga.orchestrator")


class PrestamoSagaOrchestrator:
    """
    Orquestador centralizado de la transacción distribuida de préstamo.
    """

    def __init__(
        self,
        db: Session,
        grpc_client: Any = None,
        sanciones_client: Any = None,
    ):
        self.db = db
        self.grpc_client = grpc_client or default_grpc_client
        self.sanciones_client = sanciones_client or default_sanciones_client.get_sanciones_client()

    def _buscar_transaccion_existente(self, idempotency_key: str) -> Optional[models.SagaTransaccion]:
        """Consulta si existe una transacción previa asociada a la clave de idempotencia."""
        stmt = select(models.SagaTransaccion).where(
            models.SagaTransaccion.idempotency_key == idempotency_key
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def _actualizar_registro_saga(
        self,
        registro: models.SagaTransaccion,
        context: SagaContext,
        respuesta_json: Optional[str] = None,
        error_detalle: Optional[str] = None,
    ) -> None:
        """Actualiza el registro persistente de la Saga en la base de datos."""
        registro.estado = context.estado.value
        registro.paso_actual = context.paso_actual.value
        registro.ejemplar_id = context.ejemplar_id
        registro.prestamo_id = context.prestamo_id

        if respuesta_json:
            registro.respuesta_json = respuesta_json
        if error_detalle:
            registro.error_detalle = error_detalle

        self.db.commit()
        self.db.refresh(registro)

    def ejecutar(self, payload: schemas.PrestamoInput) -> Tuple[models.Prestamo, SagaContext]:
        """
        Ejecuta el flujo completo de la Saga:
          Paso 1: Reservar ejemplar en Catálogo (gRPC).
          Paso 2: Verificar sanciones en Sanciones (HTTP).
          Paso 3: Registrar préstamo en Préstamos (DB local).
        Ante cualquier fallo, ejecuta compensación en orden inverso.
        """
        # Validación previa de socio existente
        socio = self.db.get(models.Socio, payload.socio_id)
        if socio is None:
            raise SagaStepError(
                SagaStep.INICIO.value,
                f"No existe un socio con ID {payload.socio_id}",
                codigo="SOCIO_NO_ENCONTRADO"
            )

        # 0. Verificación de Idempotencia
        if payload.idempotency_key:
            transaccion_previa = self._buscar_transaccion_existente(payload.idempotency_key)
            if transaccion_previa:
                if transaccion_previa.estado == SagaState.COMPLETADA.value:
                    logger.info(
                        "[IDEMPOTENCIA] Solicitud repetida con clave '%s'. Retornando resultado previo.",
                        payload.idempotency_key
                    )
                    prestamo_existente = self.db.get(models.Prestamo, transaccion_previa.prestamo_id)
                    if prestamo_existente:
                        ctx_reconstruido = SagaContext(
                            socio_id=payload.socio_id,
                            libro_id=payload.libro_id,
                            idempotency_key=payload.idempotency_key,
                            saga_id=transaccion_previa.saga_id,
                            ejemplar_id=transaccion_previa.ejemplar_id,
                            prestamo_id=transaccion_previa.prestamo_id,
                            estado=SagaState.COMPLETADA,
                            paso_actual=SagaStep.PASO_3_REGISTRAR_PRESTAMO,
                        )
                        return prestamo_existente, ctx_reconstruido

                if transaccion_previa.estado in [
                    SagaState.INICIADA.value,
                    SagaState.EJEMPLAR_RESERVADO.value,
                    SagaState.SANCIONES_VERIFICADAS.value,
                    SagaState.COMPENSANDO.value,
                ]:
                    logger.warning(
                        "[IDEMPOTENCIA] Transacción en conflicto/progreso para la clave '%s'.",
                        payload.idempotency_key
                    )
                    raise IdempotencyConflictError(payload.idempotency_key)

        # Inicialización del contexto en memoria
        context = SagaContext(
            socio_id=payload.socio_id,
            libro_id=payload.libro_id,
            idempotency_key=payload.idempotency_key,
            simular_fallo_paso3=bool(payload.simular_fallo_paso3),
        )

        # Registro persistente inicial de la Saga
        registro_saga = models.SagaTransaccion(
            saga_id=context.saga_id,
            idempotency_key=context.idempotency_key,
            socio_id=context.socio_id,
            libro_id=context.libro_id,
            estado=context.estado.value,
            paso_actual=context.paso_actual.value,
        )
        self.db.add(registro_saga)
        self.db.commit()
        self.db.refresh(registro_saga)

        # -------------------------------------------------------------
        # PASO 1: Reservar ejemplar en Catálogo (gRPC)
        # -------------------------------------------------------------
        try:
            logger.info("[SAGA %s] Iniciando Paso 1: Reserva de ejemplar en Catálogo...", context.saga_id)
            ejemplar_id = self.grpc_client.reservar_ejemplar(payload.libro_id)
            context.ejemplar_id = ejemplar_id
            context.registrar_paso_exitoso(
                SagaStep.PASO_1_RESERVA_CATALOGO,
                SagaState.EJEMPLAR_RESERVADO
            )
            self._actualizar_registro_saga(registro_saga, context)
            logger.info(
                "[SAGA %s] Paso 1 completado exitosamente: ejemplar_id=%d para libro_id=%d",
                context.saga_id,
                ejemplar_id,
                payload.libro_id
            )
        except Exception as exc:
            context.estado = SagaState.FALLIDA
            context.error = str(exc)
            self._actualizar_registro_saga(registro_saga, context, error_detalle=str(exc))
            logger.error("[SAGA %s] Falló Paso 1 (Reserva): %s", context.saga_id, exc)
            raise

        # -------------------------------------------------------------
        # PASO 2: Verificar sanciones en Sanciones (HTTP Resiliente)
        # -------------------------------------------------------------
        try:
            logger.info("[SAGA %s] Iniciando Paso 2: Verificación de sanciones...", context.saga_id)
            # Se utiliza el RUT del socio como identificador unificado de negocio
            resultado_sanciones = self.sanciones_client.verificar_sanciones(socio.rut)

            # Caso 2.A: Socio con sanción activa (violación de regla de negocio)
            if resultado_sanciones.get("tiene_sancion") is True:
                logger.warning(
                    "[SAGA %s] Paso 2: Socio '%s' con sanciones vigentes. Abortando Saga e iniciando compensación.",
                    context.saga_id,
                    socio.rut
                )
                self.compensar(context, registro_saga, motivo="Socio con sanciones activas")
                raise SocioSancionadoError(socio.id, "El socio posee sanciones o bloqueos activos.")

            # Caso 2.B: Verificador no responde o en fallback (degradación elegante)
            if resultado_sanciones.get("degradado") is True:
                context.degradado = True
                context.motivo_degradacion = resultado_sanciones.get("motivo_degradacion", "FALLBACK")
                logger.info(
                    "[SAGA %s] Paso 2 degradado por resiliencia: motivo=%s",
                    context.saga_id,
                    context.motivo_degradacion
                )

            context.registrar_paso_exitoso(
                SagaStep.PASO_2_VERIFICAR_SANCIONES,
                SagaState.SANCIONES_VERIFICADAS
            )
            self._actualizar_registro_saga(registro_saga, context)
            logger.info("[SAGA %s] Paso 2 completado exitosamente.", context.saga_id)

        except SocioSancionadoError:
            raise
        except Exception as exc:
            logger.error("[SAGA %s] Error inesperado en Paso 2: %s", context.saga_id, exc)
            self.compensar(context, registro_saga, motivo=f"Fallo en Paso 2: {exc}")
            raise SagaStepError(SagaStep.PASO_2_VERIFICAR_SANCIONES.value, str(exc))

        # -------------------------------------------------------------
        # PASO 3: Registrar préstamo en Préstamos (DB Local)
        # -------------------------------------------------------------
        try:
            logger.info("[SAGA %s] Iniciando Paso 3: Persistencia de préstamo...", context.saga_id)

            # Verificación de inducción de falla (Escenario 2 de ABET 6)
            if context.simular_fallo_paso3:
                logger.warning(
                    "[SAGA %s] Simulación de falla forzada activada en Paso 3 (Escenario 2).",
                    context.saga_id
                )
                raise RuntimeError("Falla inducida controlada en Paso 3 para verificar compensación inversa de Saga")

            # Regla de degradación: si Sanciones falló, el préstamo queda en revisión
            estado_prestamo = "EN_REVISION" if context.degradado else "ACTIVO"

            nuevo_prestamo = models.Prestamo(
                socio_id=payload.socio_id,
                libro_id=payload.libro_id,
                ejemplar_id=context.ejemplar_id,
                fecha_limite=date.today() + timedelta(days=14),
                estado=estado_prestamo,
            )
            self.db.add(nuevo_prestamo)
            self.db.commit()
            self.db.refresh(nuevo_prestamo)

            context.prestamo_id = nuevo_prestamo.id
            context.registrar_paso_exitoso(
                SagaStep.PASO_3_REGISTRAR_PRESTAMO,
                SagaState.COMPLETADA
            )

            # Serialización para respuesta idempotente futura
            respuesta_data = {
                "id": nuevo_prestamo.id,
                "socio_id": nuevo_prestamo.socio_id,
                "libro_id": nuevo_prestamo.libro_id,
                "ejemplar_id": nuevo_prestamo.ejemplar_id,
                "fecha_prestamo": nuevo_prestamo.fecha_prestamo.isoformat() if nuevo_prestamo.fecha_prestamo else None,
                "fecha_limite": nuevo_prestamo.fecha_limite.isoformat() if nuevo_prestamo.fecha_limite else None,
                "estado": nuevo_prestamo.estado,
                "idempotency_key": context.idempotency_key,
            }
            context.resultado = respuesta_data
            self._actualizar_registro_saga(
                registro_saga,
                context,
                respuesta_json=json.dumps(respuesta_data)
            )

            logger.info(
                "[SAGA %s] Transacción distribuida consolidada con éxito (Prestamo ID=%d, Estado=%s, Duración=%.2fms)",
                context.saga_id,
                nuevo_prestamo.id,
                nuevo_prestamo.estado,
                context.duracion_ms()
            )
            return nuevo_prestamo, context

        except Exception as exc:
            self.db.rollback()
            logger.error("[SAGA %s] Falló Paso 3: %s. Iniciando compensación en orden inverso.", context.saga_id, exc)
            self.compensar(context, registro_saga, motivo=f"Fallo en Paso 3: {exc}")
            raise SagaStepError(
                SagaStep.PASO_3_REGISTRAR_PRESTAMO.value,
                f"Falla al registrar préstamo: {exc}",
                codigo="FALLO_PASO_3_COMPENSADO"
            )

    def compensar(
        self,
        context: SagaContext,
        registro_saga: models.SagaTransaccion,
        motivo: str = "",
    ) -> None:
        """
        Ejecuta las compensaciones semánticas en estricto orden inverso de los pasos completados:
          - Reversión de Paso 3: Anular registro de préstamo si llegó a crearse.
          - Reversión de Paso 2: No-op (operación de solo lectura).
          - Reversión de Paso 1: Liberar ejemplar en Catálogo (gRPC).
        """
        context.estado = SagaState.COMPENSANDO
        self._actualizar_registro_saga(registro_saga, context, error_detalle=motivo)
        logger.info("[SAGA %s] Ejecutando compensaciones inversas para: %s", context.saga_id, context.pasos_completados)

        # Desapilar en orden inverso (LIFO)
        pasos_a_compensar = list(reversed(context.pasos_completados))

        for paso in pasos_a_compensar:
            if paso == SagaStep.PASO_3_REGISTRAR_PRESTAMO.value:
                # Compensación Paso 3: Anular préstamo si existe registro parcial
                if context.prestamo_id:
                    try:
                        prestamo = self.db.get(models.Prestamo, context.prestamo_id)
                        if prestamo:
                            prestamo.estado = "ANULADO"
                            self.db.commit()
                            logger.info("[COMPENSACIÓN] Paso 3 compensado: préstamo %d marcado como ANULADO", context.prestamo_id)
                    except Exception as e:
                        logger.error("[COMPENSACIÓN] Error al anular préstamo %d: %s", context.prestamo_id, e)

            elif paso == SagaStep.PASO_2_VERIFICAR_SANCIONES.value:
                # Compensación Paso 2: Solo lectura, idempotente, no requiere reversión
                logger.info("[COMPENSACIÓN] Paso 2 (Sanciones) es de solo lectura. No requiere acción.")

            elif paso == SagaStep.PASO_1_RESERVA_CATALOGO.value:
                # Compensación Paso 1: Liberar ejemplar reservado en Catálogo
                if context.ejemplar_id:
                    try:
                        logger.info(
                            "[COMPENSACIÓN] Paso 1: Liberando ejemplar %d en Catálogo...",
                            context.ejemplar_id
                        )
                        self.grpc_client.liberar_ejemplar(context.ejemplar_id)
                        logger.info(
                            "[COMPENSACIÓN] Paso 1 compensado exitosamente: ejemplar %d retornado a DISPONIBLE",
                            context.ejemplar_id
                        )
                    except Exception as e:
                        logger.critical(
                            "[COMPENSACIÓN CRÍTICA] Falló liberación de ejemplar %d en Catálogo: %s",
                            context.ejemplar_id,
                            e
                        )
                        raise SagaCompensationError(SagaStep.PASO_1_RESERVA_CATALOGO.value, str(e))

        context.estado = SagaState.COMPENSADA
        self._actualizar_registro_saga(registro_saga, context, error_detalle=motivo)
        logger.info("[SAGA %s] Compensación en orden inverso finalizada con éxito. Consistencia restablecida.", context.saga_id)
