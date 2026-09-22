import os
import sys
import time
import logging
import grpc

PROTOS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "protos"))
if PROTOS_DIR not in sys.path:
    sys.path.insert(0, PROTOS_DIR)

import catalogo_pb2
import catalogo_pb2_grpc
from src.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError

logger = logging.getLogger(__name__)

CATALOGO_HOST = os.getenv("CATALOGO_GRPC_HOST", "localhost")
CATALOGO_PORT = os.getenv("CATALOGO_GRPC_PORT", "50051")
TIME_OUT_SEGS = float(os.getenv("CATALOGO_TIMEOUT_SEGS", "2.0"))
MAX_RETRIES = int(os.getenv("CATALOGO_MAX_RETRIES", "2"))
INITIAL_BACKOFF_SEGS = float(os.getenv("CATALOGO_INITIAL_BACKOFF_SEGS", "0.1"))

canal = grpc.insecure_channel(f"{CATALOGO_HOST}:{CATALOGO_PORT}")
stub = catalogo_pb2_grpc.CatalogoStub(canal)


# --- JERARQUÍA DE EXCEPCIONES DE CATÁLOGO ---

class CatalogoError(Exception):
    """Excepción base para errores relacionados con el servicio de Catálogo."""

    def __init__(self, mensaje: str, codigo: str = "ERROR_CATALOGO"):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.codigo = codigo


class CatalogoNoDisponibleError(CatalogoError):
    """Falla de conectividad o servicio no disponible (UNAVAILABLE)."""

    def __init__(self, mensaje: str = "El servicio de Catálogo no responde o no está disponible."):
        super().__init__(mensaje, codigo="CATALOGO_NO_DISPONIBLE")


class CatalogoTimeoutError(CatalogoNoDisponibleError):
    """Tiempo de espera agotado al comunicarse con Catálogo (DEADLINE_EXCEEDED)."""

    def __init__(self, mensaje: str = "Tiempo de espera agotado al comunicarse con el servicio de Catálogo."):
        super().__init__(mensaje)
        self.codigo = "CATALOGO_TIMEOUT"


class CatalogoNotFoundError(CatalogoError):
    """Recurso no encontrado en el servicio de Catálogo (NOT_FOUND)."""

    def __init__(self, mensaje: str = "El recurso solicitado no fue encontrado en Catálogo."):
        super().__init__(mensaje, codigo="CATALOGO_NO_ENCONTRADO")


class CatalogoInvalidArgumentError(CatalogoError):
    """Parámetro o argumento inválido enviado a Catálogo (INVALID_ARGUMENT)."""

    def __init__(self, mensaje: str = "Parámetro inválido enviado al servicio de Catálogo."):
        super().__init__(mensaje, codigo="PARAMETRO_INVALIDO")


class SinStockError(CatalogoError):
    """No hay ejemplares disponibles para el libro solicitado."""

    def __init__(self, motivo: str = "No hay stock disponible"):
        super().__init__(motivo, codigo="SIN_STOCK")


class LiberacionFallidaError(CatalogoError):
    """Falla al liberar el ejemplar en Catálogo."""

    def __init__(self, motivo: str = "No se pudo liberar el ejemplar en Catalogo"):
        super().__init__(motivo, codigo="LIBERACION_FALLIDA")


# --- CIRCUIT BREAKER ---

catalogo_circuit_breaker = CircuitBreaker(
    failure_threshold=int(os.getenv("CIRCUIT_BREAKER_THRESHOLD", "3")),
    recovery_timeout=float(os.getenv("CIRCUIT_BREAKER_RECOVERY_TIMEOUT", "10.0")),
    expected_exceptions=(CatalogoNoDisponibleError, CatalogoTimeoutError),
    name="catalogo_grpc",
)


# --- TRADUCCIÓN DE ERRORES GRPC ---

def _traducir_rpc_error(e: grpc.RpcError) -> CatalogoError:
    """Traduce códigos de estado gRPC a excepciones semánticas de dominio."""
    code = e.code() if hasattr(e, "code") else None
    details = e.details() if hasattr(e, "details") else str(e)

    if code == grpc.StatusCode.UNAVAILABLE:
        return CatalogoNoDisponibleError(f"Servicio de Catálogo no disponible: {details or 'conexión rechazada'}")
    elif code == grpc.StatusCode.DEADLINE_EXCEEDED:
        return CatalogoTimeoutError(f"Tiempo de espera agotado al comunicarse con Catálogo: {details or 'deadline exceeded'}")
    elif code == grpc.StatusCode.NOT_FOUND:
        return CatalogoNotFoundError(f"Recurso no encontrado en Catálogo: {details or 'no encontrado'}")
    elif code == grpc.StatusCode.INVALID_ARGUMENT:
        return CatalogoInvalidArgumentError(f"Argumento inválido enviado a Catálogo: {details or 'argumento inválido'}")
    else:
        return CatalogoError(f"Error en Catálogo ({code}): {details}", codigo="ERROR_CATALOGO")


# --- POLÍTICA DE REINTENTOS CON BACKOFF EXPONENCIAL ---

def _ejecutar_con_reintentos(operacion, *args, **kwargs):
    """
    Ejecuta una llamada remota aplicando reintentos con backoff exponencial
    exclusivamente ante errores transitorios de red/disponibilidad.
    """
    intentos = 0
    backoff = INITIAL_BACKOFF_SEGS

    while True:
        try:
            return operacion(*args, **kwargs)
        except grpc.RpcError as e:
            err = _traducir_rpc_error(e)
            if isinstance(err, (CatalogoNoDisponibleError, CatalogoTimeoutError)) and intentos < MAX_RETRIES:
                intentos += 1
                logger.warning(
                    "Fallo transitorio gRPC (%s). Reintento %d/%d tras %.2fs...",
                    err.mensaje,
                    intentos,
                    MAX_RETRIES,
                    backoff,
                )
                time.sleep(backoff)
                backoff *= 2
                continue
            raise err from e


# --- OPERACIONES DEL CLIENTE ---

def _reservar_raw(libro_id: int) -> int:
    request = catalogo_pb2.ReservaRequest(libro_id=libro_id)
    response = _ejecutar_con_reintentos(stub.ReservarEjemplar, request, timeout=TIME_OUT_SEGS)

    if not response.exito:
        raise SinStockError(response.motivo or "No hay stock disponible")

    return response.ejemplar_id


def reservar_ejemplar(libro_id: int) -> int:
    """Reserva un ejemplar en Catálogo protegido por Circuit Breaker y política de reintentos."""
    return catalogo_circuit_breaker.call(_reservar_raw, libro_id)


def _liberar_raw(ejemplar_id: int) -> bool:
    request = catalogo_pb2.LiberarRequest(ejemplar_id=ejemplar_id)
    response = _ejecutar_con_reintentos(stub.LiberarEjemplar, request, timeout=TIME_OUT_SEGS)

    if not response.exito:
        raise LiberacionFallidaError(response.motivo or "No se pudo liberar el ejemplar en Catalogo")

    return True


def liberar_ejemplar(ejemplar_id: int) -> bool:
    """Libera un ejemplar en Catálogo protegido por Circuit Breaker y política de reintentos."""
    return catalogo_circuit_breaker.call(_liberar_raw, ejemplar_id)


def verificar_salud_grpc(timeout: float = 1.0) -> dict:
    """
    Verifica la conectividad con el servicio gRPC mediante el canal.
    Retorna información estructurada de su estado.
    """
    try:
        grpc.channel_ready_future(canal).result(timeout=timeout)
        return {"status": "ok", "state": "ready"}
    except Exception as exc:
        logger.warning("Fallo en verificación de salud gRPC de Catálogo: %s", exc)
        return {"status": "unreachable", "error": str(exc)}


def cerrar_canal():
    """Cierra el canal de comunicación gRPC de forma ordenada."""
    canal.close()