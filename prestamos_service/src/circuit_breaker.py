import enum
import logging
import math
import threading
import time
from typing import Any, Callable, Tuple, Type

logger = logging.getLogger(__name__)


class CircuitState(str, enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    def __init__(self, mensaje="El circuito remoto no admite llamadas temporalmente.", codigo="CIRCUITO_ABIERTO"):
        super().__init__(mensaje)
        self.mensaje, self.codigo = mensaje, codigo


class CircuitBreaker:
    """Una sonda HALF_OPEN; resultados tardíos solo afectan su generación original."""

    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 10.0,
            half_open_success_threshold: int = 1, expected_exceptions: Tuple[Type[Exception], ...] = (Exception,),
            name: str = "default"):
        if failure_threshold < 1 or half_open_success_threshold < 1 or not math.isfinite(recovery_timeout) or recovery_timeout <= 0:
            raise ValueError("Umbrales y tiempo de recuperación deben ser positivos")
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_success_threshold = half_open_success_threshold
        self.expected_exceptions = expected_exceptions
        self.name = name
        self._state = CircuitState.CLOSED
        self._failure_count = self._success_count = 0
        self._generation = self._rejected_calls = 0
        self._probe_active = False
        self._last_state_change = time.monotonic()
        self._lock = threading.Lock()

    def _transicion(self, estado):
        previo = self._state
        self._state = estado
        self._generation += 1
        self._probe_active = False
        self._success_count = 0
        self._last_state_change = time.monotonic()
        logger.info("CircuitBreaker [%s]: %s -> %s generacion=%s", self.name, previo.value, estado.value, self._generation)

    def _evaluar_transicion_recuperacion(self):
        if self._state == CircuitState.OPEN and time.monotonic() - self._last_state_change >= self.recovery_timeout:
            self._transicion(CircuitState.HALF_OPEN)

    @property
    def state(self):
        with self._lock:
            self._evaluar_transicion_recuperacion()
            return self._state

    def get_state(self):
        return self.state.value

    def get_stats(self):
        with self._lock:
            self._evaluar_transicion_recuperacion()
            return {"name": self.name, "state": self._state.value, "failure_count": self._failure_count,
                "failure_threshold": self.failure_threshold, "recovery_timeout": self.recovery_timeout,
                "generation": self._generation, "probe_active": self._probe_active,
                "rejected_calls": self._rejected_calls,
                "seconds_since_state_change": round(time.monotonic() - self._last_state_change, 3)}

    def reset(self):
        with self._lock:
            self._transicion(CircuitState.CLOSED)
            self._failure_count = 0

    def call(self, func: Callable[..., Any], *args, **kwargs):
        with self._lock:
            self._evaluar_transicion_recuperacion()
            if self._state == CircuitState.OPEN or (self._state == CircuitState.HALF_OPEN and self._probe_active):
                self._rejected_calls += 1
                raise CircuitBreakerOpenError(f"Circuito {self.name}: abierto o sonda de recuperación ocupada")
            generacion = self._generation
            if self._state == CircuitState.HALF_OPEN:
                self._probe_active = True
        try:
            resultado = func(*args, **kwargs)
        except self.expected_exceptions:
            with self._lock:
                if generacion == self._generation:
                    self._probe_active = False
                    self._failure_count += 1
                    if self._state == CircuitState.HALF_OPEN or self._failure_count >= self.failure_threshold:
                        self._transicion(CircuitState.OPEN)
            raise
        except BaseException:
            # Error de negocio/cancelación: no es evidencia de caída ni éxito remoto.
            with self._lock:
                if generacion == self._generation:
                    self._probe_active = False
            raise
        with self._lock:
            if generacion == self._generation:
                self._probe_active = False
                if self._state == CircuitState.HALF_OPEN:
                    self._success_count += 1
                    if self._success_count >= self.half_open_success_threshold:
                        self._transicion(CircuitState.CLOSED)
                        self._failure_count = 0
                elif self._state == CircuitState.CLOSED:
                    self._failure_count = 0
        return resultado
