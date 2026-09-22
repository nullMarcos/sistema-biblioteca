from typing import List, Optional
from pydantic import BaseModel, EmailStr, ConfigDict
from datetime import datetime, date

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
    model_config = ConfigDict(from_attributes=True)