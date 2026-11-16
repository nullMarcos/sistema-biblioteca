"""Un único caso de uso durable para POST devolución y su alias DELETE."""

from datetime import datetime, timezone
import logging
import re
import time
import uuid

from src import models, schemas
from src import grpc_client as default_grpc_client
from src.database import escritura
from src.saga.exceptions import ReglaPrestamoError, OperacionPendienteError, LeasePerdidoError, respuesta_error
from src.saga.storage import LEASE_SECONDS, escritura_propietaria, soltar, proximo_intento, validar_presupuesto_lease

logger = logging.getLogger(__name__)


class DevolucionService:
    def __init__(self, db, grpc_client=None):
        self.db = db
        self.grpc_client = grpc_client or default_grpc_client
        self.owner = str(uuid.uuid4())
        validar_presupuesto_lease(self.grpc_client, None)

    def _escritura(self, identificador):
        return escritura_propietaria(self.db, models.DevolucionOperacion, identificador, self.owner)

    def devolver(self, prestamo_id, idempotency_key=None, actor_id=None):
        clave = idempotency_key if idempotency_key is not None else str(uuid.uuid4())
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", clave):
            raise ReglaPrestamoError("Clave de idempotencia inválida", "ERROR_VALIDACION", 400)
        if actor_id is not None and (not actor_id or len(actor_id) > 128):
            raise ReglaPrestamoError("Identidad interna inválida", "ACTOR_INVALIDO", 400)
        with escritura(self.db):
            previa = self.db.query(models.DevolucionOperacion).filter_by(idempotency_key=clave).first()
            if previa:
                if previa.prestamo_id != prestamo_id or previa.actor_id != actor_id:
                    raise ReglaPrestamoError("La clave pertenece a otro préstamo o actor", "IDEMPOTENCIA_PAYLOAD_DISTINTO")
                if previa.estado == "COMPLETADA":
                    return schemas.PrestamoOut.model_validate_json(previa.respuesta_json)
                raise ReglaPrestamoError(f"Devolución pendiente ({previa.operacion_id}); consulte su estado", "DEVOLUCION_PENDIENTE")
            prestamo = self.db.get(models.Prestamo, prestamo_id)
            if prestamo is None:
                raise ReglaPrestamoError("No existe un préstamo con ese ID", "PRESTAMO_NO_ENCONTRADO", 404)
            if prestamo.estado == "DEVUELTO":
                raise ReglaPrestamoError("El préstamo ya fue devuelto anteriormente", "PRESTAMO_YA_DEVUELTO")
            if prestamo.estado == "DEVOLUCION_PENDIENTE":
                raise ReglaPrestamoError("El préstamo tiene una devolución pendiente", "DEVOLUCION_PENDIENTE")
            if prestamo.estado not in {"ACTIVO", "EN_REVISION"}:
                raise ReglaPrestamoError("El estado del préstamo no permite esta transición", "ESTADO_PRESTAMO_INVALIDO")
            sagas = self.db.query(models.SagaTransaccion).filter_by(prestamo_id=prestamo_id).limit(2).all()
            if len(sagas) != 1 or not sagas[0].reserva_id:
                raise ReglaPrestamoError("Préstamo anterior sin identidad de reserva; requiere conciliación", "RESERVA_SIN_IDENTIDAD")
            if sagas[0].estado != "COMPLETADA":
                raise ReglaPrestamoError("La Saga del préstamo aún no está confirmada", "PRESTAMO_SAGA_PENDIENTE")
            operacion_id = str(uuid.uuid4())
            operacion = models.DevolucionOperacion(operacion_id=operacion_id, idempotency_key=clave,
                prestamo_id=prestamo_id, actor_id=actor_id, reserva_id=sagas[0].reserva_id,
                liberacion_id=operacion_id + ":liberar", estado="PENDIENTE", accion_pendiente="LIBERAR_EJEMPLAR",
                estado_final="DEVUELTO" if prestamo.estado == "ACTIVO" else "ANULADO",
                lease_owner=self.owner, lease_until=time.time() + LEASE_SECONDS)
            self.db.add(operacion)
            prestamo.estado = "DEVOLUCION_PENDIENTE"
            self.db.flush()
            identificador = operacion.id
        return self.ejecutar_pendiente(identificador)

    def ejecutar_pendiente(self, identificador):
        operacion_id = str(identificador)
        try:
            with self._escritura(identificador) as actual:
                operacion_id = actual.operacion_id
                prestamo = self.db.get(models.Prestamo, actual.prestamo_id)
                if prestamo is None or prestamo.estado != "DEVOLUCION_PENDIENTE":
                    raise RuntimeError("Estado local incompatible; requiere conciliación")
                if actual.accion_pendiente not in {"LIBERAR_EJEMPLAR", "FINALIZAR_DEVOLUCION"}:
                    raise RuntimeError("Acción de devolución desconocida")
                accion, ejemplar, liberacion, reserva = actual.accion_pendiente, prestamo.ejemplar_id, actual.liberacion_id, actual.reserva_id
            if accion == "LIBERAR_EJEMPLAR":
                if not self.grpc_client.liberar_ejemplar(ejemplar, liberacion, reserva):
                    raise default_grpc_client.LiberacionFallidaError()
                with self._escritura(identificador) as actual:
                    actual.accion_pendiente = "FINALIZAR_DEVOLUCION"
            with self._escritura(identificador) as actual:
                prestamo = self.db.get(models.Prestamo, actual.prestamo_id)
                if prestamo.estado != "DEVOLUCION_PENDIENTE":
                    raise RuntimeError("El préstamo cambió durante la devolución")
                prestamo.estado = actual.estado_final
                prestamo.fecha_devolucion = datetime.now(timezone.utc) if actual.estado_final == "DEVUELTO" else None
                resultado = schemas.PrestamoOut.model_validate(prestamo)
                resultado.idempotency_key = actual.idempotency_key
                actual.estado = "COMPLETADA"
                actual.accion_pendiente = None
                actual.respuesta_json = resultado.model_dump_json()
                soltar(actual)
            logger.info("devolucion=%s completada prestamo=%s estado=%s", operacion_id, resultado.id, resultado.estado)
            return resultado
        except LeasePerdidoError:
            self.db.rollback()
            raise
        except Exception as exc:
            self.db.rollback()
            logger.exception("devolucion=%s pendiente", operacion_id)
            try:
                previa = self.db.get(models.DevolucionOperacion, identificador, populate_existing=True)
                if previa.estado == "COMPLETADA":
                    return schemas.PrestamoOut.model_validate_json(previa.respuesta_json)
                with self._escritura(identificador) as actual:
                    actual.error_detalle = str(exc)
                    actual.proximo_intento = proximo_intento(actual.recuperacion_intentos)
                    soltar(actual)
            except Exception:
                self.db.rollback()
                logger.exception("devolucion=%s no se pudo guardar nuevo progreso", operacion_id)
            http = respuesta_error(exc)["http"] if isinstance(exc, (default_grpc_client.CatalogoError,
                default_grpc_client.CircuitBreakerOpenError)) else 503
            raise OperacionPendienteError(operacion_id, "Devolución pendiente de recuperación", http_status=http) from exc
