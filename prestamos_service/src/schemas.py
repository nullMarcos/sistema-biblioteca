from typing import List, Optional, Annotated
from datetime import datetime, date
from pydantic import BaseModel, ConfigDict, StringConstraints, Field
try:
    import email_validator  # noqa: F401
    from pydantic import EmailStr
except ImportError:
    # Fallback seguro cuando email_validator no está en el entorno local
    EmailStr = Annotated[str, StringConstraints(pattern=r"^[\w\.-]+@[\w\.-]+\.\w+$")]

class SocioInput(BaseModel):
    rut: str = Field(..., max_length=12, description="RUT del socio (ej. 11.111.111-1)")
    nombre: str
    correo: EmailStr

class SocioOut(SocioInput):
    id: int
    prestamos: List["PrestamoOut"] = []
    model_config = ConfigDict(from_attributes=True)

ClaveIdempotencia = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]


class PrestamoInput(BaseModel):
    socio_id: int = Field(gt=0, strict=True)
    libro_id: int = Field(gt=0, le=2147483647, strict=True)
    idempotency_key: Optional[ClaveIdempotencia] = Field(None, description="Identidad estable de la solicitud")
    simular_fallo_paso3: Optional[bool] = Field(False, description="Bandera para inducir fallo en el paso 3 y verificar compensación")

class PrestamoOut(BaseModel):
    id: int
    socio_id: int
    libro_id: int
    ejemplar_id: int
    fecha_prestamo: datetime
    fecha_limite: date
    fecha_devolucion: Optional[datetime] = None
    estado: str
    idempotency_key: Optional[str] = None
    links: Optional[dict] = Field(None, alias="_links")
    model_config = ConfigDict(from_attributes=True)

class SagaStatusOut(BaseModel):
    saga_id: str
    idempotency_key: Optional[str] = None
    socio_id: int
    libro_id: int
    ejemplar_id: Optional[int] = None
    prestamo_id: Optional[int] = None
    estado: str
    paso_actual: str
    error_detalle: Optional[str] = None
    reserva_id: Optional[str] = None
    accion_pendiente: Optional[str] = None
    recuperacion_intentos: int = 0
    proximo_intento: float = 0
    lease_until: float = 0
    fecha_creacion: datetime
    fecha_actualizacion: datetime
    model_config = ConfigDict(from_attributes=True)


class DevolucionStatusOut(BaseModel):
    operacion_id: str
    idempotency_key: str
    prestamo_id: int
    reserva_id: str
    estado: str
    estado_final: str
    accion_pendiente: Optional[str] = None
    error_detalle: Optional[str] = None
    recuperacion_intentos: int
    proximo_intento: float
    lease_until: float
    fecha_creacion: datetime
    fecha_actualizacion: datetime
    model_config = ConfigDict(from_attributes=True)

class ErrorResponse(BaseModel):
    codigo: str
    mensaje: str

class DependencyStatus(BaseModel):
    database: str
    catalogo_grpc: str
    circuit_breaker: str
    sanciones_circuit_breaker: Optional[str] = None

class HealthResponse(BaseModel):
    status: str
    service: str
    dependencies: DependencyStatus
