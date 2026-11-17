from typing import List, Optional, Annotated, Literal
from datetime import datetime, date, timezone
from pydantic import BaseModel, ConfigDict, StringConstraints, Field, AfterValidator
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


def fecha_utc(valor):
    # CURRENT_TIMESTAMP de SQLite está en UTC, pero el driver devuelve naive.
    return (valor.replace(tzinfo=timezone.utc) if valor.tzinfo is None else valor.astimezone(timezone.utc))


FechaUTC = Annotated[datetime, AfterValidator(fecha_utc)]


class PrestamoInput(BaseModel):
    socio_id: int = Field(gt=0, lt=9223372036854775808, strict=True)
    libro_id: int = Field(gt=0, le=2147483647, strict=True)
    idempotency_key: Optional[ClaveIdempotencia] = Field(None, description="Identidad estable de la solicitud")
    simular_fallo_paso3: bool = Field(False, strict=True,
        description="Solo demostración: requiere HABILITAR_FALLOS_DEMO=true y actor administrador; de otro modo devuelve 403")

class PrestamoOut(BaseModel):
    id: int
    socio_id: int
    libro_id: int
    ejemplar_id: int
    fecha_prestamo: FechaUTC
    fecha_limite: date
    fecha_devolucion: Optional[FechaUTC] = None
    estado: Literal["ACTIVO", "EN_REVISION", "DEVOLUCION_PENDIENTE", "DEVUELTO", "ANULADO", "COMPENSADO"]
    idempotency_key: Optional[str] = None
    links: Optional[dict] = Field(None, alias="_links",
        description="Enlaces internos /v1. ACTIVO ofrece devolver por POST; EN_REVISION cancelar por POST. Gateway publica /api/v1.")
    model_config = ConfigDict(from_attributes=True)

class SagaStatusOut(BaseModel):
    saga_id: str
    idempotency_key: Optional[str] = None
    socio_id: int
    libro_id: int
    ejemplar_id: Optional[int] = None
    prestamo_id: Optional[int] = None
    estado: Literal["INICIADA", "EJEMPLAR_RESERVADO", "SANCIONES_VERIFICADAS", "COMPLETADA", "COMPENSANDO",
        "COMPENSACION_PENDIENTE", "COMPENSADA", "FALLIDA"]
    paso_actual: str
    error_detalle: Optional[str] = None
    reserva_id: Optional[str] = None
    accion_pendiente: Optional[str] = None
    recuperacion_intentos: int = 0
    proximo_intento: float = 0
    lease_until: float = 0
    fecha_creacion: FechaUTC
    fecha_actualizacion: FechaUTC
    model_config = ConfigDict(from_attributes=True)


class DevolucionStatusOut(BaseModel):
    operacion_id: str
    idempotency_key: str
    prestamo_id: int
    reserva_id: str
    estado: Literal["PENDIENTE", "COMPLETADA"]
    estado_final: Literal["DEVUELTO", "ANULADO"]
    accion_pendiente: Optional[str] = None
    error_detalle: Optional[str] = None
    recuperacion_intentos: int
    proximo_intento: float
    lease_until: float
    fecha_creacion: FechaUTC
    fecha_actualizacion: FechaUTC
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
