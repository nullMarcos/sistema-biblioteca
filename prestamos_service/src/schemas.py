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

class PrestamoInput(BaseModel):
    socio_id: int
    libro_id: int
    idempotency_key: Optional[str] = Field(None, description="Clave única para garantizar idempotencia de la transacción")
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