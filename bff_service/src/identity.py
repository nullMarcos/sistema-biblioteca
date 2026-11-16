from dataclasses import dataclass
import hmac
import re

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@dataclass(frozen=True)
class Identidad:
    cuenta: str
    rol: str
    socio_id: int | None

    def autorizar(self, socio_id):
        if self.rol == "socio" and self.socio_id != socio_id:
            raise HTTPException(403, {"codigo": "SOCIO_NO_AUTORIZADO", "mensaje": "No tiene acceso a ese socio"})

    def headers(self, request_id):
        resultado = {"X-User-Id": self.cuenta, "X-User-Role": self.rol, "X-Request-Id": request_id}
        if self.socio_id is not None:
            resultado["X-Socio-Id"] = str(self.socio_id)
        return resultado


def verificar_api_key(request: Request, clave: str | None = Security(api_key_header)):
    if not clave or not clave.isascii() or not hmac.compare_digest(clave, request.app.state.config.api_key):
        raise HTTPException(401, {"codigo": "NO_AUTORIZADO", "mensaje": "Credencial interna ausente o inválida"})


def identidad_gateway(request: Request, _: None = Security(verificar_api_key)):
    cuenta, rol = request.headers.get("X-User-Id", ""), request.headers.get("X-User-Role")
    socio = request.headers.get("X-Socio-Id", "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}", cuenta) or rol not in {"admin", "socio"}:
        raise HTTPException(401, {"codigo": "IDENTIDAD_INVALIDA", "mensaje": "Se requiere identidad validada por Gateway"})
    if rol == "socio" and (not re.fullmatch(r"[1-9][0-9]{0,18}", socio) or int(socio) > 9223372036854775807):
        raise HTTPException(401, {"codigo": "IDENTIDAD_INVALIDA", "mensaje": "La cuenta no tiene un socio vinculado"})
    return Identidad(cuenta, rol, int(socio) if rol == "socio" else None)
