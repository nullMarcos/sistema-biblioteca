import re
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Configuracion(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BFF_", populate_by_name=True, extra="ignore", allow_inf_nan=False, frozen=True)

    api_key: str = Field(min_length=1, validation_alias="API_KEY", repr=False)
    prestamos_url: str = "http://prestamos:8000"
    sanciones_url: str = "http://sanciones:8000"
    catalogo_target: str = "catalogo:50051"
    resumen_timeout: float = Field(3, gt=0, validation_alias="BFF_RESUMEN_TIMEOUT_SEGS")
    prestamos_timeout: float = Field(0.8, gt=0, validation_alias="BFF_PRESTAMOS_TIMEOUT_SEGS")
    sanciones_timeout: float = Field(1.5, gt=0, validation_alias="BFF_SANCIONES_TIMEOUT_SEGS")
    catalogo_timeout: float = Field(0.6, gt=0, validation_alias="BFF_CATALOGO_TIMEOUT_SEGS")
    post_timeout: float = Field(9, gt=0, validation_alias="BFF_POST_TIMEOUT_SEGS")
    saga_timeout: float = Field(8, gt=0, validation_alias="SAGA_TIMEOUT_SEGS")
    max_peticiones: int = Field(32, ge=1, le=256)
    prestamos_max_concurrent: int = Field(16, ge=1, le=128)
    sanciones_max_concurrent: int = Field(5, ge=1, le=128)
    catalogo_max_concurrent: int = Field(8, ge=1, le=128)
    catalogo_por_resumen: int = Field(4, ge=1, le=32)
    sanciones_cb_threshold: int = Field(3, ge=1)
    sanciones_cb_recovery: float = Field(5, gt=0, validation_alias="BFF_SANCIONES_CB_RECOVERY_SEGS")
    max_respuesta_bytes: int = Field(2_097_152, ge=1024, le=8_388_608)

    @field_validator("api_key")
    @classmethod
    def clave_no_vacia(cls, valor):
        if not valor or not valor.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in valor):
            raise ValueError("API_KEY debe contener caracteres ASCII visibles sin espacios")
        return valor

    @field_validator("prestamos_url", "sanciones_url")
    @classmethod
    def url_interna(cls, valor):
        url = urlsplit(valor)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment or url.path not in {"", "/"}):
            raise ValueError("La URL debe ser HTTP(S), sin credenciales, query ni prefijo de rutas")
        return valor.rstrip("/")

    @field_validator("catalogo_target")
    @classmethod
    def destino_grpc(cls, valor):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+:[0-9]{1,5}", valor) or not 1 <= int(valor.rsplit(":", 1)[1]) <= 65535:
            raise ValueError("BFF_CATALOGO_TARGET debe tener formato host:puerto válido")
        return valor

    @model_validator(mode="after")
    def presupuestos(self):
        if self.resumen_timeout <= self.prestamos_timeout:
            raise ValueError("BFF_RESUMEN_TIMEOUT_SEGS debe dejar margen después de consultar Préstamos")
        if self.post_timeout <= self.saga_timeout + 0.25:
            raise ValueError("BFF_POST_TIMEOUT_SEGS debe superar SAGA_TIMEOUT_SEGS más 0,25 s")
        if self.catalogo_por_resumen > self.catalogo_max_concurrent:
            raise ValueError("BFF_CATALOGO_POR_RESUMEN no puede superar BFF_CATALOGO_MAX_CONCURRENT")
        return self
