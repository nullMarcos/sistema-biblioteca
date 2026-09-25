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
    nombre: str
    correo: EmailStr

class SocioOut(SocioInput):
    id: int
    prestamos: List["PrestamoOut"] = []
    model_config = ConfigDict(from_attributes=True)

class PrestamoInput(BaseModel):
    socio_id: int
    libro_id: int

class PrestamoOut(PrestamoInput):
    id: int
    ejemplar_id: int
    fecha_prestamo: datetime
    fecha_limite: date
    fecha_devolucion: Optional[datetime]
    estado: str
    links: Optional[dict] = Field(None, alias="_links")
    model_config = ConfigDict(from_attributes=True)

class ErrorResponse(BaseModel):
    codigo: str
    mensaje: str

class DependencyStatus(BaseModel):
    database: str
    catalogo_grpc: str
    circuit_breaker: str

class HealthResponse(BaseModel):
    status: str
    service: str
    dependencies: DependencyStatus