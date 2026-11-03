from decimal import Decimal
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class BloqueoOut(BaseModel):
    id: int
    motivo: str
    fecha_inicio: datetime
    fecha_fin: Optional[datetime] = None
    estado: str

    model_config = ConfigDict(from_attributes=True)


class MultaOut(BaseModel):
    id: int
    tipo: str
    libro_id: int
    ejemplar_id: int
    dias_atraso: Optional[int] = None
    monto: Optional[Decimal] = None
    estado: str
    fecha_emision: datetime
    fecha_pago: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class SancionSocioResponse(BaseModel):
    socio_id: int
    rut: str
    nombre: str
    tiene_sancion: bool
    bloqueado: bool
    multas_pendientes: int
    monto_total_multas: float
    bloqueos_activos: List[BloqueoOut] = Field(default_factory=list)
    multas_activas: List[MultaOut] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ErrorResponse(BaseModel):
    codigo: str
    mensaje: str


class DependencyStatus(BaseModel):
    database: str


class HealthResponse(BaseModel):
    status: str
    service: str
    dependencies: DependencyStatus


class ChaosState(BaseModel):
    delay_seconds: float
    fault_status: Optional[int] = None
