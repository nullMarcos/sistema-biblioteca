"""
Jerarquía de excepciones para la transacción distribuida Saga.
"""

from typing import Optional


class SagaError(Exception):
    """Excepción base para errores durante la ejecución de la Saga."""

    def __init__(self, mensaje: str, codigo: str = "ERROR_SAGA", detalle: Optional[dict] = None):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.codigo = codigo
        self.detalle = detalle or {}


class ReglaPrestamoError(SagaError):
    def __init__(self, mensaje, codigo, http_status=409):
        super().__init__(mensaje, codigo)
        self.http_status = http_status


class OperacionPendienteError(SagaError):
    def __init__(self, saga_id, mensaje="La operación requiere recuperación", http_status=503):
        super().__init__(f"{mensaje}. Correlación: {saga_id}; consulte el estado con la misma clave", "OPERACION_PENDIENTE")
        self.http_status = http_status


class LeasePerdidoError(OperacionPendienteError):
    pass


def respuesta_error(exc):
    """Resultado terminal persistible; conserva las causas HTTP de la API."""
    from src import grpc_client as catalogo

    if hasattr(exc, "http_status"):
        http = exc.http_status
    elif isinstance(exc, (SocioSancionadoError, catalogo.SinStockError, catalogo.CatalogoConflictoError)):
        http = 409
    elif isinstance(exc, catalogo.CatalogoTimeoutError):
        http = 504
    elif isinstance(exc, (catalogo.CatalogoNoDisponibleError, catalogo.CircuitBreakerOpenError)):
        http = 503
    elif isinstance(exc, catalogo.CatalogoNotFoundError):
        http = 404
    elif isinstance(exc, catalogo.CatalogoInvalidArgumentError):
        http = 400
    elif isinstance(exc, catalogo.CatalogoError):
        http = 502
    else:
        http = 500
    return {"http": http, "error": {"codigo": getattr(exc, "codigo", "ERROR_SAGA"),
        "mensaje": getattr(exc, "mensaje", "No se pudo confirmar la operación")}}


class SagaStepError(SagaError):
    """Error originado durante la ejecución de un paso específico de la Saga."""

    def __init__(self, paso: str, mensaje: str, codigo: str = "ERROR_PASO_SAGA", detalle: Optional[dict] = None):
        super().__init__(f"Error en paso '{paso}': {mensaje}", codigo=codigo, detalle=detalle)
        self.paso = paso


class SagaCompensationError(SagaError):
    """Error ocurrido durante la ejecución de una compensación semántica inversa."""

    def __init__(self, paso: str, mensaje: str, codigo: str = "ERROR_COMPENSACION_SAGA", detalle: Optional[dict] = None):
        super().__init__(f"Fallo al compensar paso '{paso}': {mensaje}", codigo=codigo, detalle=detalle)
        self.paso = paso


class SocioSancionadoError(SagaError):
    """Lanzada cuando un socio posee bloqueos o sanciones activas que impiden el préstamo."""

    def __init__(self, socio_id: int, motivo: str = "El socio posee sanciones o bloqueos activos"):
        super().__init__(motivo, codigo="SOCIO_SANCIONADO", detalle={"socio_id": socio_id})
        self.socio_id = socio_id


class IdempotencyConflictError(SagaError):
    """Lanzada cuando se intenta ejecutar una transacción con una llave ya en procesamiento."""

    def __init__(self, idempotency_key: str):
        super().__init__(
            f"Transacción en curso para la llave de idempotencia '{idempotency_key}'",
            codigo="IDEMPOTENCIA_CONFLICTO",
            detalle={"idempotency_key": idempotency_key}
        )
        self.idempotency_key = idempotency_key
