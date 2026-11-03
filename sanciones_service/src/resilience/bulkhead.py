import logging
import asyncio
from typing import Dict, Any

logger = logging.getLogger("sanciones.bulkhead")


class BulkheadFullError(Exception):
    """Excepción lanzada cuando el Bulkhead alcanza su límite de concurrencia y rechaza llamadas."""

    def __init__(
        self,
        mensaje: str = "El pool de concurrencia hacia Sanciones está saturado (Bulkhead lleno). Llamada rechazada.",
        codigo: str = "BULKHEAD_SATURADO",
    ):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.codigo = codigo


class Bulkhead:
    """
    Patrón Bulkhead (Aislamiento de Recursos):
    Acota el número máximo de llamadas simultáneas hacia el servicio externo de Sanciones
    para evitar que llamadas lentas agoten los hilos o recursos del consumidor.
    """

    def __init__(self, max_concurrent: int = 5, name: str = "sanciones_bulkhead"):
        self.max_concurrent = max_concurrent
        self.name = name

        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active_calls = 0
        self._rejected_calls = 0
        self._total_calls = 0
        self._lock = asyncio.Lock()

    @property
    def active_calls(self) -> int:
        return self._active_calls

    @property
    def rejected_calls(self) -> int:
        return self._rejected_calls

    async def acquire(self) -> bool:
        """
        Intenta adquirir un espacio en el Bulkhead sin esperar indefinidamente.
        Retorna True si adquirió el slot, o False si está saturado (fail-fast).
        """
        async with self._lock:
            self._total_calls += 1
            if self._active_calls >= self.max_concurrent:
                self._rejected_calls += 1
                logger.warning(
                    "[%s] Rechazo por saturación: %d llamadas activas (límite=%d). Rechazos acumulados: %d",
                    self.name,
                    self._active_calls,
                    self.max_concurrent,
                    self._rejected_calls,
                )
                return False

            self._active_calls += 1
            return True

    async def release(self) -> None:
        """Libera un slot de concurrencia."""
        async with self._lock:
            if self._active_calls > 0:
                self._active_calls -= 1

    async def __aenter__(self):
        acquired = await self.acquire()
        if not acquired:
            raise BulkheadFullError()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release()

    def get_stats(self) -> Dict[str, Any]:
        """Retorna estadísticas operativas del Bulkhead."""
        return {
            "name": self.name,
            "max_concurrent": self.max_concurrent,
            "active_calls": self._active_calls,
            "available_slots": max(0, self.max_concurrent - self._active_calls),
            "rejected_calls": self._rejected_calls,
            "total_calls": self._total_calls,
        }
