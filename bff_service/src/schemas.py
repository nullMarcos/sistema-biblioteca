from datetime import date, timezone
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_serializer, model_validator

ClaveIdempotencia = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
EstadoPrestamo = Literal["ACTIVO", "EN_REVISION", "DEVOLUCION_PENDIENTE", "DEVUELTO", "ANULADO", "COMPENSADO"]
EstadoDependencia = Literal["DISPONIBLE", "NO_CONSULTADO", "TIMEOUT", "NO_DISPONIBLE", "CIRCUITO_ABIERTO", "SATURADO", "RESPUESTA_INVALIDA", "NO_ENCONTRADO", "RECHAZADA"]


class ErrorResponse(BaseModel):
    codigo: str
    mensaje: str


class SolicitudInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    socio_id: int = Field(gt=0, lt=9223372036854775808)
    libro_id: int = Field(gt=0, le=2147483647)


class PrestamoRemoto(BaseModel):
    model_config = ConfigDict(strict=True)
    id: int = Field(gt=0)
    socio_id: int = Field(gt=0)
    libro_id: int = Field(gt=0, le=2147483647)
    ejemplar_id: int = Field(gt=0)
    fecha_prestamo: AwareDatetime
    fecha_limite: date
    fecha_devolucion: AwareDatetime | None = None
    estado: EstadoPrestamo
    idempotency_key: str | None = None

    @field_serializer("fecha_prestamo", "fecha_devolucion")
    def fechas_utc(self, valor):
        return valor.astimezone(timezone.utc) if valor is not None else None


class SocioRemoto(BaseModel):
    model_config = ConfigDict(strict=True)
    id: int = Field(gt=0)
    rut: str = Field(min_length=1, max_length=12)
    nombre: str = Field(min_length=1)
    prestamos: list[PrestamoRemoto] = Field(max_length=2000)

    @model_validator(mode="after")
    def propietarios(self):
        if any(p.socio_id != self.id for p in self.prestamos) or len({p.id for p in self.prestamos}) != len(self.prestamos):
            raise ValueError("Préstamos de otro socio o IDs duplicados")
        return self


class SocioWeb(BaseModel):
    id: int
    nombre: str


class PrestamoWeb(PrestamoRemoto):
    titulo: str | None = None
    autor: str | None = None


class SolicitudOut(PrestamoRemoto):
    idempotency_key: ClaveIdempotencia
    request_id: str


class BloqueoRemoto(BaseModel):
    model_config = ConfigDict(strict=True)
    id: int = Field(gt=0)
    motivo: str
    fecha_inicio: AwareDatetime
    fecha_fin: AwareDatetime | None
    estado: Literal["ACTIVO"]


class MultaRemota(BaseModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False)
    id: int = Field(gt=0)
    tipo: str
    libro_id: int = Field(gt=0)
    ejemplar_id: int = Field(gt=0)
    estado: Literal["PENDIENTE"]
    fecha_emision: AwareDatetime


class SancionesRemotas(BaseModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False)
    socio_id: int = Field(gt=0)
    rut: str
    nombre: str
    tiene_sancion: bool
    bloqueado: bool
    multas_pendientes: int = Field(ge=0)
    monto_total_multas: float = Field(ge=0)
    bloqueos_activos: list[BloqueoRemoto]
    multas_activas: list[MultaRemota]

    @model_validator(mode="after")
    def coherencia(self):
        if (self.bloqueado != bool(self.bloqueos_activos) or self.multas_pendientes != len(self.multas_activas)
                or self.tiene_sancion != (self.bloqueado or self.multas_pendientes > 0)
                or (self.multas_pendientes == 0 and self.monto_total_multas != 0)):
            raise ValueError("Resumen de sanciones inconsistente")
        return self


class SancionesWeb(BaseModel):
    estado: Literal["VERIFICADO", "NO_REGISTRADO", "DESCONOCIDO"]
    tiene_sancion: bool | None = None
    bloqueado: bool | None = None
    multas_pendientes: int | None = None
    monto_total_multas: float | None = None
    motivo: EstadoDependencia | None = None


class Dependencias(BaseModel):
    prestamos: EstadoDependencia
    catalogo: EstadoDependencia
    sanciones: EstadoDependencia


class Advertencia(BaseModel):
    codigo: str
    mensaje: str
    libro_id: int | None = None


class ResumenOut(BaseModel):
    socio: SocioWeb
    prestamos_activos: list[PrestamoWeb]
    prestamos_en_revision: list[PrestamoWeb]
    prestamos_pendientes: list[PrestamoWeb]
    sanciones: SancionesWeb
    parcial: bool
    dependencias: Dependencias
    advertencias: list[Advertencia]
    request_id: str


class HealthOut(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["bff"] = "bff"
