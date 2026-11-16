from dataclasses import dataclass
import re

from fastapi import Depends, HTTPException, Request

from src.auth import verificar_api_key


@dataclass(frozen=True)
class Identidad:
    cuenta: str | None
    rol: str
    socio_id: int | None = None

    def autorizar(self, socio_id):
        if self.rol == "socio" and self.socio_id != socio_id:
            raise HTTPException(403, {"codigo": "SOCIO_NO_AUTORIZADO", "mensaje": "No tiene acceso a ese socio"})

    def exigir_personal(self):
        if self.rol == "socio":
            raise HTTPException(403, {"codigo": "ROL_NO_AUTORIZADO", "mensaje": "La operación requiere personal autorizado"})


def identidad_interna(request: Request, _: None = Depends(verificar_api_key)):
    cuenta, rol, socio = (request.headers.get(h) for h in ("X-User-Id", "X-User-Role", "X-Socio-Id"))
    # API Key sin actor es la interfaz de mantenimiento interna ya existente.
    # Gateway siempre reemplaza e inyecta la identidad del usuario público.
    if cuenta is None and rol is None and socio is None:
        return Identidad(None, "interno")
    if not cuenta or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}", cuenta) or rol not in {"admin", "socio"}:
        raise HTTPException(401, {"codigo": "IDENTIDAD_INVALIDA", "mensaje": "Identidad interna incompleta o inválida"})
    if rol == "socio" and (not socio or not re.fullmatch(r"[1-9][0-9]{0,18}", socio) or int(socio) > 9223372036854775807):
        raise HTTPException(401, {"codigo": "IDENTIDAD_INVALIDA", "mensaje": "La cuenta no tiene un socio válido vinculado"})
    return Identidad(cuenta, rol, int(socio) if rol == "socio" else None)
