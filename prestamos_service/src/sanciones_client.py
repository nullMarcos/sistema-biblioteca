"""
Cliente HTTP resiliente para el consumo de sanciones_service desde prestamos_service.
Implementa:
  - Timeout estricto (1.5s).
  - Circuit Breaker (CLOSED, OPEN, HALF_OPEN con fail-fast inmediato).
  - Bulkhead (aislamiento de concurrencia mediante semáforo thread-safe).
  - Fallback elegante (degradación a EN_REVISION_MANUAL ante indisponibilidad).
"""

import os
import json
import time
import logging
import threading
import urllib.request
import urllib.error
from typing import Dict, Any, Optional

from src.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError

logger = logging.getLogger("prestamos.sanciones_client")

SANCIONES_DEFAULT_URL = os.getenv("SANCIONES_URL", "http://sanciones:8000")


class SancionesClientError(Exception):
    """Excepción base para errores del cliente de Sanciones."""
    pass


class SancionesTimeoutError(SancionesClientError):
    """Lanzada cuando la petición a Sanciones excede el tiempo límite."""
    pass


class SancionesServiceUnavailableError(SancionesClientError):
    """Lanzada cuando el servicio de Sanciones no está disponible o responde 5xx."""
    pass


class BulkheadFullError(SancionesClientError):
    """Lanzada cuando el límite de concurrencia hacia Sanciones está saturado."""
    pass


class ThreadSafeBulkhead:
    """Aislamiento de recursos mediante semáforo con rechazo inmediato sin bloqueo prolongado."""

    def __init__(self, max_concurrent: int = 5, name: str = "sanciones_bulkhead"):
        self.max_concurrent = max_concurrent
        self.name = name
        self._semaphore = threading.BoundedSemaphore(value=max_concurrent)
        self._active_calls = 0
        self._rejected_calls = 0
        self._total_calls = 0
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        """Intenta adquirir un slot inmediatamente sin bloquear hilos."""
        with self._lock:
            self._total_calls += 1
            if self._active_calls >= self.max_concurrent:
                self._rejected_calls += 1
                logger.warning(
                    "[%s] Bulkhead saturado: %d llamadas activas (máximo %d). Rechazos: %d",
                    self.name,
                    self._active_calls,
                    self.max_concurrent,
                    self._rejected_calls,
                )
                return False

            self._active_calls += 1
            return True

    def release(self) -> None:
        """Libera el slot de concurrencia adquirido."""
        with self._lock:
            if self._active_calls > 0:
                self._active_calls -= 1

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self.name,
                "max_concurrent": self.max_concurrent,
                "active_calls": self._active_calls,
                "available_slots": max(0, self.max_concurrent - self._active_calls),
                "rejected_calls": self._rejected_calls,
                "total_calls": self._total_calls,
            }


class SancionesResilientClient:
    """
    Cliente que integra los patrones de resiliencia requeridos para consumir Sanciones.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: float = 1.5,
        failure_threshold: int = 3,
        recovery_timeout: float = 5.0,
        max_concurrent: int = 5,
        enable_fallback: bool = True,
    ):
        self.base_url = (base_url or SANCIONES_DEFAULT_URL).rstrip("/")
        self.timeout = timeout
        self.enable_fallback = enable_fallback

        self.circuit_breaker = CircuitBreaker(
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            expected_exceptions=(SancionesTimeoutError, SancionesServiceUnavailableError),
            name="sanciones_http",
        )
        self.bulkhead = ThreadSafeBulkhead(
            max_concurrent=max_concurrent,
            name="sanciones_bulkhead",
        )

    def _http_get(self, url: str) -> Dict[str, Any]:
        """Ejecuta petición HTTP GET con cabeceras y control estricto de timeout."""
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            logger.error("HTTPError %d desde Sanciones: %s", e.code, body)
            if e.code == 404:
                raise SancionesClientError("SOCIO_NO_ENCONTRADO") from e
            raise SancionesServiceUnavailableError(f"HTTP_{e.code}") from e
        except urllib.error.URLError as e:
            if "timed out" in str(e.reason).lower():
                raise SancionesTimeoutError("TIMEOUT_SOCKET") from e
            raise SancionesServiceUnavailableError(f"CONEXION_FALLIDA: {e.reason}") from e
        except TimeoutError as e:
            raise SancionesTimeoutError("TIMEOUT_SOCKET") from e

    def _aplicar_fallback(self, identificador: str, motivo: str, error: Exception) -> Dict[str, Any]:
        """
        Regla de negocio: si el verificador externo no responde o falla,
        no se rechaza a ciegas el préstamo, se degrada a EN_REVISION_MANUAL.
        """
        logger.warning(
            "[FALLBACK] Degradando verificación de socio '%s' a EN_REVISION_MANUAL. Motivo: %s (%s)",
            identificador,
            motivo,
            error,
        )
        return {
            "estado": "EN_REVISION_MANUAL",
            "degradado": True,
            "motivo_degradacion": motivo,
            "detalle_error": str(error),
            "socio_id": identificador,
            "tiene_sancion": None,
            "bloqueado": False,
            "multas_pendientes": 0,
            "requiere_aprobacion_manual": True,
        }

    def verificar_sanciones(self, identificador: str) -> Dict[str, Any]:
        """
        Consulta las sanciones de un socio protegiendo la llamada con la cadena de resiliencia:
        Bulkhead -> Circuit Breaker -> Timeout -> Fallback.
        """
        inicio = time.monotonic()

        # 1. Validación de Circuit Breaker (fail-fast si está OPEN)
        if self.circuit_breaker.get_state() == "open":
            err = CircuitBreakerOpenError(
                mensaje=f"Circuito OPEN hacia Sanciones (fail-fast en {round((time.monotonic() - inicio)*1000, 2)}ms)"
            )
            if self.enable_fallback:
                return self._aplicar_fallback(identificador, "CIRCUIT_BREAKER_OPEN", err)
            raise err

        # 2. Validación de Bulkhead (fail-fast si está saturado)
        adquirido = self.bulkhead.acquire()
        if not adquirido:
            err = BulkheadFullError(
                f"Límite de concurrencia ({self.bulkhead.max_concurrent}) excedido"
            )
            if self.enable_fallback:
                return self._aplicar_fallback(identificador, "BULKHEAD_SATURADO", err)
            raise err

        # 3. Ejecución de la llamada protegida
        try:
            url = f"{self.base_url}/v1/sanciones/socios/{identificador}"
            data = self.circuit_breaker.call(self._http_get, url)

            tiene_sancion = bool(data.get("tiene_sancion", False))
            estado = "RECHAZADO" if tiene_sancion else "APROBADO"

            return {
                "estado": estado,
                "degradado": False,
                "tiene_sancion": tiene_sancion,
                "bloqueado": bool(data.get("bloqueado", False)),
                "multas_pendientes": int(data.get("multas_pendientes", 0)),
                "monto_total_multas": float(data.get("monto_total_multas", 0.0)),
                "detalle": data,
                "duracion_ms": round((time.monotonic() - inicio) * 1000, 2),
            }

        except (SancionesTimeoutError, SancionesServiceUnavailableError) as e:
            motivo = "TIMEOUT" if isinstance(e, SancionesTimeoutError) else "SERVICIO_NO_DISPONIBLE"
            if self.enable_fallback:
                return self._aplicar_fallback(identificador, motivo, e)
            raise

        except SancionesClientError as e:
            logger.info("Consulta de socio '%s' retornó error de cliente: %s", identificador, e)
            raise

        except Exception as e:
            if self.enable_fallback:
                return self._aplicar_fallback(identificador, "ERROR_INESPERADO", e)
            raise

        finally:
            self.bulkhead.release()

    def get_metrics(self) -> Dict[str, Any]:
        """Retorna estadísticas operativas de resiliencia."""
        return {
            "circuit_breaker": self.circuit_breaker.get_stats(),
            "bulkhead": self.bulkhead.get_stats(),
            "timeout_seconds": self.timeout,
            "fallback_enabled": self.enable_fallback,
        }


# Instancia singleton predeterminada
_sanciones_client_instance: Optional[SancionesResilientClient] = None


def get_sanciones_client() -> SancionesResilientClient:
    global _sanciones_client_instance
    if _sanciones_client_instance is None:
        _sanciones_client_instance = SancionesResilientClient()
    return _sanciones_client_instance
