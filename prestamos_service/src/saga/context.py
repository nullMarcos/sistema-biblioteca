"""
Contexto de ejecución en memoria para una instancia de Saga.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from src.saga.constants import SagaState, SagaStep


@dataclass
class SagaContext:
    """Mantiene el estado y variables operativas durante la ejecución de una Saga."""
    socio_id: int
    libro_id: int
    idempotency_key: Optional[str] = None
    simular_fallo_paso3: bool = False
    saga_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ejemplar_id: Optional[int] = None
    prestamo_id: Optional[int] = None
    estado: SagaState = SagaState.INICIADA
    paso_actual: SagaStep = SagaStep.INICIO
    pasos_completados: List[str] = field(default_factory=list)
    degradado: bool = False
    motivo_degradacion: Optional[str] = None
    error: Optional[str] = None
    resultado: Optional[Dict[str, Any]] = None
    tiempo_inicio: float = field(default_factory=time.monotonic)

    def registrar_paso_exitoso(self, paso: SagaStep, estado: SagaState):
        """Registra un paso completado en la pila de ejecución y actualiza el estado."""
        self.pasos_completados.append(paso.value)
        self.paso_actual = paso
        self.estado = estado

    def duracion_ms(self) -> float:
        """Calcula la duración transcurrida en milisegundos."""
        return round((time.monotonic() - self.tiempo_inicio) * 1000, 2)
