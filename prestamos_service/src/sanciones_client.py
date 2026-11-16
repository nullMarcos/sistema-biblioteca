"""Consumidor efectivo de la Saga: validación estricta, deadline total, CB y Bulkhead."""

import asyncio
import json
import logging
import math
import os
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from src.presupuestos import SANCIONES_TIMEOUT, restante

logger = logging.getLogger("prestamos.sanciones_client")
SANCIONES_DEFAULT_URL = os.getenv("SANCIONES_URL", "http://sanciones:8000")


class SancionesClientError(Exception):
    pass


class SancionesTimeoutError(SancionesClientError):
    pass


class SancionesServiceUnavailableError(SancionesClientError):
    pass


class SancionesRespuestaInvalidaError(SancionesClientError):
    pass


class SocioSancionesDesconocidoError(SancionesClientError):
    pass


class BulkheadFullError(SancionesClientError):
    pass


class RespuestaSanciones(BaseModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False)
    socio_id: int = Field(gt=0)
    rut: str
    nombre: str
    tiene_sancion: bool
    bloqueado: bool
    multas_pendientes: int = Field(ge=0)
    monto_total_multas: float = Field(ge=0)
    bloqueos_activos: list[dict]
    multas_activas: list[dict]


class ThreadSafeBulkhead:
    """Cuenta la vida completa del trabajo de red; no hay Future separado que sobreviva."""

    def __init__(self, max_concurrent=5, name="sanciones_bulkhead"):
        if max_concurrent < 1:
            raise ValueError("max_concurrent debe ser positivo")
        self.max_concurrent, self.name = max_concurrent, name
        self._active_calls = self._rejected_calls = self._total_calls = self._peak_calls = 0
        self._lock = threading.Lock()

    def acquire(self):
        with self._lock:
            self._total_calls += 1
            if self._active_calls >= self.max_concurrent:
                self._rejected_calls += 1
                return False
            self._active_calls += 1
            self._peak_calls = max(self._peak_calls, self._active_calls)
            return True

    def release(self):
        with self._lock:
            if self._active_calls <= 0:
                raise RuntimeError("Liberación de Bulkhead sin adquisición")
            self._active_calls -= 1

    def get_stats(self):
        with self._lock:
            return {"name": self.name, "max_concurrent": self.max_concurrent, "active_calls": self._active_calls,
                "available_slots": self.max_concurrent - self._active_calls, "rejected_calls": self._rejected_calls,
                "total_calls": self._total_calls, "peak_calls": self._peak_calls}


class SancionesResilientClient:
    def __init__(self, base_url: Optional[str] = None, timeout: float = SANCIONES_TIMEOUT,
            failure_threshold: int = 3, recovery_timeout: float = 5.0, max_concurrent: int = 5, enable_fallback=True):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout debe ser positivo y finito")
        self.base_url = (base_url or SANCIONES_DEFAULT_URL).rstrip("/")
        self.timeout, self.enable_fallback = timeout, enable_fallback
        self.circuit_breaker = CircuitBreaker(failure_threshold=failure_threshold, recovery_timeout=recovery_timeout,
            expected_exceptions=(SancionesTimeoutError, SancionesServiceUnavailableError, SancionesRespuestaInvalidaError),
            name="sanciones_http")
        self.bulkhead = ThreadSafeBulkhead(max_concurrent=max_concurrent)
        self._metrics_lock = threading.Lock()
        self._network_calls = self._total_calls = 0
        self._last_duration_ms = 0

    def _http_get(self, url):
        """Deadline de toda la respuesta, incluso un cuerpo a goteo; cancela y cierra el socket."""
        limite = restante(self.timeout)

        async def obtener():
            async with asyncio.timeout(limite):
                async with httpx.AsyncClient(timeout=limite, trust_env=False, follow_redirects=False) as client:
                    async with client.stream("GET", url, headers={"Accept": "application/json"}) as resp:
                        if resp.status_code == 404:
                            raise SocioSancionesDesconocidoError("SOCIO_NO_ENCONTRADO")
                        if resp.status_code >= 500:
                            raise SancionesServiceUnavailableError(f"HTTP_{resp.status_code}")
                        if resp.status_code != 200:
                            raise SancionesRespuestaInvalidaError(f"HTTP_{resp.status_code}")
                        cuerpo = bytearray()
                        async for chunk in resp.aiter_bytes():
                            cuerpo.extend(chunk)
                            if len(cuerpo) > 65536:
                                raise SancionesRespuestaInvalidaError("Respuesta mayor que 64 KiB")
                        try:
                            return json.loads(cuerpo)
                        except (ValueError, UnicodeError) as exc:
                            raise SancionesRespuestaInvalidaError("JSON_INVALIDO") from exc

        with self._metrics_lock:
            self._network_calls += 1
        try:
            return asyncio.run(obtener())
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise SancionesTimeoutError("TIMEOUT_TOTAL") from exc
        except httpx.RequestError as exc:
            raise SancionesServiceUnavailableError(type(exc).__name__) from exc

    def _consultar_validar(self, identificador):
        data = self._http_get(f"{self.base_url}/v1/sanciones/socios/{quote(identificador, safe='')}")
        try:
            respuesta = RespuestaSanciones.model_validate(data)
        except ValidationError as exc:
            raise SancionesRespuestaInvalidaError("SCHEMA_INVALIDO") from exc
        identidad_correcta = (respuesta.socio_id == int(identificador)) if identificador.isdigit() else respuesta.rut == identificador
        if (not identidad_correcta or respuesta.bloqueado != bool(respuesta.bloqueos_activos)
                or respuesta.multas_pendientes != len(respuesta.multas_activas)
                or respuesta.tiene_sancion != (respuesta.bloqueado or respuesta.multas_pendientes > 0)
                or (respuesta.multas_pendientes == 0 and respuesta.monto_total_multas != 0)):
            raise SancionesRespuestaInvalidaError("IDENTIDAD_O_RESUMEN_INCONSISTENTE")
        return respuesta.model_dump()

    def _aplicar_fallback(self, identificador, motivo, error):
        logger.warning("[FALLBACK] EN_REVISION_MANUAL motivo=%s tipo=%s", motivo, type(error).__name__)
        return {"estado": "EN_REVISION_MANUAL", "degradado": True, "motivo_degradacion": motivo,
            "identificador": identificador, "tiene_sancion": None, "bloqueado": None, "multas_pendientes": None,
            "monto_total_multas": None, "detalle": None, "requiere_aprobacion_manual": True}

    def verificar_sanciones(self, identificador) -> Dict[str, Any]:
        inicio, adquirido, resultado = time.monotonic(), False, None
        try:
            if self.circuit_breaker.get_state() == "open":
                raise CircuitBreakerOpenError()
            adquirido = self.bulkhead.acquire()
            if not adquirido:
                raise BulkheadFullError("Límite local de concurrencia excedido")
            data = self.circuit_breaker.call(self._consultar_validar, str(identificador))
            resultado = {"estado": "RECHAZADO" if data["tiene_sancion"] else "APROBADO", "degradado": False,
                "tiene_sancion": data["tiene_sancion"], "bloqueado": data["bloqueado"],
                "multas_pendientes": data["multas_pendientes"], "monto_total_multas": data["monto_total_multas"], "detalle": data}
            return resultado
        except (CircuitBreakerOpenError, SancionesClientError) as exc:
            if not self.enable_fallback:
                raise
            motivos = {CircuitBreakerOpenError: "CIRCUIT_BREAKER_OPEN", BulkheadFullError: "BULKHEAD_SATURADO",
                SancionesTimeoutError: "TIMEOUT", SancionesRespuestaInvalidaError: "RESPUESTA_INVALIDA",
                SocioSancionesDesconocidoError: "SOCIO_NO_ENCONTRADO", SancionesServiceUnavailableError: "SERVICIO_NO_DISPONIBLE"}
            resultado = self._aplicar_fallback(identificador, motivos.get(type(exc), "ERROR_SANCIONES"), exc)
            return resultado
        finally:
            # asyncio.run terminó y el transporte está cerrado antes de devolver el cupo.
            if adquirido:
                self.bulkhead.release()
            duracion = round((time.monotonic() - inicio) * 1000, 3)
            if resultado is not None:
                resultado["duracion_ms"] = duracion
            with self._metrics_lock:
                self._total_calls += 1
                self._last_duration_ms = duracion
            logger.info("sanciones duracion_ms=%s resultado=%s", duracion, resultado.get("estado") if resultado else "ERROR")

    def get_metrics(self):
        with self._metrics_lock:
            llamadas = {"network_calls": self._network_calls, "total_calls": self._total_calls, "last_duration_ms": self._last_duration_ms}
        return {"circuit_breaker": self.circuit_breaker.get_stats(), "bulkhead": self.bulkhead.get_stats(),
            "timeout_seconds": self.timeout, "fallback_enabled": self.enable_fallback, **llamadas}


_sanciones_client_instance = None
_instance_lock = threading.Lock()


def get_sanciones_client():
    global _sanciones_client_instance
    with _instance_lock:
        if _sanciones_client_instance is None:
            _sanciones_client_instance = SancionesResilientClient(
                failure_threshold=int(os.getenv("SANCIONES_CB_THRESHOLD", "3")),
                recovery_timeout=float(os.getenv("SANCIONES_CB_RECOVERY_SEGS", "5")),
                max_concurrent=int(os.getenv("SANCIONES_MAX_CONCURRENT", "5")))
        return _sanciones_client_instance
