from decimal import Decimal
from typing import List, Optional, Annotated
from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field, AfterValidator


def fecha_utc(valor):
    # El driver SQLite pierde la zona incluso con DateTime(timezone=True).
    return (valor.replace(tzinfo=timezone.utc) if valor.tzinfo is None else valor.astimezone(timezone.utc))


FechaUTC = Annotated[datetime, AfterValidator(fecha_utc)]


class BloqueoOut(BaseModel):
    id: int
    motivo: str
    fecha_inicio: FechaUTC
    fecha_fin: Optional[FechaUTC] = None
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
    fecha_emision: FechaUTC
    fecha_pago: Optional[FechaUTC] = None

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
