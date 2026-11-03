import json
import time
import logging
import asyncio
import urllib.request
import urllib.error
from typing import Dict, Any, Optional

from src.resilience.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from src.resilience.bulkhead import Bulkhead, BulkheadFullError

logger = logging.getLogger("sanciones.client")


class SancionesClientError(Exception):
    """Excepción base para errores del cliente de Sanciones."""
    pass


class SancionesTimeoutError(SancionesClientError):
    """Lanzada cuando la llamada a Sanciones excede el tiempo límite."""
    pass


class SancionesServiceUnavailableError(SancionesClientError):
    """Lanzada cuando el servicio de Sanciones responde con error 5xx o no conecta."""
    pass


class SancionesResilientClient:
    """
    Cliente HTTP resiliente para consumir sanciones_service.
    Integra:
      1. Timeout estricto (ej. 1.5s).
      2. Circuit Breaker (CLOSED, OPEN, HALF_OPEN con fail-fast en milisegundos).
      3. Bulkhead (concurrencia acotada, ej. máx 5 llamadas simultáneas).
      4. Fallback (degradación elegante a ESTADO: EN_REVISION_MANUAL ante fallas).
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8001",
        timeout: float = 1.5,
        failure_threshold: int = 3,
        recovery_timeout: float = 5.0,
        max_concurrent: int = 5,
        enable_fallback: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.enable_fallback = enable_fallback

        self.circuit_breaker = CircuitBreaker(
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            name="client_circuit_breaker",
        )
        self.bulkhead = Bulkhead(
            max_concurrent=max_concurrent,
            name="client_bulkhead",
        )

    def _http_get_sync(self, url: str, timeout: float) -> Dict[str, Any]:
        """Ejecuta petición HTTP GET síncrona con urllib nativo."""
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
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

    async def _execute_http_call(self, identificador: str) -> Dict[str, Any]:
        """Realiza la llamada HTTP con control de timeout asíncrono."""
        url = f"{self.base_url}/v1/sanciones/socios/{identificador}"
        try:
            # Ejecuta la llamada en un hilo secundario respetando el timeout de asyncio
            data = await asyncio.wait_for(
                asyncio.to_thread(self._http_get_sync, url, self.timeout),
                timeout=self.timeout,
            )
            return data
        except asyncio.TimeoutError as e:
            raise SancionesTimeoutError(f"Tiempo de espera excedido (> {self.timeout}s)") from e

    def _aplicar_fallback(self, identificador: str, motivo: str, error: Exception) -> Dict[str, Any]:
        """
        Regla de negocio: Si el circuito está abierto, hay timeout o falla el servicio,
        no se rechaza el préstamo a ciegas: se degrada a EN_REVISION_MANUAL.
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
            "requiere_aprobacion_manual": True,
        }

    async def verificar_sanciones(self, identificador: str) -> Dict[str, Any]:
        """
        Consulta las sanciones de un socio aplicando toda la cadena de resiliencia:
        Bulkhead -> Circuit Breaker -> Timeout -> Llamada HTTP -> Fallback si aplica.
        """
        inicio = time.monotonic()

        # 1. Comprobar si el Circuit Breaker permite la llamada (fail-fast si está OPEN)
        if not await self.circuit_breaker.can_execute():
            err = CircuitBreakerOpenError(
                mensaje=f"Circuito OPEN hacia Sanciones (fail-fast en {round((time.monotonic() - inicio)*1000, 2)}ms)"
            )
            if self.enable_fallback:
                return self._aplicar_fallback(identificador, "CIRCUIT_BREAKER_OPEN", err)
            raise err

        # 2. Intentar adquirir slot en el Bulkhead (fail-fast si está saturado)
        adquirido = await self.bulkhead.acquire()
        if not adquirido:
            err = BulkheadFullError(
                mensaje=f"Límite de concurrencia ({self.bulkhead.max_concurrent}) excedido"
            )
            if self.enable_fallback:
                return self._aplicar_fallback(identificador, "BULKHEAD_SATURADO", err)
            raise err

        # 3. Ejecutar llamada protegida dentro del Bulkhead
        try:
            data = await self._execute_http_call(identificador)
            await self.circuit_breaker.record_success()

            # Evaluación de respuesta limpia de negocio
            tiene_sancion = data.get("tiene_sancion", False)
            estado = "RECHAZADO" if tiene_sancion else "APROBADO"

            return {
                "estado": estado,
                "degradado": False,
                "tiene_sancion": tiene_sancion,
                "bloqueado": data.get("bloqueado", False),
                "multas_pendientes": data.get("multas_pendientes", 0),
                "monto_total_multas": data.get("monto_total_multas", 0.0),
                "detalle": data,
                "duracion_ms": round((time.monotonic() - inicio) * 1000, 2),
            }

        except (SancionesTimeoutError, SancionesServiceUnavailableError) as e:
            # Fallo técnico remoto: computa en el contador de fallos del Circuit Breaker
            await self.circuit_breaker.record_failure(e)
            motivo = "TIMEOUT" if isinstance(e, SancionesTimeoutError) else "SERVICIO_NO_DISPONIBLE"
            if self.enable_fallback:
                return self._aplicar_fallback(identificador, motivo, e)
            raise

        except SancionesClientError as e:
            # Error de cliente (ej. 404 socio no encontrado) no degrada el circuito
            logger.info("Consulta de socio '%s' retornó: %s", identificador, e)
            raise

        except Exception as e:
            await self.circuit_breaker.record_failure(e)
            if self.enable_fallback:
                return self._aplicar_fallback(identificador, "ERROR_INESPERADO", e)
            raise

        finally:
            await self.bulkhead.release()

    def get_metrics(self) -> Dict[str, Any]:
        """Retorna el estado y métricas combinadas de resiliencia."""
        return {
            "circuit_breaker": self.circuit_breaker.get_stats(),
            "bulkhead": self.bulkhead.get_stats(),
            "timeout_seconds": self.timeout,
            "fallback_enabled": self.enable_fallback,
        }
