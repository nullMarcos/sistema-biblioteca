import enum
import time
import logging
import threading
from typing import Callable, Any, Tuple, Type

logger = logging.getLogger(__name__)


class CircuitState(str, enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    """Excepción lanzada cuando el circuito está abierto y se rechaza la llamada (fail-fast)."""

    def __init__(
        self,
        mensaje: str = "El circuito hacia el servicio de Catálogo está abierto. El servicio no está disponible temporalmente.",
        codigo: str = "CIRCUITO_ABIERTO",
    ):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.codigo = codigo


class CircuitBreaker:
    """
    Implementación del patrón Circuit Breaker con estados CLOSED, OPEN y HALF_OPEN.
    Protege llamadas remotas frente a fallos en cascada y proporciona fail-fast.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 10.0,
        half_open_success_threshold: int = 1,
        expected_exceptions: Tuple[Type[Exception], ...] = (Exception,),
        name: str = "default",
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_success_threshold = half_open_success_threshold
        self.expected_exceptions = expected_exceptions
        self.name = name

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_state_change = time.monotonic()
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._evaluar_transicion_recuperacion()
            return self._state

    def get_state(self) -> str:
        return self.state.value

    def get_stats(self) -> dict:
        with self._lock:
            self._evaluar_transicion_recuperacion()
            return {
                "name": self.name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "failure_threshold": self.failure_threshold,
                "recovery_timeout": self.recovery_timeout,
                "seconds_since_state_change": round(time.monotonic() - self._last_state_change, 2),
            }

    def reset(self) -> None:
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._last_state_change = time.monotonic()
            logger.info("CircuitBreaker [%s] reseteado manualmente a CLOSED.", self.name)

    def _evaluar_transicion_recuperacion(self) -> None:
        """Evalúa si el circuito debe pasar de OPEN a HALF_OPEN tras expirar recovery_timeout."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_state_change
            if elapsed >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._success_count = 0
                self._last_state_change = time.monotonic()
                logger.info(
                    "CircuitBreaker [%s]: Tiempo de recuperación expirado (%.2fs >= %.2fs). Transición a HALF_OPEN.",
                    self.name,
                    elapsed,
                    self.recovery_timeout,
                )

    def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            self._evaluar_transicion_recuperacion()

            if self._state == CircuitState.OPEN:
                logger.warning(
                    "CircuitBreaker [%s] está OPEN. Rechazando llamada de inmediato (fail-fast).",
                    self.name,
                )
                raise CircuitBreakerOpenError(
                    f"El circuito hacia el servicio de Catálogo está abierto. El servicio no está disponible temporalmente. "
                    f"Reintentos en pausa por {self.recovery_timeout}s."
                )

        # Ejecución fuera del lock para permitir concurrencia en llamadas
        try:
            resultado = func(*args, **kwargs)
        except self.expected_exceptions as exc:
            self._on_failure(exc)
            raise

        self._on_success()
        return resultado

    def _on_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.half_open_success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
                    self._last_state_change = time.monotonic()
                    logger.info("CircuitBreaker [%s]: Prueba exitosa en HALF_OPEN. Circuito restablecido a CLOSED.", self.name)
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0

    def _on_failure(self, exc: Exception) -> None:
        with self._lock:
            self._failure_count += 1
            logger.warning(
                "CircuitBreaker [%s]: Fallo registrado (%s). Fallos consecutivos: %d/%d.",
                self.name,
                exc,
                self._failure_count,
                self.failure_threshold,
            )

            if self._state == CircuitState.HALF_OPEN:
                # Si falló en prueba, volver de inmediato a OPEN
                self._state = CircuitState.OPEN
                self._last_state_change = time.monotonic()
                logger.warning("CircuitBreaker [%s]: Fallo en HALF_OPEN. Regresando a OPEN.", self.name)
            elif self._state == CircuitState.CLOSED and self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._last_state_change = time.monotonic()
                logger.error(
                    "CircuitBreaker [%s]: Umbral de fallos alcanzado (%d/%d). Circuito ABIERTO.",
                    self.name,
                    self._failure_count,
                    self.failure_threshold,
                )
