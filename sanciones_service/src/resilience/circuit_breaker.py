import time
import enum
import logging
import asyncio
from typing import Callable, Any, Optional, Dict

logger = logging.getLogger("sanciones.circuit_breaker")


class CircuitState(str, enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    """Excepción lanzada cuando el circuito hacia Sanciones está abierto (fail-fast)."""

    def __init__(
        self,
        mensaje: str = "El circuito hacia el servicio de Sanciones está abierto. Llamada rechazada de inmediato.",
        codigo: str = "CIRCUITO_ABIERTO",
    ):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.codigo = codigo


class CircuitBreaker:
    """
    Máquina de estados Circuit Breaker para proteger llamadas hacia Sanciones.
    Estados:
      - CLOSED: Operación normal. Cuenta fallos consecutivos.
      - OPEN: Falla consecutiva >= failure_threshold. Rechaza llamadas en milisegundos (fail-fast).
      - HALF_OPEN: Tras expirar recovery_timeout, permite probar la disponibilidad del servicio.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 5.0,
        half_open_success_threshold: int = 1,
        name: str = "sanciones_circuit_breaker",
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_success_threshold = half_open_success_threshold
        self.name = name

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_state_change = time.monotonic()
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        self._evaluar_recuperacion()
        return self._state

    def get_state(self) -> str:
        return self.state.value

    def _evaluar_recuperacion(self) -> None:
        """Transiciona de OPEN a HALF_OPEN si el tiempo de recuperación ha vencido."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_state_change
            if elapsed >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._success_count = 0
                self._last_state_change = time.monotonic()
                logger.info(
                    "[%s] Tiempo de recuperación vencido (%.2fs >= %.2fs). Transición: OPEN -> HALF_OPEN",
                    self.name,
                    elapsed,
                    self.recovery_timeout,
                )

    async def can_execute(self) -> bool:
        """Verifica si se permite ejecutar una llamada a través del circuito."""
        async with self._lock:
            self._evaluar_recuperacion()
            if self._state == CircuitState.OPEN:
                return False
            return True

    async def record_success(self) -> None:
        """Registra una llamada exitosa."""
        async with self._lock:
            self._evaluar_recuperacion()
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                logger.info(
                    "[%s] Éxito en HALF_OPEN (%d/%d)",
                    self.name,
                    self._success_count,
                    self.half_open_success_threshold,
                )
                if self._success_count >= self.half_open_success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
                    self._last_state_change = time.monotonic()
                    logger.info("[%s] Servicio recuperado. Transición: HALF_OPEN -> CLOSED", self.name)
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0

    async def record_failure(self, error: Optional[Exception] = None) -> None:
        """Registra un fallo en la llamada remota."""
        async with self._lock:
            self._evaluar_recuperacion()
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._failure_count = self.failure_threshold
                self._success_count = 0
                self._last_state_change = time.monotonic()
                logger.warning(
                    "[%s] Falló llamada de prueba en HALF_OPEN (%s). Transición inmediata a OPEN",
                    self.name,
                    error,
                )
            elif self._state == CircuitState.CLOSED:
                self._failure_count += 1
                logger.warning(
                    "[%s] Fallo registrado (%d/%d): %s",
                    self.name,
                    self._failure_count,
                    self.failure_threshold,
                    error,
                )
                if self._failure_count >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    self._last_state_change = time.monotonic()
                    logger.error(
                        "[%s] Umbral de fallos alcanzado (%d). Transición: CLOSED -> OPEN (fail-fast activo)",
                        self.name,
                        self._failure_count,
                    )

    def reset(self) -> None:
        """Restablece manualmente el circuito a CLOSED."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_state_change = time.monotonic()
        logger.info("[%s] Circuito restablecido manualmente a CLOSED", self.name)

    def get_stats(self) -> Dict[str, Any]:
        """Retorna estadísticas operativas del Circuit Breaker."""
        self._evaluar_recuperacion()
        return {
            "name": self.name,
            "state": self._state.value,
            "failure_count": self._failure_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
            "seconds_since_state_change": round(time.monotonic() - self._last_state_change, 3),
        }
