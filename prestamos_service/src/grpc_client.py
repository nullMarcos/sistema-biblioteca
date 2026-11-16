import os
import sys
import time
import logging
import threading
import grpc

PROTOS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "protos"))
if PROTOS_DIR not in sys.path:
    sys.path.insert(0, PROTOS_DIR)

import catalogo_pb2
import catalogo_pb2_grpc
from src.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from src.presupuestos import CATALOGO_TIMEOUT, CATALOGO_RETRIES, CATALOGO_BACKOFF, restante

logger = logging.getLogger(__name__)

CATALOGO_HOST = os.getenv("CATALOGO_GRPC_HOST", "localhost")
CATALOGO_PORT = os.getenv("CATALOGO_GRPC_PORT", "50051")
TIME_OUT_SEGS = CATALOGO_TIMEOUT
MAX_RETRIES = CATALOGO_RETRIES
INITIAL_BACKOFF_SEGS = CATALOGO_BACKOFF

canal = grpc.insecure_channel(f"{CATALOGO_HOST}:{CATALOGO_PORT}", options=(("grpc.enable_retries", 0),))
stub = catalogo_pb2_grpc.CatalogoStub(canal)
_metricas_lock = threading.Lock()
_metricas = {"calls": 0, "attempts": 0, "last_duration_ms": 0}


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


class CatalogoConflictoError(CatalogoError):
    def __init__(self, mensaje="Conflicto de identidad o estado de reserva"):
        super().__init__(mensaje, codigo="CATALOGO_CONFLICTO")


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
    elif code in (grpc.StatusCode.ALREADY_EXISTS, grpc.StatusCode.FAILED_PRECONDITION):
        return CatalogoConflictoError(details)
    else:
        return CatalogoError(f"Error en Catálogo ({code}): {details}", codigo="ERROR_CATALOGO")


# --- POLÍTICA DE REINTENTOS CON BACKOFF EXPONENCIAL ---

def _ejecutar_con_reintentos(operacion, *args, **kwargs):
    """
    Ejecuta una llamada remota aplicando reintentos con backoff exponencial
    exclusivamente ante errores transitorios de red/disponibilidad.
    """
    inicio = time.monotonic()
    presupuesto = TIME_OUT_SEGS * (MAX_RETRIES + 1) + INITIAL_BACKOFF_SEGS * (2 ** MAX_RETRIES - 1)
    limite = inicio + restante(presupuesto)
    intentos, backoff = 0, INITIAL_BACKOFF_SEGS
    try:
        while True:
            disponible = limite - time.monotonic()
            if disponible <= 0:
                raise CatalogoTimeoutError("Presupuesto total de gRPC agotado")
            llamada = {**kwargs, "timeout": restante(min(kwargs.get("timeout", TIME_OUT_SEGS), disponible))}
            intentos += 1
            try:
                return operacion(*args, **llamada)
            except grpc.RpcError as exc:
                err = _traducir_rpc_error(exc)
                if (not isinstance(err, CatalogoNoDisponibleError) or intentos > MAX_RETRIES
                        or limite - time.monotonic() <= backoff):
                    raise err from exc
                logger.warning("gRPC intento=%s/%s codigo=%s backoff_s=%.3f", intentos, MAX_RETRIES + 1, err.codigo, backoff)
                time.sleep(backoff)
                backoff *= 2
    finally:
        duracion = round((time.monotonic() - inicio) * 1000, 3)
        with _metricas_lock:
            _metricas["calls"] += 1
            _metricas["attempts"] += intentos
            _metricas["last_duration_ms"] = duracion
        logger.info("gRPC operacion=%s intentos=%s duracion_ms=%s", getattr(args[0], "operacion_id", "lectura") if args else "lectura", intentos, duracion)


def metricas():
    with _metricas_lock:
        return {**_metricas, "timeout_s": TIME_OUT_SEGS, "max_retries": MAX_RETRIES,
            "initial_backoff_s": INITIAL_BACKOFF_SEGS, "circuit_breaker": catalogo_circuit_breaker.get_stats()}


# --- OPERACIONES DEL CLIENTE ---

def _reservar_raw(libro_id: int, operacion_id: str) -> int:
    request = catalogo_pb2.ReservaRequest(libro_id=libro_id, operacion_id=operacion_id)
    response = _ejecutar_con_reintentos(stub.ReservarEjemplar, request, timeout=TIME_OUT_SEGS)

    if response.estado in {"CANCELADA", "LIBERADA"}:
        raise CatalogoConflictoError("La reserva ya está terminada: " + response.estado)
    if response.estado == "SIN_STOCK" and not response.exito and response.ejemplar_id == 0:
        raise SinStockError(response.motivo or "No hay stock disponible")
    if response.estado != "RESERVADA" or not response.exito or response.ejemplar_id <= 0:
        raise CatalogoError("Catálogo no confirmó una reserva identificable y vigente", codigo="RESPUESTA_CATALOGO_INVALIDA")

    return response.ejemplar_id


def reservar_ejemplar(libro_id: int, operacion_id: str) -> int:
    """Reserva un ejemplar en Catálogo protegido por Circuit Breaker y política de reintentos."""
    return catalogo_circuit_breaker.call(_reservar_raw, libro_id, operacion_id)


def _liberar_raw(ejemplar_id: int, operacion_id: str, reserva_id: str) -> bool:
    request = catalogo_pb2.LiberarRequest(ejemplar_id=ejemplar_id, operacion_id=operacion_id, reserva_id=reserva_id)
    response = _ejecutar_con_reintentos(stub.LiberarEjemplar, request, timeout=TIME_OUT_SEGS)

    if not response.exito:
        raise LiberacionFallidaError(response.motivo or "No se pudo liberar el ejemplar en Catalogo")
    if response.estado != "LIBERADA":
        raise CatalogoError("Catálogo no confirmó el estado de liberación", codigo="RESPUESTA_CATALOGO_INVALIDA")

    return True


def liberar_ejemplar(ejemplar_id: int, operacion_id: str, reserva_id: str) -> bool:
    """Libera un ejemplar en Catálogo protegido por Circuit Breaker y política de reintentos."""
    return catalogo_circuit_breaker.call(_liberar_raw, ejemplar_id, operacion_id, reserva_id)


def cancelar_reserva(libro_id: int, reserva_id: str, operacion_id: str) -> bool:
    def ejecutar():
        request = catalogo_pb2.CancelacionRequest(libro_id=libro_id, reserva_id=reserva_id, operacion_id=operacion_id)
        response = _ejecutar_con_reintentos(stub.CancelarReserva, request, timeout=TIME_OUT_SEGS)
        if not response.exito:
            raise LiberacionFallidaError(response.motivo)
        if response.estado != "CANCELADA":
            raise CatalogoError("Catálogo no confirmó el estado de cancelación", codigo="RESPUESTA_CATALOGO_INVALIDA")
        return True
    return catalogo_circuit_breaker.call(ejecutar)


def consultar_operacion(operacion_id: str):
    return catalogo_circuit_breaker.call(_ejecutar_con_reintentos, stub.ConsultarOperacion,
        catalogo_pb2.OperacionRequest(operacion_id=operacion_id), timeout=TIME_OUT_SEGS)


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
