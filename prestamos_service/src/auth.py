import os

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

API_KEY_VALIDA = os.getenv("API_KEY")
if not API_KEY_VALIDA:
    raise RuntimeError("La variable de entorno API_KEY no está definida.")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def verificar_api_key(x_api_key: str = Security(api_key_header)):
    if not x_api_key or x_api_key != API_KEY_VALIDA:
        raise HTTPException(
            status_code=401,
            detail={"codigo": "NO_AUTORIZADO", "mensaje": "API KEY inválida o ausente"}
        )